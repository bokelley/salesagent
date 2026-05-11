"""Wire-code coverage for update_media_buy not-found rejections (issue #73).

The AdCP storyboard's ``invalid_transitions`` scenarios assert that
``update_media_buy`` rejections surface specific spec codes:

* unknown ``media_buy_id`` -> ``MEDIA_BUY_NOT_FOUND``
* unknown ``package_id`` -> ``PACKAGE_NOT_FOUND``
* cross-tenant access (a buy that exists on a different tenant) ->
  ``MEDIA_BUY_NOT_FOUND`` (NOT a permissions code; surfacing 403 leaks
  cross-tenant existence to attackers)

These tests pin both layers:

1. ``_update_media_buy_impl`` raises the typed ``AdCPError`` subclass
   (transport-agnostic).
2. ``_delegate_update_media_buy`` translates that into the framework's
   wire-shaped ``AdcpError`` so the dispatcher emits the correct
   ``adcp_error.code`` envelope.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import (
    AdCPMediaBuyNotFoundError,
    AdCPPackageNotFoundError,
)
from src.core.schemas import UpdateMediaBuyRequest
from src.core.tools.media_buy_update import _update_media_buy_impl, _verify_principal
from tests.factories.spec_required_kwargs import required_request_kwargs
from tests.unit._update_media_buy_helpers import (
    UpdateMediaBuyImplFixture,
    make_delegate_ctx,
    make_identity,
    run_delegate_coro,
)

# ---------------------------------------------------------------------------
# Layer 1: _impl raises AdCPMediaBuyNotFoundError / AdCPPackageNotFoundError
# ---------------------------------------------------------------------------


class TestImplRaisesTypedNotFoundErrors:
    """``_update_media_buy_impl`` raises the typed AdCPError, not ToolError."""

    def test_unknown_media_buy_raises_media_buy_not_found(self) -> None:
        """media_buy_id with no matching row in the caller's tenant raises
        AdCPMediaBuyNotFoundError, which projects to wire code MEDIA_BUY_NOT_FOUND."""
        repo = MagicMock()
        repo.get_by_id.return_value = None

        with pytest.raises(AdCPMediaBuyNotFoundError) as exc_info:
            _verify_principal("mb_does_not_exist", make_identity(), repo)

        assert exc_info.value.error_code == "MEDIA_BUY_NOT_FOUND"
        assert "mb_does_not_exist" in str(exc_info.value)

    def test_cross_tenant_lookup_surfaces_as_media_buy_not_found(self) -> None:
        """Tenant isolation invariant: when a buyer probes a media_buy_id that
        belongs to a different tenant, the tenant-scoped repo returns ``None``
        and we surface MEDIA_BUY_NOT_FOUND -- never a permissions error.
        Returning AUTHORIZATION_ERROR would leak cross-tenant existence.
        """
        # Repo is tenant-scoped; cross-tenant rows come back as None.
        repo = MagicMock()
        repo.get_by_id.return_value = None

        with pytest.raises(AdCPMediaBuyNotFoundError) as exc_info:
            _verify_principal("mb_exists_on_other_tenant", make_identity(tenant_id="tenant_a"), repo)

        # Critically NOT AdCPAuthorizationError / AUTHORIZATION_ERROR.
        assert exc_info.value.error_code == "MEDIA_BUY_NOT_FOUND"

    def test_unknown_package_in_targeting_overlay_raises_package_not_found(self) -> None:
        """Updating targeting on a package_id that doesn't exist on this media buy
        raises AdCPPackageNotFoundError (wire code PACKAGE_NOT_FOUND)."""
        with UpdateMediaBuyImplFixture() as uow:
            # Media buy exists, package does not.
            uow.media_buys.get_package.return_value = None

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(),
                media_buy_id="mb_exists",
                packages=[
                    {
                        "package_id": "pkg_does_not_exist",
                        "targeting_overlay": {"include_segment": [{"segment_id": "s1"}]},
                    }
                ],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=make_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)

    def test_unknown_package_with_paused_raises_package_not_found(self) -> None:
        """Pausing a package_id that doesn't exist on this media buy raises
        AdCPPackageNotFoundError (wire code PACKAGE_NOT_FOUND).

        This is the live-storyboard scenario: ``packages=[{"package_id":
        "does-not-exist", "paused": true}]`` against a real media buy.
        Without impl-level validation, the adapter returns a non-AdCP code
        like ``package_not_found`` (lowercase) which surfaces as opaque
        INTERNAL_ERROR after delegate translation.
        """
        with UpdateMediaBuyImplFixture() as uow:
            uow.media_buys.get_package.return_value = None

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(),
                media_buy_id="mb_exists",
                packages=[{"package_id": "pkg_does_not_exist", "paused": True}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=make_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)

    def test_unknown_package_with_budget_raises_package_not_found(self) -> None:
        """Budget update on a package_id that doesn't exist on this media buy
        raises AdCPPackageNotFoundError."""
        existing_mb = MagicMock()
        existing_mb.currency = "USD"

        with UpdateMediaBuyImplFixture(existing_media_buy=existing_mb) as uow:
            uow.media_buys.get_package.return_value = None

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(),
                media_buy_id="mb_exists",
                packages=[{"package_id": "pkg_does_not_exist", "budget": 1000.0}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=make_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)

    def test_unknown_package_under_manual_approval_raises_package_not_found(self) -> None:
        """When the publisher requires manual approval for update_media_buy,
        a bogus package_id must still surface PACKAGE_NOT_FOUND -- not a
        spurious "pending approval" success envelope with empty
        affected_packages.

        Regression for issue #251: live deployment was returning
        ``{"affected_packages": [], "errors": []}`` because the manual-
        approval branch short-circuited before the per-package gate from
        PR #215 ran. The fix hoists the package-existence check above the
        manual-approval gate so a buyer probing with a fake package_id
        always gets PACKAGE_NOT_FOUND.
        """
        with UpdateMediaBuyImplFixture(manual_approval=True) as uow:
            uow.media_buys.get_package.return_value = None

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(),
                media_buy_id="mb_exists",
                packages=[{"package_id": "pkg_does_not_exist", "paused": True}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=make_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)

    def test_unknown_package_with_only_package_id_raises_package_not_found(self) -> None:
        """A bare-reference package update (only ``package_id`` set, no
        fields to mutate) referencing a non-existent package must surface
        PACKAGE_NOT_FOUND. Previously fell through the per-package loop
        silently and returned a 200 success with empty affected_packages
        (issue #251)."""
        with UpdateMediaBuyImplFixture() as uow:
            uow.media_buys.get_package.return_value = None

            req = UpdateMediaBuyRequest(
                **required_request_kwargs(),
                media_buy_id="mb_exists",
                packages=[{"package_id": "pkg_does_not_exist"}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=make_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Layer 2: delegate translates AdCPError -> framework AdcpError on the wire
# ---------------------------------------------------------------------------


class TestDelegateProjectsTypedErrorsToWireEnvelope:
    """``_delegate_update_media_buy`` translates AdCPError subclasses into
    the framework's decisioning ``AdcpError`` so the dispatcher emits a
    spec-compliant ``adcp_error.code`` envelope."""

    def test_media_buy_not_found_projects_to_wire_code(self) -> None:
        from adcp.decisioning.types import AdcpError

        from core.platforms._delegate import _delegate_update_media_buy

        ctx = make_delegate_ctx()

        with patch(
            "core.platforms._delegate._update_media_buy_impl",
            side_effect=AdCPMediaBuyNotFoundError("Media buy 'mb_x' not found."),
        ):
            with pytest.raises(AdcpError) as exc_info:
                run_delegate_coro(_delegate_update_media_buy("mb_x", required_request_kwargs(), ctx))

        assert exc_info.value.code == "MEDIA_BUY_NOT_FOUND"
        assert exc_info.value.recovery == "correctable"
        assert "mb_x" in (exc_info.value.args[0] if exc_info.value.args else "")

    def test_package_not_found_projects_to_wire_code(self) -> None:
        from adcp.decisioning.types import AdcpError

        from core.platforms._delegate import _delegate_update_media_buy

        ctx = make_delegate_ctx()

        with patch(
            "core.platforms._delegate._update_media_buy_impl",
            side_effect=AdCPPackageNotFoundError("Package 'pkg_z' not found"),
        ):
            with pytest.raises(AdcpError) as exc_info:
                run_delegate_coro(
                    _delegate_update_media_buy(
                        "mb_x",
                        {
                            **required_request_kwargs(),
                            "packages": [{"package_id": "pkg_z", "paused": True}],
                        },
                        ctx,
                    )
                )

        assert exc_info.value.code == "PACKAGE_NOT_FOUND"
        assert exc_info.value.recovery == "correctable"
