"""``update_media_buy`` rejects pause/resume on a terminal-state buy with
``AdCPInvalidStateError`` (wire code ``INVALID_STATE``).

Storyboard ``media_buy_state_machine/pause_canceled_buy`` sets up a media
buy in ``canceled`` state, then sends ``update_media_buy`` with ``paused=true``.
The spec requires rejection with ``/adcp_error/code == "INVALID_STATE"``. The
cancel branch already guards re-cancel via ``AdCPNotCancellableError``; the
pause/resume branch needed the symmetric guard.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AdCPError, AdCPInvalidStateError


class TestAdCPInvalidStateError:
    """Pin the wire vocabulary on the new exception class."""

    def test_error_code_is_canonical(self) -> None:
        """``INVALID_STATE`` is the AdCP 3.0 error-code enum member for
        illegal state transitions. The boundary translator reads
        ``error_code`` and projects it onto the wire ``adcp_error.code``."""
        exc = AdCPInvalidStateError("media_buy_id='mb_1' is in terminal state 'canceled'")
        assert exc.error_code == "INVALID_STATE"

    def test_recovery_is_correctable(self) -> None:
        """Buyer can pick a different action (e.g. inspect via ``get_media_buys``
        and stop attempting writes) without changing payload shape."""
        assert AdCPInvalidStateError("foo").recovery == "correctable"

    def test_status_code_422(self) -> None:
        """422 Unprocessable Entity — payload is syntactically valid but
        semantically rejected by the state machine."""
        assert AdCPInvalidStateError("foo").status_code == 422

    def test_inherits_adcp_error(self) -> None:
        """``translate_adcp_errors`` catches ``AdCPError`` to project
        typed codes onto the wire — a non-``AdCPError`` subclass would
        fall through to opaque ``INTERNAL_ERROR``."""
        assert issubclass(AdCPInvalidStateError, AdCPError)


class TestPauseCanceledBuyGuard:
    """Behavioral coverage: ``_update_media_buy_impl`` raises
    ``AdCPInvalidStateError`` when called with ``paused=True/False`` against
    a media buy whose status is terminal.

    The "is this state terminal?" decision delegates to the upstream AdCP
    graph (:data:`adcp.decisioning.state_machines.MEDIA_BUY_TRANSITIONS`):
    any state with an empty legal-next set is rejected. The earlier
    hand-rolled tuple covered only ``("canceled", "completed")`` and
    missed ``rejected`` (the third terminal in the spec graph). The
    parametrize matrix below pins every terminal in the graph so a
    future spec addition forces the test author to acknowledge the new
    state explicitly.
    """

    @pytest.mark.parametrize("terminal_status", ["canceled", "completed", "rejected"])
    @pytest.mark.parametrize("paused_value", [True, False])
    def test_pause_or_resume_on_terminal_buy_raises_invalid_state(
        self, terminal_status: str, paused_value: bool
    ) -> None:
        """Both pause (``paused=True``) and resume (``paused=False``) on
        any terminal state per :data:`MEDIA_BUY_TRANSITIONS` must raise
        ``AdCPInvalidStateError`` before any adapter dispatch.

        Parametrized over every (state, action) combination so a future
        change that handles only some correctly is caught by the others."""
        from tests.harness.media_buy_update import MediaBuyUpdateEnv

        with MediaBuyUpdateEnv() as env:
            env.set_media_buy(media_buy_id="mb-terminal", status=terminal_status)
            with pytest.raises(AdCPInvalidStateError) as excinfo:
                env.call_impl(media_buy_id="mb-terminal", paused=paused_value)

            assert excinfo.value.error_code == "INVALID_STATE"
            assert terminal_status in str(excinfo.value), (
                f"Error message must name the offending terminal state; got {excinfo.value!s}"
            )

    def test_terminal_states_match_upstream_graph(self) -> None:
        """The set of states the guard rejects must equal the set of
        terminal states in the upstream AdCP graph.

        Lock-in regression: if upstream adds (or removes) a terminal
        state, this test fails and forces the parametrize list above to
        be updated. The hand-rolled tuple this refactor replaced had
        drifted silently — this test prevents the same drift from
        happening to the parametrize."""
        from adcp.decisioning.state_machines import MEDIA_BUY_TRANSITIONS

        upstream_terminals = {state for state, legal_next in MEDIA_BUY_TRANSITIONS.items() if not legal_next}
        # Read the parametrize values straight off the method so the
        # source of truth is the test's own decoration — adding a state
        # there without adding it here (or vice-versa) is the failure
        # mode this test catches.
        marker = next(
            m
            for m in self.test_pause_or_resume_on_terminal_buy_raises_invalid_state.pytestmark  # type: ignore[attr-defined]
            if m.name == "parametrize" and m.args[0] == "terminal_status"
        )
        tested_terminals = set(marker.args[1])
        assert tested_terminals == upstream_terminals, (
            f"Test parametrize {tested_terminals!r} drifted from upstream "
            f"MEDIA_BUY_TRANSITIONS terminals {upstream_terminals!r}. Update "
            "the parametrize to cover every state with no outgoing edges."
        )

    @pytest.mark.parametrize("non_terminal_status", ["active", "paused", "pending_approval", "draft"])
    def test_pause_on_non_terminal_buy_does_not_raise_invalid_state(self, non_terminal_status: str) -> None:
        """Negative case: the guard MUST NOT fire on a non-terminal
        status — otherwise legitimate pause requests would be rejected.
        Any non-terminal state should at least reach the adapter dispatch
        without raising ``AdCPInvalidStateError`` (downstream behaviour
        is out of scope for this guard test)."""
        from tests.harness.media_buy_update import MediaBuyUpdateEnv

        with MediaBuyUpdateEnv() as env:
            env.set_media_buy(media_buy_id="mb-nt", status=non_terminal_status)
            try:
                env.call_impl(media_buy_id="mb-nt", paused=True)
            except AdCPInvalidStateError:  # pragma: no cover — negative case
                pytest.fail(f"AdCPInvalidStateError must not fire on non-terminal status {non_terminal_status!r}")
            except Exception:
                # Any other exception is fine — we're only asserting the
                # guard didn't false-positive on a non-terminal buy.
                pass
