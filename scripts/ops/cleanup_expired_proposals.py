"""Prune expired unconsumed AdCP proposal rows.

Run periodically (cron-able) to keep the ``proposals`` table from growing
without bound. The current table name is ``proposals``; older issue text may
refer to the upstream default ``proposal_drafts`` name.

Only expired ``draft`` and ``committed`` rows are deleted. ``consumed`` rows are
retained as audit history for media buys created from proposals, and
``consuming`` rows are left for the proposal store to resolve.

Usage::

    uv run python scripts/ops/cleanup_expired_proposals.py
    uv run python scripts/ops/cleanup_expired_proposals.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure ``src.*`` imports resolve when running this as a stand-alone script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.database.database_session import get_db_session  # noqa: E402
from src.core.database.repositories.proposal import ProposalMaintenanceRepository  # noqa: E402

logger = logging.getLogger("cleanup_expired_proposals")

DEFAULT_BATCH_SIZE = int(os.environ.get("PROPOSAL_CLEANUP_BATCH_SIZE", "1000"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would be deleted without actually deleting.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Delete at most this many rows per transaction. Default: {DEFAULT_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(argv)


def cleanup(dry_run: bool = False, *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Delete or count expired draft/committed proposal rows."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    with get_db_session() as session:
        if dry_run:
            count = ProposalMaintenanceRepository.count_expired_unconsumed(session)
            logger.info("Expired unconsumed proposals: %d (dry-run, not deleted)", count)
            return count

        affected = 0
        while True:
            batch_count = ProposalMaintenanceRepository.delete_expired_unconsumed(session, batch_size=batch_size)
            if batch_count == 0:
                break

            session.commit()
            affected += batch_count
            logger.info(
                "Deleted %d expired unconsumed proposals in this batch (%d total)",
                batch_count,
                affected,
            )
            if batch_count < batch_size:
                break

        logger.info("Deleted %d expired unconsumed proposals", affected)
        return affected


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        cleanup(dry_run=args.dry_run, batch_size=args.batch_size)
    except Exception:
        logger.exception("Expired proposal cleanup failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
