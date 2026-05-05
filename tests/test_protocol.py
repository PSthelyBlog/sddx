"""Tests for sddx.protocol — state machine basics + sddx enhancements."""

from __future__ import annotations

import pytest

from sddx import State, StateMachine
from sddx.timing import VirtualClock


# --- Basic vanilla-compatible behavior ---


class Lifecycle(StateMachine):
    pending = State(initial=True)
    active = State()
    done = State(final=True)

    activate = pending.to(active)
    finish = active.to(done)


def test_initial_state_is_set():
    m = Lifecycle()
    assert m.current_state == "pending"
    assert not m.is_final


def test_fire_advances_state():
    m = Lifecycle()
    result = m.fire("activate")
    assert result.success
    assert m.current_state == "active"


def test_fire_unknown_transition_fails():
    m = Lifecycle()
    result = m.fire("nonexistent")
    assert not result.success
    assert result.failure_reason == "unknown_transition"


def test_fire_invalid_source_state_fails():
    m = Lifecycle()
    m.fire("activate")
    m.fire("finish")
    assert m.is_final
    result = m.fire("activate")
    assert not result.success
    assert result.failure_reason == "invalid_source_state"


def test_metaclass_rejects_no_initial_state():
    with pytest.raises(TypeError, match="exactly one initial state"):
        class Bad(StateMachine):
            a = State()
            b = State(final=True)


def test_metaclass_rejects_multiple_initial_states():
    with pytest.raises(TypeError, match="multiple initial states"):
        class Bad(StateMachine):
            a = State(initial=True)
            b = State(initial=True)
            c = State(final=True)


def test_metaclass_rejects_no_final_state():
    with pytest.raises(TypeError, match="at least one final state"):
        class Bad(StateMachine):
            a = State(initial=True)


# --- sddx: .loop() helper ---


class WithLoops(StateMachine):
    a = State(initial=True)
    b = State()
    c = State(final=True)

    advance = a.to(b)
    finish = b.to(c)
    bounce_a = a.loop()
    bounce_group = (a | b).loop()


def test_state_loop_creates_self_transition():
    m = WithLoops()
    result = m.fire("bounce_a")
    assert result.success
    assert result.source == "a"
    assert result.target == "a"


def test_state_group_loop_creates_self_loops_per_state():
    m = WithLoops()
    m.fire("bounce_group")
    assert m.current_state == "a"
    m.fire("advance")
    m.fire("bounce_group")
    assert m.current_state == "b"


# --- sddx: source-aware hooks ---


class HooksRecorder(StateMachine):
    a = State(initial=True)
    b = State(final=True)

    move = a.to(b)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.records: list[tuple[str, ...]] = []

    def on_exit_a(self, source: str, target: str) -> None:
        self.records.append(("exit_a", source, target))

    def on_transition_move(self, source: str, target: str) -> None:
        self.records.append(("transition", source, target))

    def on_enter_b(self, source: str) -> None:
        self.records.append(("enter_b", source))


def test_hooks_receive_source_and_target():
    m = HooksRecorder()
    m.fire("move")
    assert m.records == [
        ("exit_a", "a", "b"),
        ("transition", "a", "b"),
        ("enter_b", "a"),
    ]


class HooksNoArgs(StateMachine):
    a = State(initial=True)
    b = State(final=True)

    move = a.to(b)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.called = False

    def on_enter_b(self) -> None:
        self.called = True


def test_hooks_without_source_param_still_work():
    """Vanilla-style hooks with no parameters keep working."""
    m = HooksNoArgs()
    m.fire("move")
    assert m.called


# --- sddx: per-source guards ---


class PerSourceGuards(StateMachine):
    one = State(initial=True)
    two = State()
    three = State(final=True)

    advance = one.to(two)
    finish = one.to(three) | two.to(three)

    def guard_finish_from_one_to_three(self, **kwargs) -> bool:
        return kwargs.get("force_one", False)

    def guard_finish_from_two_to_three(self, **kwargs) -> bool:
        return True

    # No fallback guard_finish_to_three — verifies per-source is consulted.


def test_per_source_guard_gates_one_branch():
    m = PerSourceGuards()
    result = m.fire("finish", force_one=False)
    assert not result.success
    assert result.failure_reason == "no_guard_passed"


def test_per_source_guard_passes_when_condition_holds():
    m = PerSourceGuards()
    result = m.fire("finish", force_one=True)
    assert result.success


def test_per_source_guard_for_other_source_does_not_apply():
    m = PerSourceGuards()
    m.fire("advance")
    result = m.fire("finish")
    assert result.success
    assert m.current_state == "three"


# --- sddx: clock injection ---


class WithTimer(StateMachine):
    pending = State(initial=True)
    fired = State(final=True)

    fire_now = pending.to(fired)

    def on_transition_fire_now(self) -> None:
        self.set_timer("test.elapsed", 1.0, {"x": 1})


def test_set_timer_requires_clock():
    m = WithTimer()
    with pytest.raises(RuntimeError, match="no clock"):
        m.fire("fire_now")


def test_set_timer_schedules_with_clock():
    clock = VirtualClock()
    m = WithTimer(clock=clock)
    m.fire("fire_now")
    pending = clock.pending()
    assert len(pending) == 1
    assert pending[0].event_name == "test.elapsed"
    assert pending[0].deadline == 1.0
