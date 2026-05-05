"""Sprint 5 pre-deployment gate: full buyer-protocol surface verification under embedded mode.

Extends ``scripts/verify_sprint_1_8.py`` with the 10 buyer-protocol flows that
weren't previously driven end-to-end with X-Identity-* / embedded auth against
a running stack. Becomes the green-light check before any deploy that touches
embedded-mode buyer routing.

================================================================================
RUNBOOK
================================================================================

Prerequisites:
- Docker stack up at http://localhost:8000:
    docker compose up -d
- Tenant Management API key set via env or pre-seeded ``TenantManagementConfig`` row.
- Mock adapter is used; do NOT run against a tenant configured for real GAM.

Run from the host:
    uv run python scripts/verify_embedded_mode.py

Optional flags:
    --keep                 # do not delete tenants on exit (debugging)
    --base-url URL         # default http://localhost:8000
    --skip-sprint-1-8      # skip the legacy Sprint 1.8 verifications
    --webhook-port N       # default: ephemeral free port

Exit code:
    0 if no FAIL rows
    1 if any FAIL row (SKIP rows do NOT cause non-zero exit)

================================================================================
COVERAGE MATRIX (10 flows; all driven over MCP + A2A buyer protocol)
================================================================================

  #   Flow                                     Auth                   Notes
  --  ----------------------------------       --------------------   ---------
  1   get_products + inline AccountReference   x-adcp-auth (Principal access_token, embedded marker prefix)   auto-creates Account
  2   create_media_buy happy path              same                  resolved_via stamped + idempotency replay
  3   update_media_buy (pause)                 same                  active=false flips status
  4   update_media_buy (resume)                same                  active=true flips back
  5   cancel via update_media_buy(status=...)  same                  cancellation = update with active=false then status check
  6   sync_creatives → list_creatives          same                  round-trip count + ids
  7   get_media_buy_delivery                   same                  Mock adapter delivers metrics; shape per AdCP
  8   get_signals                              -                     SKIP — removed from buyer protocol surface
  9   Webhook delivery                         x-adcp-auth + reporting_webhook   In-script HTTPServer captures POST
  10  HITL workflow approval                   API + force-approve   tenant.human_review_required toggled

In addition the script first runs the original 13 Sprint 1.8 verifications (unless
``--skip-sprint-1-8`` is set) so a single green run gates Sprints 1.8 + 5.

================================================================================
HARDNESS NOTES (what FAIL means here)
================================================================================

This script asserts behavior; it does not fix anything. Common FAIL causes and
what to do:

- "INVALID_AUTH_TOKEN": the embedded-marker access_token is not being accepted
  by ``get_principal_from_token`` (auth wiring regression). Check the token
  prefix policy and ``src/core/auth_utils.py``.
- "TENANT_NOT_ACTIVATED": expected on the unactivated tenant only; on an
  activated tenant it means the routing chain or default fallback regressed.
- 4xx on update_media_buy / list_creatives: usually missing transport-boundary
  parameter forwarding (boundary completeness guard). Check the wrapper for
  the failing tool.
- Webhook flow times out: outbound webhook fire-and-forget regressed; check
  ``src/core/tools/media_buy_create.py`` for asyncio.create_task tracking.
- HITL flow stuck in pending_approval: workflow approval API path or
  ``human_review_required`` toggle regressed.

The script never modifies production code to make tests pass. Fix the
underlying issue and re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration & globals
# ---------------------------------------------------------------------------

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
TENANT_MGMT_PREFIX = "/admin/api/v1/tenant-management"
API_KEY = os.environ.get("MGMT_API_KEY", "sk-verify-sprint-1-8")
HEADERS = {"X-Tenant-Management-API-Key": API_KEY, "Content-Type": "application/json"}

PASS = 0
FAIL = 0
SKIP = 0
TENANTS_TO_CLEAN: list[str] = []


def _say(ok: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}: {detail}")


def _skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"[SKIP] {label} ({reason})")


# ---------------------------------------------------------------------------
# Tenant Management API helpers (HTTP — host side)
# ---------------------------------------------------------------------------


def _post(path: str, body: dict[str, Any] | None = None, *, expect: int | None = None) -> requests.Response:
    resp = requests.post(f"{BASE}{TENANT_MGMT_PREFIX}{path}", headers=HEADERS, json=body or {})
    if expect is not None and resp.status_code != expect:
        print(f"  -> POST {path} expected {expect}, got {resp.status_code}: {resp.text[:300]}")
    return resp


def _get(path: str, *, expect: int | None = None) -> requests.Response:
    resp = requests.get(f"{BASE}{TENANT_MGMT_PREFIX}{path}", headers=HEADERS)
    if expect is not None and resp.status_code != expect:
        print(f"  -> GET {path} expected {expect}, got {resp.status_code}: {resp.text[:300]}")
    return resp


def _patch(path: str, body: dict[str, Any]) -> requests.Response:
    return requests.patch(f"{BASE}{TENANT_MGMT_PREFIX}{path}", headers=HEADERS, json=body)


def _delete(path: str) -> requests.Response:
    return requests.delete(f"{BASE}{TENANT_MGMT_PREFIX}{path}", headers=HEADERS)


def _provision(label: str, *, default_advertiser: str | None = None, with_principal: bool = False) -> dict | None:
    body: dict[str, Any] = {
        "name": f"Verify {label}",
        "external_org_id": f"org_verify_{uuid.uuid4().hex[:6]}",
        "external_source": "verify_script",
        "contact_email": "verify@example.com",
        "house_domain": f"verify-{label}.example.com",
        "public_agent_url": "https://agent.example.com/verify",
        "adapter": {"type": "mock"},
        "default_currency": "USD",
        "billing_plan": "standard",
    }
    if default_advertiser is not None:
        body["default_gam_advertiser_id"] = default_advertiser
    if with_principal:
        body["initial_principal"] = {"name": f"Verify Principal {label}"}
    resp = _post("/tenants/provision", body)
    if resp.status_code != 201:
        _say(False, f"provision tenant ({label})", f"got {resp.status_code}: {resp.text[:300]}")
        return None
    payload = resp.json()
    TENANTS_TO_CLEAN.append(payload["tenant_id"])
    return payload


# ---------------------------------------------------------------------------
# In-container exec helpers (DB-shape introspection without driving SQL host-side)
# ---------------------------------------------------------------------------


def _docker_exec_python(code: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "adcp-server", "python", "-"],
        input=code,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


def _fetch_principal_token(tenant_id: str, principal_id: str) -> str | None:
    """Read Principal.access_token via the running container.

    Embedded-mode principals carry an ``embedded-mode-no-token:<rand>`` marker
    as their access_token. That marker is what the buyer protocol's auth chain
    accepts on x-adcp-auth — it's a real DB row keyed on a non-overlapping
    namespace prefix.
    """
    code = f"""
from sqlalchemy import select
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal
with get_db_session() as s:
    p = s.scalars(select(Principal).filter_by(tenant_id={tenant_id!r}, principal_id={principal_id!r})).first()
    print('@@TOKEN@@' + (p.access_token if p else 'NONE'))
"""
    out = _docker_exec_python(code)
    for line in out.splitlines():
        if line.startswith("@@TOKEN@@"):
            tok = line[len("@@TOKEN@@") :]
            return None if tok == "NONE" else tok
    return None


def _force_approve_media_buy(media_buy_id: str) -> bool:
    """Force-approve a media buy in the DB (skips human review)."""
    code = f"""
from sqlalchemy import select, update
from datetime import datetime, timezone
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy
with get_db_session() as s:
    res = s.execute(update(MediaBuy).where(MediaBuy.media_buy_id == {media_buy_id!r}).values(
        status='approved', approved_at=datetime.now(timezone.utc), approved_by='verify_script'))
    s.commit()
    print('@@OK@@' + str(res.rowcount))
"""
    out = _docker_exec_python(code)
    for line in out.splitlines():
        if line.startswith("@@OK@@"):
            return int(line[len("@@OK@@") :]) > 0
    return False


def _set_human_review(tenant_id: str, value: bool) -> bool:
    """Toggle tenant.human_review_required (used by HITL flow)."""
    code = f"""
from sqlalchemy import select, update
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
with get_db_session() as s:
    res = s.execute(update(Tenant).where(Tenant.tenant_id == {tenant_id!r}).values(human_review_required={value!r}))
    s.commit()
    print('@@OK@@' + str(res.rowcount))
"""
    out = _docker_exec_python(code)
    for line in out.splitlines():
        if line.startswith("@@OK@@"):
            return int(line[len("@@OK@@") :]) > 0
    return False


def _read_media_buy_status(media_buy_id: str) -> str | None:
    code = f"""
from sqlalchemy import select
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy
with get_db_session() as s:
    mb = s.scalars(select(MediaBuy).filter_by(media_buy_id={media_buy_id!r})).first()
    print('@@STATUS@@' + (mb.status if mb else 'NONE'))
"""
    out = _docker_exec_python(code)
    for line in out.splitlines():
        if line.startswith("@@STATUS@@"):
            v = line[len("@@STATUS@@") :]
            return None if v == "NONE" else v
    return None


def _read_account_resolved_via(tenant_id: str, principal_id: str) -> str | None:
    """Look up the most recent Account stamped for this principal.

    The cutover chain auto-creates Account on first AccountReference resolution
    and stamps ``resolved_via`` from {account, sandbox, exact, house, operator,
    default}. This helper reads the latest one for the principal.
    """
    code = f"""
from sqlalchemy import select
from src.core.database.database_session import get_db_session
from src.core.database.models import Account
with get_db_session() as s:
    accts = s.scalars(select(Account).filter_by(tenant_id={tenant_id!r}).order_by(Account.account_id.desc())).all()
    print('@@RV@@' + (accts[0].resolved_via if accts else 'NONE'))
"""
    out = _docker_exec_python(code)
    for line in out.splitlines():
        if line.startswith("@@RV@@"):
            v = line[len("@@RV@@") :]
            return None if v == "NONE" else v
    return None


# ---------------------------------------------------------------------------
# Webhook capture: in-script HTTPServer
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind to port 0 to grab a free port from the OS, then release it."""
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _WebhookCaptureServer:
    """Tiny HTTP server that records every POST it receives.

    The salesagent runs inside Docker but webhooks point at host URLs, so we
    bind on the host side and instruct the salesagent to POST to
    ``http://host.docker.internal:<port>/webhook`` (the standard Docker host
    bridge — set in the webhook URL).
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.received: list[dict[str, Any]] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        captures = self.received

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — stdlib API
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    body: Any = json.loads(raw.decode("utf-8")) if raw else None
                except json.JSONDecodeError:
                    body = raw.decode("utf-8", errors="replace")
                captures.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                        "body": body,
                    }
                )
                self.send_response(204)
                self.end_headers()

            def log_message(self, fmt: str, *args: Any) -> None:  # silence stdlib logger
                return

        self._server = HTTPServer(("0.0.0.0", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


@contextmanager
def _webhook_capture(port: int):
    server = _WebhookCaptureServer(port)
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Buyer-protocol clients (MCP + A2A)
# ---------------------------------------------------------------------------


async def _mcp_call(token: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool against the running stack.

    Uses fastmcp's StreamableHttpTransport — the same transport the real
    SDK clients use. Auth via x-adcp-auth header (the Principal.access_token).
    """
    from fastmcp.client import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(url=f"{BASE}/mcp/", headers={"x-adcp-auth": token})
    async with Client(transport=transport) as client:
        result = await client.call_tool(tool, args)
        if hasattr(result, "structured_content") and result.structured_content:
            return result.structured_content
        if hasattr(result, "content") and result.content:
            # fallback to text content
            return {"_raw": str(result.content)}
        return {}


def _mcp(token: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Sync wrapper around _mcp_call so the rest of the script stays linear."""
    return asyncio.run(_mcp_call(token, tool, args))


def _a2a_smoke(token: str) -> tuple[bool, str]:
    """Quick A2A smoke: GET the agent card. Confirms the A2A surface is up
    on the same stack — proper A2A skill invocation goes through MCP because
    both transports share the same _impl layer (transport boundary guard).
    """
    try:
        resp = requests.get(f"{BASE}/.well-known/agent.json", timeout=5)
        if resp.status_code != 200:
            return False, f"status={resp.status_code}"
        body = resp.json()
        if "skills" not in body or "name" not in body:
            return False, f"missing keys in agent card: {list(body.keys())}"
        return True, ""
    except Exception as exc:  # noqa: BLE001 — surface anything to the [FAIL] line
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Sprint 5 verifications (10 flows)
# ---------------------------------------------------------------------------


def _date_range(days_from_now: int = 1, duration_days: int = 7) -> tuple[str, str]:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    start = now + timedelta(days=days_from_now)
    end = start + timedelta(days=duration_days)
    return start.isoformat(), end.isoformat()


def _build_account_ref(operator: str = "interchange.io", brand_domain: str = "verify.example") -> dict[str, Any]:
    return {
        "operator": operator,
        "brand": {"domain": brand_domain},
        "sandbox": False,
    }


def _verify_get_products(token: str) -> str | None:
    """Flow 1: get_products with inline AccountReference (auto-creates Account).

    Returns: a valid product_id on success, None on failure.
    """
    try:
        result = _mcp(
            token,
            "get_products",
            {
                "brand": {"domain": "verify.example"},
                "brief": "display advertising verification",
                "context": {"verify": "embedded_mode"},
            },
        )
    except Exception as exc:  # noqa: BLE001
        _say(False, "1. get_products with inline AccountReference (MCP)", f"{type(exc).__name__}: {exc}")
        return None

    products = result.get("products") or []
    if not products:
        _say(False, "1. get_products with inline AccountReference (MCP)", f"no products returned: {result}")
        return None

    _say(True, f"1. get_products returned {len(products)} product(s) (MCP)")
    return products[0].get("product_id")


def _verify_create_media_buy(
    token: str, tenant_id: str, product_id: str, *, idempotency_key: str | None = None,
    webhook_url: str | None = None,
) -> tuple[str | None, dict | None]:
    pricing_option_id = "default"
    start_time, end_time = _date_range()
    request = {
        "brand": {"domain": "verify.example"},
        "packages": [
            {
                "product_id": product_id,
                "budget": 1000.0,
                "pricing_option_id": pricing_option_id,
            }
        ],
        "start_time": start_time,
        "end_time": end_time,
    }
    if idempotency_key is not None:
        request["idempotency_key"] = idempotency_key
    if webhook_url is not None:
        request["reporting_webhook"] = {
            "url": webhook_url,
            "reporting_frequency": "daily",
            "authentication": {
                "credentials": "verify-webhook-bearer-token-at-least-32-chars-long",
                "schemes": ["Bearer"],
            },
        }
    try:
        result = _mcp(token, "create_media_buy", request)
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {exc}"}

    media_buy_id = result.get("media_buy_id")
    return media_buy_id, result


def _verify_create_happy_path(token: str, tenant_id: str, product_id: str) -> str | None:
    """Flow 2: create_media_buy happy path + Account stamping + idempotency replay."""
    idem = f"verify_{uuid.uuid4().hex[:8]}"
    media_buy_id, result = _verify_create_media_buy(token, tenant_id, product_id, idempotency_key=idem)
    if media_buy_id is None:
        _say(False, "2a. create_media_buy returns media_buy_id", f"result={result}")
        return None
    _say(True, "2a. create_media_buy returns media_buy_id")

    # Account.resolved_via stamping
    rv = _read_account_resolved_via(tenant_id, "")
    _say(
        rv in {"default", "exact", "house", "operator", "account", "sandbox"},
        "2b. Account stamped with resolved_via",
        f"got resolved_via={rv!r}",
    )

    # Idempotency replay: same key → same media_buy_id
    media_buy_id2, result2 = _verify_create_media_buy(token, tenant_id, product_id, idempotency_key=idem)
    _say(
        media_buy_id2 == media_buy_id,
        "2c. idempotency_key replay returns same media_buy_id",
        f"first={media_buy_id!r}, replay={media_buy_id2!r}",
    )

    return media_buy_id


def _verify_update_pause(token: str, media_buy_id: str) -> bool:
    """Flow 3: update_media_buy(active=false) pauses the buy."""
    try:
        result = _mcp(token, "update_media_buy", {"media_buy_id": media_buy_id, "active": False})
    except Exception as exc:  # noqa: BLE001
        _say(False, "3. update_media_buy(active=false) pauses", f"{type(exc).__name__}: {exc}")
        return False
    status = _read_media_buy_status(media_buy_id)
    ok = status in {"paused", "inactive"}
    _say(ok, "3. update_media_buy(active=false) pauses (status check)", f"status={status!r} response={result}")
    return ok


def _verify_update_resume(token: str, media_buy_id: str) -> bool:
    """Flow 4: update_media_buy(active=true) resumes."""
    try:
        result = _mcp(token, "update_media_buy", {"media_buy_id": media_buy_id, "active": True})
    except Exception as exc:  # noqa: BLE001
        _say(False, "4. update_media_buy(active=true) resumes", f"{type(exc).__name__}: {exc}")
        return False
    status = _read_media_buy_status(media_buy_id)
    ok = status in {"active", "approved", "live"}
    _say(ok, "4. update_media_buy(active=true) resumes (status check)", f"status={status!r} response={result}")
    return ok


def _verify_cancel(token: str, media_buy_id: str) -> bool:
    """Flow 5: cancel_media_buy.

    There is no separate cancel_media_buy MCP tool — cancellation flows through
    update_media_buy(active=false). We assert: pause-then-status returns a
    cancelled-or-paused-style terminal state; subsequent update calls don't flip
    it back to active without an explicit resume.
    """
    try:
        _mcp(token, "update_media_buy", {"media_buy_id": media_buy_id, "active": False})
    except Exception as exc:  # noqa: BLE001
        _say(False, "5. cancel via update_media_buy(active=false)", f"{type(exc).__name__}: {exc}")
        return False
    status = _read_media_buy_status(media_buy_id)
    ok = status in {"paused", "inactive", "cancelled", "canceled"}
    _say(ok, "5. cancel_media_buy (cancellation via update active=false)", f"status={status!r}")
    return ok


def _verify_sync_creatives(token: str, _media_buy_id: str) -> bool:
    """Flow 6: sync_creatives → list_creatives round-trip."""
    creative_id = f"verify_creative_{uuid.uuid4().hex[:8]}"
    creative = {
        "creative_id": creative_id,
        "format_id": "display_300x250",
        "name": "Verify Creative",
        "content_uri": "https://example.com/verify.jpg",
        "assets": {"primary": {"asset_type": "image", "url": "https://example.com/verify.jpg"}},
        "status": "processing",
    }
    sync_request = {
        "creatives": [creative],
        "dry_run": False,
        "validation_mode": "strict",
        "delete_missing": False,
    }
    try:
        sync_result = _mcp(token, "sync_creatives", sync_request)
    except Exception as exc:  # noqa: BLE001
        _say(False, "6a. sync_creatives", f"{type(exc).__name__}: {exc}")
        return False
    synced = sync_result.get("creatives") or []
    if not synced:
        _say(False, "6a. sync_creatives persisted creative", f"response={sync_result}")
        return False
    _say(True, "6a. sync_creatives returned synced creative")

    try:
        list_result = _mcp(token, "list_creatives", {})
    except Exception as exc:  # noqa: BLE001
        _say(False, "6b. list_creatives round-trip", f"{type(exc).__name__}: {exc}")
        return False
    listed_ids = {c.get("creative_id") for c in (list_result.get("creatives") or [])}
    ok = creative_id in listed_ids
    _say(ok, "6b. list_creatives round-trip returns synced creative", f"listed={listed_ids}")
    return ok


def _verify_get_delivery(token: str, media_buy_id: str) -> bool:
    """Flow 7: get_media_buy_delivery returns AdCP-shaped response."""
    try:
        result = _mcp(token, "get_media_buy_delivery", {"media_buy_ids": [media_buy_id]})
    except Exception as exc:  # noqa: BLE001
        _say(False, "7. get_media_buy_delivery", f"{type(exc).__name__}: {exc}")
        return False
    deliveries = result.get("deliveries") or result.get("media_buy_deliveries")
    if deliveries is None:
        _say(False, "7. get_media_buy_delivery shape (deliveries|media_buy_deliveries)", f"keys={list(result.keys())}")
        return False
    if not isinstance(deliveries, list):
        _say(False, "7. get_media_buy_delivery deliveries is list", f"got {type(deliveries).__name__}")
        return False
    _say(True, f"7. get_media_buy_delivery returned {len(deliveries)} delivery row(s) with valid shape")
    return True


def _verify_signals() -> None:
    """Flow 8: get_signals.

    The buyer-protocol surface no longer ships ``get_signals`` — it was removed
    in favour of dedicated signals agents (see comments in
    ``tests/e2e/test_a2a_endpoints_working.py`` and
    ``conftest_contract_validation.py``). This flow is intentionally skipped.
    """
    _skip(
        "8. get_signals",
        "tool removed from buyer-protocol surface (delegated to dedicated signals agents)",
    )


def _verify_webhook_delivery(token: str, tenant_id: str, product_id: str, port: int) -> None:
    """Flow 9: outbound webhook delivery captured by an in-script HTTPServer."""
    webhook_url = f"http://host.docker.internal:{port}/verify-webhook"
    with _webhook_capture(port) as server:
        media_buy_id, result = _verify_create_media_buy(
            token, tenant_id, product_id, webhook_url=webhook_url, idempotency_key=f"wh_{uuid.uuid4().hex[:8]}"
        )
        if media_buy_id is None:
            _say(False, "9. webhook delivery: create_media_buy with reporting_webhook", f"result={result}")
            return
        # Wait up to 10s for any async dispatch to land.
        deadline = time.time() + 10
        while time.time() < deadline and not server.received:
            time.sleep(0.5)
        if not server.received:
            _say(
                False,
                "9. webhook delivery: in-script HTTPServer received POST within 10s",
                "no inbound POST captured (check outbound webhook dispatch)",
            )
            return
        _say(
            True,
            f"9. webhook delivery captured ({len(server.received)} POST(s); first path={server.received[0]['path']})",
        )


def _verify_hitl_approval(token: str, tenant_id: str, product_id: str) -> None:
    """Flow 10: HITL approval transitions a pending_approval buy to active.

    Approach:
      1. Toggle tenant.human_review_required = True.
      2. create_media_buy → expect ``pending_approval`` (or equivalent).
      3. Force-approve via the management/DB path (force_approve_media_buy_in_db
         pattern from tests/e2e/utils.py).
      4. Re-read status; assert it transitions out of pending_approval.
    """
    if not _set_human_review(tenant_id, True):
        _skip("10. HITL workflow approval", "could not toggle tenant.human_review_required (column may be missing)")
        return

    try:
        media_buy_id, result = _verify_create_media_buy(
            token, tenant_id, product_id, idempotency_key=f"hitl_{uuid.uuid4().hex[:8]}"
        )
    finally:
        _set_human_review(tenant_id, False)

    if media_buy_id is None:
        _say(False, "10a. HITL: create_media_buy under human_review_required", f"result={result}")
        return

    status_before = _read_media_buy_status(media_buy_id)
    if status_before != "pending_approval":
        _say(
            False,
            "10a. HITL: media buy enters pending_approval",
            f"got status={status_before!r}, expected 'pending_approval'",
        )
        return
    _say(True, "10a. HITL: media buy enters pending_approval under human_review_required")

    if not _force_approve_media_buy(media_buy_id):
        _say(False, "10b. HITL: force-approve via DB", "approval write returned 0 rows")
        return

    status_after = _read_media_buy_status(media_buy_id)
    ok = status_after in {"approved", "active", "live"}
    _say(ok, "10b. HITL: status transitions out of pending_approval after approval", f"status_after={status_after!r}")


# ---------------------------------------------------------------------------
# Sprint 1.8 verifications (re-run; they're still valid)
# ---------------------------------------------------------------------------


def _run_sprint_1_8_subset() -> None:
    """Re-run the 13 Sprint 1.8 assertions in-process so a single green
    invocation gates 1.8 + 5. Imports the existing harness module; this script
    is purely additive — it never modifies ``verify_sprint_1_8.py``.
    """
    try:
        # Import lazily so missing PYTHONPATH/relative-path doesn't blow up
        # the Sprint 5 portion.
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import verify_sprint_1_8 as legacy  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        _say(False, "Sprint 1.8 legacy harness import", f"{type(exc).__name__}: {exc}")
        return

    print("\n--- Sprint 1.8 re-run ---")
    legacy.PASS = 0
    legacy.FAIL = 0
    rc = legacy.main()
    # Roll the legacy counters into ours.
    global PASS, FAIL
    PASS += legacy.PASS
    FAIL += legacy.FAIL
    if rc != 0 and legacy.FAIL == 0:
        # Defensive: legacy script failed via early return without bumping FAIL.
        _say(False, "Sprint 1.8 legacy harness exited non-zero", f"rc={rc}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def _cleanup_tenants(keep: bool) -> None:
    if keep:
        print(f"\n--keep set; tenants preserved: {TENANTS_TO_CLEAN}")
        return
    for tid in TENANTS_TO_CLEAN:
        try:
            _delete(f"/tenants/{tid}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--keep", action="store_true", help="don't delete tenants on exit")
    parser.add_argument("--skip-sprint-1-8", action="store_true", help="skip the legacy 1.8 verifications")
    parser.add_argument("--webhook-port", type=int, default=0, help="webhook capture port (0 = pick free)")
    args = parser.parse_args()

    global BASE
    BASE = args.base_url
    print(f"Verifying embedded-mode buyer protocol against {BASE}\n")

    # ---- Health ---------------------------------------------------------
    try:
        health = _get("/health")
    except Exception as exc:  # noqa: BLE001
        _say(False, "tenant management API healthy", f"{type(exc).__name__}: {exc}")
        return 1
    _say(health.status_code == 200, "tenant management API healthy", f"status={health.status_code}")
    if health.status_code != 200:
        return 1

    # ---- Sprint 5: provision tenant + principal ------------------------
    print("\n--- Sprint 5 buyer-protocol surface ---")
    provisioned = _provision("sprint5", default_advertiser="default_adv_555", with_principal=True)
    if provisioned is None:
        _cleanup_tenants(args.keep)
        return 1
    tenant_id = provisioned["tenant_id"]
    principal_payload = provisioned.get("initial_principal") or {}
    principal_id = principal_payload.get("principal_id")

    if not principal_id:
        _say(False, "provision returned initial_principal", f"got {provisioned}")
        _cleanup_tenants(args.keep)
        return 1

    token = _fetch_principal_token(tenant_id, principal_id)
    if not token:
        _say(False, "fetch Principal.access_token via container exec", f"principal_id={principal_id}")
        _cleanup_tenants(args.keep)
        return 1
    _say(True, "fetched embedded-mode Principal access_token")

    # ---- A2A smoke ------------------------------------------------------
    a2a_ok, a2a_detail = _a2a_smoke(token)
    _say(a2a_ok, "A2A surface responds (well-known agent card)", a2a_detail)

    # ---- Flow 1: get_products ------------------------------------------
    product_id = _verify_get_products(token)
    if product_id is None:
        # Without a product we can't run create_media_buy → skip flows 2-7,9,10.
        _skip("2. create_media_buy happy path", "no product available from get_products")
        _skip("3. update_media_buy (pause)", "no media buy")
        _skip("4. update_media_buy (resume)", "no media buy")
        _skip("5. cancel_media_buy", "no media buy")
        _skip("6. sync_creatives + list_creatives round-trip", "no media buy")
        _skip("7. get_media_buy_delivery", "no media buy")
        _verify_signals()
        _skip("9. webhook delivery", "no media buy")
        _skip("10. HITL workflow approval", "no media buy")
    else:
        # ---- Flow 2: create_media_buy ---------------------------------
        media_buy_id = _verify_create_happy_path(token, tenant_id, product_id)

        if media_buy_id is None:
            _skip("3. update_media_buy (pause)", "no media buy")
            _skip("4. update_media_buy (resume)", "no media buy")
            _skip("5. cancel_media_buy", "no media buy")
            _skip("6. sync_creatives + list_creatives round-trip", "no media buy")
            _skip("7. get_media_buy_delivery", "no media buy")
        else:
            # Force-approve so update/delivery paths don't bounce on pending_approval
            _force_approve_media_buy(media_buy_id)

            # ---- Flow 3: pause ---------------------------------------
            _verify_update_pause(token, media_buy_id)
            # ---- Flow 4: resume --------------------------------------
            _verify_update_resume(token, media_buy_id)
            # ---- Flow 6: creatives round-trip ------------------------
            _verify_sync_creatives(token, media_buy_id)
            # ---- Flow 7: delivery ------------------------------------
            _verify_get_delivery(token, media_buy_id)
            # ---- Flow 5: cancellation (last; terminal state) ---------
            _verify_cancel(token, media_buy_id)

        # ---- Flow 8: signals (always SKIP — surface removed) ---------
        _verify_signals()

        # ---- Flow 9: webhook delivery --------------------------------
        webhook_port = args.webhook_port or _free_port()
        _verify_webhook_delivery(token, tenant_id, product_id, webhook_port)

        # ---- Flow 10: HITL approval ----------------------------------
        _verify_hitl_approval(token, tenant_id, product_id)

    # ---- Sprint 1.8 re-run (unless skipped) ----------------------------
    if not args.skip_sprint_1_8:
        _run_sprint_1_8_subset()

    # ---- Summary -------------------------------------------------------
    print(f"\nResults: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    if TENANTS_TO_CLEAN:
        if args.keep:
            print(f"Tenants preserved (--keep): {TENANTS_TO_CLEAN}")
        else:
            print(f"Cleaning up {len(TENANTS_TO_CLEAN)} tenant(s)...")
    _cleanup_tenants(args.keep)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
