"""Pin the v1 brief-mode proposal builder (#352).

When ``get_products`` is called with ``buying_mode='brief'``, the
``SalesAgentProposalManager`` must decorate the response with a
``proposals[]`` array carrying at least one ``Proposal``. The
``proposal_id`` is what buyers echo into ``create_media_buy`` to
execute the bundle; ``allocations`` must reference real ``product_id``
values from the response and sum to 100%.

This test exercises the pure builder helper rather than the full
manager pipeline — no DB, no factories required. The helper is the
seam every future allocation strategy will swap.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.proposal.manager import _build_v1_brief_proposal, _first_pricing_option_id


def _mock_product(product_id: str, pricing_option_id: str | None = "cpm_usd_fixed") -> Any:
    """Build a duck-typed product. Using ``SimpleNamespace`` rather than
    ``MagicMock`` because the production code path probes ``.root`` for
    RootModel unwrapping — a MagicMock would synthesize that attribute
    and leak a ``Mock`` into ``pricing_option_id``.
    """
    if pricing_option_id is None:
        pricing_options = []
    else:
        pricing_options = [SimpleNamespace(pricing_option_id=pricing_option_id)]
    return SimpleNamespace(product_id=product_id, pricing_options=pricing_options)


class TestBuildV1BriefProposal:
    def test_returns_none_when_no_products(self) -> None:
        """Empty product list → no proposal. ``allocations`` requires
        ``min_length=1`` on the spec model, so an empty proposal would
        fail Pydantic construction; the caller falls through to
        products-only output (also spec-legal).
        """
        response = SimpleNamespace(products=[])
        assert _build_v1_brief_proposal(response) is None

    def test_single_product_gets_100_percent(self) -> None:
        response = SimpleNamespace(products=[_mock_product("prod_a")])

        proposal = _build_v1_brief_proposal(response)

        assert proposal is not None
        assert proposal.proposal_id.startswith("prop_")
        assert len(proposal.allocations) == 1
        assert proposal.allocations[0].product_id == "prod_a"
        assert proposal.allocations[0].allocation_percentage == 100.0
        assert proposal.allocations[0].pricing_option_id == "cpm_usd_fixed"

    def test_two_products_split_evenly(self) -> None:
        response = SimpleNamespace(products=[_mock_product("prod_a"), _mock_product("prod_b")])

        proposal = _build_v1_brief_proposal(response)

        assert proposal is not None
        assert len(proposal.allocations) == 2
        # Even split, summing to 100.
        total = sum(a.allocation_percentage for a in proposal.allocations)
        assert total == 100.0

    def test_three_products_sum_is_exactly_100(self) -> None:
        """Three-way split where ``100 / 3`` is non-terminating. The
        builder must compensate so the sum still lands on 100 — the
        spec docs allocation percentages \"MUST sum to 100\" and a
        strict buyer validator will reject 99.99 or 100.01.
        """
        response = SimpleNamespace(products=[_mock_product(f"prod_{i}") for i in range(3)])

        proposal = _build_v1_brief_proposal(response)

        assert proposal is not None
        assert len(proposal.allocations) == 3
        total = sum(a.allocation_percentage for a in proposal.allocations)
        assert total == 100.0

    def test_unique_proposal_id_per_call(self) -> None:
        """Each call mints a fresh ``proposal_id`` — buyers use it to
        execute via ``create_media_buy(proposal_id=...)``, and an
        accidentally-shared id would let two unrelated buyers consume
        the same draft.
        """
        response = SimpleNamespace(products=[_mock_product("prod_a")])

        p1 = _build_v1_brief_proposal(response)
        p2 = _build_v1_brief_proposal(response)

        assert p1 is not None and p2 is not None
        assert p1.proposal_id != p2.proposal_id

    def test_pricing_option_id_optional(self) -> None:
        """A product with no pricing_options still gets an allocation;
        the optional ``pricing_option_id`` is just left absent.
        """
        response = SimpleNamespace(products=[_mock_product("prod_a", pricing_option_id=None)])

        proposal = _build_v1_brief_proposal(response)

        assert proposal is not None
        assert proposal.allocations[0].pricing_option_id is None


class TestFirstPricingOptionId:
    def test_unwraps_rootmodel_wrapper(self) -> None:
        """adcp 2.14.0+ wraps pricing options in a RootModel. The
        helper must unwrap so the legacy and modern shapes both work.
        """
        wrapper = SimpleNamespace(root=SimpleNamespace(pricing_option_id="cpm_usd_fixed"))
        product = SimpleNamespace(pricing_options=[wrapper])
        assert _first_pricing_option_id(product) == "cpm_usd_fixed"

    def test_handles_unwrapped_option(self) -> None:
        """Legacy shape: pricing option is a bare object with no
        ``root`` attribute. The helper reads ``pricing_option_id``
        off the object directly.
        """
        product = SimpleNamespace(pricing_options=[SimpleNamespace(pricing_option_id="cpm_usd_fixed")])
        assert _first_pricing_option_id(product) == "cpm_usd_fixed"

    def test_returns_none_when_empty(self) -> None:
        product = SimpleNamespace(pricing_options=[])
        assert _first_pricing_option_id(product) is None

    def test_returns_none_when_attribute_absent(self) -> None:
        """Defensive: products that legitimately don't have a
        ``pricing_options`` attribute fall through to ``None`` rather
        than ``AttributeError``.
        """

        class BareProduct:
            pass

        assert _first_pricing_option_id(BareProduct()) is None
