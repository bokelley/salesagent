#!/usr/bin/env python3
"""Import a tenant bundle produced by ``export_tenant.py``.

The entire import runs in a single transaction — any failure rolls back
cleanly. Collisions abort by default; pass ``--mode=replace`` to delete
the existing tenant row first (CASCADE wipes all children).

The importer refuses to run if the bundle's alembic revision doesn't
match the target database. Run migrations first to align, or pass
``--allow-schema-drift`` if you've verified compatibility manually.

Examples::

    # Import into the same deployment (preserves IDs, tokens, secrets)
    uv run python scripts/ops/import_tenant.py acme.json

    # Replace an existing tenant
    uv run python scripts/ops/import_tenant.py acme.json --mode=replace

    # Flip the tenant to embedded mode during import
    uv run python scripts/ops/import_tenant.py acme.json --flip-to-embedded

    # Move to a new tenant_id (avoid collision on cross-deployment moves)
    uv run python scripts/ops/import_tenant.py acme.json --target-tenant-id acme-new

    # Dry-run check (will raise on schema mismatch or collision)
    uv run python scripts/ops/import_tenant.py acme.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.database.database_session import get_db_session
from src.core.database.tenant_export import (
    BundleSchemaMismatchError,
    TenantAlreadyExistsError,
    TenantImportCollisionError,
    import_tenant,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle_path", help="Path to bundle JSON file (or '-' for stdin)")
    parser.add_argument(
        "--mode",
        choices=("fail", "replace"),
        default="fail",
        help="On tenant_id collision: 'fail' aborts (default), 'replace' CASCADE-deletes the existing tenant first",
    )
    parser.add_argument(
        "--flip-to-embedded",
        action="store_true",
        help="Force tenants.is_embedded=True on the imported row",
    )
    parser.add_argument(
        "--target-tenant-id",
        help="Rewrite tenant_id throughout the bundle (cross-deployment moves)",
    )
    parser.add_argument(
        "--allow-schema-drift",
        action="store_true",
        help="Skip alembic_revision match check (use after confirming schema compatibility manually)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the import but roll back instead of committing — verifies the bundle is loadable",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.bundle_path == "-":
        bundle = json.load(sys.stdin)
    else:
        with open(args.bundle_path, encoding="utf-8") as fh:
            bundle = json.load(fh)

    try:
        with get_db_session() as session:
            try:
                connection = session.connection()
                summary = import_tenant(
                    connection,
                    bundle,
                    mode=args.mode,
                    flip_to_embedded=args.flip_to_embedded,
                    target_tenant_id=args.target_tenant_id,
                    require_alembic_match=not args.allow_schema_drift,
                )
                if args.dry_run:
                    session.rollback()
                    print(
                        f"dry-run OK: would import tenant_id={summary['tenant_id']!r} "
                        f"({summary['rows']} rows across {len(summary['tables'])} tables)",
                        file=sys.stderr,
                    )
                    return 0
                session.commit()
            except Exception:
                # Explicit rollback on any failure path — don't rely on the session
                # context manager's close-time rollback heuristics.
                session.rollback()
                raise
    except (BundleSchemaMismatchError, TenantAlreadyExistsError, TenantImportCollisionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(
            f"imported tenant_id={summary['tenant_id']!r}: "
            f"{summary['rows']} rows across {len(summary['tables'])} tables "
            f"(mode={args.mode}, flip_to_embedded={args.flip_to_embedded})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
