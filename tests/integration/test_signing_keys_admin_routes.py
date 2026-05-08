"""Admin UI routes for outbound webhook-signing key generate / rotate-out.

Slice 3 task #29 wires UI to the rotation backend the listener fix
landed earlier. Two POST routes:

* ``/tenant/<id>/signing-keys/generate`` — creates a fresh Ed25519
  keypair, writes the PEM under ``WEBHOOK_SIGNING_KEYS_DIR`` mode 0600,
  inserts a new active credential, and rotates out any previous active
  one in the same transaction.
* ``/tenant/<id>/signing-keys/<kid>/rotate-out`` — marks an existing
  credential ``is_active=False``. The PEM file is intentionally
  retained (buyers may have in-flight verification against the kid).

Cache invalidation is handled transparently by the SQLAlchemy session
listener registered in ``src.services.webhook_signing``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import TenantSigningCredential

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.admin]


def _active_creds(tenant_id: str) -> list[TenantSigningCredential]:
    with get_db_session() as session:
        return list(
            session.scalars(
                select(TenantSigningCredential)
                .filter_by(tenant_id=tenant_id, purpose="webhook-signing", is_active=True)
                .order_by(TenantSigningCredential.created_at)
            ).all()
        )


def _all_creds(tenant_id: str) -> list[TenantSigningCredential]:
    with get_db_session() as session:
        return list(
            session.scalars(
                select(TenantSigningCredential)
                .filter_by(tenant_id=tenant_id, purpose="webhook-signing")
                .order_by(TenantSigningCredential.created_at)
            ).all()
        )


class TestGenerateWebhookSigningKey:
    """POST /tenant/<id>/signing-keys/generate creates a fresh active credential."""

    def test_generate_creates_active_credential_and_writes_pem(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        resp = authenticated_admin_session.post(
            f"/tenant/{tenant_id}/signing-keys/generate",
            follow_redirects=False,
        )
        assert resp.status_code == 302, f"expected redirect, got {resp.status_code}: {resp.data!r}"
        assert "signing-keys" in resp.headers["Location"]

        # Follow the redirect to confirm the settings page renders the
        # new credential without choking on the new buttons.
        followed = authenticated_admin_session.get(resp.headers["Location"])
        assert followed.status_code == 200, f"settings page returned {followed.status_code}"
        assert b"Generate Ed25519 keypair" in followed.data
        assert b"Rotate out" in followed.data

        creds = _active_creds(tenant_id)
        assert len(creds) == 1, f"expected exactly one active credential, got {len(creds)}"
        cred = creds[0]
        assert cred.backend == "local_pem"
        assert cred.key_id, "kid must be populated"
        assert cred.public_jwk and cred.public_jwk.get("kty") == "OKP"
        assert cred.public_jwk.get("crv") == "Ed25519"

        pem_path = Path(cred.backend_ref)
        assert pem_path.exists(), f"PEM not written to disk at {pem_path}"
        assert pem_path.read_bytes().startswith(b"-----BEGIN "), "PEM does not look like a PEM file"
        # Mode 0600 on POSIX (skip on filesystems that don't preserve mode bits).
        mode = pem_path.stat().st_mode & 0o777
        assert mode in (0o600, 0o644), f"PEM mode bits unexpected: {oct(mode)}"

    def test_generate_twice_rotates_out_the_first(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        authenticated_admin_session.post(f"/tenant/{tenant_id}/signing-keys/generate")
        authenticated_admin_session.post(f"/tenant/{tenant_id}/signing-keys/generate")

        active = _active_creds(tenant_id)
        all_creds = _all_creds(tenant_id)
        assert len(active) == 1, "at-most-one-active invariant violated"
        assert len(all_creds) == 2, "old credential should remain on the table as inactive"
        # The older one is the rotated-out one.
        old, new = all_creds
        assert old.is_active is False
        assert old.rotated_out_at is not None
        assert new.is_active is True
        assert new.key_id != old.key_id


class TestRotateOutWebhookSigningKey:
    """POST /tenant/<id>/signing-keys/<kid>/rotate-out marks the row inactive."""

    def test_rotate_out_marks_row_inactive(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        # Seed an active credential via the generate route so the test
        # exercises the production data shape end-to-end.
        authenticated_admin_session.post(f"/tenant/{tenant_id}/signing-keys/generate")
        kid = _active_creds(tenant_id)[0].key_id

        resp = authenticated_admin_session.post(
            f"/tenant/{tenant_id}/signing-keys/{kid}/rotate-out",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert _active_creds(tenant_id) == []
        # The row still exists, just inactive — the PEM stays on disk so
        # in-flight buyer verification keeps working.
        all_creds = _all_creds(tenant_id)
        assert len(all_creds) == 1
        assert all_creds[0].is_active is False
        assert all_creds[0].rotated_out_at is not None

    def test_rotate_out_unknown_kid_returns_redirect_with_flash(
        self, authenticated_admin_session, test_tenant_with_data
    ):
        """Hitting rotate-out on a kid that doesn't exist must not 500."""
        tenant_id = test_tenant_with_data["tenant_id"]
        resp = authenticated_admin_session.post(
            f"/tenant/{tenant_id}/signing-keys/kid-nonexistent/rotate-out",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert _all_creds(tenant_id) == []
