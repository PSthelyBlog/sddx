"""Tests for sddx.timing — VirtualClock + scheduled events."""

from __future__ import annotations

import pytest

from sddx.timing import VirtualClock


def test_clock_starts_at_zero():
    c = VirtualClock()
    assert c.now() == 0.0


def test_advance_moves_time_forward():
    c = VirtualClock()
    c.advance(5)
    assert c.now() == 5.0
    c.advance(2.5)
    assert c.now() == 7.5


def test_schedule_creates_pending_event():
    c = VirtualClock()
    c.schedule(delay=10, event_name="x", payload={})
    assert len(c.pending()) == 1
    assert c.pending()[0].deadline == 10.0


def test_advance_fires_due_events_in_order():
    c = VirtualClock()
    c.schedule(delay=5, event_name="b", payload={})
    c.schedule(delay=2, event_name="a", payload={})
    c.schedule(delay=10, event_name="c", payload={})
    fired = c.advance(7)
    assert [s.event_name for s in fired] == ["a", "b"]
    assert len(c.pending()) == 1
    assert c.pending()[0].event_name == "c"


def test_advance_does_not_fire_future_events():
    c = VirtualClock()
    c.schedule(delay=10, event_name="future", payload={})
    fired = c.advance(5)
    assert fired == []


def test_negative_delay_rejected():
    c = VirtualClock()
    with pytest.raises(ValueError):
        c.schedule(delay=-1, event_name="x", payload={})


def test_negative_advance_rejected():
    c = VirtualClock()
    with pytest.raises(ValueError):
        c.advance(-1)


def test_snapshot_restore_round_trip():
    c = VirtualClock()
    c.advance(3.5)
    c.schedule(delay=5, event_name="later", payload={"x": 1})
    snap = c.snapshot()

    restored = VirtualClock.restore(snap)
    assert restored.now() == 3.5
    assert len(restored.pending()) == 1
    assert restored.pending()[0].event_name == "later"
    assert restored.pending()[0].deadline == 8.5


def test_reset_clears_state():
    c = VirtualClock()
    c.advance(5)
    c.schedule(delay=2, event_name="x", payload={})
    c.reset()
    assert c.now() == 0
    assert c.pending() == []
