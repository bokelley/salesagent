"""Sprint 1.8 live-verification script.

Drives the full Sprint 1.8 surface against a running dev Docker stack:
- Provision managed-mode tenants (with + without default_gam_advertiser_id)
- Buyer-advertiser-mappings CRUD
- Cutover behavior: routing-chain auto-create + TENANT_NOT_ACTIVATED
- /recent-buyers rollup with resolved_via
- /status setup_tasks block
- POST /refresh idempotency window

Run inside the adcp-server container so it can hit localhost + share DB:

    docker compose exec adcp-server python /app/scripts/verify_sprint_1_8.py

Set ``MGMT_API_KEY`` env var to the management API key (or pre-seed the
TenantManagementConfig row before invoking).

Exit code is 0 on all-pass, 1 if any row fails. Each row prints one
line: ``[PASS] description`` or ``[FAIL] description: detail``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from typing import Any

import requests

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
TENANT_MGMT_PREFIX = "/admin/api/v1/tenant-management"
API_KEY = os.environ.get("MGMT_API_KEY", "sk-verify-sprint-1-8")

HEADERS = {"X-Tenant-Management-API-Key": API_KEY, "Content-Type": "application/json"}

PASS = 0
FAIL = 0
TENANTS_TO_CLEAN: list[str] = []


def _say(ok: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}: {detail}")


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


def _provision(label: str, *, default_advertiser: str | None = None) -> str | None:
    body = {
        "name": f"Verify {label}",
        "external_org_id": f"org_verify_{uuid.uuid4().hex[:6]}",
        "external_source": "verify_script",
        "contact_email": "verify@example.com",
        "public_agent_url": "https://agent.example.com/verify",
        "adapter": {"type": "mock"},
        "default_currency": "USD",
        "billing_plan": "standard",
    }
    if default_advertiser is not None:
        body["default_gam_advertiser_id"] = default_advertiser
    resp = _post("/tenants/provision", body)
    if resp.status_code != 201:
        _say(False, f"provision tenant ({label})", f"got {resp.status_code}: {resp.text[:300]}")
        return None
    tid = resp.json()["tenant_id"]
    TENANTS_TO_CLEAN.append(tid)
    return tid


def _docker_exec_python(code: str) -> str:
    """Run Python inside the adcp-server container, piping the source on stdin.

    Avoids quoting hell from os.popen + ``python -c`` with multiline source.
    """
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "adcp-server", "python", "-"],
        input=code,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


def _exercise_cutover_in_container(tenant_id: str, *, expect_advertiser: str | None, expect_via: str) -> dict | None:
    """Drive _resolve_by_natural_key inside the container so we exercise
    the live DB + ORM + routing chain end-to-end."""
    code = f"""
import json
from sqlalchemy import select
from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Principal
from src.core.helpers.account_helpers import resolve_account
from src.core.resolved_identity import ResolvedIdentity
from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference2
from adcp.types.generated_poc.core.brand_ref import BrandReference
from src.core.database.repositories.uow import AccountUoW

with get_db_session() as s:
    s.info["management_api_caller"] = True
    if not s.scalars(select(Principal).filter_by(tenant_id={tenant_id!r}, principal_id='verify_buyer')).first():
        s.add(Principal(tenant_id={tenant_id!r}, principal_id='verify_buyer', name='Verify Buyer',
                        access_token='embedded-mode-no-token:verify',
                        platform_mappings={{'mock': {{'advertiser_id': 'placeholder'}}}}))
        s.commit()

ref = AccountReference(root=AccountReference2(
    operator='interchange.io',
    brand=BrandReference(domain='coca-cola.com', brand_id='sprite'),
    sandbox=False,
))
identity = ResolvedIdentity(tenant_id={tenant_id!r}, principal_id='verify_buyer')

try:
    with AccountUoW({tenant_id!r}) as uow:
        account_id = resolve_account(ref, identity, uow.accounts)
    with get_db_session() as s:
        acct = s.scalars(select(Account).filter_by(account_id=account_id)).first()
        gam_id = (acct.platform_mappings or {{}}).get('google_ad_manager', {{}}).get('advertiser_id')
        print('@@RESULT@@' + json.dumps({{
            'account_id': account_id,
            'resolved_via': acct.resolved_via,
            'advertiser_id': gam_id,
        }}))
except Exception as e:
    print('@@ERROR@@' + type(e).__name__ + ':' + str(e))
"""
    out = _docker_exec_python(code)
    for line in out.splitlines():
        if line.startswith("@@RESULT@@"):
            return json.loads(line[len("@@RESULT@@") :])
        if line.startswith("@@ERROR@@"):
            print(f"  -> in-container error: {line[len('@@ERROR@@') :]}")
            return None
    print(f"  -> unexpected output: {out[-500:]}")
    return None


def _exercise_unactivated_in_container(tenant_id: str) -> str | None:
    """Run resolve_account on an unactivated tenant; expect TENANT_NOT_ACTIVATED."""
    code = f"""
from src.core.helpers.account_helpers import resolve_account
from src.core.resolved_identity import ResolvedIdentity
from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference2
from adcp.types.generated_poc.core.brand_ref import BrandReference
from src.core.database.repositories.uow import AccountUoW

ref = AccountReference(root=AccountReference2(
    operator='nobody.example',
    brand=BrandReference(domain='nothing.example'),
    sandbox=False,
))
identity = ResolvedIdentity(tenant_id={tenant_id!r}, principal_id=None)
try:
    with AccountUoW({tenant_id!r}) as uow:
        resolve_account(ref, identity, uow.accounts)
    print('@@NO_RAISE@@')
except Exception as e:
    code = getattr(e, 'code', None)
    print('@@ERR@@' + (code or type(e).__name__))
"""
    out = _docker_exec_python(code)
    for line in out.splitlines():
        if line.startswith("@@ERR@@"):
            return line[len("@@ERR@@") :]
        if line == "@@NO_RAISE@@":
            return "NO_RAISE"
    print(f"  -> unexpected output: {out[-500:]}")
    return None


def main() -> int:
    print(f"Verifying Sprint 1.8 against {BASE}\n")

    # ---- Health ---------------------------------------------------------
    health = _get("/health")
    _say(health.status_code == 200, "tenant management API healthy", f"status={health.status_code}")
    if health.status_code != 200:
        return 1

    # ---- Provision activated tenant ------------------------------------
    activated = _provision("activated", default_advertiser="default_adv_999")
    if activated is None:
        return 1

    detail = _get(f"/tenants/{activated}").json()
    _say(
        detail.get("default_gam_advertiser_id") == "default_adv_999",
        "TenantDetail surfaces default_gam_advertiser_id",
        f"got {detail.get('default_gam_advertiser_id')!r}",
    )

    # ---- Cutover: auto-create on default fallback -----------------------
    result = _exercise_cutover_in_container(activated, expect_advertiser="default_adv_999", expect_via="default")
    if result is None:
        _say(False, "cutover: routing chain auto-creates Account on default fallback", "no result returned")
    else:
        _say(
            result["resolved_via"] == "default" and result["advertiser_id"] == "default_adv_999",
            "cutover: routing chain auto-creates Account on default fallback",
            f"got resolved_via={result['resolved_via']!r}, advertiser_id={result['advertiser_id']!r}",
        )

    # ---- Cutover: fast-path reuse on second call ------------------------
    second = _exercise_cutover_in_container(activated, expect_advertiser="default_adv_999", expect_via="default")
    _say(
        second is not None and result is not None and second["account_id"] == result["account_id"],
        "cutover: second call reuses Account via natural-key fast path",
        "different account_id returned" if second and result else "no result",
    )

    # ---- Routing rule CRUD ---------------------------------------------
    new_rule = _post(
        f"/tenants/{activated}/buyer-advertiser-mappings",
        {
            "operator_domain": "buyer.scope3.com",
            "brand_house": "pepsi.com",
            "brand_id": None,
            "gam_advertiser_id": "rule_adv_222",
        },
    )
    _say(
        new_rule.status_code == 201,
        "POST /buyer-advertiser-mappings creates rule",
        f"status={new_rule.status_code}: {new_rule.text[:200]}",
    )

    # Duplicate should 409
    dup = _post(
        f"/tenants/{activated}/buyer-advertiser-mappings",
        {
            "operator_domain": "buyer.scope3.com",
            "brand_house": "pepsi.com",
            "brand_id": None,
            "gam_advertiser_id": "different_adv",
        },
    )
    _say(dup.status_code == 409, "POST duplicate natural key returns 409", f"status={dup.status_code}")

    # List mappings
    listed = _get(f"/tenants/{activated}/buyer-advertiser-mappings").json()
    _say(listed.get("count") == 1, "GET /buyer-advertiser-mappings returns count", f"count={listed.get('count')}")

    # ---- /recent-buyers ------------------------------------------------
    rb = _get(f"/tenants/{activated}/recent-buyers")
    if rb.status_code != 200:
        _say(False, "GET /recent-buyers", f"status={rb.status_code}")
    else:
        buyers = rb.json().get("buyers", [])
        has_default = any(b.get("resolved_via") == "default" for b in buyers)
        _say(has_default, "GET /recent-buyers surfaces resolved_via=default", f"buyers={buyers}")

    # ---- /status setup_tasks block -------------------------------------
    status = _get(f"/tenants/{activated}/status")
    if status.status_code != 200:
        _say(False, "GET /status", f"status={status.status_code}")
    else:
        body = status.json()
        st = body.get("setup_tasks", {})
        _say(
            "blocker_count" in st and "warning_count" in st and "items" in st,
            "setup_tasks block has blocker_count + warning_count + items",
            f"keys={list(st.keys())}",
        )
        # AAO public_agent_url item should be hidden on managed tenants with it set
        item_ids = {i["id"] for i in st.get("items", [])}
        _say(
            "public_agent_url" not in item_ids,
            "setup_tasks hides public_agent_url item on managed tenant with it set",
            f"items={item_ids}",
        )

    # ---- /refresh idempotency ------------------------------------------
    r1 = _post(f"/tenants/{activated}/refresh")
    if r1.status_code != 202:
        _say(False, "POST /refresh returns 202", f"status={r1.status_code}: {r1.text[:200]}")
    else:
        ids1 = r1.json().get("sync_run_ids", {})
        _say(
            set(ids1.keys()) == {"inventory", "custom_targeting", "advertisers"},
            "/refresh fans out to all 3 sync types",
            f"keys={list(ids1.keys())}",
        )
        r2 = _post(f"/tenants/{activated}/refresh")
        ids2 = r2.json().get("sync_run_ids", {})
        _say(ids1 == ids2, "/refresh is idempotent within 60s window", f"first={ids1}, second={ids2}")

    # ---- Provision UNactivated tenant ----------------------------------
    unactivated = _provision("unactivated")
    if unactivated is not None:
        # ---- Cutover: TENANT_NOT_ACTIVATED on unactivated tenant -------
        err = _exercise_unactivated_in_container(unactivated)
        _say(
            err == "TENANT_NOT_ACTIVATED",
            "cutover: unactivated tenant → TENANT_NOT_ACTIVATED",
            f"got {err!r}",
        )

    # ---- Summary -------------------------------------------------------
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    print(f"Tenants created (manual cleanup if you want): {TENANTS_TO_CLEAN}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
