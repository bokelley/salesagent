from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select

from scripts.ops.cleanup_expired_proposals import cleanup
from src.core.database.models import Base
from tests.factories import TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _proposal_table():
    return Base.metadata.tables["proposals"]


def _insert_proposal(
    session,
    *,
    tenant_id: str,
    proposal_id: str,
    state: str,
    expires_at: datetime | None,
) -> None:
    proposals = _proposal_table()
    session.execute(
        insert(proposals).values(
            account_id=f"{tenant_id}:buyer",
            proposal_id=proposal_id,
            state=state,
            recipes={"brief": proposal_id},
            proposal_payload={"proposal_id": proposal_id, "allocations": []},
            expires_at=expires_at,
            media_buy_id=f"mb_{proposal_id}" if state == "consumed" else None,
            recipe_schema_version=1,
        )
    )


def _proposal_ids(session) -> set[str]:
    proposals = _proposal_table()
    return set(session.execute(select(proposals.c.proposal_id)).scalars().all())


def test_cleanup_deletes_only_expired_unconsumed_proposals(factory_session):
    now = datetime.now(UTC)
    tenant = TenantFactory()
    other_tenant = TenantFactory()

    deleted_ids = {"expired_draft", "expired_committed", "other_tenant_expired"}
    kept_ids = {
        "future_draft",
        "null_expiry_committed",
        "expired_consuming",
        "expired_consumed",
    }

    _insert_proposal(
        factory_session,
        tenant_id=tenant.tenant_id,
        proposal_id="expired_draft",
        state="draft",
        expires_at=now - timedelta(hours=1),
    )
    _insert_proposal(
        factory_session,
        tenant_id=tenant.tenant_id,
        proposal_id="expired_committed",
        state="committed",
        expires_at=now - timedelta(hours=1),
    )
    _insert_proposal(
        factory_session,
        tenant_id=other_tenant.tenant_id,
        proposal_id="other_tenant_expired",
        state="draft",
        expires_at=now - timedelta(hours=1),
    )
    _insert_proposal(
        factory_session,
        tenant_id=tenant.tenant_id,
        proposal_id="future_draft",
        state="draft",
        expires_at=now + timedelta(hours=1),
    )
    _insert_proposal(
        factory_session,
        tenant_id=tenant.tenant_id,
        proposal_id="null_expiry_committed",
        state="committed",
        expires_at=None,
    )
    _insert_proposal(
        factory_session,
        tenant_id=tenant.tenant_id,
        proposal_id="expired_consuming",
        state="consuming",
        expires_at=now - timedelta(hours=1),
    )
    _insert_proposal(
        factory_session,
        tenant_id=tenant.tenant_id,
        proposal_id="expired_consumed",
        state="consumed",
        expires_at=now - timedelta(hours=1),
    )
    factory_session.commit()
    factory_session.close()

    deleted = cleanup(dry_run=False, batch_size=2)

    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine

    with SASession(bind=get_engine()) as confirmation_session:
        remaining = _proposal_ids(confirmation_session)

    assert deleted == len(deleted_ids)
    assert deleted_ids.isdisjoint(remaining)
    assert kept_ids <= remaining


def test_cleanup_dry_run_counts_without_deleting(factory_session):
    now = datetime.now(UTC)
    tenant = TenantFactory()
    _insert_proposal(
        factory_session,
        tenant_id=tenant.tenant_id,
        proposal_id="dry_run_expired",
        state="draft",
        expires_at=now - timedelta(hours=1),
    )
    factory_session.commit()
    factory_session.close()

    count = cleanup(dry_run=True)

    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine

    with SASession(bind=get_engine()) as confirmation_session:
        remaining = _proposal_ids(confirmation_session)

    assert count == 1
    assert "dry_run_expired" in remaining
