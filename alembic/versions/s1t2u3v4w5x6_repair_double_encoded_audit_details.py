"""repair double-encoded audit_logs.details

A bug in ``log_security_violation`` (src/core/audit_logger.py) passed
``details=json.dumps({...})`` into the JSONB column, double-encoding the
payload: JSONType.process_bind_param serialized the already-stringified
JSON, producing a JSONB value of type ``string`` instead of ``object``.

Strict readers (notably ``src.core.database.tenant_export``) refuse those
rows, blocking tenant exports. This migration unwraps them in-place so
the column holds proper JSON objects again.

Idempotent: only matches rows where ``jsonb_typeof(details) = 'string'``.
Re-running this migration on a clean DB is a no-op.

Downgrade is intentionally a no-op — re-wrapping correctly-shaped objects
back into strings would re-corrupt the data the fix repaired.
"""

from alembic import op

revision = "s1t2u3v4w5x6"
down_revision = "r0s1t2u3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``details #>> '{}'`` extracts the JSONB value as text using an empty
    # path, which for a JSONB string returns the underlying string contents
    # (not the JSON-quoted form). Casting back to jsonb re-parses that
    # contents as JSON — which is what the original writer intended to store.
    op.execute(
        """
        UPDATE audit_logs
        SET details = ((details::jsonb) #>> '{}')::jsonb
        WHERE details IS NOT NULL
          AND jsonb_typeof(details::jsonb) = 'string'
        """
    )


def downgrade() -> None:
    # The upgrade is a forward-only data fix. Reversing it would mean
    # re-encoding correctly-shaped JSONB objects back into JSON-encoded
    # strings — i.e. deliberately re-introducing the bug. Refuse instead
    # of silently corrupting data on downgrade.
    #
    # The schema is unchanged across this revision boundary, so prior
    # revisions' code reads the repaired (object-shaped) rows fine. If a
    # schema rollback past this point is required, leave the data fix in
    # place and downgrade through the surrounding migration(s) only.
    raise NotImplementedError(
        "downgrade of s1t2u3v4w5x6 is unsupported: re-encoding repaired "
        "audit_logs.details rows back into JSON strings would re-introduce "
        "the bug this migration fixed. The repaired data is compatible "
        "with prior revisions; skip this migration on downgrade."
    )
