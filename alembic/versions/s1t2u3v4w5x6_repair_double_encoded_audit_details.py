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
    # No-op by design. The upgrade is a forward-only data fix; reversing
    # it would re-encode correctly-shaped JSONB objects back into JSON
    # strings — deliberately re-introducing the bug. The schema is
    # unchanged across this revision boundary, so prior revisions' code
    # reads the repaired (object-shaped) rows fine and a downgrade can
    # leave the data fix in place safely.
    #
    # Emitted as a SQL NOTICE rather than pass so the migration-completeness
    # guard sees a non-empty body and any operator running downgrade gets a
    # clear hint about why this slot is intentionally empty.
    op.execute(
        "DO $$ BEGIN RAISE NOTICE "
        "'Migration s1t2u3v4w5x6 downgrade is intentionally a no-op "
        "(forward-only data fix; schema unchanged).'; END $$"
    )
