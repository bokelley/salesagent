"""Round-trip and collision-mode tests for tenant_export.

Exercises the export/import library against a real Postgres instance:
seed a tenant with representative data, export, delete, import, verify
row counts and key field round-trip.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from src.core.database.models import (
    AdapterConfig,
    AuditLog,
    Creative,
    CurrencyLimit,
    MediaBuy,
    MediaPackage,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.database.tenant_export import (
    BundleSchemaMismatchError,
    TenantAlreadyExistsError,
    TenantImportCollisionError,
    TenantNotFoundError,
    delete_tenant_data,
    discover_tenant_scoped_tables,
    export_tenant,
    import_tenant,
)
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _ExportEnv(IntegrationEnv):
    """Bare integration env — no external patches needed for raw DB work."""

    EXTERNAL_PATCHES: dict[str, str] = {}
    use_real_db = True

    def get_session(self):
        self._commit_factory_data()
        return self._session


def _seed_tenant(session, tenant_id: str) -> dict:
    """Create a tenant with one row in each of several tenant-scoped tables.

    Returns a dict of identifiers we'll later assert survived the round-trip.
    """
    from tests.factories import (
        AdapterConfigFactory,
        AuditLogFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        PrincipalFactory,
        ProductFactory,
        PropertyTagFactory,
        TenantFactory,
    )

    tenant = TenantFactory(
        tenant_id=tenant_id,
        name="Round Trip Co",
        is_embedded=False,
    )
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory")
    AdapterConfigFactory(tenant=tenant, adapter_type="mock")
    principal = PrincipalFactory(
        tenant=tenant,
        principal_id=f"{tenant_id}_buyer",
        access_token=f"{tenant_id}_token_xyz",
        name="Acme Buyer",
    )
    product = ProductFactory(
        tenant=tenant,
        product_id=f"{tenant_id}_prod",
        name="Gold Inventory",
    )
    media_buy = MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        media_buy_id=f"{tenant_id}_mb_001",
    )
    MediaPackageFactory(media_buy=media_buy, package_id=f"{tenant_id}_pkg_001")
    CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=f"{tenant_id}_cr_001",
    )
    AuditLogFactory(tenant=tenant, operation="test_round_trip")

    session.commit()

    return {
        "tenant_id": tenant_id,
        "principal_id": principal.principal_id,
        "access_token": principal.access_token,
        "product_id": product.product_id,
        "media_buy_id": media_buy.media_buy_id,
    }


def _count_rows(session, tenant_id: str) -> dict[str, int]:
    """Sample row counts across tenant-scoped tables — enough to detect drops."""
    return {
        "principals": session.scalar(
            select(func.count()).select_from(Principal).where(Principal.tenant_id == tenant_id)
        ),
        "products": session.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)),
        "media_buys": session.scalar(select(func.count()).select_from(MediaBuy).where(MediaBuy.tenant_id == tenant_id)),
        "media_packages": session.scalar(
            select(func.count())
            .select_from(MediaPackage)
            .where(MediaPackage.media_buy_id.in_(select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == tenant_id)))
        ),
        "creatives": session.scalar(select(func.count()).select_from(Creative).where(Creative.tenant_id == tenant_id)),
        # Exclude tenant.imported — that's an audit record the importer emits,
        # not user data being round-tripped.
        "audit_logs": session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.tenant_id == tenant_id, AuditLog.operation != "tenant.imported")
        ),
        "property_tags": session.scalar(
            select(func.count()).select_from(PropertyTag).where(PropertyTag.tenant_id == tenant_id)
        ),
        "adapter_config": session.scalar(
            select(func.count()).select_from(AdapterConfig).where(AdapterConfig.tenant_id == tenant_id)
        ),
        "currency_limits": session.scalar(
            select(func.count()).select_from(CurrencyLimit).where(CurrencyLimit.tenant_id == tenant_id)
        ),
    }


class TestDiscoverTenantScopedTables:
    """Sanity-check the reflection helper without touching the DB."""

    def test_includes_direct_tenant_id_tables(self):
        names = {t.name for t in discover_tenant_scoped_tables()}
        for expected in ("principals", "products", "media_buys", "creatives", "audit_logs"):
            assert expected in names, f"{expected} should be tenant-scoped"

    def test_includes_transitive_tables(self):
        """Tables without a tenant_id column but reachable via FK should appear."""
        names = {t.name for t in discover_tenant_scoped_tables()}
        # media_packages → media_buys → tenant_id
        assert "media_packages" in names
        # strategy_states → strategies → tenant_id
        assert "strategy_states" in names

    def test_excludes_tenants_table(self):
        """The tenants table itself is handled separately by callers."""
        names = {t.name for t in discover_tenant_scoped_tables()}
        assert "tenants" not in names

    def test_excludes_global_tables(self):
        names = {t.name for t in discover_tenant_scoped_tables()}
        assert "superadmin_config" not in names
        assert "alembic_version" not in names

    def test_order_is_fk_safe(self):
        """Parents appear before children — required for insert order."""
        order = [t.name for t in discover_tenant_scoped_tables()]
        # media_buys must come before media_packages (FK dependency)
        assert order.index("media_buys") < order.index("media_packages")


class TestRoundTrip:
    """Export → delete → import → assert state matches."""

    def test_round_trip_preserves_counts_and_tokens(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            seeded = _seed_tenant(session, "rt_basic")
            counts_before = _count_rows(session, "rt_basic")

            bundle = export_tenant(session.connection(), "rt_basic")
            session.commit()

            # Bundle should be JSON-serializable as-is.
            reloaded = json.loads(json.dumps(bundle))

            delete_tenant_data(session.connection(), "rt_basic")
            session.commit()
            assert session.scalar(select(func.count()).select_from(Tenant).where(Tenant.tenant_id == "rt_basic")) == 0

            summary = import_tenant(session.connection(), reloaded)
            session.commit()

            counts_after = _count_rows(session, "rt_basic")
            assert counts_after == counts_before, f"row counts diverged: before={counts_before}, after={counts_after}"

            principal = session.scalars(
                select(Principal).where(Principal.principal_id == seeded["principal_id"])
            ).first()
            assert principal is not None
            assert principal.access_token == seeded["access_token"], (
                "principal access_token must round-trip — buyers' integrations depend on it"
            )

            assert summary["tenant_id"] == "rt_basic"
            assert summary["rows"] > 0


class TestCollisionModes:
    def test_fail_mode_raises_on_existing_tenant(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_collide_fail")
            bundle = export_tenant(session.connection(), "rt_collide_fail")
            session.commit()

            with pytest.raises(TenantAlreadyExistsError):
                import_tenant(session.connection(), bundle, mode="fail")

    def test_replace_mode_overwrites(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            seeded = _seed_tenant(session, "rt_collide_replace")
            bundle = export_tenant(session.connection(), "rt_collide_replace")
            session.commit()

            # Mutate the live tenant's name so we can detect that replace wiped it.
            session.info["super_admin_override"] = True
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_collide_replace")).first()
            tenant.name = "MUTATED"
            session.commit()

            import_tenant(session.connection(), bundle, mode="replace")
            session.commit()

            restored = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_collide_replace")).first()
            assert restored is not None
            assert restored.name == "Round Trip Co", "replace mode should restore the bundle's tenant.name"

            principal = session.scalars(
                select(Principal).where(Principal.principal_id == seeded["principal_id"])
            ).first()
            assert principal is not None
            assert principal.access_token == seeded["access_token"]


class TestFlipToEmbedded:
    def test_flip_to_embedded_sets_flag(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_flip")
            bundle = export_tenant(session.connection(), "rt_flip")
            session.commit()

            delete_tenant_data(session.connection(), "rt_flip")
            session.commit()

            import_tenant(session.connection(), bundle, flip_to_embedded=True)
            session.commit()

            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_flip")).first()
            assert tenant is not None
            assert tenant.is_embedded is True


class TestTargetTenantId:
    def test_retargets_all_rows(self, integration_db):
        """Simulates a cross-deployment move — source tenant is gone, retargeted bundle lands fresh.

        Same-database retargeting hits unique constraints on subdomain /
        virtual_host / public_agent_url; that's a feature, not a bug —
        those columns identify the tenant in the outside world.
        """
        with _ExportEnv() as env:
            session = env.get_session()
            seeded = _seed_tenant(session, "rt_src")
            counts_before = _count_rows(session, "rt_src")
            bundle = export_tenant(session.connection(), "rt_src")
            session.commit()

            delete_tenant_data(session.connection(), "rt_src")
            session.commit()

            import_tenant(session.connection(), bundle, target_tenant_id="rt_dst")
            session.commit()

            dst_count = _count_rows(session, "rt_dst")
            assert dst_count == counts_before, (
                f"retarget should preserve row counts: before={counts_before}, after={dst_count}"
            )

            dst_principal = session.scalars(select(Principal).where(Principal.tenant_id == "rt_dst")).first()
            assert dst_principal is not None
            assert dst_principal.access_token == seeded["access_token"]


class TestStripSecrets:
    def test_strip_secrets_wipes_encrypted_columns(self, integration_db):
        from src.core.utils.encryption import encrypt_api_key

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_secrets")

            # Stamp an encrypted-at-rest credential we expect to be stripped.
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_secrets")).first()
            session.info["super_admin_override"] = True
            tenant._gemini_api_key = encrypt_api_key("ya29.fake-key")
            session.commit()

            bundle = export_tenant(session.connection(), "rt_secrets", strip_secrets=True)
            session.commit()

            assert bundle["tenant"]["gemini_api_key"] is None, "strip_secrets must wipe tenants.gemini_api_key"

    def test_default_export_preserves_encrypted_columns(self, integration_db):
        from src.core.utils.encryption import encrypt_api_key

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_secrets_keep")
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_secrets_keep")).first()
            session.info["super_admin_override"] = True
            ciphertext = encrypt_api_key("ya29.preserve-me")
            tenant._gemini_api_key = ciphertext
            session.commit()

            bundle = export_tenant(session.connection(), "rt_secrets_keep")
            session.commit()

            assert bundle["tenant"]["gemini_api_key"] == ciphertext

    def test_strip_secrets_wipes_plaintext_bearer_credentials(self, integration_db):
        """Bearer credentials that are NOT Fernet-encrypted must also be wiped."""
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_plaintext")
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_plaintext")).first()
            session.info["super_admin_override"] = True
            tenant.admin_token = "plaintext-admin-bearer"
            tenant.slack_webhook_url = "https://hooks.slack.com/services/T000/B000/SECRET"
            tenant.slack_audit_webhook_url = "https://hooks.slack.com/services/T000/B001/SECRET"
            tenant.hitl_webhook_url = "https://example.com/hitl-callback"
            session.commit()

            bundle = export_tenant(session.connection(), "rt_plaintext", strip_secrets=True)
            session.commit()

            assert bundle["tenant"]["admin_token"] is None
            assert bundle["tenant"]["slack_webhook_url"] is None
            assert bundle["tenant"]["slack_audit_webhook_url"] is None
            assert bundle["tenant"]["hitl_webhook_url"] is None
            # access_token is intentionally preserved — buyers' integrations depend on it.
            principal_rows = bundle["tables"]["principals"]
            assert principal_rows[0]["access_token"] == "rt_plaintext_token_xyz"


class TestCollisionPreflight:
    """Pre-flight collision check on globally-unique columns."""

    def test_subdomain_collision_aborts_with_clear_message(self, integration_db):
        from tests.factories import TenantFactory

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_collide_subdomain")
            bundle = export_tenant(session.connection(), "rt_collide_subdomain")
            session.commit()

            delete_tenant_data(session.connection(), "rt_collide_subdomain")
            session.commit()

            # Park a different tenant on the same subdomain — the import
            # would otherwise hit IntegrityError when inserting tenants.
            TenantFactory(tenant_id="rt_squatter", subdomain="pub-rt_collide_subdomain")
            session.commit()

            with pytest.raises(TenantImportCollisionError) as exc_info:
                import_tenant(session.connection(), bundle)
            assert "subdomain" in str(exc_info.value)
            assert "rt_squatter" in str(exc_info.value)

    def test_access_token_collision_detected(self, integration_db):
        from tests.factories import PrincipalFactory, TenantFactory

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_collide_token")
            bundle = export_tenant(session.connection(), "rt_collide_token")
            session.commit()

            delete_tenant_data(session.connection(), "rt_collide_token")
            session.commit()

            # Place the same access_token under a different tenant.
            other_tenant = TenantFactory(tenant_id="rt_other_tenant", subdomain="pub-rt-other")
            PrincipalFactory(
                tenant=other_tenant,
                principal_id="rt_other_buyer",
                access_token="rt_collide_token_token_xyz",
            )
            session.commit()

            with pytest.raises(TenantImportCollisionError) as exc_info:
                import_tenant(session.connection(), bundle)
            assert "access_token" in str(exc_info.value)


class TestStrictColumnFiltering:
    """When alembic revisions match, unknown columns are a bug — not noise."""

    def test_unknown_column_with_matching_alembic_raises(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_strict")
            bundle = export_tenant(session.connection(), "rt_strict")
            session.commit()
            delete_tenant_data(session.connection(), "rt_strict")
            session.commit()

            # Spike a fake column into the bundle's principal rows.
            bundle["tables"]["principals"][0]["nonexistent_column"] = "boom"
            # Force schema_matches=True by setting both revisions equal to a known string.
            bundle["alembic_revision"] = "matched-revision"

            # Inject the same revision into the DB so the match check passes.
            from sqlalchemy import inspect as sa_inspect

            if sa_inspect(session.connection()).has_table("alembic_version"):
                db_rev = session.connection().exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                bundle["alembic_revision"] = db_rev
            else:
                # Without alembic_version, schema_matches stays False; the test would
                # exercise the lenient path. Skip in that case.
                pytest.skip("alembic_version table missing — strict path requires a real migration")

            with pytest.raises(BundleSchemaMismatchError) as exc_info:
                import_tenant(session.connection(), bundle)
            assert "nonexistent_column" in str(exc_info.value)

    def test_unknown_column_with_schema_drift_warns_and_continues(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_drift")
            bundle = export_tenant(session.connection(), "rt_drift")
            session.commit()
            delete_tenant_data(session.connection(), "rt_drift")
            session.commit()

            bundle["tables"]["principals"][0]["nonexistent_column"] = "boom"

            summary = import_tenant(session.connection(), bundle, require_alembic_match=False)
            session.commit()

            assert summary["rows"] > 0


class TestImportAuditLog:
    def test_import_writes_audit_log_row(self, integration_db):
        from src.core.database.models import AuditLog

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_audit")
            audit_count_before = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.tenant_id == "rt_audit",
                    AuditLog.operation == "tenant.imported",
                )
            )
            bundle = export_tenant(session.connection(), "rt_audit")
            session.commit()
            delete_tenant_data(session.connection(), "rt_audit")
            session.commit()

            import_tenant(session.connection(), bundle, mode="fail", flip_to_embedded=True)
            session.commit()

            audit_row = session.scalars(
                select(AuditLog).where(
                    AuditLog.tenant_id == "rt_audit",
                    AuditLog.operation == "tenant.imported",
                )
            ).first()
            assert audit_row is not None
            assert audit_row.success is True
            assert audit_row.details["flip_to_embedded"] is True
            assert audit_row.details["mode"] == "fail"
            assert audit_row.details["rows"] > 0
            assert audit_count_before == 0


class TestSchemaMismatch:
    def test_old_schema_version_rejected(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_schema")
            bundle = export_tenant(session.connection(), "rt_schema")
            session.commit()
            delete_tenant_data(session.connection(), "rt_schema")
            session.commit()

            bundle["schema_version"] = 999

            with pytest.raises(BundleSchemaMismatchError):
                import_tenant(session.connection(), bundle)


class TestExportMissingTenant:
    def test_export_unknown_tenant_raises(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            with pytest.raises(TenantNotFoundError):
                export_tenant(session.connection(), "does_not_exist")
