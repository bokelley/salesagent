"""Unit tests for AAO lookup service status + validation helpers.

Covers:

- :func:`get_publisher_partner_status` returns the right ``status`` literal +
  counts for the three flows the UI cares about (authorized, pending,
  unreachable).
- :func:`validate_public_agent_url_hostname` rejects URLs whose hostname
  doesn't match the tenant's serving host (the trust-chain invariant —
  publishers' adagents.json would otherwise point at a host this salesagent
  doesn't answer on).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.aao_lookup_service import (
    PublicAgentUrlMismatch,
    get_publisher_partner_status,
    invalidate_adagents_cache,
    validate_public_agent_url_hostname,
)
from tests.helpers.adagents import managed_website_property


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_adagents_cache()
    yield
    invalidate_adagents_cache()


class TestGetPublisherPartnerStatusAuthorized:
    """When the agent_url IS listed, status='authorized' and counts populate."""

    @pytest.mark.asyncio
    async def test_authorized_returns_counts(self):
        # Spec-compliant payload — entries declare authorization_type +
        # matching selector. validate_adagents_structure passes through.
        adagents = {
            "authorized_agents": [
                {
                    "url": "https://interchange.io",
                    "authorized_for": "Demand for inline-listed properties",
                    "authorization_type": "inline_properties",
                    "properties": [
                        {
                            "property_id": "p1",
                            "property_type": "website",
                            "name": "P1",
                            "identifiers": [{"type": "domain", "value": "p1.example.com"}],
                        },
                        {
                            "property_id": "p2",
                            "property_type": "website",
                            "name": "P2",
                            "identifiers": [{"type": "domain", "value": "p2.example.com"}],
                        },
                    ],
                },
                {
                    "url": "https://other-agent.example.com",
                    "authorized_for": "Demand for inline-listed properties",
                    "authorization_type": "inline_properties",
                    "properties": [
                        {
                            "property_id": "p3",
                            "property_type": "website",
                            "name": "P3",
                            "identifiers": [{"type": "domain", "value": "p3.example.com"}],
                        },
                    ],
                },
            ]
        }
        with (
            patch(
                "src.services.aao_lookup_service.fetch_adagents",
                AsyncMock(return_value=adagents),
            ),
            patch(
                "src.services.aao_lookup_service.get_all_properties",
                return_value=[{"property_id": "p1"}, {"property_id": "p2"}, {"property_id": "p3"}],
            ),
            patch(
                "src.services.aao_lookup_service.get_properties_by_agent",
                return_value=[{"property_id": "p1"}, {"property_id": "p2"}],
            ),
        ):
            status = await get_publisher_partner_status("wonderstruck.org", "https://interchange.io")

        assert status.status == "authorized"
        assert status.total_properties == 3
        assert status.authorized_properties == 2
        assert status.aao_onboarding_url == "https://agenticadvertising.org/publisher/wonderstruck.org"
        assert status.error is None

    @pytest.mark.asyncio
    async def test_authorized_resolves_publisher_properties_compact_form(self):
        """Managed-network selector shape resolves through the SDK, not the
        legacy selector-dict passthrough that made CafeMedia look pending."""
        adagents = {
            "properties": [
                managed_website_property("site_a", "a.example.com", "Site A"),
                managed_website_property("site_b", "b.example.com", "Site B"),
                {
                    "property_id": "site_c",
                    "property_type": "website",
                    "name": "Site C",
                    "identifiers": [{"type": "domain", "value": "c.example.com"}],
                    "publisher_domain": "c.example.com",
                    "tags": ["other"],
                },
            ],
            "authorized_agents": [
                {
                    "url": "https://interchange.io",
                    "authorization_type": "publisher_properties",
                    "publisher_properties": [
                        {
                            "publisher_domains": ["a.example.com", "b.example.com", "c.example.com"],
                            "selection_type": "by_tag",
                            "property_tags": ["managed"],
                        }
                    ],
                }
            ],
        }
        with patch(
            "src.services.aao_lookup_service.fetch_adagents",
            AsyncMock(return_value=adagents),
        ):
            status = await get_publisher_partner_status("cafemedia.com", "https://interchange.io")

        assert status.status == "authorized"
        assert status.total_properties == 2
        assert status.authorized_properties == 2
        assert status.error is None


class TestGetPublisherPartnerStatusPending:
    """When fetch succeeds but agent_url isn't listed: status='pending'."""

    @pytest.mark.asyncio
    async def test_pending_when_no_authorized_properties(self):
        adagents = {
            "authorized_agents": [
                {
                    "url": "https://other-agent.example.com",
                    "authorized_for": "Demand for inline-listed properties",
                    "authorization_type": "inline_properties",
                    "properties": [
                        {
                            "property_id": "p1",
                            "property_type": "website",
                            "name": "P1",
                            "identifiers": [{"type": "domain", "value": "p1.example.com"}],
                        },
                        {
                            "property_id": "p2",
                            "property_type": "website",
                            "name": "P2",
                            "identifiers": [{"type": "domain", "value": "p2.example.com"}],
                        },
                    ],
                }
            ]
        }
        with (
            patch(
                "src.services.aao_lookup_service.fetch_adagents",
                AsyncMock(return_value=adagents),
            ),
            patch(
                "src.services.aao_lookup_service.get_all_properties",
                return_value=[{"property_id": "p1"}, {"property_id": "p2"}],
            ),
            patch(
                "src.services.aao_lookup_service.get_properties_by_agent",
                return_value=[],
            ),
        ):
            status = await get_publisher_partner_status("wonderstruck.org", "https://interchange.io")

        assert status.status == "pending"
        assert status.total_properties == 2
        assert status.authorized_properties == 0
        assert status.error is None


class TestGetPublisherPartnerStatusUnbound:
    """Wonderstruck-class file: our agent is in ``authorized_agents`` with a
    bare entry (no ``authorization_type``, no selector) and the file has a
    top-level ``properties[]`` block. Status is ``unbound`` — the file isn't
    spec-conformant but operationally usable (products bind to the top-level
    properties). The chip + error hint nudge the publisher to add a typed
    binding. See salesagent#377."""

    @pytest.mark.asyncio
    async def test_unbound_resolves_to_top_level_properties(self):
        adagents = {
            "$schema": "https://adcontextprotocol.org/schemas/v1/adagents.json",
            "authorized_agents": [
                {
                    "url": "https://interchange.io",
                    "authorized_for": "Display banners",
                },
                {
                    "url": "https://other-agent.example.com",
                    "authorized_for": "Display banners",
                },
            ],
            "properties": [
                {
                    "property_id": "main_site",
                    "property_type": "website",
                    "name": "Main site",
                    "identifiers": [{"type": "domain", "value": "wonderstruck.org"}],
                    "tags": ["sites"],
                }
            ],
        }
        with patch(
            "src.services.aao_lookup_service.fetch_adagents",
            AsyncMock(return_value=adagents),
        ):
            status = await get_publisher_partner_status("wonderstruck.org", "https://interchange.io")

        assert status.status == "unbound"
        assert status.total_properties == 1
        assert status.authorized_properties == 1
        assert status.error is not None
        assert "authorization_type" in status.error
        assert status.aao_onboarding_url == "https://agenticadvertising.org/publisher/wonderstruck.org"


class TestGetPublisherPartnerStatusNoProperties:
    """Raptive-class file (in its blocked variant): file fetches cleanly but
    exposes zero properties — nothing to sell even though our agent may be
    listed. Operator must ask the publisher to add a ``properties[]`` block
    before this row can do anything."""

    @pytest.mark.asyncio
    async def test_no_properties_when_listed_but_empty_inventory(self):
        adagents = {
            "authorized_agents": [
                {
                    "url": "https://interchange.io",
                    "authorized_for": "Display banners",
                },
            ],
            # No top-level properties[] and no inline properties anywhere.
        }
        with patch(
            "src.services.aao_lookup_service.fetch_adagents",
            AsyncMock(return_value=adagents),
        ):
            status = await get_publisher_partner_status("empty.example.com", "https://interchange.io")

        assert status.status == "no_properties"
        assert status.total_properties == 0
        assert status.authorized_properties == 0
        assert status.error is not None
        assert "properties[]" in status.error

    @pytest.mark.asyncio
    async def test_no_properties_when_not_listed_and_empty_inventory(self):
        adagents = {
            "authorized_agents": [
                {
                    "url": "https://other-agent.example.com",
                    "authorized_for": "Display banners",
                },
            ],
        }
        with patch(
            "src.services.aao_lookup_service.fetch_adagents",
            AsyncMock(return_value=adagents),
        ):
            status = await get_publisher_partner_status("empty.example.com", "https://interchange.io")

        assert status.status == "no_properties"


class TestGetPublisherPartnerStatusUnreachable:
    """When fetch raises: status='unreachable' carries the error message."""

    @pytest.mark.asyncio
    async def test_unreachable_on_fetch_failure(self):
        with patch(
            "src.services.aao_lookup_service.fetch_adagents",
            AsyncMock(side_effect=RuntimeError("DNS failure")),
        ):
            status = await get_publisher_partner_status("broken.example.com", "https://interchange.io")

        assert status.status == "unreachable"
        assert status.total_properties == 0
        assert status.authorized_properties == 0
        assert "DNS failure" in (status.error or "")
        assert status.aao_onboarding_url == "https://agenticadvertising.org/publisher/broken.example.com"


class TestValidatePublicAgentUrlHostname:
    """Hostname-match guard for public_agent_url saves."""

    def test_embedded_accepts_interchange(self):
        # Doesn't raise.
        validate_public_agent_url_hostname(
            "https://interchange.io",
            is_embedded=True,
            virtual_host=None,
            subdomain=None,
            sales_agent_domain=None,
        )

    def test_embedded_rejects_arbitrary_host(self):
        with pytest.raises(PublicAgentUrlMismatch):
            validate_public_agent_url_hostname(
                "https://random.example.com",
                is_embedded=True,
                virtual_host=None,
                subdomain=None,
                sales_agent_domain=None,
            )

    def test_self_hosted_accepts_virtual_host(self):
        validate_public_agent_url_hostname(
            "https://sales-agent.wonderstruck.org",
            is_embedded=False,
            virtual_host="sales-agent.wonderstruck.org",
            subdomain="wonderstruck",
            sales_agent_domain="sales-agent.scope3.com",
        )

    def test_self_hosted_accepts_subdomain_default(self):
        validate_public_agent_url_hostname(
            "https://wonderstruck.sales-agent.scope3.com",
            is_embedded=False,
            virtual_host=None,
            subdomain="wonderstruck",
            sales_agent_domain="sales-agent.scope3.com",
        )

    def test_self_hosted_rejects_mismatch(self):
        with pytest.raises(PublicAgentUrlMismatch) as exc_info:
            validate_public_agent_url_hostname(
                "https://attacker.example.com",
                is_embedded=False,
                virtual_host="sales-agent.wonderstruck.org",
                subdomain="wonderstruck",
                sales_agent_domain="sales-agent.scope3.com",
            )
        assert "attacker.example.com" in str(exc_info.value)

    def test_url_without_hostname_rejected(self):
        with pytest.raises(PublicAgentUrlMismatch):
            validate_public_agent_url_hostname(
                "not-a-url",
                is_embedded=True,
                virtual_host=None,
                subdomain=None,
                sales_agent_domain=None,
            )

    def test_invalid_port_rejected(self):
        with pytest.raises(PublicAgentUrlMismatch, match="invalid port"):
            validate_public_agent_url_hostname(
                "https://sales-agent.wonderstruck.org:abc",
                is_embedded=False,
                virtual_host="sales-agent.wonderstruck.org",
                subdomain=None,
                sales_agent_domain=None,
            )

    def test_trailing_dot_fqdn_normalized(self):
        """``urlparse("https://example.com.").hostname`` returns
        ``"example.com."`` — the validator strips the trailing dot before
        compare so legitimate FQDN-form URLs validate."""
        validate_public_agent_url_hostname(
            "https://sales-agent.wonderstruck.org.",
            is_embedded=False,
            virtual_host="sales-agent.wonderstruck.org",
            subdomain=None,
            sales_agent_domain=None,
        )

    def test_idn_punycode_matches_unicode_virtual_host(self):
        """If the URL hostname comes through as punycode (``xn--bcher-kva.example``)
        but ``virtual_host`` is stored as unicode (``bücher.example``), the
        validator IDN-folds both sides to ASCII before comparing — same
        domain, same match."""
        validate_public_agent_url_hostname(
            "https://xn--bcher-kva.example",
            is_embedded=False,
            virtual_host="bücher.example",
            subdomain=None,
            sales_agent_domain=None,
        )

    def test_idn_unicode_url_matches_punycode_virtual_host(self):
        """And the reverse: unicode in URL, punycode in virtual_host."""
        validate_public_agent_url_hostname(
            "https://bücher.example",
            is_embedded=False,
            virtual_host="xn--bcher-kva.example",
            subdomain=None,
            sales_agent_domain=None,
        )


# SSRF guard for publisher_domain lives in src.core.security.url_validator
# (introduced by main's PR #98); see tests/unit/test_publisher_domain_ssrf.py
# for that coverage. Removed validate_publisher_domain_safe here in favor
# of the shared helper.


class TestFetchPublishersFromDirectory:
    """Wraps the SDK's inverse-lookup with pagination + dataclass projection.

    The salesagent code shouldn't take a hard dependency on the SDK's
    Pydantic shape (it iterates rapidly across beta versions). These tests
    verify:

    - Pagination walks every page the SDK returns and stops on absent
      ``next_cursor``.
    - SDK Pydantic objects are projected onto :class:`DirectoryPublisher`
      dataclass fields with the right defaults when the SDK omits values.
    - The freshest ``directory_indexed_at`` (last page seen) is preserved.
    - SDK exceptions surface unchanged so endpoint code can translate to
      HTTP 502/504.
    """

    @pytest.mark.asyncio
    async def test_paginates_until_no_cursor_and_projects_fields(self):
        from src.services.aao_lookup_service import (
            DirectoryPublisher,
            fetch_publishers_from_directory,
        )

        def _make_page(domains, cursor):
            # Mirror the SDK's Pydantic shape with attribute access.
            class _Pub:
                def __init__(self, d):
                    self.publisher_domain = d
                    self.discovery_method = "ads_txt_managerdomain"
                    self.manager_domain = "cafemedia.com"
                    self.status = "authorized"
                    self.properties_total = 2
                    self.properties_authorized = 1
                    self.last_verified_at = None

            class _Page:
                def __init__(self, ds, cur):
                    self.publishers = [_Pub(d) for d in ds]
                    self.next_cursor = cur
                    self.directory_indexed_at = "2026-05-22T11:24:16.689Z"

            return _Page(domains, cursor)

        # Three pages: a, b, then c with no next_cursor → stop.
        pages = [
            _make_page(["a.com", "b.com"], "cursor-1"),
            _make_page(["c.com"], "cursor-2"),
            _make_page(["d.com"], None),
        ]
        with patch(
            "src.services.aao_lookup_service.fetch_agent_authorizations_from_directory",
            new=AsyncMock(side_effect=pages),
        ) as sdk_call:
            result = await fetch_publishers_from_directory(
                "https://agent.example.com",
                directory_url="https://aao.example.com",
            )

        # Walked exactly three pages (stopped on absent next_cursor, not
        # the 50-page cap).
        assert sdk_call.await_count == 3
        assert result.pages_fetched == 3
        # Cursors threaded through correctly: page 1 had no cursor, page 2
        # got "cursor-1", page 3 got "cursor-2".
        assert sdk_call.await_args_list[0].kwargs["cursor"] is None
        assert sdk_call.await_args_list[1].kwargs["cursor"] == "cursor-1"
        assert sdk_call.await_args_list[2].kwargs["cursor"] == "cursor-2"
        # All four publishers concatenated.
        assert [p.publisher_domain for p in result.publishers] == ["a.com", "b.com", "c.com", "d.com"]
        # SDK Pydantic objects projected onto our dataclass shape.
        assert all(isinstance(p, DirectoryPublisher) for p in result.publishers)
        assert result.publishers[0].discovery_method == "ads_txt_managerdomain"
        assert result.publishers[0].manager_domain == "cafemedia.com"
        assert result.publishers[0].properties_authorized == 1
        # directory_indexed_at preserved verbatim from the page (string form
        # — the projection only calls .isoformat() when the SDK gave us a
        # datetime).
        assert result.directory_indexed_at == "2026-05-22T11:24:16.689Z"

    @pytest.mark.asyncio
    async def test_sdk_error_propagates(self):
        from adcp.exceptions import AdagentsValidationError

        from src.services.aao_lookup_service import fetch_publishers_from_directory

        with patch(
            "src.services.aao_lookup_service.fetch_agent_authorizations_from_directory",
            new=AsyncMock(side_effect=AdagentsValidationError("directory 502")),
        ):
            with pytest.raises(AdagentsValidationError, match="directory 502"):
                await fetch_publishers_from_directory("https://agent.example.com")
