"""Prune webhook_delivery_log rows older than the configured retention.

Run periodically (cron-able) to keep the table small. Default retention
is 30 days; override with ``--retention-days`` or the
``WEBHOOK_LOG_RETENTION_DAYS`` environment variable.

Hard delete — there's no soft-delete fallback, audit trail in
PostgreSQL transaction logs is sufficient. If a tenant needs longer
retention, raise it tenant-wide via the env var (per-tenant config is a
follow-up).

Usage::

    uv run python scripts/ops/cleanup_webhook_deliveries.py
    uv run python scripts/ops/cleanup_webhook_deliveries.py --retention-days 90
    uv run python scripts/ops/cleanup_webhook_deliveries.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure ``src.*`` imports resolve when running this as a stand-alone script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.database.database_session import get_db_session  # noqa: E402
from src.core.database.repositories.delivery import DeliveryRepository  # noqa: E402

logger = logging.getLogger("cleanup_webhook_deliveries")

DEFAULT_RETENTION_DAYS = int(os.environ.get("WEBHOOK_LOG_RETENTION_DAYS", "30"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Delete rows whose created_at is older than this. Default: {DEFAULT_RETENTION_DAYS}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would be deleted without actually deleting.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(argv)


def cleanup(retention_days: int, dry_run: bool = False) -> int:
    """Delete (or count, in dry-run) webhook_delivery_log rows older than the cutoff.

    Returns the number of rows affected (or that would be affected in
    dry-run). DELETE statement uses ``rowcount`` directly — no
    pre-count + delete race window. Pre-count only runs on dry-run.
    """
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    logger.info("Cutoff: %s (retention=%d days, dry_run=%s)", cutoff.isoformat(), retention_days, dry_run)

    with get_db_session() as session:
        if dry_run:
            count = DeliveryRepository.count_logs_older_than(session, cutoff)
            logger.info("Rows older than cutoff: %d (dry-run, not deleted)", count)
            return count

        affected = DeliveryRepository.delete_logs_older_than(session, cutoff)
        session.commit()
        logger.info("Deleted %d webhook_delivery_log rows", affected)
        return affected


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        cleanup(args.retention_days, dry_run=args.dry_run)
    except Exception:
        logger.exception("Cleanup failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
