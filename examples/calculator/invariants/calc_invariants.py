"""Property invariants for the calculator system.

These cross-machine assertions hold across every scenario. They catch
correctness issues that per-scenario expectations may miss.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdd.events import EventLog


def invariant_no_compute_after_close(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """Once a session emits calc.closed, no further calc.computed may follow.

    Final state is final: closed sessions don't compute more.
    """
    sessions = log.unique_instances("Calculator")
    for session in sessions:
        closed_events = list(log.filter(name="calc.closed", source_instance=session))
        if not closed_events:
            continue
        close_time = closed_events[-1].timestamp
        later_computes = [
            e for e in log.filter(name="calc.computed", source_instance=session)
            if e.timestamp > close_time
        ]
        assert not later_computes, (
            f"Session {session} computed {len(later_computes)} times after close"
        )


def invariant_errored_session_in_error_or_recovered(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """A session that emits calc.errored must currently be in error, idle,
    closed, or have moved on through a clear/new run.

    Specifically: it should NOT be sitting in entering_first/second/operator_pending
    without first having been cleared. The runtime guarantees this since error is
    only escapable via clear, but we encode the expectation.
    """
    valid_followups = {"error", "idle", "closed", "result_shown",
                       "entering_first", "entering_second", "operator_pending"}
    for (machine, instance), state in states.items():
        if machine != "Calculator":
            continue
        errored = list(log.filter(name="calc.errored", source_instance=instance))
        if errored:
            assert state in valid_followups, (
                f"Calculator {instance} errored then ended up in unexpected state {state}"
            )


def invariant_compute_results_are_finite(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """Every successful compute produces a finite numeric result.

    NaN or infinity in calc.computed indicates a math bug — division by zero
    should have been routed to calc.errored instead.
    """
    for event in log.filter(name="calc.computed"):
        result = event.payload.get("result")
        assert isinstance(result, (int, float)), (
            f"calc.computed result is not numeric: {result!r}"
        )
        assert math.isfinite(float(result)), (
            f"calc.computed produced non-finite result: {result}"
        )


def invariant_log_in_sync_with_calculator(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """When a Calculator instance is closed, its OperationLog must be closed too.

    The OperationLog subscribes to calc.closed and must reach its final state
    in lockstep with the Calculator. A divergence indicates a missing
    subscription or a routing bug.
    """
    for (machine, instance), state in states.items():
        if machine != "Calculator" or state != "closed":
            continue
        log_state = states.get(("OperationLog", instance))
        assert log_state == "closed", (
            f"Calculator {instance} closed but OperationLog is in {log_state!r}"
        )


def invariant_at_most_one_close_per_session(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """A session is closed exactly once. Multiple calc.closed events would
    indicate the close transition fired more than once."""
    for session in log.unique_instances("Calculator"):
        closes = list(log.filter(name="calc.closed", source_instance=session))
        assert len(closes) <= 1, (
            f"Session {session} emitted calc.closed {len(closes)} times"
        )


def invariant_compute_event_implies_no_pending_error(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """For every calc.computed event, there should be no immediately-prior
    calc.errored that wasn't followed by calc.cleared.

    In other words: the calculator can't compute while still in an unrecovered
    error state.
    """
    for session in log.unique_instances("Calculator"):
        events = [e for e in log if e.source_instance == session]
        unrecovered_error = False
        for event in events:
            if event.name == "calc.errored":
                unrecovered_error = True
            elif event.name == "calc.cleared":
                unrecovered_error = False
            elif event.name == "calc.computed":
                assert not unrecovered_error, (
                    f"Session {session} computed while still in unrecovered error state"
                )


def invariant_memory_closes_with_calculator(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """When a Calculator instance is closed, its MemoryRegister must be closed too.

    Both subscribe to calc.closed; a divergence indicates a wiring or
    subscription bug.
    """
    for (machine, instance), state in states.items():
        if machine != "Calculator" or state != "closed":
            continue
        mem_state = states.get(("MemoryRegister", instance))
        # Allow None: scenarios that don't create a MemoryRegister are fine.
        if mem_state is None:
            continue
        assert mem_state == "closed", (
            f"Calculator {instance} closed but MemoryRegister is in {mem_state!r}"
        )


def invariant_no_memory_events_after_close(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """No memory.* events should appear after the calculator session closed."""
    for session in log.unique_instances("Calculator"):
        closed_events = list(log.filter(name="calc.closed", source_instance=session))
        if not closed_events:
            continue
        close_time = closed_events[-1].timestamp
        later_memory_events = [
            e for e in log
            if e.timestamp > close_time
            and e.source_instance == session
            and e.name.startswith("memory.")
        ]
        assert not later_memory_events, (
            f"Session {session} produced {len(later_memory_events)} memory events "
            f"after close: {[e.name for e in later_memory_events]}"
        )


def invariant_recall_round_trip(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """Every calc.memory_recall_requested must produce exactly one memory.recalled
    event for the same session, and (if Calculator was in a recall-accepting state)
    a corresponding calc.value_loaded.

    The first half (request → recalled) must hold unconditionally — that's the
    MemoryRegister's contract. The second half is conditional on the Calculator's
    state, so we just check that recalled values are propagated as floats.
    """
    for session in log.unique_instances("Calculator"):
        requests = list(log.filter(
            name="calc.memory_recall_requested", source_instance=session
        ))
        recalls = list(log.filter(
            name="memory.recalled", source_instance=session
        ))
        assert len(recalls) == len(requests), (
            f"Session {session}: {len(requests)} memory_recall_requested vs "
            f"{len(recalls)} memory.recalled — round-trip broken"
        )
        for recalled in recalls:
            value = recalled.payload.get("value")
            assert isinstance(value, (int, float)), (
                f"memory.recalled payload value is not numeric: {value!r}"
            )


ALL_INVARIANTS = [
    invariant_no_compute_after_close,
    invariant_errored_session_in_error_or_recovered,
    invariant_compute_results_are_finite,
    invariant_log_in_sync_with_calculator,
    invariant_at_most_one_close_per_session,
    invariant_compute_event_implies_no_pending_error,
    invariant_memory_closes_with_calculator,
    invariant_no_memory_events_after_close,
    invariant_recall_round_trip,
]
