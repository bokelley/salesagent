"""Seed sample signing data on the ``default`` tenant for PR 3A demo.

Idempotent: re-running won't crash, won't duplicate. Adds:

* 2 admitted operators (one trusted, one untrusted) — pretend buyers
  registered with AAO under the ``operators-r-us`` and ``buyer-collective``
  member slugs.
* 1 operator-advertiser link tying the untrusted operator to a sample
  principal.
* Tenant signing policy enabled with ``required_for=[create_media_buy]``
  and a sample brand_json_url for our own outbound surface.
* 1 outbound webhook-signing credential (local_pem backend) so the
  credentials page has a row.

Run from the salesagent root::

    docker compose exec adcp-server python scripts/seed_signing_demo.py
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdmittedOperator,
    OperatorAdvertiserLink,
    Principal,
    Tenant,
    TenantSigningCredential,
    TenantSigningPolicy,
)

TENANT_ID = "default"
DEFAULT_PRINCIPAL_ID = "test_advertiser"


def main() -> int:
    with get_db_session() as session:
        # session.info flag bypasses the platform-managed write guard. Mirrors
        # what tenant_management_api.provision_tenant does.
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).first()
        if tenant is None:
            print(f"tenant {TENANT_ID!r} not found — run after the default tenant is provisioned")
            return 1

        # Tenant brand_json_url so the policy page shows our own publication URL.
        if not tenant.brand_json_url:
            tenant.brand_json_url = "https://salesagent-demo.example.com/.well-known/brand.json"
            print(f"set tenant.brand_json_url = {tenant.brand_json_url}")

        # Operator 1 — untrusted (the production-shape case).
        op_a = session.scalars(
            select(AdmittedOperator).filter_by(tenant_id=TENANT_ID, operator_id="op_buyer_collective")
        ).first()
        if op_a is None:
            op_a = AdmittedOperator(
                tenant_id=TENANT_ID,
                operator_id="op_buyer_collective",
                brand_json_url="https://buyer-collective.example.com/.well-known/brand.json",
                aao_member_slug="buyer-collective",
                house_domain="buyer-collective.example.com",
                display_name="Buyer Collective",
                is_trusted=False,
                is_active=True,
            )
            session.add(op_a)
            print(f"added admitted operator {op_a.operator_id!r}")

        # Operator 2 — trusted (the embedded-host case). Stays around so the
        # template rendering is exercised even on non-embedded tenants.
        op_b = session.scalars(
            select(AdmittedOperator).filter_by(tenant_id=TENANT_ID, operator_id="op_demo_interchange")
        ).first()
        if op_b is None:
            op_b = AdmittedOperator(
                tenant_id=TENANT_ID,
                operator_id="op_demo_interchange",
                brand_json_url="embedded://default/op_demo_interchange",
                aao_member_slug="demo-interchange",
                house_domain="interchange.example.com",
                display_name="Demo Interchange",
                is_trusted=True,
                is_active=True,
            )
            session.add(op_b)
            print(f"added admitted operator {op_b.operator_id!r} (trusted)")

        # Wire op_a to the default principal if one exists.
        principal = session.scalars(
            select(Principal).filter_by(tenant_id=TENANT_ID, principal_id=DEFAULT_PRINCIPAL_ID)
        ).first()
        if principal is not None:
            link = session.scalars(
                select(OperatorAdvertiserLink).filter_by(
                    tenant_id=TENANT_ID,
                    operator_id="op_buyer_collective",
                    principal_id=principal.principal_id,
                )
            ).first()
            if link is None:
                session.add(
                    OperatorAdvertiserLink(
                        tenant_id=TENANT_ID,
                        operator_id="op_buyer_collective",
                        principal_id=principal.principal_id,
                        billing_mode="operator_bills",
                        is_active=True,
                    )
                )
                print(f"linked op_buyer_collective ↔ {principal.principal_id} (operator_bills)")

        # Tenant signing policy.
        policy = session.scalars(select(TenantSigningPolicy).filter_by(tenant_id=TENANT_ID)).first()
        if policy is None:
            session.add(
                TenantSigningPolicy(
                    tenant_id=TENANT_ID,
                    enabled=True,
                    required_for=["create_media_buy", "update_media_buy"],
                    covers_digest_policy="either",
                    max_skew_seconds=60,
                    max_window_seconds=300,
                )
            )
            print("added tenant signing policy (enabled=True, required_for=[create_media_buy, update_media_buy])")

        # Outbound webhook-signing credential.
        cred = session.scalars(
            select(TenantSigningCredential).filter_by(
                tenant_id=TENANT_ID, purpose="webhook-signing", key_id="kid-demo-2026q2"
            )
        ).first()
        if cred is None:
            session.add(
                TenantSigningCredential(
                    tenant_id=TENANT_ID,
                    purpose="webhook-signing",
                    backend="local_pem",
                    backend_ref="/secrets/signing/webhook-2026q2.pem",
                    public_jwk={
                        "kty": "OKP",
                        "crv": "Ed25519",
                        "kid": "kid-demo-2026q2",
                        "x": "demo-public-key-bytes-base64url-encoded",
                        "use": "sig",
                        "adcp_use": "webhook-signing",
                    },
                    key_id="kid-demo-2026q2",
                    is_active=True,
                )
            )
            print("added outbound signing credential (kid-demo-2026q2)")

        session.commit()
        print("seed complete")
        return 0


if __name__ == "__main__":
    sys.exit(main())
