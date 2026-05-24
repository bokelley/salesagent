"""Integration test for POST /publisher-partners/sync-from-directory.

Verifies the upsert behavior the endpoint advertises:

- Publisher in the directory + not in DB → create row, populate counts.
- Publisher in the directory + already in DB → update verification + counts,
  preserve manually-set display_name.
- Publisher in DB + not in the directory → left alone (no deletion).

The AAO directory call itself is mocked at the service-function boundary;
the directory's contract is exercised separately in
``tests/unit/test_aao_lookup_service.py::TestFetchPublishersFromDirectory``.
This test focuses on the endpoint's DB side: row counts, field projection,
and the preserve-manual-display_name invariant.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from flask import session
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, PublisherPartner, Tenant


@contextmanager
def _authorized_request_context(app):
    with app.test_request_context():
        session["test_user"] = "test@example.com"
        session["test_user_role"] = "super_admin"
        session["test_user_name"] = "Test Admin"
        yield


def _make_directory_publisher(domain, status="authorized", props_total=5, props_authorized=5):
    """Build a DirectoryPublisher dataclass for mocking the service function."""
    from src.services.aao_lookup_service import DirectoryPublisher

    return DirectoryPublisher(
        publisher_domain=domain,
        discovery_method="ads_txt_managerdomain",
        manager_domain="cafemedia.com",
        status=status,
        properties_total=props_total,
        properties_authorized=props_authorized,
        last_verified_at="2026-05-22T11:24:16.689Z",
    )


@pytest.mark.requires_db
class TestSyncFromDirectoryUpsert:
    """Endpoint upserts rows from directory response without clobbering manual edits."""

    @pytest.fixture
    def tenant_id(self, integration_db):
        tid = "test_directory_sync"
        with get_db_session() as sess:
            tenant = Tenant(
                tenant_id=tid,
                name="Directory Sync Test",
                subdomain="dir-sync",
                ad_server="mock",
                virtual_host="agent.example.com",
                authorized_emails=["test@example.com"],
                auth_setup_mode=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            sess.add(tenant)
            sess.flush()
            sess.add(AdapterConfig(tenant_id=tid, adapter_type="mock"))
            sess.commit()

        yield tid

        with get_db_session() as sess:
            for row in sess.scalars(select(PublisherPartner).filter_by(tenant_id=tid)).all():
                sess.delete(row)
            for ac in sess.scalars(select(AdapterConfig).filter_by(tenant_id=tid)).all():
                sess.delete(ac)
            for t in sess.scalars(select(Tenant).filter_by(tenant_id=tid)).all():
                sess.delete(t)
            sess.commit()

    def test_upserts_creates_new_and_updates_existing(self, tenant_id):
        from src.admin.app import create_app
        from src.admin.blueprints.publisher_partners import (
            sync_publisher_partners_from_directory,
        )
        from src.services.aao_lookup_service import DirectorySyncResult

        # Seed one existing partner with a manually-set display_name. The
        # endpoint must update its counts but not overwrite the name.
        with get_db_session() as sess:
            sess.add(
                PublisherPartner(
                    tenant_id=tenant_id,
                    publisher_domain="existing.com",
                    display_name="Manually Renamed Publisher",
                    sync_status="pending",
                    is_verified=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            sess.commit()

        # Directory returns two publishers — one matches the existing row,
        # one is new.
        directory_result = DirectorySyncResult(
            agent_url="https://agent.example.com",
            publishers=[
                _make_directory_publisher("existing.com", props_total=10, props_authorized=7),
                _make_directory_publisher("newly-discovered.com", props_total=3, props_authorized=3),
            ],
            directory_indexed_at="2026-05-22T11:24:16.689Z",
            pages_fetched=1,
        )

        app = create_app()
        with _authorized_request_context(app):
            with patch(
                "src.services.aao_lookup_service.fetch_publishers_from_directory",
                new=AsyncMock(return_value=directory_result),
            ):
                response = sync_publisher_partners_from_directory(tenant_id)

        body = response.get_json()
        assert body["discovered"] == 2
        assert body["created"] == 1
        assert body["updated"] == 1
        assert body["directory_indexed_at"] == "2026-05-22T11:24:16.689Z"

        # DB state: existing row updated + new row created. Existing row's
        # manual display_name is preserved.
        with get_db_session() as sess:
            existing = sess.scalars(
                select(PublisherPartner).filter_by(tenant_id=tenant_id, publisher_domain="existing.com")
            ).first()
            assert existing.display_name == "Manually Renamed Publisher"
            assert existing.is_verified is True
            assert existing.sync_status == "success"
            assert existing.total_properties == 10
            assert existing.authorized_properties == 7
            assert existing.aao_status_kind == "authorized"

            new = sess.scalars(
                select(PublisherPartner).filter_by(tenant_id=tenant_id, publisher_domain="newly-discovered.com")
            ).first()
            assert new is not None
            assert new.display_name == "newly-discovered.com"  # default from domain
            assert new.is_verified is True
            assert new.total_properties == 3
            assert new.authorized_properties == 3

    def test_publisher_only_in_db_not_in_directory_is_left_alone(self, tenant_id):
        from src.admin.app import create_app
        from src.admin.blueprints.publisher_partners import (
            sync_publisher_partners_from_directory,
        )
        from src.services.aao_lookup_service import DirectorySyncResult

        with get_db_session() as sess:
            sess.add(
                PublisherPartner(
                    tenant_id=tenant_id,
                    publisher_domain="not-in-directory.com",
                    display_name="not-in-directory.com",
                    sync_status="pending",
                    is_verified=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            sess.commit()

        directory_result = DirectorySyncResult(
            agent_url="https://agent.example.com",
            publishers=[_make_directory_publisher("other.com")],
            directory_indexed_at=None,
            pages_fetched=1,
        )

        app = create_app()
        with _authorized_request_context(app):
            with patch(
                "src.services.aao_lookup_service.fetch_publishers_from_directory",
                new=AsyncMock(return_value=directory_result),
            ):
                response = sync_publisher_partners_from_directory(tenant_id)

        assert response.get_json()["discovered"] == 1

        # The pre-existing manual row is not deleted; the directory's
        # "didn't see it" is not an authoritative removal signal.
        with get_db_session() as sess:
            survivor = sess.scalars(
                select(PublisherPartner).filter_by(tenant_id=tenant_id, publisher_domain="not-in-directory.com")
            ).first()
            assert survivor is not None
            assert survivor.is_verified is False
