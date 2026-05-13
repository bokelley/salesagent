"""Pin the v1 refine-mode behaviour of :class:`SalesAgentProposalManager`.

Storyboard ``media_buy_seller/proposal_finalize/get_products_refine`` sends
``buying_mode='refine'`` with a ``refine[]`` array of scope-specific asks;
the spec response must carry ``proposals[]`` (fresh proposal) and
``refinement_applied[]`` (one entry per refine ask with status/notes).

These tests exercise the pure builder helper without the full manager
pipeline — same shape as ``test_proposal_manager_brief.py`` for the
brief-mode allocator. The manager's plumbing around the helper is
covered by the storyboard runner end-to-end.
"""

from __future__ import annotations

from types import SimpleNamespace

from adcp.decisioning.proposal_manager import ProposalCapabilities

from core.proposal.manager import (
    SalesAgentProposalManager,
    _build_v1_refinement_applied,
)


class TestSalesAgentProposalManagerCapabilities:
    """Capability advertisement controls framework routing.

    With ``refine=True``, the framework dispatches ``buying_mode='refine'``
    to :meth:`refine_products`. With ``refine=False`` it falls through to
    :meth:`get_products`. The storyboard fails closed in the second case
    because ``get_products`` doesn't populate ``refinement_applied``.
    """

    def test_refine_capability_is_advertised(self) -> None:
        caps: ProposalCapabilities = SalesAgentProposalManager.capabilities
        assert caps.refine is True, (
            "ProposalCapabilities.refine must be True so the framework routes "
            "buying_mode='refine' to refine_products; the storyboard "
            "media_buy_seller/proposal_finalize/get_products_refine relies on this."
        )

    def test_sales_specialism_is_non_guaranteed(self) -> None:
        """Sanity-pin the unchanged half of the capabilities declaration."""
        assert SalesAgentProposalManager.capabilities.sales_specialism == "sales-non-guaranteed"


class TestBuildV1RefinementApplied:
    """Pure-helper tests for the refinement-echo builder."""

    def test_returns_empty_list_for_empty_input(self) -> None:
        """No refine entries → empty list. The spec response field is
        optional; emitting an empty list keeps the type stable and lets
        buyers ``len()`` it without a None check."""
        assert _build_v1_refinement_applied([]) == []
        assert _build_v1_refinement_applied(None) == []

    def test_request_scope_passes_through(self) -> None:
        """Request-scope refines carry only ``scope`` + ``ask`` — no
        ID. The applied entry mirrors with status=applied."""
        entries = [SimpleNamespace(scope="request", ask="All products must support frequency capping at 3/day")]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        inner = applied[0].root
        assert inner.scope == "request"
        assert inner.status.value == "applied"
        assert inner.notes is not None
        assert "v1" in inner.notes.lower()

    def test_product_scope_preserves_product_id(self) -> None:
        """Product-scope refines reference a specific ``product_id`` —
        the applied entry MUST echo the same id so buyers can correlate
        their ask with the response."""
        entries = [SimpleNamespace(scope="product", product_id="prod_video_outdoor", action=None, ask="drop")]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        inner = applied[0].root
        assert inner.scope == "product"
        assert inner.product_id == "prod_video_outdoor"
        assert inner.status.value == "applied"

    def test_proposal_scope_preserves_proposal_id(self) -> None:
        """Proposal-scope refines reference a prior ``proposal_id`` —
        same echo invariant. v2 will use this id to load the prior
        draft via ProposalStore."""
        entries = [
            SimpleNamespace(scope="proposal", proposal_id="prop_abc123", action=None, ask="Shift 60% of budget to CTV.")
        ]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        inner = applied[0].root
        assert inner.scope == "proposal"
        assert inner.proposal_id == "prop_abc123"
        assert inner.status.value == "applied"

    def test_mixed_scopes_preserve_order(self) -> None:
        """Buyers send refines as an ordered array; the response must
        preserve the order so each ask correlates with its response by
        index. A future implementation that filters out unhandled
        scopes would break this; the test pins the contract."""
        entries = [
            SimpleNamespace(scope="proposal", proposal_id="prop_1", action=None, ask=None),
            SimpleNamespace(scope="product", product_id="prod_a", action=None, ask=None),
            SimpleNamespace(scope="request", ask="raise spend cap"),
        ]

        applied = _build_v1_refinement_applied(entries)

        scopes = [a.root.scope for a in applied]
        assert scopes == ["proposal", "product", "request"]

    def test_product_scope_missing_product_id_is_dropped(self) -> None:
        """Defensive: malformed product-scope entry without
        ``product_id`` is silently dropped rather than crashing the
        whole response. The spec requires ``product_id`` on
        ``RefinementApplied2``, so emitting one without it would
        violate Pydantic validation downstream."""
        entries = [
            SimpleNamespace(scope="product", product_id=None, action=None, ask="drop"),
            SimpleNamespace(scope="request", ask="raise spend cap"),
        ]

        applied = _build_v1_refinement_applied(entries)

        # Only the request entry survives.
        assert len(applied) == 1
        assert applied[0].root.scope == "request"

    def test_unknown_scope_dropped_silently(self) -> None:
        """Forward-compat: a future spec scope (e.g. ``catalog``) lands
        in the buyer SDK before our handler knows about it. Silently
        drop rather than crash so buyers can still call refine."""
        entries = [
            SimpleNamespace(scope="catalog", ask="unknown future scope"),
            SimpleNamespace(scope="request", ask="known scope"),
        ]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        assert applied[0].root.scope == "request"

    def test_root_model_wrapped_entry(self) -> None:
        """Refine RootModel-wrapped entries unwrap via ``.root`` —
        same convention as PricingOption / Refine in the AdCP types."""
        inner_entry = SimpleNamespace(scope="request", ask="some ask")
        wrapped = SimpleNamespace(root=inner_entry)

        applied = _build_v1_refinement_applied([wrapped])

        assert len(applied) == 1
        assert applied[0].root.scope == "request"


class TestRefineEchoLengthCaps:
    """Defensive caps on buyer-supplied refine echo.

    The adcp library doesn't constrain ``RefinementApplied{2,3}`` id
    string lengths, so without these caps a malicious buyer could send
    a 10MB ``product_id`` and force the server to hold it through
    Pydantic validation and echo it back. Verify oversize ids and
    oversize arrays are silently dropped rather than echoed.
    """

    def test_oversized_product_id_dropped(self) -> None:
        """A ``product_id`` longer than the cap is dropped from the
        echo, not truncated. Truncation would corrupt the id for
        downstream correlation; drop is safer."""
        oversize = "x" * 257  # _MAX_REFINE_ID_LEN + 1
        entries = [
            SimpleNamespace(scope="product", product_id=oversize, action=None, ask="drop"),
            SimpleNamespace(scope="request", ask="legit ask"),
        ]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        assert applied[0].root.scope == "request"

    def test_oversized_proposal_id_dropped(self) -> None:
        """Same cap applies to ``proposal_id``."""
        oversize = "y" * 257
        entries = [
            SimpleNamespace(scope="proposal", proposal_id=oversize, action=None, ask="x"),
            SimpleNamespace(scope="request", ask="legit"),
        ]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        assert applied[0].root.scope == "request"

    def test_empty_product_id_dropped(self) -> None:
        """Zero-length id is not a meaningful echo — drop alongside
        the oversize case so the cap is symmetric."""
        entries = [SimpleNamespace(scope="product", product_id="", action=None, ask="drop")]

        applied = _build_v1_refinement_applied(entries)

        assert applied == []

    def test_max_length_id_accepted(self) -> None:
        """Boundary: exactly at the cap is accepted. Off-by-one guard."""
        at_cap = "z" * 256  # _MAX_REFINE_ID_LEN
        entries = [SimpleNamespace(scope="product", product_id=at_cap, action=None, ask="x")]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        assert applied[0].root.product_id == at_cap

    def test_excess_array_length_truncated(self) -> None:
        """Refine arrays longer than ``_MAX_REFINE_ENTRIES`` are
        truncated to the cap; the prefix is echoed, the tail is
        dropped. Caps the request-driven memory before the per-entry
        loop runs."""
        # 100 entries — twice the cap.
        entries = [SimpleNamespace(scope="request", ask=f"ask {i}") for i in range(100)]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 50  # _MAX_REFINE_ENTRIES

    def test_non_string_product_id_dropped(self) -> None:
        """Pydantic should already reject these upstream, but the
        defensive layer holds for callers bypassing model validation
        (e.g. in-process tests, future RootModel shape drift)."""
        entries = [
            SimpleNamespace(scope="product", product_id=12345, action=None, ask="x"),  # int, not str
            SimpleNamespace(scope="request", ask="legit"),
        ]

        applied = _build_v1_refinement_applied(entries)

        assert len(applied) == 1
        assert applied[0].root.scope == "request"


class TestRefinementAppliedNote:
    """The acknowledgement note is the buyer-facing breadcrumb that
    distinguishes v1 echo-only behaviour from v2 semantic refinement."""

    def test_note_explains_v1_acknowledgement_shape(self) -> None:
        """The note must signal that the ask was acknowledged but not
        semantically applied. Buyers comparing the proposal before and
        after a refine should see the note as the reason allocations
        didn't shift in the way they asked. v2 will swap the note's
        content (or drop it) when the allocation actually reflects the
        ask."""
        applied = _build_v1_refinement_applied([SimpleNamespace(scope="request", ask="x")])
        assert applied[0].root.notes is not None
        # Two invariants: (1) mentions v1 so future readers can grep,
        # (2) admits the response carries a fresh-but-unchanged-strategy proposal.
        assert "v1" in applied[0].root.notes.lower()
        assert "fresh" in applied[0].root.notes.lower() or "even-split" in applied[0].root.notes.lower()
