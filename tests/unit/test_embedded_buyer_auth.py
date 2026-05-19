"""Unit tests for the embedded-mode buyer-protocol identity resolver.

Covers ``_try_resolve_embedded_buyer_identity`` — the helper that lets buyer
protocol endpoints (`/mcp/`, `/a2a`) authenticate via ``X-Principal-Id`` +
``X-Identity-*`` headers from a trusted network instead of a per-principal
``x-adcp-auth`` bearer. See ``docs/design/embedded-mode.md`` §2.

Contract gates:
  * ``MANAGED_INSTANCE=true`` deployment env (or returns None)
  * ``tenant.is_embedded=True`` (or returns None)
  * Either an explicit valid ``X-Principal-Id``, or exactly one principal
    in the tenant (the backfill default).
"""

from unittest.mock import MagicMock, patch

import pytest


def _call(headers, tenant_context, principals_in_tenant, *, require_valid_token=True):
    """Invoke the helper. Returns (principal_id_or_None, added_principal_or_None).

    ``added_principal`` is the ModelPrincipal instance the helper added via
    ``session.add(...)`` when the zero-principal auto-create path runs.
    """
    from src.core import auth as auth_module

    added = []

    with patch.object(
        auth_module, "_get_header_case_insensitive", side_effect=lambda h, n: h.get(n)
    ):
        with patch("src.admin.utils.embedded_mode_auth.is_managed_instance", return_value=True):
            with patch.object(auth_module, "get_db_session") as mock_db:
                session = MagicMock()

                def _scalars(stmt):
                    result = MagicMock()
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
                    result.all.return_value = [
                        MagicMock(principal_id=p["principal_id"]) for p in principals_in_tenant
                    ]
                    return result

                session.scalars.side_effect = _scalars
                session.add.side_effect = lambda obj: added.append(obj)
                mock_db.return_value.__enter__ = MagicMock(return_value=session)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = auth_module._try_resolve_embedded_buyer_identity(
                    headers, tenant_context, require_valid_token
                )
                return result, (added[0] if added else None)


class TestEmbeddedBuyerIdentity:
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
        result, _ = _call(
            headers={"X-Principal-Id": "principal_x"},
            tenant_context={"tenant_id": "tenant_a", "is_embedded": False},
            principals_in_tenant=[{"principal_id": "principal_x"}],
        )
        assert result is None

    def test_returns_none_when_no_tenant_context(self):
        result, _ = _call(
            headers={"X-Principal-Id": "principal_x"},
            tenant_context=None,
            principals_in_tenant=[],
        )
        assert result is None

    def test_resolves_explicit_principal_when_match_exists(self):
        result, _ = _call(
            headers={"X-Principal-Id": "principal_x"},
            tenant_context={"tenant_id": "tenant_a", "is_embedded": True},
            principals_in_tenant=[{"principal_id": "principal_x"}],
        )
        assert result == "principal_x"

    def test_explicit_principal_mismatch_raises_when_require_valid(self):
        from src.core.exceptions import AdCPAuthenticationError

        with pytest.raises(AdCPAuthenticationError):
            _call(
                headers={"X-Principal-Id": "principal_other"},
                tenant_context={"tenant_id": "tenant_a", "is_embedded": True},
                principals_in_tenant=[{"principal_id": "principal_x"}],
                require_valid_token=True,
            )

    def test_explicit_principal_mismatch_returns_none_when_not_require_valid(self):
        result, _ = _call(
            headers={"X-Principal-Id": "principal_other"},
            tenant_context={"tenant_id": "tenant_a", "is_embedded": True},
            principals_in_tenant=[{"principal_id": "principal_x"}],
            require_valid_token=False,
        )
        assert result is None

    def test_defaults_to_lone_principal_when_no_header(self):
        result, _ = _call(
            headers={},
            tenant_context={"tenant_id": "tenant_a", "is_embedded": True},
            principals_in_tenant=[{"principal_id": "principal_lone"}],
        )
        assert result == "principal_lone"

    def test_returns_none_when_multiple_principals_and_no_header(self):
        result, _ = _call(
            headers={},
            tenant_context={"tenant_id": "tenant_a", "is_embedded": True},
            principals_in_tenant=[
                {"principal_id": "principal_a"},
                {"principal_id": "principal_b"},
            ],
        )
        assert result is None

    def test_auto_creates_default_principal_when_zero_exist(self):
        """Embedded tenants with no Principal row self-heal on first auth call.

        Removes the need for any host-side backfill of pre-existing tenants
        that were provisioned without `initial_principal`. The host product
        is the source of truth; the Principal row is a schema-level detail.
        """
        result, added = _call(
            headers={},
            tenant_context={
                "tenant_id": "tenant_a",
                "is_embedded": True,
                "ad_server": "google_ad_manager",
            },
            principals_in_tenant=[],
        )
        assert result is not None
        assert result.startswith("prin_")
        # Inline-created Principal carries the embedded marker on its
        # access_token so it's distinguishable from open-instance bearers.
        assert added is not None
        assert added.tenant_id == "tenant_a"
        assert added.principal_id == result
        assert added.access_token.startswith("embedded-mode-no-token:")
        # Placeholder platform mapping keyed by the tenant's adapter type
        # — required by the PlatformMappingModel validator; operator can
        # patch the advertiser_id once a real one is known.
        assert added.platform_mappings == {
            "google_ad_manager": {"advertiser_id": "placeholder"}
        }

    def test_auto_create_falls_back_to_mock_mapping_for_unknown_adapter(self):
        result, added = _call(
            headers={},
            tenant_context={
                "tenant_id": "tenant_a",
                "is_embedded": True,
                "ad_server": None,
            },
            principals_in_tenant=[],
        )
        assert result is not None
        assert added.platform_mappings == {"mock": {"advertiser_id": "default"}}
