"""Side-by-side wire-format comparison: Mathijs's calls vs our adapter's calls.

Runs each of the three endpoints Mathijs exercised twice — once with his
wire shape (query-string params, his field names), once with ours (JSON
body, our field names) — and prints both status + truncated body so we
can see which the live API actually accepts.

Reads are always run. Writes (create demand tag) are gated behind
``--write`` because they actually create entities. A successful create
is followed immediately by a delete to avoid orphans.

Credentials (provide one):
    SPRINGSERVE_TEST_API_TOKEN
    or SPRINGSERVE_USERNAME + SPRINGSERVE_PASSWORD

Tunables (optional):
    SPRINGSERVE_TEST_SUPPLY_ROUTER_ID    (default 148010, Mathijs's example)
    SPRINGSERVE_TEST_DEMAND_PARTNER_ID   (default 88061, Talpa)
    SPRINGSERVE_TEST_DEMAND_TAG_ID       (default 2149081, Mathijs's example)

Usage:
    uv run python scripts/springserve_compare_wire.py
    uv run python scripts/springserve_compare_wire.py --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests

DEFAULT_BASE_URL = "https://console.springserve.com/api/v0"
TIMEOUT = 30.0


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _mint_token(session: requests.Session, base_url: str, email: str, password: str) -> str:
    response = session.post(
        f"{base_url}/auth",
        json={"email": email, "password": password},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    token = body.get("token")
    if not token:
        raise RuntimeError(f"/auth response missing token: {body!r}")
    return token


def _resolve_token(session: requests.Session, base_url: str) -> str:
    token = os.environ.get("SPRINGSERVE_TEST_API_TOKEN")
    if token:
        return token
    email = _first_env("SPRINGSERVE_USERNAME", "SPRINGSERVE_TEST_EMAIL")
    password = _first_env("SPRINGSERVE_PASSWORD", "SPRINGSERVE_TEST_PASSWORD")
    if email and password:
        return _mint_token(session, base_url, email, password)
    print(
        "ERROR: set SPRINGSERVE_TEST_API_TOKEN or (SPRINGSERVE_USERNAME + SPRINGSERVE_PASSWORD)",
        file=sys.stderr,
    )
    sys.exit(2)


def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... ({len(text)} bytes total)"


def _request(
    session: requests.Session,
    token: str,
    base_url: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    """Return (status, url_sent, body_truncated). Never raises on HTTP error."""
    url = f"{base_url}{path}"
    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"
    headers = {"Authorization": token, "Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    response = session.request(
        method=method,
        url=url,
        headers=headers,
        json=json_body if json_body is not None else None,
        timeout=TIMEOUT,
    )
    return response.status_code, url, _truncate(response.text or "")


def _print_header(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


def _print_call(
    label: str, method: str, url: str, body: dict[str, Any] | None, status: int, response_body: str
) -> None:
    print(f"\n--- {label} ---")
    print(f"{method} {url}")
    if body is not None:
        print(f"body: {json.dumps(body)}")
    print(f"HTTP {status}")
    print(f"response: {response_body}")


def compare_supply_tags(
    session: requests.Session,
    token: str,
    base_url: str,
    supply_router_id: int,
) -> None:
    _print_header("1. GET /supply_tags")

    # Mathijs: GET /supply_tags?supply_router_id=148010
    status, url, body = _request(
        session,
        token,
        base_url,
        "GET",
        "/supply_tags",
        params={"supply_router_id": supply_router_id},
    )
    _print_call("Mathijs (supply_router_id filter)", "GET", url, None, status, body)

    # Ours: GET /supply_tags?page=1&per_page=100
    status, url, body = _request(
        session,
        token,
        base_url,
        "GET",
        "/supply_tags",
        params={"page": 1, "per_page": 100},
    )
    _print_call("Ours (paginated, no filter)", "GET", url, None, status, body)


def compare_report(
    session: requests.Session,
    token: str,
    base_url: str,
    demand_tag_id: int,
) -> None:
    _print_header("2. POST /report")

    today = datetime.now(UTC).date()
    start = (today - timedelta(days=30)).isoformat()
    end = today.isoformat()

    # Mathijs: POST /report?start_date=...&end_date=...&interval=day&demand_tag_ids=...
    status, url, body = _request(
        session,
        token,
        base_url,
        "POST",
        "/report",
        params={
            "start_date": start,
            "end_date": end,
            "interval": "day",
            "demand_tag_ids": demand_tag_id,
        },
    )
    _print_call("Mathijs (query string, start_date/end_date, interval=day)", "POST", url, None, status, body)

    # Ours: POST /report with JSON body using date_start/date_end + dimensions/metrics/filters
    json_body = {
        "date_start": start,
        "date_end": end,
        "dimensions": ["campaign_id", "demand_tag_id"],
        "metrics": ["impressions", "spend", "completions", "clicks"],
        "filters": {"demand_tag_id": [demand_tag_id]},
    }
    status, url, resp_body = _request(
        session,
        token,
        base_url,
        "POST",
        "/report",
        json_body=json_body,
    )
    _print_call("Ours (JSON body, date_start/date_end, dimensions+metrics)", "POST", url, json_body, status, resp_body)


def compare_demand_tag_create(
    session: requests.Session,
    token: str,
    base_url: str,
    demand_partner_id: int,
    write: bool,
) -> None:
    _print_header("3. POST /demand_tags")

    if not write:
        print("\nSkipped — pass --write to actually create demand tags.")
        print("Would have sent both Mathijs's query-string shape and our JSON-body shape.")
        return

    label = f"adcp-wire-compare-{int(datetime.now(UTC).timestamp())}"

    # Mathijs: POST /demand_tags?name=...&rate=25&vast_endpoint_url=...&demand_partner_id=...
    status, url, body = _request(
        session,
        token,
        base_url,
        "POST",
        "/demand_tags",
        params={
            "name": f"{label}_mathijs",
            "rate": 25,
            "vast_endpoint_url": "https://example.invalid/vast",
            "demand_partner_id": demand_partner_id,
        },
    )
    _print_call("Mathijs (query string, 4 fields, no campaign_id)", "POST", url, None, status, body)
    _maybe_cleanup_created_tag(session, token, base_url, body, label_prefix="Mathijs")

    # Ours: POST /demand_tags with full JSON body
    start = datetime.now(UTC) + timedelta(days=7)
    end = start + timedelta(days=14)
    # We need campaign_id — try creating a transient campaign for the test
    print("\n(Our shape requires campaign_id; creating a transient campaign first)")
    campaign_status, campaign_url, campaign_body = _request(
        session,
        token,
        base_url,
        "POST",
        "/campaigns",
        json_body={
            "name": f"{label}_campaign",
            "demand_partner_id": demand_partner_id,
            "is_active": False,
            "secondary_code": label,
        },
    )
    print(f"  POST /campaigns -> HTTP {campaign_status}")
    print(f"  response: {campaign_body}")
    campaign_id = None
    try:
        campaign_id = json.loads(campaign_body).get("id")
    except (json.JSONDecodeError, AttributeError):
        pass
    if not campaign_id:
        print("  -> could not extract campaign_id; skipping our JSON-body POST")
        return

    json_body = {
        "name": f"{label}_ours",
        "campaign_id": campaign_id,
        "demand_partner_id": demand_partner_id,
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "end_date": end.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "format": "video",
        "rate": "0.01",
        "rate_currency": "EUR",
        "cost_model_type": 0,
        "is_active": False,
        "country_codes": ["NL"],
        "country_targeting": "White List",
    }
    status, url, resp_body = _request(
        session,
        token,
        base_url,
        "POST",
        "/demand_tags",
        json_body=json_body,
    )
    _print_call("Ours (JSON body, campaign_id + start/end + format)", "POST", url, json_body, status, resp_body)
    _maybe_cleanup_created_tag(session, token, base_url, resp_body, label_prefix="Ours")

    # Cleanup the campaign we made
    print(f"\n  Cleaning up transient campaign id={campaign_id}")
    cleanup_status, _, _ = _request(session, token, base_url, "DELETE", f"/campaigns/{campaign_id}")
    print(f"  DELETE /campaigns/{campaign_id} -> HTTP {cleanup_status}")


def _maybe_cleanup_created_tag(
    session: requests.Session,
    token: str,
    base_url: str,
    response_body: str,
    *,
    label_prefix: str,
) -> None:
    """If the response contains an ``id``, DELETE that demand tag."""
    try:
        parsed = json.loads(response_body)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(parsed, dict):
        return
    tag_id = parsed.get("id")
    if not tag_id:
        return
    print(f"  Cleaning up {label_prefix} demand_tag id={tag_id}")
    status, _, _ = _request(session, token, base_url, "DELETE", f"/demand_tags/{tag_id}")
    print(f"  DELETE /demand_tags/{tag_id} -> HTTP {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--write", action="store_true", help="Run the demand-tag create comparisons (creates + deletes entities)"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    supply_router_id = int(os.environ.get("SPRINGSERVE_TEST_SUPPLY_ROUTER_ID", "148010"))
    demand_partner_id = int(os.environ.get("SPRINGSERVE_TEST_DEMAND_PARTNER_ID", "88061"))
    demand_tag_id = int(os.environ.get("SPRINGSERVE_TEST_DEMAND_TAG_ID", "2149081"))

    session = requests.Session()
    base_url = args.base_url.rstrip("/")
    token = _resolve_token(session, base_url)
    print(f"Authenticated against {base_url}")
    print(f"  supply_router_id={supply_router_id} demand_partner_id={demand_partner_id} demand_tag_id={demand_tag_id}")

    compare_supply_tags(session, token, base_url, supply_router_id)
    compare_report(session, token, base_url, demand_tag_id)
    compare_demand_tag_create(session, token, base_url, demand_partner_id, write=args.write)

    print()
    print("=" * 78)
    print("  Done. Compare statuses + response shapes above to spot wire-format gaps.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
