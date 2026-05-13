"""Tenant-scoped data export/import.

Discovers tenant-scoped tables by walking SQLAlchemy metadata: a table is
tenant-scoped if it has a ``tenant_id`` column or has a foreign key that
transitively reaches a tenant-scoped table. Exports/imports rows in
FK-dependency order so the importer can replay them without violating
constraints.

Bundle format (version 1)::

    {
      "schema_version": 1,
      "exported_at": "2026-05-13T12:34:56+00:00",
      "alembic_revision": "abc123",
      "source": {"tenant_id": "...", "database_url_host": "..."},
      "tenant": { ...tenants row... },
      "tables": {
        "principals": [ {...row...}, ... ],
        "products":   [ {...row...}, ... ],
        ...
      }
    }

IDs and access tokens round-trip as-is — buyers integrating against a
preserved ``principals.access_token`` continue to work after import.
``--strip-secrets`` zeros encrypted-at-rest columns for cross-deployment
moves where the Fernet key differs.

Bypasses ORM event listeners (including the ``is_embedded`` write guard)
by using SQLAlchemy Core directly. Callers wrap the entire import in a
single transaction; collisions either abort or replace via CASCADE delete
on the tenant row.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import pydantic_core
from sqlalchemy import MetaData, Table, delete, insert, inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import ColumnElement

from src.core.database.models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TENANTS_TABLE = "tenants"

# Tables that are global, not tenant-owned, and must never appear in a bundle.
EXCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "superadmin_config",
        "alembic_version",
    }
)

# Sensitive columns wiped when ``strip_secrets=True``. Two categories live here:
#   - Encrypted-at-rest (Fernet ciphertext) — must be stripped for cross-deployment
#     moves where the target's ENCRYPTION_KEY differs.
#   - Plaintext bearer credentials — anyone with the value can authenticate as
#     the tenant or write to its receivers. Strip whenever the bundle leaves the
#     deployment of origin, regardless of encryption keys.
# ``principals.access_token`` is INTENTIONALLY excluded — preserving it keeps
# buyers' MCP/A2A integrations working after import. If you need to rotate
# those, do it separately after import.
SENSITIVE_COLUMNS: dict[str, frozenset[str]] = {
    "tenants": frozenset(
        {
            "gemini_api_key",  # Fernet ciphertext
            "admin_token",  # plaintext tenant admin bearer
            "slack_webhook_url",  # POSTing to this URL is the credential
            "slack_audit_webhook_url",
            "hitl_webhook_url",
        }
    ),
    "adapter_config": frozenset(
        {
            "gam_service_account_json",  # Fernet ciphertext
            "gam_refresh_token",  # plaintext OAuth refresh token
        }
    ),
    "tenant_auth_configs": frozenset(
        {
            "oidc_client_secret_encrypted",  # Fernet ciphertext
        }
    ),
    "creative_agents": frozenset({"auth_credentials"}),
    "signals_agents": frozenset({"auth_credentials"}),
    "push_notification_configs": frozenset({"authentication_token", "validation_token", "webhook_secret"}),
    "webhook_subscriptions": frozenset(
        {"secret_hash"},  # sha256 of webhook secret — leak enables offline crack
    ),
}

# JSON columns whose payload embeds sensitive values stripped when ``strip_secrets=True``.
# Listed as ``(table, column, dotted-path-inside-json)``.
SENSITIVE_JSON_PATHS: tuple[tuple[str, str, str], ...] = (("tenants", "ai_config", "api_key"),)


class BundleSchemaMismatchError(Exception):
    """Bundle was produced against a different alembic revision than the target DB."""


class TenantAlreadyExistsError(Exception):
    """Tenant row already exists in the target database and ``mode='fail'`` was set."""


class TenantNotFoundError(Exception):
    """Source tenant does not exist (export)."""


class TenantImportCollisionError(Exception):
    """Bundle import would collide with an existing row on a globally-unique column.

    Raised during the pre-flight check when ``tenants.subdomain``,
    ``tenants.virtual_host``, or any ``principals.access_token`` in the
    bundle is already in use by a row outside the tenant being imported.
    """


def discover_tenant_scoped_tables(metadata: MetaData | None = None) -> list[Table]:
    """Return tenant-scoped tables in FK-dependency order (parents first).

    A table is tenant-scoped if it has a ``tenant_id`` column or if any of
    its foreign keys reaches a tenant-scoped table transitively. The
    ``tenants`` table itself is excluded — callers handle it separately.
    """
    md = metadata if metadata is not None else Base.metadata
    scoped: set[Table] = set()

    for table in md.tables.values():
        if table.name in EXCLUDED_TABLES or table.name == TENANTS_TABLE:
            continue
        if "tenant_id" in table.columns:
            scoped.add(table)

    # Walk FK graph until fixed point — picks up tables like media_packages
    # (FK to media_buys), strategy_states (FK to strategies), and
    # object_workflow_mapping (FK to workflow_steps).
    changed = True
    while changed:
        changed = False
        for table in md.tables.values():
            if table in scoped or table.name in EXCLUDED_TABLES or table.name == TENANTS_TABLE:
                continue
            for fk in table.foreign_keys:
                if fk.column.table in scoped:
                    scoped.add(table)
                    changed = True
                    break

    # Return in metadata.sorted_tables order — topological by FK. delete_tenant_data
    # depends on reversed(...) of this list giving a child-before-parent delete order.
    return [t for t in md.sorted_tables if t in scoped]


def _is_tenant_scoped(table: Table, scoped_set: set[Table]) -> bool:
    return table in scoped_set or "tenant_id" in table.columns


def build_tenant_filter(
    table: Table,
    tenant_id: str,
    scoped_set: set[Table],
) -> ColumnElement:
    """Return a WHERE clause filtering ``table`` to rows owned by ``tenant_id``.

    Direct: ``tenant_id`` column equality. Transitive: subquery up an FK
    chain to the nearest ancestor that does have a ``tenant_id`` column.

    When a table has multiple FKs to scoped parents (junction-style), prefer
    the one whose parent has ``tenant_id`` directly — that's the shortest
    correct path. Ties broken by FK column name for determinism.
    """
    if "tenant_id" in table.columns:
        return table.c.tenant_id == tenant_id

    candidates = [fk for fk in table.foreign_keys if _is_tenant_scoped(fk.column.table, scoped_set)]
    if not candidates:
        raise ValueError(f"Table {table.name!r} is not tenant-scoped and has no FK chain to a tenant-scoped table")

    # Sort: direct-tenant-id parents first, then alphabetically by FK column name.
    candidates.sort(
        key=lambda fk: (
            0 if "tenant_id" in fk.column.table.columns else 1,
            fk.parent.name,
        )
    )
    chosen = candidates[0]
    parent_filter = build_tenant_filter(chosen.column.table, tenant_id, scoped_set)
    return chosen.parent.in_(select(chosen.column).where(parent_filter))


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def _json_default(obj: Any) -> Any:
    """Fallback for types pydantic_core doesn't render natively (none for our schema)."""
    return str(obj)


def _serialize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round-trip through pydantic_core for datetime/Decimal/UUID safety."""
    serialized: list[dict[str, Any]] = []
    for row in rows:
        # to_json produces JSON-safe bytes; loading back gives plain dict/list/str/number.
        as_json = pydantic_core.to_json(row, fallback=_json_default)
        serialized.append(json.loads(as_json))
    return serialized


def _strip_secret_columns(table_name: str, rows: list[dict[str, Any]]) -> None:
    """Zero out sensitive columns and known sensitive JSON sub-paths."""
    secret_cols = SENSITIVE_COLUMNS.get(table_name, frozenset())
    for row in rows:
        for col in secret_cols:
            if col in row:
                row[col] = None

    for owner_table, json_col, dotted_path in SENSITIVE_JSON_PATHS:
        if owner_table != table_name:
            continue
        parts = dotted_path.split(".")
        for row in rows:
            payload = row.get(json_col)
            if not isinstance(payload, dict):
                continue
            cursor: Any = payload
            for part in parts[:-1]:
                cursor = cursor.get(part) if isinstance(cursor, dict) else None
                if cursor is None:
                    break
            if isinstance(cursor, dict) and parts[-1] in cursor:
                cursor[parts[-1]] = None


def delete_tenant_data(
    connection: Connection,
    tenant_id: str,
    *,
    metadata: MetaData | None = None,
) -> None:
    """Delete a tenant and every tenant-scoped row that references it.

    Some FKs in the schema lack ``ON DELETE CASCADE`` (e.g.
    ``media_packages → media_buys``). Relying on the tenants-row CASCADE
    alone misses those — Postgres halts the delete mid-chain. We walk the
    discovered tenant-scoped tables in reverse FK order so children go
    before their parents, then drop the tenants row last.

    Caller controls the transaction.
    """
    md = metadata if metadata is not None else Base.metadata
    scoped_tables = discover_tenant_scoped_tables(md)
    scoped_set = set(scoped_tables)
    tenants_table = md.tables[TENANTS_TABLE]

    for table in reversed(scoped_tables):
        where_clause = build_tenant_filter(table, tenant_id, scoped_set)
        connection.execute(delete(table).where(where_clause))

    connection.execute(delete(tenants_table).where(tenants_table.c.tenant_id == tenant_id))


def _read_alembic_revision(connection: Connection) -> str | None:
    """Read alembic_version.version_num, or None if the table doesn't exist.

    Uses ``inspect()`` to check for the table without issuing a SELECT —
    a missing-table SELECT aborts the Postgres transaction, and a bare
    ``except`` would leave that aborted state poisoning subsequent calls
    on the same connection.
    """
    if not inspect(connection).has_table("alembic_version"):
        return None
    result = connection.exec_driver_sql("SELECT version_num FROM alembic_version LIMIT 1")
    row = result.first()
    return row[0] if row else None


def export_tenant(
    connection: Connection,
    tenant_id: str,
    *,
    strip_secrets: bool = False,
    include_audit_logs: bool = True,
    metadata: MetaData | None = None,
) -> dict[str, Any]:
    """Export a single tenant to a JSON-serializable bundle dict.

    ``connection`` must already be open inside a (read) transaction. The
    function does no commits; it only reads.
    """
    md = metadata if metadata is not None else Base.metadata
    tenants_table = md.tables[TENANTS_TABLE]

    tenant_row = connection.execute(select(tenants_table).where(tenants_table.c.tenant_id == tenant_id)).first()
    if tenant_row is None:
        raise TenantNotFoundError(f"No tenant with id={tenant_id!r}")

    scoped_tables = discover_tenant_scoped_tables(md)
    scoped_set = set(scoped_tables)

    tenant_dict = _serialize_rows([_row_to_dict(tenant_row)])[0]
    if strip_secrets:
        _strip_secret_columns(TENANTS_TABLE, [tenant_dict])

    tables_out: dict[str, list[dict[str, Any]]] = {}
    total_rows = 0
    for table in scoped_tables:
        if not include_audit_logs and table.name == "audit_logs":
            continue
        where_clause = build_tenant_filter(table, tenant_id, scoped_set)
        rows = connection.execute(select(table).where(where_clause)).all()
        if not rows:
            continue
        serialized = _serialize_rows(_row_to_dict(r) for r in rows)
        if strip_secrets:
            _strip_secret_columns(table.name, serialized)
        tables_out[table.name] = serialized
        total_rows += len(serialized)

    logger.info(
        "Exported tenant %s: %d tables, %d rows total (strip_secrets=%s)",
        tenant_id,
        len(tables_out),
        total_rows,
        strip_secrets,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "alembic_revision": _read_alembic_revision(connection),
        "source": {"tenant_id": tenant_id},
        "tenant": tenant_dict,
        "tables": tables_out,
    }


def _retarget_tenant_id(bundle: dict[str, Any], new_tenant_id: str) -> dict[str, Any]:
    """Return a shallow copy of ``bundle`` with all tenant_id references rewritten.

    Limitation: only the ``tenant_id`` column itself is rewritten — tenant
    identifiers embedded inside JSON columns (audit log details, adapter
    configs, etc.) are NOT touched. For first-class cross-deployment moves
    where stale embedded references would matter, grep the JSON content of
    the bundle before importing.
    """
    new = dict(bundle)
    new["tenant"] = dict(bundle["tenant"], tenant_id=new_tenant_id)
    new_tables: dict[str, list[dict[str, Any]]] = {}
    for name, rows in bundle["tables"].items():
        rewritten = []
        for row in rows:
            if "tenant_id" in row:
                row = dict(row, tenant_id=new_tenant_id)
            rewritten.append(row)
        new_tables[name] = rewritten
    new["tables"] = new_tables
    new["source"] = dict(bundle.get("source", {}), retargeted_to=new_tenant_id)
    return new


def import_tenant(
    connection: Connection,
    bundle: dict[str, Any],
    *,
    mode: str = "fail",
    flip_to_embedded: bool = False,
    target_tenant_id: str | None = None,
    require_alembic_match: bool = True,
    metadata: MetaData | None = None,
) -> dict[str, Any]:
    """Import a bundle into the database connected via ``connection``.

    Caller controls the transaction: pass a connection inside a ``begin()``
    block. Any error raises and rolls back at the caller's transaction
    boundary; this function does not commit.

    ``mode``:
      - ``"fail"`` — raise if the tenant_id already exists (default).
      - ``"replace"`` — delete the existing tenant row first (CASCADE
        wipes all children) and reinsert.

    ``flip_to_embedded``: force ``tenants.is_embedded = True`` on the
    imported row. Independent of the source's value.

    ``target_tenant_id``: if set, rewrites the ``tenant_id`` column
    throughout the bundle. Tenant identifiers embedded inside JSON columns
    are NOT rewritten — see :func:`_retarget_tenant_id`.

    Before inserting, performs a pre-flight check on globally-unique
    columns (``tenants.subdomain``, ``tenants.virtual_host``, and every
    ``principals.access_token`` in the bundle). Raises
    :class:`TenantImportCollisionError` with a list of conflicts if any
    are already used by rows outside the tenant being imported.

    Writes one ``audit_logs`` row recording the import on success.

    Returns a summary dict: ``{"tenant_id", "tables", "rows"}``.
    """
    if mode not in {"fail", "replace"}:
        raise ValueError(f"invalid mode {mode!r}; must be 'fail' or 'replace'")

    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise BundleSchemaMismatchError(
            f"bundle schema_version={bundle.get('schema_version')!r}, expected {SCHEMA_VERSION}"
        )

    md = metadata if metadata is not None else Base.metadata

    schema_matches = False
    if require_alembic_match:
        bundle_rev = bundle.get("alembic_revision")
        db_rev = _read_alembic_revision(connection)
        if bundle_rev and db_rev and bundle_rev != db_rev:
            raise BundleSchemaMismatchError(
                f"alembic revision mismatch: bundle={bundle_rev}, db={db_rev}. "
                "Run migrations to align, or pass require_alembic_match=False."
            )
        schema_matches = bool(bundle_rev and db_rev and bundle_rev == db_rev)

    if target_tenant_id is not None:
        bundle = _retarget_tenant_id(bundle, target_tenant_id)

    tenant_row = dict(bundle["tenant"])
    tenant_id = tenant_row["tenant_id"]
    if flip_to_embedded:
        tenant_row["is_embedded"] = True

    tenants_table = md.tables[TENANTS_TABLE]
    existing = connection.execute(
        select(tenants_table.c.tenant_id).where(tenants_table.c.tenant_id == tenant_id)
    ).first()

    if existing is not None:
        if mode == "fail":
            raise TenantAlreadyExistsError(
                f"tenant {tenant_id!r} already exists; rerun with mode='replace' to overwrite"
            )
        # Several FKs lack ON DELETE CASCADE (media_packages → media_buys,
        # etc.) so we can't rely on the tenants-row CASCADE alone. Delete
        # children first, then the tenant row.
        delete_tenant_data(connection, tenant_id, metadata=md)

    _check_unique_collisions(connection, bundle, tenant_id, md)

    # When the alembic revision matches, unknown columns indicate a real
    # bug (forked bundle, hand-edited file) — fail loudly. When the caller
    # opted into schema drift, downgrade to a warning so the import still
    # makes progress.
    strict_columns = schema_matches

    def _filtered(table: Table, row: dict[str, Any]) -> dict[str, Any]:
        cols = {c.name for c in table.columns}
        unknown = set(row) - cols
        if unknown:
            if strict_columns:
                raise BundleSchemaMismatchError(
                    f"unknown columns on table {table.name!r} in bundle: {sorted(unknown)}. "
                    "Pass require_alembic_match=False (--allow-schema-drift) to drop and continue."
                )
            logger.warning(
                "dropping unknown columns on %s during import: %s",
                table.name,
                sorted(unknown),
            )
        return {k: v for k, v in row.items() if k in cols}

    connection.execute(insert(tenants_table).values(**_filtered(tenants_table, tenant_row)))

    scoped_tables = discover_tenant_scoped_tables(md)
    summary_tables: dict[str, int] = {}
    total_rows = 0
    for table in scoped_tables:
        rows = bundle["tables"].get(table.name)
        if not rows:
            continue
        cleaned = [_filtered(table, r) for r in rows]
        try:
            connection.execute(insert(table), cleaned)
        except IntegrityError as exc:
            raise RuntimeError(f"Insert failed on table {table.name!r}: {exc.orig}") from exc
        summary_tables[table.name] = len(cleaned)
        total_rows += len(cleaned)

    _write_import_audit_log(
        connection,
        md,
        tenant_id=tenant_id,
        mode=mode,
        flip_to_embedded=flip_to_embedded,
        target_tenant_id=target_tenant_id,
        rows=total_rows,
        tables=summary_tables,
    )

    logger.info(
        "Imported tenant %s: %d tables, %d rows total (mode=%s, flip_to_embedded=%s)",
        tenant_id,
        len(summary_tables),
        total_rows,
        mode,
        flip_to_embedded,
    )

    return {"tenant_id": tenant_id, "tables": summary_tables, "rows": total_rows}


def _check_unique_collisions(
    connection: Connection,
    bundle: dict[str, Any],
    tenant_id: str,
    metadata: MetaData,
) -> None:
    """Raise TenantImportCollisionError if the bundle's globally-unique values
    are already used by rows belonging to a different tenant.

    Checks ``tenants.subdomain``, ``tenants.virtual_host``, and every
    ``principals.access_token`` in the bundle. Pre-flighting these before
    insert turns an opaque ``IntegrityError`` into a precise message.
    """
    tenants_table = metadata.tables[TENANTS_TABLE]
    principals_table = metadata.tables.get("principals")
    conflicts: list[str] = []

    subdomain = bundle["tenant"].get("subdomain")
    if subdomain:
        clashing = connection.execute(
            select(tenants_table.c.tenant_id).where(
                tenants_table.c.subdomain == subdomain,
                tenants_table.c.tenant_id != tenant_id,
            )
        ).first()
        if clashing:
            conflicts.append(f"tenants.subdomain={subdomain!r} already owned by tenant_id={clashing[0]!r}")

    virtual_host = bundle["tenant"].get("virtual_host")
    if virtual_host:
        clashing = connection.execute(
            select(tenants_table.c.tenant_id).where(
                tenants_table.c.virtual_host == virtual_host,
                tenants_table.c.tenant_id != tenant_id,
            )
        ).first()
        if clashing:
            conflicts.append(f"tenants.virtual_host={virtual_host!r} already owned by tenant_id={clashing[0]!r}")

    if principals_table is not None:
        principal_rows = bundle["tables"].get("principals", [])
        tokens = [row.get("access_token") for row in principal_rows if row.get("access_token")]
        if tokens:
            clashing_tokens = connection.execute(
                select(principals_table.c.access_token, principals_table.c.tenant_id).where(
                    principals_table.c.access_token.in_(tokens),
                    principals_table.c.tenant_id != tenant_id,
                )
            ).all()
            for token, clashing_tenant in clashing_tokens:
                conflicts.append(f"principals.access_token={token[:8]}… already owned by tenant_id={clashing_tenant!r}")

    if conflicts:
        raise TenantImportCollisionError(
            "bundle collides with existing rows on globally-unique columns:\n  - " + "\n  - ".join(conflicts)
        )


def _write_import_audit_log(
    connection: Connection,
    metadata: MetaData,
    *,
    tenant_id: str,
    mode: str,
    flip_to_embedded: bool,
    target_tenant_id: str | None,
    rows: int,
    tables: dict[str, int],
) -> None:
    """Insert a single audit_logs row capturing the import.

    Best-effort: if the audit_logs table is missing (very stripped-down
    test schemas), the import still succeeds — auditing the operator is
    not a hard requirement for the data move itself, and surfacing it
    would mask the import's success.
    """
    audit_table = metadata.tables.get("audit_logs")
    if audit_table is None:
        return
    operator = os.environ.get("USER") or os.environ.get("LOGNAME") or "operator"
    details = {
        "mode": mode,
        "flip_to_embedded": flip_to_embedded,
        "target_tenant_id": target_tenant_id,
        "rows": rows,
        "tables": tables,
    }
    connection.execute(
        insert(audit_table).values(
            tenant_id=tenant_id,
            operation="tenant.imported",
            principal_name=operator,
            success=True,
            details=details,
        )
    )
