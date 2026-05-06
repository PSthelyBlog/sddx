"""Tests for sddx.runner — registration, firing, cascades, snapshot/restore."""

from __future__ import annotations

import pytest

from sddx import (
    Event,
    SimulationRunner,
    State,
    StateMachine,
)


class Producer(StateMachine):
    idle = State(initial=True)
    fired = State(final=True)

    do_it = idle.to(fired)

    def on_enter_fired(self) -> None:
        self.emit("ping", {"id": self._instance_id})


class Consumer(StateMachine):
    waiting = State(initial=True)
    notified = State(final=True)

    notify = waiting.to(notified)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {"ping": "notify"}


@pytest.fixture
def runner():
    r = SimulationRunner()
    r.register(Producer)
    r.register(Consumer)
    r.register_resolver(Producer, lambda e: e.payload.get("id", ""))
    r.register_resolver(Consumer, lambda e: e.payload.get("id", ""))
    return r


def test_register_and_create_instances(runner):
    p = runner.create(Producer, "p1")
    c = runner.create(Consumer, "p1")
    assert runner.get(Producer, "p1") is p
    assert runner.get(Consumer, "p1") is c


def test_event_cascade_through_subscription(runner):
    runner.create(Producer, "p1")
    runner.create(Consumer, "p1")
    runner.fire("p1", Producer, "do_it")
    assert runner.get(Consumer, "p1").current_state == "notified"


def test_implicit_consumer_creation(runner):
    runner.create(Producer, "p1")
    # No Consumer created — subscription handler should auto-create.
    runner.fire("p1", Producer, "do_it")
    consumer = runner.get(Consumer, "p1")
    assert consumer is not None
    assert consumer.current_state == "notified"


def test_structural_check_clean(runner):
    report = runner.check()
    assert report.is_valid
    assert report.dead_letters == []


def test_dead_letter_detected_when_no_subscriber():
    class Lonely(StateMachine):
        a = State(initial=True)
        b = State(final=True)
        go = a.to(b)
        def on_enter_b(self):
            self.emit("noone.listens", {})

    r = SimulationRunner()
    r.register(Lonely)
    report = r.check()
    assert "noone.listens" in report.dead_letters


def test_set_timer_events_detected_as_emissions():
    """Events scheduled via self.set_timer() should not appear as phantom subs.

    The structural check parses hooks for emit() calls; it must also recognize
    set_timer() event names so timer-driven cascades don't show up as phantom
    subscriptions to the convergence checker.
    """
    class Scheduler(StateMachine):
        idle = State(initial=True)
        scheduled = State()
        elapsed = State(final=True)
        schedule = idle.to(scheduled)
        receive = scheduled.to(elapsed)

        def on_transition_schedule(self):
            self.set_timer("test.timer", 5.0, {"id": self._instance_id})
            # Also use keyword form to verify both styles are detected.
            self.set_timer(event_name="test.kw_timer", delay=10.0, payload={})

        @classmethod
        def subscriptions(cls):
            return {"test.timer": "receive", "test.kw_timer": "receive"}

    r = SimulationRunner()
    r.register(Scheduler)
    report = r.check()
    # Without set_timer detection these would be phantom subscriptions.
    assert "test.timer" not in report.phantom_subscriptions
    assert "test.kw_timer" not in report.phantom_subscriptions
    assert report.is_valid


def test_telemetry_events_excluded_from_dead_letters():
    class TelemOnly(StateMachine):
        a = State(initial=True)
        b = State(final=True)
        go = a.to(b)
        def on_enter_b(self):
            self.emit("telem.observed", {})
        @classmethod
        def telemetry_events(cls):
            return {"telem.observed"}

    r = SimulationRunner()
    r.register(TelemOnly)
    report = r.check()
    assert "telem.observed" not in report.dead_letters
    assert report.is_valid


def test_pattern_subscription_wired_by_runner():
    class Listener(StateMachine):
        idle = State(initial=True)
        heard = State(final=True)
        record = idle.loop() | idle.to(heard)
        @classmethod
        def pattern_subscriptions(cls):
            return {"thing.*": "record"}
        def guard_record_to_heard(self, **kw):
            return kw.get("final", False)
        def guard_record_to_idle(self, **kw):
            return not kw.get("final", False)

    r = SimulationRunner()
    r.register(Listener)
    r.register_resolver(Listener, lambda e: e.payload.get("id", ""))
    r.create(Listener, "L1")

    # Inject events directly through the bus to test pattern dispatch.
    r.event_bus.emit(Event(
        name="thing.alpha", payload={"id": "L1", "final": False},
        source_machine="ext", source_instance="ext",
    ))
    r.event_bus.emit(Event(
        name="thing.omega", payload={"id": "L1", "final": True},
        source_machine="ext", source_instance="ext",
    ))
    assert r.get(Listener, "L1").current_state == "heard"


def test_advance_time_fires_scheduled_events():
    class Scheduler(StateMachine):
        idle = State(initial=True)
        scheduled = State()
        elapsed = State(final=True)
        schedule = idle.to(scheduled)
        receive = scheduled.to(elapsed)

        def on_transition_schedule(self):
            self.set_timer("test.timer", 5.0, {"id": self._instance_id})

        @classmethod
        def subscriptions(cls):
            return {"test.timer": "receive"}

    r = SimulationRunner()
    r.register(Scheduler)
    r.register_resolver(Scheduler, lambda e: e.payload.get("id", ""))
    r.create(Scheduler, "S1")

    r.fire("S1", Scheduler, "schedule")
    assert r.get(Scheduler, "S1").current_state == "scheduled"

    # Advance less than the timer — nothing fires.
    fired = r.advance_time(2.0)
    assert fired == []
    assert r.get(Scheduler, "S1").current_state == "scheduled"

    # Advance past the timer — event fires and consumer transitions.
    fired = r.advance_time(5.0)
    assert len(fired) == 1
    assert r.get(Scheduler, "S1").current_state == "elapsed"


def test_runner_snapshot_restore(runner):
    p = runner.create(Producer, "p1", {"label": "first"})
    runner.fire("p1", Producer, "do_it")
    snap = runner.snapshot()

    # Build a clean runner with the same registrations + resolvers.
    fresh = SimulationRunner()
    fresh.register(Producer)
    fresh.register(Consumer)
    fresh.register_resolver(Producer, lambda e: e.payload.get("id", ""))
    fresh.register_resolver(Consumer, lambda e: e.payload.get("id", ""))
    fresh.restore(snap)

    restored_producer = fresh.get(Producer, "p1")
    assert restored_producer is not None
    assert restored_producer.current_state == "fired"
    assert restored_producer.context["label"] == "first"
    restored_consumer = fresh.get(Consumer, "p1")
    assert restored_consumer is not None
    assert restored_consumer.current_state == "notified"
    assert len(fresh.event_log) > 0
