"""Unit tests for the embedded-mode buyer-protocol identity resolver.

Covers ``_try_resolve_embedded_buyer_identity`` — the helper that lets buyer
protocol endpoints (`/mcp/`, `/a2a`) authenticate via ``X-Principal-Id`` +
``X-Identity-*`` headers from a trusted network instead of a per-principal
``x-adcp-auth`` bearer. See ``docs/design/embedded-mode.md`` §2.

Contract gates:
  * ``MANAGED_INSTANCE=true`` deployment env (or returns None)
  * ``tenant.is_embedded=True`` (or returns None)
  * Required ``X-Identity-*`` headers, with the org id matched when the
    tenant has an embedding entity id configured
  * An explicit valid ``X-Principal-Id`` in the tenant
"""

from unittest.mock import MagicMock, patch

import pytest


def _identity_headers(*, org_id="org_a"):
    return {
        "X-Identity-Email": "buyer@example.com",
        "X-Identity-Org-Id": org_id,
        "X-Identity-Role": "admin",
        "X-Identity-Source": "storefront",
    }


def _embedded_tenant():
    return {"tenant_id": "tenant_a", "is_embedded": True, "external_org_id": "org_a"}


def _call(headers, tenant_context, principals_in_tenant, *, require_valid_token=True):
    """Invoke the helper with the given inputs. Returns the principal_id or None."""
    from src.core import auth as auth_module

    with patch.object(auth_module, "_get_header_case_insensitive", side_effect=lambda h, n: h.get(n)):
        with patch("src.admin.utils.embedded_mode_auth.is_managed_instance", return_value=True):
            with patch.object(auth_module, "get_db_session") as mock_db:
                session = MagicMock()

                # If an explicit principal id was queried, return the matching
                # principal (or None). Otherwise list_all returns all principals.
                def _scalars(stmt):
                    result = MagicMock()
                    # filter_by inside the stmt isn't introspectable in the mock,
                    # so the test drives behaviour by setting principals_in_tenant
                    # and the requested header. The helper calls .first() for
                    # explicit lookups and .all() for the default-principal path.
                    explicit = headers.get("X-Principal-Id")
                    if explicit is not None:
                        match = next(
                            (p for p in principals_in_tenant if p["principal_id"] == explicit),
                            None,
                        )
                        if match:
                            mock = MagicMock()
                            mock.principal_id = match["principal_id"]
                            result.first.return_value = mock
                        else:
                            result.first.return_value = None
                    result.all.return_value = [MagicMock(principal_id=p["principal_id"]) for p in principals_in_tenant]
                    return result

                session.scalars.side_effect = _scalars
                mock_db.return_value.__enter__ = MagicMock(return_value=session)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                return auth_module._try_resolve_embedded_buyer_identity(headers, tenant_context, require_valid_token)


class TestEmbeddedBuyerIdentity:
    @pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
    def test_managed_instance_boolean_parsing_matches_core(self, monkeypatch, value):
        from src.admin.utils.embedded_mode_auth import is_managed_instance

        monkeypatch.setenv("MANAGED_INSTANCE", value)
        assert is_managed_instance() is True

    def test_serialized_tenant_context_contains_external_org_id(self):
        from src.core.tenant_context import TenantContext
        from src.core.utils.tenant_utils import serialize_tenant_to_dict

        tenant = MagicMock()
        tenant.tenant_id = "tenant_a"
        tenant.name = "Tenant A"
        tenant.subdomain = "tenant-a"
        tenant.is_embedded = True
        tenant.external_org_id = "org_a"

        serialized = serialize_tenant_to_dict(tenant)

        assert serialized["external_org_id"] == "org_a"
        assert (
            TenantContext.from_dict(
                {"tenant_id": serialized["tenant_id"], "external_org_id": serialized["external_org_id"]}
            ).external_org_id
            == "org_a"
        )

    def test_returns_none_when_managed_instance_disabled(self):
        from src.core import auth as auth_module

        with patch("src.admin.utils.embedded_mode_auth.is_managed_instance", return_value=False):
            result = auth_module._try_resolve_embedded_buyer_identity(
                {"X-Principal-Id": "principal_x"},
                {"tenant_id": "tenant_a", "is_embedded": True},
                require_valid_token=True,
            )
        assert result is None

    def test_returns_none_when_tenant_not_embedded(self):
        result = _call(
            headers={"X-Principal-Id": "principal_x"},
            tenant_context={"tenant_id": "tenant_a", "is_embedded": False},
            principals_in_tenant=[{"principal_id": "principal_x"}],
        )
        assert result is None

    def test_returns_none_when_no_tenant_context(self):
        result = _call(
            headers={"X-Principal-Id": "principal_x"},
            tenant_context=None,
            principals_in_tenant=[],
        )
        assert result is None

    def test_resolves_explicit_principal_when_match_exists(self):
        result = _call(
            headers={"X-Principal-Id": "principal_x", **_identity_headers()},
            tenant_context=_embedded_tenant(),
            principals_in_tenant=[{"principal_id": "principal_x"}],
        )
        assert result == "principal_x"

    def test_resolves_principal_when_embedding_entity_id_not_configured(self):
        result = _call(
            headers={"X-Principal-Id": "principal_x", **_identity_headers(org_id="storefront_a")},
            tenant_context={"tenant_id": "tenant_a", "is_embedded": True, "external_org_id": None},
            principals_in_tenant=[{"principal_id": "principal_x"}],
        )
        assert result == "principal_x"

    def test_explicit_principal_mismatch_raises_when_require_valid(self):
        from src.core.exceptions import AdCPAuthenticationError

        with pytest.raises(AdCPAuthenticationError):
            _call(
                headers={"X-Principal-Id": "principal_other", **_identity_headers()},
                tenant_context=_embedded_tenant(),
                principals_in_tenant=[{"principal_id": "principal_x"}],
                require_valid_token=True,
            )

    def test_explicit_principal_mismatch_returns_none_when_not_require_valid(self):
        result = _call(
            headers={"X-Principal-Id": "principal_other", **_identity_headers()},
            tenant_context=_embedded_tenant(),
            principals_in_tenant=[{"principal_id": "principal_x"}],
            require_valid_token=False,
        )
        assert result is None

    def test_missing_principal_header_raises_when_require_valid(self):
        from src.core.exceptions import AdCPAuthenticationError

        with pytest.raises(AdCPAuthenticationError):
            _call(
                headers=_identity_headers(),
                tenant_context=_embedded_tenant(),
                principals_in_tenant=[{"principal_id": "principal_lone"}],
            )

    def test_missing_principal_header_returns_none_when_not_require_valid(self):
        result = _call(
            headers=_identity_headers(),
            tenant_context=_embedded_tenant(),
            principals_in_tenant=[{"principal_id": "principal_lone"}],
            require_valid_token=False,
        )
        assert result is None

    def test_missing_identity_headers_raise_when_require_valid(self):
        from src.core.exceptions import AdCPAuthenticationError

        with pytest.raises(AdCPAuthenticationError):
            _call(
                headers={"X-Principal-Id": "principal_x"},
                tenant_context=_embedded_tenant(),
                principals_in_tenant=[{"principal_id": "principal_x"}],
            )

    def test_identity_org_mismatch_raises_when_require_valid(self):
        from src.core.exceptions import AdCPAuthenticationError

        with pytest.raises(AdCPAuthenticationError):
            _call(
                headers={"X-Principal-Id": "principal_x", **_identity_headers(org_id="org_other")},
                tenant_context=_embedded_tenant(),
                principals_in_tenant=[{"principal_id": "principal_x"}],
            )
