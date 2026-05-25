from __future__ import annotations

from unittest.mock import Mock

from src.core.database.repositories.proposal import (
    EXPIRED_PROPOSAL_STATES,
    ProposalMaintenanceRepository,
)


def test_delete_expired_unconsumed_filters_to_draft_and_committed_states():
    session = Mock()
    session.execute.return_value.rowcount = 3

    deleted = ProposalMaintenanceRepository.delete_expired_unconsumed(session, batch_size=25)

    statement, params = session.execute.call_args.args
    sql = str(statement)
    assert deleted == 3
    assert "DELETE FROM proposals" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :batch_size" in sql
    assert "expires_at < now()" in sql
    assert "state IN (__[POSTCOMPILE_states])" in sql
    assert params == {"states": EXPIRED_PROPOSAL_STATES, "batch_size": 25}


def test_delete_expired_unconsumed_rejects_invalid_batch_size():
    session = Mock()

    try:
        ProposalMaintenanceRepository.delete_expired_unconsumed(session, batch_size=0)
    except ValueError as exc:
        assert str(exc) == "batch_size must be >= 1"
    else:
        raise AssertionError("Expected invalid batch_size to fail")

    session.execute.assert_not_called()


def test_count_expired_unconsumed_uses_same_expiration_scope():
    result = Mock()
    result.scalar_one.return_value = 2
    session = Mock()
    session.execute.return_value = result

    count = ProposalMaintenanceRepository.count_expired_unconsumed(session)

    statement, params = session.execute.call_args.args
    sql = str(statement)
    assert count == 2
    assert "SELECT count(*)" in sql
    assert "FROM proposals" in sql
    assert "expires_at < now()" in sql
    assert "state IN (__[POSTCOMPILE_states])" in sql
    assert params == {"states": EXPIRED_PROPOSAL_STATES}
