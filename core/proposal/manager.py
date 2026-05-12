"""SalesAgentProposalManager — wraps salesagent's get_products with the
v1.5 ``ProposalManager`` primitive so a single code path serves both
the stateless ``buying_mode='brief'`` flow AND the stateful
``buying_mode='refine'`` proposal-iteration flow.

Architectural shape (per @bokelley): every seller's get_products is
*conceptually* a proposal generation step — the response is a proposed
configuration of products, pricing, formats, inventory hints. v1.5
ProposalManager + ProposalStore lifts that fully into a managed
primitive: ``get_products`` produces a draft, ``refine_products``
iterates, ``ProposalStore.commit`` persists, and downstream
``create_media_buy(proposal_id=X)`` consumes via ``try_reserve_consumption``
+ ``finalize_consumption``.

When the framework's :class:`LazyPlatformRouter` has a wired
:class:`ProposalManager` for a tenant, it routes ``get_products`` to
``manager.get_products`` instead of ``platform.get_products`` —
subsuming the call entirely. ``platform.get_products`` becomes the
fallback for tenants without a manager wired (none of ours, but the
framework keeps it for migration).

v1 scope: stateless ``get_products`` that delegates to
``_get_products_impl`` (same brain as core/platforms/_delegate.py).
DRAFT persistence + refinement land in v2 once the storyboard's
sales-proposal-mode bundle drives them. Keep the surface narrow until
real flows demand more — premature lifecycle hooks would lock us into
shapes that don't match the actual proposal flow we want.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from adcp.decisioning import AdcpError, RequestContext
from adcp.decisioning.proposal_manager import ProposalCapabilities
from adcp.types import GetProductsRequest, GetProductsResponse
from adcp.types.generated_poc.core.product_allocation import ProductAllocation
from adcp.types.generated_poc.core.proposal import Proposal

from core.platforms._delegate import _build_identity, _coerce_to_request_model, translate_adcp_errors
from src.core.tools.products import _get_products_impl


class SalesAgentProposalManager:
    """Single-tenant proposal manager that subsumes ``get_products``
    via the existing ``_get_products_impl`` business logic.

    Implements the :class:`adcp.decisioning.proposal_manager.ProposalManager`
    Protocol structurally — the SDK's reference ``MockProposalManager``
    follows the same no-inheritance pattern.

    The manager declares ``ProposalCapabilities(refine=False)`` for v1
    — the framework router falls through to :meth:`get_products` even
    when a buyer sends ``buying_mode='refine'``. v2 flips refine on,
    persists DRAFTs via ``ProposalStore.put_draft``, and loads prior
    drafts in :meth:`refine_products` to support iterative shortlisting.
    """

    # Match the platform's specialism declaration; v1 ships only the
    # non-guaranteed sales path (CPM auctions, no fixed-quantity holds).
    capabilities: ClassVar[ProposalCapabilities] = ProposalCapabilities(
        sales_specialism="sales-non-guaranteed",
        refine=False,
    )

    @translate_adcp_errors
    async def get_products(
        self,
        req: GetProductsRequest,
        ctx: RequestContext[Any],
    ) -> GetProductsResponse:
        """Delegate to ``_get_products_impl`` — same path the platform
        method took before the manager subsumed it. Returns the
        ``GetProductsResponse`` Pydantic model rather than a wire dict
        because the framework's ProposalManager protocol declares the
        typed return; the inner adcp serializer handles model_dump.

        ``@translate_adcp_errors`` is mandatory here even though every
        delegate it wraps lives in ``_delegate.py``: when a tenant has a
        proposal manager wired (and every active tenant does per
        :func:`core.main._build_proposal_managers`), the framework router
        routes ``get_products`` to this method instead of
        :func:`core.platforms._delegate._delegate_get_products`. Without
        the decorator, salesagent ``AdCPError`` raises and pydantic
        ``ValidationError`` raises surface as opaque ``INTERNAL_ERROR``
        on the wire. The decorator also performs the
        ``adcp_major_version`` negotiation check before the impl runs.
        """
        identity = _build_identity(ctx)
        req_model = _coerce_to_request_model(req, GetProductsRequest)
        response = await _get_products_impl(req_model, identity)

        # Decorate brief-mode responses with a v1 proposal (#352).
        # ``buying_mode='wholesale'`` and ``buying_mode='refine'`` opt out
        # of curated proposals per the AdCP spec; brief is the only mode
        # where the seller is expected to offer strategic bundling. The
        # ``proposals[]`` array is what the
        # ``media_buy_seller/proposal_finalize/get_products_brief``
        # storyboard asserts on, and the ``proposal_id`` is what buyers
        # echo into ``create_media_buy(proposal_id=...)`` to execute the
        # bundle.
        buying_mode = getattr(req_model, "buying_mode", None) or getattr(req, "buying_mode", None)
        buying_mode_str = getattr(buying_mode, "value", buying_mode)
        if buying_mode_str == "brief":
            proposal = _build_v1_brief_proposal(response)
            if proposal is not None:
                response.proposals = [proposal]
        return response

    async def refine_products(
        self,
        req: GetProductsRequest,
        ctx: RequestContext[Any],
    ) -> GetProductsResponse:
        """Refine-mode stub.

        Required by the :class:`ProposalManager` Protocol surface so
        adopters declaring ``capabilities.refine=True`` have a typed
        signature to override. v1 sets ``capabilities.refine=False`` —
        the framework routes refine requests to :meth:`get_products`
        instead and this method is never invoked. Raises
        ``UNSUPPORTED_FEATURE`` defensively if reached.
        """
        del req, ctx
        raise AdcpError(
            "UNSUPPORTED_FEATURE",
            message=(
                "refine_products called on SalesAgentProposalManager; v1 "
                "declares capabilities.refine=False. Buyers should rely on "
                "get_products for product discovery."
            ),
        )


def _build_v1_brief_proposal(response: GetProductsResponse) -> Proposal | None:
    """Build a v1 ``Proposal`` from a ``get_products`` response (#352).

    Splits budget evenly across every product the publisher returned —
    minimal but spec-compliant: every product allocation references a
    ``product_id`` and ``pricing_option_id`` from the response, the
    percentages sum to 100, and the response carries a
    ``proposal_id`` buyers can echo into ``create_media_buy``.

    Returns ``None`` when the response has no products (the spec
    requires ``min_length=1`` on ``allocations`` — an empty proposal
    would fail Pydantic construction). The caller falls back to
    products-only output, which is also spec-legal because
    ``proposals`` is optional.

    Future revisions (refine flow, weighted allocations, persisted
    drafts) ride the same hook — they only need to swap the allocation
    strategy.
    """
    products = list(getattr(response, "products", []) or [])
    if not products:
        return None
    share = round(100.0 / len(products), 2)
    # Pin the final allocation to whatever remains so the sum lands on
    # exactly 100 — Pydantic's ge=0 / le=100 bounds allow this, and the
    # spec only requires the sum, not equal weighting.
    allocations: list[ProductAllocation] = []
    running_total = 0.0
    for i, product in enumerate(products):
        if i == len(products) - 1:
            percentage = round(max(0.0, 100.0 - running_total), 2)
        else:
            percentage = share
            running_total += percentage
        pricing_option_id = _first_pricing_option_id(product)
        allocations.append(
            ProductAllocation(
                product_id=product.product_id,
                allocation_percentage=percentage,
                pricing_option_id=pricing_option_id,
                rationale=None,
            )
        )
    return Proposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:12]}",
        name="Recommended bundle",
        description=(
            "Even-budget split across every matched product. v1 strategy — "
            "refine the allocations via a subsequent get_products call "
            '(buying_mode="refine") once refine support is wired.'
        ),
        allocations=allocations,
    )


def _first_pricing_option_id(product: Any) -> str | None:
    """Pull the first ``pricing_option_id`` off a product, tolerating
    library RootModel wrappers and absent pricing options.
    """
    options = getattr(product, "pricing_options", None) or []
    if not options:
        return None
    first = options[0]
    # adcp 2.14.0+ wraps pricing options in a RootModel; unwrap.
    first = getattr(first, "root", first)
    return getattr(first, "pricing_option_id", None)
