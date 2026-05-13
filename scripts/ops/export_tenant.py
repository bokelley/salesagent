#!/usr/bin/env python3
"""Export all data for a single tenant to a versioned JSON bundle.

Bundle preserves IDs and credentials (access tokens, encrypted secrets) so
buyers integrating against existing tokens continue to work after import.
For cross-deployment moves where the Fernet ENCRYPTION_KEY differs, pass
``--strip-secrets`` to zero encrypted-at-rest columns; the operator
re-enters them on the target deployment.

Examples::

    # Export to file
    uv run python scripts/ops/export_tenant.py acme --out /tmp/acme.json

    # Stdout (pipe to gzip, scp, etc.)
    uv run python scripts/ops/export_tenant.py acme > acme.json

    # Strip secrets for cross-deployment move
    uv run python scripts/ops/export_tenant.py acme --strip-secrets --out acme.json
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
    TenantNotFoundError,
    export_tenant,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tenant_id", help="ID of the tenant to export")
    parser.add_argument(
        "--out",
        "-o",
        help="Output path for the bundle JSON (default: stdout)",
    )
    parser.add_argument(
        "--strip-secrets",
        action="store_true",
        help="Wipe encrypted-at-rest columns (Gemini key, GAM service account, OIDC secret, etc.)",
    )
    parser.add_argument(
        "--exclude-audit-logs",
        action="store_true",
        help="Exclude audit_logs from the bundle (smaller file, no history)",
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

    with get_db_session() as session:
        connection = session.connection()
        try:
            bundle = export_tenant(
                connection,
                args.tenant_id,
                strip_secrets=args.strip_secrets,
                include_audit_logs=not args.exclude_audit_logs,
            )
        except TenantNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    serialized = json.dumps(bundle, indent=2, sort_keys=True)

    if args.out:
        # 0600 — bundle contains tenant secrets (encrypted credentials, principal
        # tokens, audit history). Default umask would write 0644, exposing it to
        # anyone with shell access on the operator host.
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
        if not args.quiet:
            byte_size = len(serialized.encode("utf-8"))
            print(f"wrote {byte_size:,} bytes to {args.out} (mode 0600)", file=sys.stderr)
    else:
        sys.stdout.write(serialized)
        sys.stdout.write("\n")
        if not args.quiet:
            print(
                "warning: bundle written to stdout — redirect target permissions are your responsibility",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
