"""Pin the v1 proposal-allocation → PackageRequest derivation.

Closes the seller-side half of bokelley/salesagent#387. PR #390 wired
``SalesAgentProposalStore`` (persistence). This derivation produces
the ``packages[]`` ``_create_media_buy_impl`` needs when the buyer
references a ``proposal_id`` without inline packages.

The framework hands us the reserved proposal's allocations + the
buyer's ``total_budget``; we distribute the budget across allocations
by percentage, pin the last entry to absorb rounding drift, and emit
spec-required ``PackageRequest`` fields.

Tests exercise the pure helper — no DB, no harness needed. Same shape
as ``test_proposal_manager_brief.py``.
"""

from __future__ import annotations

import pytest
from adcp.decisioning import AdcpError
from adcp.types.generated_poc.media_buy.create_media_buy_request import TotalBudget

from core.proposal.derivation import derive_packages_from_proposal


def _budget(amount: float = 50000.0, currency: str = "USD") -> TotalBudget:
    return TotalBudget(amount=amount, currency=currency)


def _alloc(product_id: str, percentage: float, pricing_option_id: str = "cpm_usd_fixed") -> dict:
    return {
        "product_id": product_id,
        "allocation_percentage": percentage,
        "pricing_option_id": pricing_option_id,
    }


class TestSingleAllocation:
    """Single allocation gets 100% of the budget."""

    def test_single_allocation_takes_full_budget(self) -> None:
        packages = derive_packages_from_proposal(
            allocations=[_alloc("prod_a", 100.0)],
            total_budget=_budget(50000.0),
        )

        assert len(packages) == 1
        assert packages[0].product_id == "prod_a"
        assert packages[0].budget == 50000.0
        assert packages[0].pricing_option_id == "cpm_usd_fixed"


class TestTwoAllocations:
    """Two allocations split per percentage; last absorbs drift."""

    def test_60_40_split_on_50000(self) -> None:
        packages = derive_packages_from_proposal(
            allocations=[
                _alloc("prod_video", 60.0),
                _alloc("prod_display", 40.0),
            ],
            total_budget=_budget(50000.0),
        )

        assert len(packages) == 2
        assert packages[0].budget == 30000.0
        assert packages[1].budget == 20000.0
        # Sum lands exactly on total.
        assert packages[0].budget + packages[1].budget == 50000.0


class TestThreeAllocationsWithRoundingDrift:
    """Three equal splits on $100 produce $33.33 + $33.33 + $33.34
    (last entry pinned to absorb the cent the percentage math drops).
    The spec requires the sum to land exactly on total_budget — adapters
    would otherwise see a phantom $0.01 discount.
    """

    def test_equal_thirds_on_100_sums_to_exactly_100(self) -> None:
        packages = derive_packages_from_proposal(
            allocations=[
                _alloc("prod_a", 33.33),
                _alloc("prod_b", 33.33),
                _alloc("prod_c", 33.34),
            ],
            total_budget=_budget(100.0),
        )

        assert len(packages) == 3
        total = sum(p.budget for p in packages)
        assert total == 100.0, f"sum must equal total_budget exactly; got {total!r}"

    def test_irrational_percentages_still_sum_exactly(self) -> None:
        """Three 33.33% allocations on a real wire payload — last entry
        absorbs the fractional drift the float math drops."""
        packages = derive_packages_from_proposal(
            allocations=[_alloc(f"prod_{i}", 33.33) for i in range(3)],
            total_budget=_budget(50000.0),
        )

        total = sum(p.budget for p in packages)
        assert total == 50000.0


class TestFieldShape:
    """``PackageRequest`` required fields propagate from allocation entries."""

    def test_pricing_option_id_propagates(self) -> None:
        packages = derive_packages_from_proposal(
            allocations=[_alloc("prod_a", 100.0, pricing_option_id="cpm_eur_auction")],
            total_budget=_budget(amount=1000.0, currency="EUR"),
        )

        assert packages[0].pricing_option_id == "cpm_eur_auction"

    def test_product_id_propagates_unchanged(self) -> None:
        packages = derive_packages_from_proposal(
            allocations=[
                _alloc("prod_video_outdoor_ctv", 60.0),
                _alloc("prod_display_premium", 40.0),
            ],
            total_budget=_budget(),
        )

        assert [p.product_id for p in packages] == ["prod_video_outdoor_ctv", "prod_display_premium"]


class TestInvalidInputs:
    """Buyer-actionable rejections per ``proposal_finalize`` storyboard."""

    def test_empty_allocations_raises_invalid_request(self) -> None:
        """A proposal with no allocations can't be consumed."""
        with pytest.raises(AdcpError) as exc_info:
            derive_packages_from_proposal(allocations=[], total_budget=_budget())
        assert exc_info.value.code == "INVALID_REQUEST"

    def test_missing_total_budget_raises_invalid_request(self) -> None:
        """``create_media_buy(proposal_id=…)`` without total_budget can't
        be sized — the percentage math has no basis."""
        with pytest.raises(AdcpError) as exc_info:
            derive_packages_from_proposal(
                allocations=[_alloc("prod_a", 100.0)],
                total_budget=None,  # type: ignore[arg-type]
            )
        assert exc_info.value.code == "INVALID_REQUEST"

    def test_zero_total_budget_raises_invalid_request(self) -> None:
        """Zero budget makes every package zero — reject up front rather
        than letting ``_create_media_buy_impl``'s downstream validation
        fire with a generic ``Invalid budget`` message."""
        with pytest.raises(AdcpError) as exc_info:
            derive_packages_from_proposal(
                allocations=[_alloc("prod_a", 100.0)],
                total_budget=_budget(amount=0.0),
            )
        assert exc_info.value.code == "INVALID_REQUEST"

    def test_allocation_missing_product_id_raises(self) -> None:
        """Malformed allocation from the persistence layer — should never
        happen because ``SalesAgentProposalManager.get_products`` always
        populates the field, but defend in depth."""
        with pytest.raises(AdcpError) as exc_info:
            derive_packages_from_proposal(
                allocations=[{"allocation_percentage": 100.0, "pricing_option_id": "cpm_usd_fixed"}],
                total_budget=_budget(),
            )
        assert exc_info.value.code == "INVALID_REQUEST"

    def test_allocation_missing_pricing_option_raises(self) -> None:
        with pytest.raises(AdcpError) as exc_info:
            derive_packages_from_proposal(
                allocations=[{"product_id": "prod_a", "allocation_percentage": 100.0}],
                total_budget=_budget(),
            )
        assert exc_info.value.code == "INVALID_REQUEST"

    def test_allocation_missing_percentage_raises(self) -> None:
        with pytest.raises(AdcpError) as exc_info:
            derive_packages_from_proposal(
                allocations=[{"product_id": "prod_a", "pricing_option_id": "cpm_usd_fixed"}],
                total_budget=_budget(),
            )
        assert exc_info.value.code == "INVALID_REQUEST"


class TestRoundingPrecision:
    """Per-package ``budget`` is rounded to 2 decimal places (cents).
    Carrying fractional cents on every package would push the rounding
    error into the adapter's invoice math; absorbing it on the last
    entry keeps the math clean."""

    def test_two_decimal_precision_on_non_terminating_split(self) -> None:
        # 33.33% of $1000 = $333.30 → round to $333.30 (already 2dp)
        packages = derive_packages_from_proposal(
            allocations=[
                _alloc("prod_a", 33.33),
                _alloc("prod_b", 33.33),
                _alloc("prod_c", 33.34),
            ],
            total_budget=_budget(amount=1000.0),
        )
        for p in packages:
            cents = round(p.budget * 100)
            assert abs(p.budget * 100 - cents) < 1e-6, f"budget {p.budget!r} carries sub-cent precision — rounding bug"
