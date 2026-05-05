"""Tests for sddx.events — event bus, exact and pattern subscriptions."""

from __future__ import annotations

from sddx import Event, EventBus, EventLog


def test_emit_appends_to_log():
    bus = EventBus()
    bus.emit(Event(name="x.happened", payload={"a": 1}, source_machine="X", source_instance="1"))
    assert len(bus.log) == 1
    assert bus.log[0].name == "x.happened"


def test_subscribe_exact_delivers_event():
    bus = EventBus()
    received = []
    bus.subscribe("x.happened", lambda e: received.append(e))
    bus.emit(Event(name="x.happened", payload={}, source_machine="X", source_instance="1"))
    bus.emit(Event(name="y.other", payload={}, source_machine="Y", source_instance="1"))
    assert len(received) == 1
    assert received[0].name == "x.happened"


def test_subscribe_pattern_matches_glob():
    bus = EventBus()
    received = []
    bus.subscribe_pattern("calc.memory_*_requested", lambda e: received.append(e.name))
    bus.emit(Event(
        name="calc.memory_set_requested", payload={}, source_machine="C", source_instance="1"
    ))
    bus.emit(Event(
        name="calc.memory_add_requested", payload={}, source_machine="C", source_instance="1"
    ))
    bus.emit(Event(
        name="calc.computed", payload={}, source_machine="C", source_instance="1"
    ))
    assert received == ["calc.memory_set_requested", "calc.memory_add_requested"]


def test_event_log_filter():
    log = EventLog([
        Event(name="a", payload={}, source_machine="X", source_instance="1"),
        Event(name="b", payload={}, source_machine="X", source_instance="2"),
        Event(name="a", payload={}, source_machine="Y", source_instance="1"),
    ])
    by_name = log.filter(name="a")
    assert len(by_name) == 2
    by_instance = log.filter(source_instance="2")
    assert len(by_instance) == 1
    by_machine = log.filter(source_machine="Y")
    assert len(by_machine) == 1


def test_unique_instances():
    log = EventLog([
        Event(name="a", payload={}, source_machine="X", source_instance="1"),
        Event(name="a", payload={}, source_machine="X", source_instance="2"),
        Event(name="a", payload={}, source_machine="X", source_instance="1"),
    ])
    assert log.unique_instances("X") == {"1", "2"}


def test_cascade_depth_protection():
    bus = EventBus(max_cascade_depth=3)
    def loop_handler(event):
        bus.emit(Event(name="loop", payload={}, source_machine="X", source_instance="1"))
    bus.subscribe("loop", loop_handler)
    try:
        bus.emit(Event(name="loop", payload={}, source_machine="X", source_instance="1"))
    except RuntimeError as e:
        assert "cascade exceeded maximum depth" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_clear_resets_state():
    bus = EventBus()
    received = []
    bus.subscribe("x", lambda e: received.append(e))
    bus.emit(Event(name="x", payload={}, source_machine="X", source_instance="1"))
    bus.clear()
    assert len(bus.log) == 0
    bus.emit(Event(name="x", payload={}, source_machine="X", source_instance="1"))
    # Subscriptions cleared too: the second emission should NOT trigger handler.
    assert len(received) == 1
