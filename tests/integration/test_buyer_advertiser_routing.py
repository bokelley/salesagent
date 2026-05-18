"""Sprint 1.8 — buyer-advertiser routing chain matrix.

Walks every step of the precedence chain end-to-end against a real
Postgres database, plus the sandbox carve-out and the
``TENANT_NOT_ACTIVATED`` fall-through.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from adcp.types.generated_poc.core.account_ref import AccountReference2
from adcp.types.generated_poc.core.brand_ref import BrandReference

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AdvertiserRoutingRule,
    Tenant,
)
from src.services.buyer_advertiser_routing import (
    AdCPTenantNotActivated,
    create_account_from_routing,
    ensure_sandbox_advertiser,
    resolve_advertiser_for_buy,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_account_ref(
    operator: str = "interchange.io",
    brand_domain: str = "coca-cola.com",
    brand_id: str | None = None,
    sandbox: bool = False,
) -> AccountReference2:
    """Build a (sprint 1.8) inline AccountReference2 for the chain tests."""
    brand = BrandReference(domain=brand_domain, brand_id=brand_id)
    return AccountReference2(operator=operator, brand=brand, sandbox=sandbox)


@pytest.fixture
def tenant_id_factory(integration_db):
    """Provision a embedded-mode tenant + AdapterConfig for routing-chain tests.

    Returns a callable so tests that need multiple isolated tenants get
    distinct rows; keeps cleanup mechanical.
    """
    created: list[str] = []

    def _make(*, default_advertiser: str | None = None) -> str:
        tid = f"tenant_routing_{datetime.now(UTC).timestamp():.0f}_{len(created)}"
        with get_db_session() as session:
            session.info["management_api_caller"] = True
            tenant = Tenant(
                tenant_id=tid,
                name=f"Routing Test {tid}",
                subdomain=tid.replace("_", "-"),
                ad_server="google_ad_manager",
                is_active=True,
                billing_plan="standard",
                is_embedded=True,
                external_org_id=tid,
                external_source="test",
                public_agent_url="https://test.scope3.com/agent",
                default_gam_advertiser_id=default_advertiser,
                authorized_emails=["test@example.com"],
                authorized_domains=[],
                human_review_required=True,
                auto_approve_format_ids=[],
            )
            session.add(tenant)
            session.add(
                AdapterConfig(
                    tenant_id=tid,
                    adapter_type="google_ad_manager",
                    gam_network_code="12345",
                )
            )
            session.commit()
        created.append(tid)
        return tid

    yield _make

    # Cleanup — Tenant cascade handles routing rules + accounts;
    # AdapterConfig has its own tenant_id FK with cascade in the model.
    from src.core.database.models import Account

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for tid in created:
            session.execute(AdvertiserRoutingRule.__table__.delete().where(AdvertiserRoutingRule.tenant_id == tid))
            session.execute(Account.__table__.delete().where(Account.tenant_id == tid))
            session.execute(AdapterConfig.__table__.delete().where(AdapterConfig.tenant_id == tid))
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _add_rule(
    tenant_id: str,
    operator: str,
    brand_house: str | None,
    brand_id: str | None,
    advertiser_id: str,
    *,
    principal_id: str | None = None,
) -> str:
    """Insert a routing rule directly. Returns the rule id.

    Rule ids are bounded to ``String(40)`` in the model — we use a hash of
    the tuple to keep length predictable across the full matrix.
    """
    import hashlib

    parts = f"{tenant_id}|{advertiser_id}|{principal_id or ''}|{brand_house or ''}|{brand_id or ''}"
    digest = hashlib.sha256(parts.encode()).hexdigest()[:16]
    rule_id = f"rule_{digest}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        session.add(
            AdvertiserRoutingRule(
                id=rule_id,
                tenant_id=tenant_id,
                principal_id=principal_id,
                operator_domain=operator,
                brand_house=brand_house,
                brand_id=brand_id,
                gam_advertiser_id=advertiser_id,
            )
        )
        session.commit()
    return rule_id


# ---------------------------------------------------------------------------
# Resolution-chain matrix
# ---------------------------------------------------------------------------


class TestResolutionChain:
    """Each test exercises one precedence step in isolation, then a couple
    of higher-priority-wins scenarios verify ordering."""

    def test_exact_match_returns_resolved_via_exact(self, tenant_id_factory):
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "111")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "111"
        assert via == "exact"

    def test_house_wildcard_when_no_exact_match(self, tenant_id_factory):
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", None, "222")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="dasani")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "222"
        assert via == "house"

    def test_operator_wildcard_when_no_house_or_exact(self, tenant_id_factory):
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", None, None, "333")
        ref = _make_account_ref(brand_domain="some-brand.com", brand_id=None)

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "333"
        assert via == "operator"

    def test_default_when_no_rules_match(self, tenant_id_factory):
        tid = tenant_id_factory(default_advertiser="999")
        ref = _make_account_ref()

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "999"
        assert via == "default"

    def test_raises_tenant_not_activated_when_no_default_no_rules(self, tenant_id_factory):
        tid = tenant_id_factory()
        ref = _make_account_ref()

        with get_db_session() as session, pytest.raises(AdCPTenantNotActivated) as exc_info:
            resolve_advertiser_for_buy(session, tid, ref)

        assert exc_info.value.code == "TENANT_NOT_ACTIVATED"
        assert exc_info.value.details["operator"] == "interchange.io"
        assert exc_info.value.details["brand_house"] == "coca-cola.com"
        assert exc_info.value.details["tenant_id"] == tid

    def test_exact_beats_house_wildcard(self, tenant_id_factory):
        """Both rules exist — exact must win."""
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "exact_adv")
        _add_rule(tid, "interchange.io", "coca-cola.com", None, "house_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "exact_adv"
        assert via == "exact"

    def test_house_beats_operator_wildcard(self, tenant_id_factory):
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", None, "house_adv")
        _add_rule(tid, "interchange.io", None, None, "operator_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="dasani")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "house_adv"
        assert via == "house"

    def test_operator_beats_tenant_default(self, tenant_id_factory):
        tid = tenant_id_factory(default_advertiser="default_adv")
        _add_rule(tid, "interchange.io", None, None, "operator_adv")
        ref = _make_account_ref(brand_domain="some-brand.com")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "operator_adv"
        assert via == "operator"

    def test_different_operator_falls_through_to_default(self, tenant_id_factory):
        """Rule for operator A must NOT match a buy from operator B."""
        tid = tenant_id_factory(default_advertiser="default_adv")
        _add_rule(tid, "interchange.io", None, None, "interchange_adv")
        ref = _make_account_ref(operator="other-buyer.com")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "default_adv"
        assert via == "default"

    def test_brand_id_none_skips_exact_step(self, tenant_id_factory):
        """When buy carries no brand_id, the exact-match step is skipped
        entirely — even if a (operator, brand_house, brand_id) rule exists,
        the buy's null brand_id can't match it."""
        tid = tenant_id_factory(default_advertiser="default_adv")
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "exact_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id=None)

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        # Falls through past exact (skipped) and house (no rule) to default.
        assert advertiser == "default_adv"
        assert via == "default"

    # ---------------------------------------------------------------------
    # Sprint 5 — agent in the routing key. Agent-tagged rules beat
    # agent-agnostic rules at every brand-specificity tier.
    # ---------------------------------------------------------------------

    def test_agent_exact_beats_agnostic_exact(self, tenant_id_factory):
        """Two exact rules under the same triple — one agent-tagged for
        ``scope3-emb``, one agent-agnostic. The agent-tagged rule wins
        when scope3-emb is the caller."""
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "agent_adv", principal_id="scope3-emb")
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "agnostic_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref, principal_id="scope3-emb")

        assert advertiser == "agent_adv"
        assert via == "exact"

    def test_agent_house_beats_agent_operator(self, tenant_id_factory):
        """Same agent, two rules — house-level beats operator-level for that agent."""
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", None, "agent_house_adv", principal_id="scope3-emb")
        _add_rule(tid, "interchange.io", None, None, "agent_operator_adv", principal_id="scope3-emb")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="dasani")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref, principal_id="scope3-emb")

        assert advertiser == "agent_house_adv"
        assert via == "house"

    def test_agent_house_beats_agnostic_exact(self, tenant_id_factory):
        """All agent-tagged tiers (1-3) beat any agent-agnostic tier (4-6).
        Agent-tagged house (tier 2) wins over agent-agnostic exact (tier 4)."""
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", None, "agent_house_adv", principal_id="scope3-emb")
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "agnostic_exact_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref, principal_id="scope3-emb")

        assert advertiser == "agent_house_adv"
        assert via == "house"

    def test_agent_falls_through_to_agnostic_when_no_agent_rule(self, tenant_id_factory):
        """Agent has no tagged rule for this triple — falls through to
        the agnostic tier and matches the agent-agnostic exact rule."""
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "agnostic_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref, principal_id="wstruck-buy")

        assert advertiser == "agnostic_adv"
        assert via == "exact"

    def test_agent_with_no_rules_falls_through_to_default(self, tenant_id_factory):
        """Tagged rules for a different agent must NOT match the caller."""
        tid = tenant_id_factory(default_advertiser="default_adv")
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "other_agent_adv", principal_id="other-agent")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref, principal_id="scope3-emb")

        assert advertiser == "default_adv"
        assert via == "default"

    def test_principal_id_none_only_matches_agnostic_rules(self, tenant_id_factory):
        """Sprint 1.8 backward-compat: caller passes no principal_id, an
        agent-tagged rule exists — must NOT match. Falls through to default."""
        tid = tenant_id_factory(default_advertiser="default_adv")
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "agent_adv", principal_id="scope3-emb")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)  # no principal_id

        assert advertiser == "default_adv"
        assert via == "default"

    def test_sprint_1_8_behavior_preserved_when_principal_id_none(self, tenant_id_factory):
        """Caller passes no principal_id, only agnostic rules exist — Sprint 1.8
        chain matches identically to before this change."""
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", None, "agnostic_house_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref)

        assert advertiser == "agnostic_house_adv"
        assert via == "house"


# ---------------------------------------------------------------------------
# Sandbox carve-out
# ---------------------------------------------------------------------------


class TestSandboxCarveOut:
    """Sandbox=true must short-circuit the chain regardless of rules/default."""

    def test_sandbox_short_circuits_rules(self, tenant_id_factory):
        tid = tenant_id_factory(default_advertiser="default_adv")
        # Add an exact rule that WOULD win for non-sandbox traffic.
        _add_rule(tid, "interchange.io", "coca-cola.com", "sprite", "rule_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite", sandbox=True)

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref, dry_run=True)

        # Sandbox path returns the synthetic dry-run id, not rule_adv.
        assert via == "sandbox"
        assert advertiser != "rule_adv"
        assert advertiser != "default_adv"

    def test_sandbox_caches_advertiser_id(self, tenant_id_factory):
        """Two sandbox calls return the same id — cached on AdapterConfig."""
        tid = tenant_id_factory()

        with get_db_session() as session:
            first = ensure_sandbox_advertiser(session, tid, dry_run=True)
            session.commit()

        with get_db_session() as session:
            second = ensure_sandbox_advertiser(session, tid, dry_run=True)

        assert first == second

    def test_sandbox_with_no_default_does_not_raise(self, tenant_id_factory):
        """Sandbox doesn't depend on tenant default — implicit-activation
        gate applies to commercial traffic only."""
        tid = tenant_id_factory()  # No default_advertiser
        ref = _make_account_ref(sandbox=True)

        with get_db_session() as session:
            advertiser, via = resolve_advertiser_for_buy(session, tid, ref, dry_run=True)

        assert via == "sandbox"
        assert advertiser is not None


# ---------------------------------------------------------------------------
# Auto-Account creation
# ---------------------------------------------------------------------------


class TestAutoAccountCreation:
    """First-buy from an unmapped triple creates an Account row with
    resolved_via stamped + advertiser already attached."""

    def test_creates_active_account_with_stamped_resolved_via(self, tenant_id_factory):
        tid = tenant_id_factory()
        _add_rule(tid, "interchange.io", "coca-cola.com", None, "house_adv")
        ref = _make_account_ref(brand_domain="coca-cola.com", brand_id="sprite")

        with get_db_session() as session:
            session.info["management_api_caller"] = True
            account = create_account_from_routing(session, tid, ref)
            session.commit()
            session.refresh(account)
            account_id = account.account_id
            assert account.status == "active"
            assert account.resolved_via == "house"
            assert account.platform_mappings["google_ad_manager"]["advertiser_id"] == "house_adv"
            assert account.operator == "interchange.io"
            # Account.brand round-trips through JSONValidatorMixin into a
            # BrandReference Pydantic model; compare on the salient fields
            # rather than the wire representation.
            brand = account.brand
            domain = brand.domain if hasattr(brand, "domain") else brand["domain"]
            brand_id_field = brand.brand_id if hasattr(brand, "brand_id") else brand.get("brand_id")
            brand_id_str = (
                str(brand_id_field.root)
                if brand_id_field is not None and hasattr(brand_id_field, "root")  # noqa: rootmodel
                else brand_id_field
            )
            assert domain == "coca-cola.com"
            assert brand_id_str == "sprite"

        # Verify it round-trips via a fresh session
        with get_db_session() as session:
            from sqlalchemy import select

            from src.core.database.models import Account

            persisted = session.scalars(select(Account).filter_by(account_id=account_id)).first()
            assert persisted is not None
            assert persisted.resolved_via == "house"

    def test_sandbox_account_creation_stamps_sandbox_resolved_via(self, tenant_id_factory):
        tid = tenant_id_factory()
        ref = _make_account_ref(brand_domain="test.example", sandbox=True)

        with get_db_session() as session:
            session.info["management_api_caller"] = True
            account = create_account_from_routing(session, tid, ref, dry_run=True)
            session.commit()
            session.refresh(account)
            assert account.resolved_via == "sandbox"
            assert account.sandbox is True
            assert account.status == "active"

    def test_principal_id_sets_billing_agent(self, tenant_id_factory):
        """Sprint 1.6 split: passing principal_id sets billing=agent +
        Account.principal_id; Sprint 1.8 chain doesn't change that."""
        tid = tenant_id_factory(default_advertiser="default_adv")
        ref = _make_account_ref()

        with get_db_session() as session:
            session.info["management_api_caller"] = True
            account = create_account_from_routing(session, tid, ref, principal_id="scope3-buyer-1")
            session.commit()
            session.refresh(account)
            assert account.billing == "agent"
            assert account.principal_id == "scope3-buyer-1"

    def test_no_principal_id_sets_billing_operator(self, tenant_id_factory):
        tid = tenant_id_factory(default_advertiser="default_adv")
        ref = _make_account_ref()

        with get_db_session() as session:
            session.info["management_api_caller"] = True
            account = create_account_from_routing(session, tid, ref)
            session.commit()
            session.refresh(account)
            assert account.billing == "operator"
            assert account.principal_id is None

    def test_unactivated_tenant_raises(self, tenant_id_factory):
        """No rules + no default + non-sandbox → TENANT_NOT_ACTIVATED."""
        tid = tenant_id_factory()
        ref = _make_account_ref()

        with get_db_session() as session, pytest.raises(AdCPTenantNotActivated):
            create_account_from_routing(session, tid, ref)
