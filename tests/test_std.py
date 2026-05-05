"""Tests for sddx.std — Barrier, Retry."""

from __future__ import annotations

from sddx import Event, SimulationRunner, State, StateMachine
from sddx.std import Barrier, Retry


# --- Barrier ---


class FakeWorkBarrier(Barrier):
    WATCHED_EVENT = "work.completed"


def test_barrier_completes_when_expected_events_arrive():
    r = SimulationRunner()
    r.register(FakeWorkBarrier)
    r.register_resolver(FakeWorkBarrier, lambda e: e.payload.get("barrier_id", ""))

    r.create(FakeWorkBarrier, "B1", {
        "barrier_id": "B1", "expected": 3, "received": 0, "timeout_seconds": None,
    })
    r.fire("B1", FakeWorkBarrier, "start")

    for _ in range(3):
        r.event_bus.emit(Event(
            name="work.completed", payload={"barrier_id": "B1"},
            source_machine="ext", source_instance="ext",
        ))

    assert r.get(FakeWorkBarrier, "B1").current_state == "completed"
    completed_events = list(r.event_log.filter(name="barrier.completed"))
    assert len(completed_events) == 1


def test_barrier_times_out_when_advanced_past_deadline():
    r = SimulationRunner()
    r.register(FakeWorkBarrier)
    r.register_resolver(FakeWorkBarrier, lambda e: e.payload.get("barrier_id", ""))

    r.create(FakeWorkBarrier, "B1", {
        "barrier_id": "B1", "expected": 5, "received": 0, "timeout_seconds": 30.0,
    })
    r.fire("B1", FakeWorkBarrier, "start")

    # Send 2 of 5 events, then advance past timeout.
    for _ in range(2):
        r.event_bus.emit(Event(
            name="work.completed", payload={"barrier_id": "B1"},
            source_machine="ext", source_instance="ext",
        ))
    r.advance_time(31.0)

    assert r.get(FakeWorkBarrier, "B1").current_state == "timed_out"


# --- Retry ---


class FakeOpRetry(Retry):
    SUCCESS_EVENT = "op.succeeded"
    FAILURE_EVENT = "op.failed"


def test_retry_succeeds_on_first_attempt():
    r = SimulationRunner()
    r.register(FakeOpRetry)
    r.register_resolver(FakeOpRetry, lambda e: e.payload.get("retry_id", ""))

    r.create(FakeOpRetry, "R1", {
        "retry_id": "R1", "max_attempts": 3, "base_delay": 1.0, "attempts": 0,
    })
    r.fire("R1", FakeOpRetry, "start")
    assert r.get(FakeOpRetry, "R1").current_state == "attempting"

    r.event_bus.emit(Event(
        name="op.succeeded", payload={"retry_id": "R1"},
        source_machine="ext", source_instance="ext",
    ))
    assert r.get(FakeOpRetry, "R1").current_state == "succeeded"


def test_retry_backs_off_then_eventually_exhausts():
    r = SimulationRunner()
    r.register(FakeOpRetry)
    r.register_resolver(FakeOpRetry, lambda e: e.payload.get("retry_id", ""))

    r.create(FakeOpRetry, "R1", {
        "retry_id": "R1", "max_attempts": 3, "base_delay": 1.0, "attempts": 0,
    })
    r.fire("R1", FakeOpRetry, "start")

    def fail_once():
        r.event_bus.emit(Event(
            name="op.failed", payload={"retry_id": "R1"},
            source_machine="ext", source_instance="ext",
        ))

    # Attempt 1 → fail → backoff
    fail_once()
    assert r.get(FakeOpRetry, "R1").current_state == "backing_off"

    # Advance through backoff → attempt 2 → fail → backoff
    r.advance_time(1.0)
    assert r.get(FakeOpRetry, "R1").current_state == "attempting"
    fail_once()

    # Backoff doubled.
    r.advance_time(2.0)
    assert r.get(FakeOpRetry, "R1").current_state == "attempting"

    # Attempt 3 → fail → exhausted (no backoff because max_attempts reached)
    fail_once()
    assert r.get(FakeOpRetry, "R1").current_state == "exhausted"
