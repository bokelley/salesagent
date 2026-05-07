"""Tests for ``ResolvedProduct`` — the sidecar wrapper that pairs a
wire-shape ``LibraryProduct`` with server-side internal fields.

Phase 2 of the #71 cleanup migrates the filter pipeline from the
``Product(LibraryProduct)`` extension (which carried internal fields
with ``exclude=True``) to ``ResolvedProduct`` (explicit composition).
These tests pin the dataclass contract.
"""

from __future__ import annotations

import pytest

from src.core.resolved_product import ResolvedProduct


@pytest.fixture
def wire_product():
    """A minimal valid library Product."""
    from tests.helpers.adcp_factories import create_test_product

    return create_test_product(product_id="rp_t1", name="Resolved Test")


class TestWireDelegation:
    def test_reads_wire_field_via_dotted_access(self, wire_product):
        r = ResolvedProduct(wire=wire_product)
        assert r.product_id == "rp_t1"
        assert r.name == "Resolved Test"

    def test_internal_field_direct_access(self, wire_product):
        r = ResolvedProduct(
            wire=wire_product,
            implementation_config={"placement_id": "12345"},
            countries=["US", "CA"],
        )
        assert r.implementation_config == {"placement_id": "12345"}
        assert r.countries == ["US", "CA"]

    def test_internal_fields_default_to_none(self, wire_product):
        r = ResolvedProduct(wire=wire_product)
        assert r.implementation_config is None
        assert r.countries is None
        assert r.device_types is None
        assert r.allowed_principal_ids is None


class TestConversionFromOrm:
    def test_convert_populates_internal_fields(self):
        """convert_product_model_to_resolved pulls internal fields from ORM.

        Reuses the shared mock-builder from test_adcp_wire_shape so we don't
        duplicate the 25-line ORM-stub setup. Only the internal fields that
        ResolvedProduct cares about need to be overridden.
        """
        from src.core.product_conversion import convert_product_model_to_resolved
        from tests.unit.test_adcp_wire_shape import _make_product_model_mock

        m = _make_product_model_mock(
            product_id="conv_t1",
            countries=["US", "CA"],
            allowed_principal_ids=["principal_a", "principal_b"],
            effective_implementation_config={"placement_id": "67890"},
            targeting_template={"device_targets": ["mobile", "desktop"]},
        )

        resolved = convert_product_model_to_resolved(m, adapter_type="mock")

        # Wire shape projects from library Product
        assert resolved.product_id == "conv_t1"
        assert resolved.wire.product_id == "conv_t1"
        # Internal fields land directly on the dataclass
        assert resolved.implementation_config == {"placement_id": "67890"}
        assert resolved.countries == ["US", "CA"]
        assert resolved.device_types == ["mobile", "desktop"]
        assert resolved.allowed_principal_ids == ["principal_a", "principal_b"]
