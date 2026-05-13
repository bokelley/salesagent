"""Derive ``PackageRequest[]`` from a reserved proposal's allocations.

Closes the seller-side half of bokelley/salesagent#387. PR #390 wired
the persistence layer (``SalesAgentProposalStore`` +
``LazyPlatformRouter.proposal_store_factory``); this module supplies
the missing derivation step the framework explicitly delegates to the
seller.

## Why this lives in salesagent

The framework's ``adcp.decisioning.proposal_dispatch.py`` already
reserves the proposal via ``try_reserve_consumption`` (state
``COMMITTED → CONSUMING``) and hydrates ``ctx.recipes`` before
dispatching ``platform.create_media_buy``. Per the framework's own
comment (``proposal_dispatch.py:590-593``):

    > "The buyer's packages may be empty when proposal_id is provided
    > (the spec allows the seller to derive packages from the proposal's
    > allocations); skip the gate in that case since there's nothing
    > to validate against."

So the seller is contractually responsible for the derivation.
Filed upstream at adcontextprotocol/adcp-client-python#727 as a
framework-level auto-injection candidate — this module collapses to
~5 LOC if that ships.

## What gets derived

The buyer sends a request like:

```json
{
    "proposal_id": "prop_abc123",
    "total_budget": { "amount": 50000, "currency": "USD" },
    "start_time": "...",
    "end_time": "...",
    "idempotency_key": "..."
}
```

with no ``packages``. The reserved proposal's payload carries the
allocations the seller minted on the prior ``get_products`` /
``refine_products``:

```json
{
    "proposal_id": "prop_abc123",
    "allocations": [
        {"product_id": "prod_video", "allocation_percentage": 60.0,
         "pricing_option_id": "cpm_usd_fixed"},
        {"product_id": "prod_display", "allocation_percentage": 40.0,
         "pricing_option_id": "cpm_usd_fixed"}
    ]
}
```

We distribute the ``total_budget.amount`` across allocations by
``allocation_percentage``, pinning the final entry to absorb any
rounding drift so the sum lands exactly on the buyer's total.

Round to 2 decimal places per package because ``Package.budget`` is
a ``float`` and currency math conventions stop at cents — preserving
fractional cents on every package would shift the rounding error to
the platform adapter's invoice math.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adcp.decisioning import AdcpError
from adcp.types.generated_poc.media_buy.create_media_buy_request import TotalBudget

from src.core.schemas import PackageRequest


def derive_packages_from_proposal(
    *,
    allocations: list[Mapping[str, Any]],
    total_budget: TotalBudget,
) -> list[PackageRequest]:
    """Split ``total_budget.amount`` across ``allocations[]`` per
    ``allocation_percentage`` and synthesize a ``PackageRequest`` for each.

    :param allocations: List of allocation dicts from
        ``ProposalRecord.proposal_payload['allocations']``. Each entry
        must carry ``product_id``, ``allocation_percentage``, and
        ``pricing_option_id`` (the three ``PackageRequest`` required
        fields).
    :param total_budget: Buyer-supplied total; the ``amount`` is split,
        the ``currency`` is informational at the package level (the
        framework's currency check happens elsewhere).
    :returns: One ``PackageRequest`` per allocation, with ``budget``
        rounded to 2 decimal places and the last entry pinned to make
        the sum exactly equal ``total_budget.amount``.
    :raises AdcpError: ``INVALID_REQUEST`` when an allocation is
        malformed (missing required field), when allocations is empty,
        or when ``total_budget`` is missing / zero. These map to the
        ``proposal_finalize/create_media_buy`` storyboard's allowed
        codes and let buyers self-correct.
    """
    if not allocations:
        raise AdcpError(
            "INVALID_REQUEST",
            message=(
                "Proposal has no allocations; cannot derive packages. "
                "Re-request via get_products to mint a proposal with "
                "products."
            ),
            recovery="correctable",
            field="proposal_id",
        )
    if total_budget is None or total_budget.amount <= 0:
        raise AdcpError(
            "INVALID_REQUEST",
            message=(
                "total_budget.amount must be a positive number when "
                "create_media_buy is called with a proposal_id; the "
                "amount is the basis for the per-package split derived "
                "from the proposal's allocation_percentages."
            ),
            recovery="correctable",
            field="total_budget.amount",
        )

    total_amount = float(total_budget.amount)
    packages: list[PackageRequest] = []
    running_total = 0.0
    last_idx = len(allocations) - 1

    for idx, alloc in enumerate(allocations):
        product_id = alloc.get("product_id")
        percentage = alloc.get("allocation_percentage")
        pricing_option_id = alloc.get("pricing_option_id")
        if not product_id or percentage is None or not pricing_option_id:
            raise AdcpError(
                "INVALID_REQUEST",
                message=(
                    f"Allocation at index {idx} is malformed: requires "
                    "product_id, allocation_percentage, pricing_option_id. "
                    "This is a server-side persistence bug — proposals "
                    "minted by SalesAgentProposalManager always populate "
                    "all three. File a regression."
                ),
                recovery="terminal",
                field=f"proposal_payload.allocations[{idx}]",
            )

        if idx == last_idx:
            # Pin the final allocation to absorb rounding drift so the
            # sum lands EXACTLY on the buyer's total. Otherwise three
            # 33.33% allocations on a $100 budget yield $99.99 — the
            # adapter's per-package invoice math would surface the
            # missing cent as a phantom discount.
            pkg_budget = round(max(0.0, total_amount - running_total), 2)
        else:
            pkg_budget = round(total_amount * (float(percentage) / 100.0), 2)
            running_total += pkg_budget

        packages.append(
            PackageRequest(
                product_id=str(product_id),
                budget=pkg_budget,
                pricing_option_id=str(pricing_option_id),
            )
        )

    return packages
