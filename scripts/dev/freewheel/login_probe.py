#!/usr/bin/env python3
"""Probe FreeWheel API login without printing secrets.

Usage:

  FREEWHEEL_USERNAME='user@example.com' FREEWHEEL_PASSWORD='...' \
    python scripts/dev/freewheel/login_probe.py

  FREEWHEEL_API_TOKEN='...' python scripts/dev/freewheel/login_probe.py

The script prints sanitized diagnostics only. It never prints the password,
submitted API token, minted bearer token, or Authorization header.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FREEWHEEL_HOSTS = {
    "production": "https://api.freewheel.tv",
    "staging": "https://api.stg.freewheel.tv",
}

TOKEN_PATH = "/auth/token"
TOKEN_INFO_PATH = "/auth/token/info"
SITES_PATH = "/services/v4/sites"

EXIT_CONFIG = 1
EXIT_AUTH = 2
EXIT_TOKEN_INFO = 3
EXIT_INVENTORY = 4

SECRET_KEYS = {"access_token", "api_token", "password", "authorization", "token"}


@dataclass
class HttpResult:
    status: int
    body: str


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("<redacted>" if key.lower() in SECRET_KEYS else _redact(val)) for key, val in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _body_excerpt(body: str, *, limit: int = 800) -> str:
    if not body:
        return ""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        lowered = body.lower()
        if any(key in lowered for key in SECRET_KEYS):
            return "<redacted: body contained secret-like field>"
        return body[:limit]
    return json.dumps(_redact(parsed), indent=2, sort_keys=True)[:limit]


def _request(
    method: str, url: str, *, headers: dict[str, str], data: bytes | None = None, timeout: float
) -> HttpResult:
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HttpResult(status=response.status, body=body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return HttpResult(status=exc.code, body=body)
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Network timeout") from exc


def _json_body(result: HttpResult) -> dict[str, Any]:
    if not result.body:
        return {}
    try:
        parsed = json.loads(result.body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Expected JSON response, got: {_body_excerpt(result.body)}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Expected JSON object, got: {_body_excerpt(result.body)}")
    return parsed


def _print_step(name: str, result: HttpResult) -> None:
    print(f"{name}: HTTP {result.status}")
    if result.body:
        print(_body_excerpt(result.body))


def _mint_token(base_url: str, username: str, password: str, timeout: float) -> tuple[str, dict[str, Any]]:
    payload = urlencode({"grant_type": "password", "username": username, "password": password}).encode()
    result = _request(
        "POST",
        f"{base_url}{TOKEN_PATH}",
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        data=payload,
        timeout=timeout,
    )
    _print_step("POST /auth/token", result)
    if not 200 <= result.status < 300:
        raise SystemExit(EXIT_AUTH)
    body = _json_body(result)
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        print("ERROR: /auth/token succeeded but response did not contain access_token.")
        raise SystemExit(EXIT_AUTH)
    return token, body


def _token_info(base_url: str, bearer: str, timeout: float) -> dict[str, Any]:
    result = _request(
        "GET",
        f"{base_url}{TOKEN_INFO_PATH}",
        headers={"accept": "application/json", "authorization": f"Bearer {bearer}"},
        timeout=timeout,
    )
    _print_step("GET /auth/token/info", result)
    if not 200 <= result.status < 300:
        raise SystemExit(EXIT_TOKEN_INFO)
    return _json_body(result)


def _inventory_probe(base_url: str, bearer: str, timeout: float) -> None:
    result = _request(
        "GET",
        f"{base_url}{SITES_PATH}?per_page=1",
        headers={"accept": "application/json", "authorization": f"Bearer {bearer}"},
        timeout=timeout,
    )
    _print_step("GET /services/v4/sites?per_page=1", result)
    if not 200 <= result.status < 300:
        raise SystemExit(EXIT_INVENTORY)
    body = _json_body(result)
    print(
        "Inventory probe summary: "
        f"items={len(body.get('items') or [])} total_count={body.get('total_count', body.get('total'))}"
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe FreeWheel auth/token-info and optional inventory access.")
    parser.add_argument(
        "--environment",
        choices=sorted(FREEWHEEL_HOSTS),
        default=os.environ.get("FREEWHEEL_ENVIRONMENT", "production"),
        help="FreeWheel environment. Env: FREEWHEEL_ENVIRONMENT. Default: production.",
    )
    parser.add_argument("--base-url", default=os.environ.get("FREEWHEEL_BASE_URL"), help="Override FreeWheel base URL.")
    parser.add_argument("--username", default=os.environ.get("FREEWHEEL_USERNAME"), help="Env: FREEWHEEL_USERNAME.")
    parser.add_argument("--password", default=os.environ.get("FREEWHEEL_PASSWORD"), help="Env: FREEWHEEL_PASSWORD.")
    parser.add_argument("--api-token", default=os.environ.get("FREEWHEEL_API_TOKEN"), help="Env: FREEWHEEL_API_TOKEN.")
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("FREEWHEEL_TIMEOUT", "30")))
    parser.add_argument("--skip-inventory", action="store_true", help="Only test auth/token-info.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base_url = (args.base_url or FREEWHEEL_HOSTS[args.environment]).rstrip("/")

    username = args.username
    password = args.password
    api_token = args.api_token

    if api_token:
        auth_mode = "api_token"
        bearer = api_token
    elif username:
        auth_mode = "password_grant"
        password = password or getpass.getpass("FreeWheel password: ")
        bearer, minted = _mint_token(base_url, username, password, args.timeout)
        print(
            "Mint summary: "
            f"token_type={minted.get('token_type')} expires_in={minted.get('expires_in')} "
            f"created_at={minted.get('created_at')}"
        )
    else:
        print("ERROR: provide FREEWHEEL_API_TOKEN or FREEWHEEL_USERNAME + FREEWHEEL_PASSWORD.", file=sys.stderr)
        return EXIT_CONFIG

    print(f"Probe target: environment={args.environment} base_url={base_url} auth_mode={auth_mode}")
    info = _token_info(base_url, bearer, args.timeout)
    print(
        "Token-info summary: "
        f"user_id={info.get('user_id')} "
        f"expires_in={info.get('expires_in', info.get('expires_in_seconds'))} "
        f"created_at={info.get('created_at')}"
    )
    if not args.skip_inventory:
        _inventory_probe(base_url, bearer, args.timeout)
    print("FreeWheel probe completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
