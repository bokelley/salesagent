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

Cross-origin POSTs are refused via an Origin/Referer check — see
``_verify_request_same_origin`` in tenants.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.core.database.database_session import get_db_session
from src.core.database.models import TenantSigningCredential

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.admin]

# Browsers always send Origin (and usually Referer) on POSTs. The
# routes' CSRF guard accepts any Origin matching ``request.host_url``;
# Flask's test client uses ``http://localhost`` as the default host, so
# this header makes the test client look like a same-origin browser.
_SAME_ORIGIN_HEADERS = {"Origin": "http://localhost"}


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
            headers=_SAME_ORIGIN_HEADERS,
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
        # Atomic ``os.open(..., O_EXCL, 0o600)`` guarantees mode 0600 at
        # create time on POSIX. CI targets Ubuntu (ext4) and macOS (APFS),
        # both of which preserve mode bits — assert exactly 0o600.
        mode = pem_path.stat().st_mode & 0o777
        assert mode == 0o600, f"PEM mode bits should be 0o600, got {oct(mode)}"

    def test_generate_twice_rotates_out_the_first(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        authenticated_admin_session.post(f"/tenant/{tenant_id}/signing-keys/generate", headers=_SAME_ORIGIN_HEADERS)
        authenticated_admin_session.post(f"/tenant/{tenant_id}/signing-keys/generate", headers=_SAME_ORIGIN_HEADERS)

        active = _active_creds(tenant_id)
        all_creds = _all_creds(tenant_id)
        assert len(active) == 1, "at-most-one-active invariant violated (serial path)"
        assert len(all_creds) == 2, "old credential should remain on the table as inactive"
        old, new = all_creds
        assert old.is_active is False
        assert old.rotated_out_at is not None
        assert new.is_active is True
        assert new.key_id != old.key_id

    def test_cross_origin_post_is_refused(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        """A POST from evil.example.com must NOT mutate signing state.

        SameSite=None in production lets the cookie ride along on
        cross-origin POSTs; the Origin/Referer check is what blocks
        the CSRF.
        """
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        resp = authenticated_admin_session.post(
            f"/tenant/{tenant_id}/signing-keys/generate",
            headers={"Origin": "https://evil.example.com"},
            follow_redirects=False,
        )
        # The redirect happens (with an error flash) — what matters is
        # that no credential row was created and no PEM was written.
        assert resp.status_code == 302
        assert _all_creds(tenant_id) == [], "cross-origin POST should not insert a credential"
        assert list(Path(tmp_path).glob("*.pem")) == [], "cross-origin POST should not write a PEM"

    def test_missing_origin_and_referer_is_refused(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        """A POST with no Origin AND no Referer (curl, headless scripts)
        must also be refused — the cookie-attached request is the same
        threat shape regardless of whether the browser supplied Origin.
        """
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        resp = authenticated_admin_session.post(
            f"/tenant/{tenant_id}/signing-keys/generate",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert _all_creds(tenant_id) == []


class TestRotateOutWebhookSigningKey:
    """POST /tenant/<id>/signing-keys/<kid>/rotate-out marks the row inactive."""

    def test_rotate_out_marks_row_inactive(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        authenticated_admin_session.post(f"/tenant/{tenant_id}/signing-keys/generate", headers=_SAME_ORIGIN_HEADERS)
        kid = _active_creds(tenant_id)[0].key_id

        resp = authenticated_admin_session.post(
            f"/tenant/{tenant_id}/signing-keys/{kid}/rotate-out",
            headers=_SAME_ORIGIN_HEADERS,
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert _active_creds(tenant_id) == []
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
            headers=_SAME_ORIGIN_HEADERS,
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert _all_creds(tenant_id) == []

    def test_rotate_out_cross_origin_is_refused(
        self, authenticated_admin_session, test_tenant_with_data, tmp_path, monkeypatch
    ):
        """A CSRF rotate-out from a third party must not flip is_active."""
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        tenant_id = test_tenant_with_data["tenant_id"]

        authenticated_admin_session.post(f"/tenant/{tenant_id}/signing-keys/generate", headers=_SAME_ORIGIN_HEADERS)
        kid = _active_creds(tenant_id)[0].key_id

        authenticated_admin_session.post(
            f"/tenant/{tenant_id}/signing-keys/{kid}/rotate-out",
            headers={"Origin": "https://evil.example.com"},
        )
        # The active row must still be active.
        assert len(_active_creds(tenant_id)) == 1


class TestAtMostOneActiveInvariant:
    """The partial unique index ``ux_tenant_signing_credentials_active``
    enforces the at-most-one-active rule at the DB layer.

    This guards against concurrent admin sessions where two operators
    each open a transaction, each see no prior active row, and each
    insert a new active row. Without the index, both commits succeed
    and the snapshot loader picks one arbitrarily.
    """

    def test_concurrent_active_inserts_raise_integrity_error(self, integration_db, test_tenant_with_data):
        from src.core.database.repositories import TenantSigningCredentialRepository

        tenant_id = test_tenant_with_data["tenant_id"]

        # First active row: commits cleanly.
        with get_db_session() as session:
            repo = TenantSigningCredentialRepository(session, tenant_id=tenant_id)
            repo.create(
                purpose="webhook-signing",
                backend="local_pem",
                backend_ref=f"/tmp/{tenant_id}-kid-a.pem",
                public_jwk={"kty": "OKP", "crv": "Ed25519", "x": "test-a"},
                key_id="kid-a",
            )
            session.commit()

        # Second active row WITHOUT rotating the first: the partial
        # unique index must reject it.
        with pytest.raises(IntegrityError):
            with get_db_session() as session:
                repo = TenantSigningCredentialRepository(session, tenant_id=tenant_id)
                repo.create(
                    purpose="webhook-signing",
                    backend="local_pem",
                    backend_ref=f"/tmp/{tenant_id}-kid-b.pem",
                    public_jwk={"kty": "OKP", "crv": "Ed25519", "x": "test-b"},
                    key_id="kid-b",
                )
                session.commit()
