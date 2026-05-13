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
from adcp.decisioning.state_machines import MEDIA_BUY_TRANSITIONS

from src.core.exceptions import AdCPError, AdCPInvalidStateError

#: Single source of truth for which terminal states the guard must
#: reject on pause/resume. Both the parametrize matrix below AND the
#: lock-in test (:meth:`TestPauseCanceledBuyGuard.test_terminal_states_match_upstream_graph`)
#: read this — adding a state in one place without the other is the
#: drift the lock-in catches.
_TESTED_TERMINAL_STATES: tuple[str, ...] = ("canceled", "completed", "rejected")


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

    @pytest.mark.parametrize("terminal_status", _TESTED_TERMINAL_STATES)
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
        state, this test fails and forces ``_TESTED_TERMINAL_STATES``
        (read by both the parametrize matrix and this assertion) to be
        updated. The hand-rolled tuple this refactor replaced had
        drifted silently — this test prevents the same drift from
        happening to the parametrize."""
        upstream_terminals = {state for state, legal_next in MEDIA_BUY_TRANSITIONS.items() if not legal_next}
        assert set(_TESTED_TERMINAL_STATES) == upstream_terminals, (
            f"_TESTED_TERMINAL_STATES {set(_TESTED_TERMINAL_STATES)!r} drifted from upstream "
            f"MEDIA_BUY_TRANSITIONS terminals {upstream_terminals!r}. Update "
            "the constant to cover every state with no outgoing edges."
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


class TestCancelBranchAsymmetry:
    """The cancel branch (``update_media_buy(canceled=True)``) uses the
    narrower ``AdCPNotCancellableError`` wire code (NOT_CANCELLABLE) for
    cancel-of-canceled; the broader pause/resume branch uses
    ``AdCPInvalidStateError`` (INVALID_STATE) for any terminal state.

    The asymmetry is deliberate — NOT_CANCELLABLE is the spec-canonical
    code for the canonical "you already canceled this" mistake. A future
    contributor "fixing the inconsistency" by extending the pause/resume
    terminal-state guard to the cancel branch would silently downgrade
    the wire vocabulary for cancel-of-canceled. These tests lock in the
    intended scope.
    """

    def test_cancel_of_completed_does_not_raise_not_cancellable(self) -> None:
        """``NOT_CANCELLABLE`` is reserved for cancel-of-canceled (the
        narrow case). Cancel of a different terminal state (e.g.,
        ``completed``) must NOT raise ``AdCPNotCancellableError`` —
        the prior tuple's narrower scope is preserved by intent."""
        from src.core.exceptions import AdCPNotCancellableError
        from tests.harness.media_buy_update import MediaBuyUpdateEnv

        with MediaBuyUpdateEnv() as env:
            env.set_media_buy(media_buy_id="mb-completed", status="completed")
            try:
                env.call_impl(media_buy_id="mb-completed", canceled=True)
            except AdCPNotCancellableError:  # pragma: no cover — would be the regression
                pytest.fail(
                    "Cancel of a completed buy must NOT raise AdCPNotCancellableError — "
                    "that code is reserved for cancel-of-canceled (the narrower case). "
                    "Extending the cancel guard to all terminals would silently downgrade "
                    "the NOT_CANCELLABLE wire vocabulary."
                )
            except Exception:
                # Any other exception is fine — we're locking in only
                # that NOT_CANCELLABLE doesn't fire here.
                pass

    def test_cancel_of_canceled_raises_not_cancellable(self) -> None:
        """The canonical narrow case: re-cancel of an already-canceled
        buy raises ``AdCPNotCancellableError`` (wire code
        NOT_CANCELLABLE). Locks in the scope this PR did NOT change."""
        from src.core.exceptions import AdCPNotCancellableError
        from tests.harness.media_buy_update import MediaBuyUpdateEnv

        with MediaBuyUpdateEnv() as env:
            env.set_media_buy(media_buy_id="mb-canceled", status="canceled")
            with pytest.raises(AdCPNotCancellableError) as excinfo:
                env.call_impl(media_buy_id="mb-canceled", canceled=True)
            assert excinfo.value.error_code == "NOT_CANCELLABLE"
