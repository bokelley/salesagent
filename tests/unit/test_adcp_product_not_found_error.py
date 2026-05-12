"""Pin the wire vocabulary on :class:`AdCPProductNotFoundError` (#351).

The class is consumed by the boundary translator
(:func:`core.platforms._delegate._translate_adcp_error`), which reads
``error_code`` and projects it onto the framework's wire ``code`` field.
Drift between the class name and the spec enum surfaces as
``"unknown code"`` handling on buyer agents walking
``STANDARD_ERROR_CODES``.
"""

from __future__ import annotations

from src.core.exceptions import AdCPNotFoundError, AdCPProductNotFoundError


class TestAdCPProductNotFoundError:
    def test_class_carries_spec_canonical_code(self) -> None:
        """``PRODUCT_NOT_FOUND`` is the AdCP 3.0 error-code enum member
        for unknown ``product_id`` references; the class must emit
        exactly that string.
        """
        exc = AdCPProductNotFoundError("Product(s) not found: nonexistent-xyz")
        assert exc.error_code == "PRODUCT_NOT_FOUND"

    def test_inherits_from_not_found(self) -> None:
        """Tenant-isolation invariant: any not-found error should
        normalize to the not-found hierarchy so cross-tenant probing
        can't distinguish 'exists elsewhere' from 'does not exist'.
        """
        assert issubclass(AdCPProductNotFoundError, AdCPNotFoundError)

    def test_recovery_is_correctable(self) -> None:
        """Spec: ``PRODUCT_NOT_FOUND`` is correctable — the buyer can
        re-discover via ``get_products`` and retry with valid IDs.
        Locking this so a future refactor doesn't accidentally flip it
        to ``terminal`` and break self-correcting buyer agents.
        """
        exc = AdCPProductNotFoundError("foo")
        assert exc.recovery == "correctable"

    def test_status_code_404(self) -> None:
        """REST/FastAPI HTTP status maps to 404 — consistent with the
        rest of the not-found hierarchy.
        """
        exc = AdCPProductNotFoundError("foo")
        assert exc.status_code == 404

    def test_details_round_trip(self) -> None:
        """``details`` is preserved on the exception so the boundary
        translator can forward it to the wire envelope. The translator
        hoists a ``field`` key from details onto the top-level wire
        ``field`` attribute — locking the convention here.
        """
        exc = AdCPProductNotFoundError(
            "Product(s) not found: nonexistent-xyz",
            details={"missing_product_ids": ["nonexistent-xyz"], "field": "packages[].product_id"},
        )
        assert exc.details is not None
        assert exc.details["missing_product_ids"] == ["nonexistent-xyz"]
        assert exc.details["field"] == "packages[].product_id"
