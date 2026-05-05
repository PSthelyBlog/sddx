"""Tests for sddx.scenario — context assertions, advance_time."""

from __future__ import annotations

from pathlib import Path

import pytest

from sddx import (
    ScenarioParser,
    ScenarioRunner,
    SimulationRunner,
    State,
    StateMachine,
)


# Test machine — counter that bumps on input and times itself out.
class Counter(StateMachine):
    idle = State(initial=True)
    counting = State()
    done = State(final=True)
    timed_out = State(final=True)

    start = idle.to(counting)
    bump = counting.loop()
    finish = counting.to(done)
    expire = counting.to(timed_out)

    def on_transition_start(self) -> None:
        self._context["count"] = 0
        self.set_timer("counter.timeout", 10.0, {"id": self._instance_id})

    def on_transition_bump(self) -> None:
        self._context["count"] = self._context.get("count", 0) + 1

    @classmethod
    def subscriptions(cls):
        return {"counter.timeout": "expire"}


@pytest.fixture
def scenario_runner():
    r = SimulationRunner()
    r.register(Counter)
    r.register_resolver(Counter, lambda e: e.payload.get("id", ""))
    return ScenarioRunner(r)


def test_advance_time_in_scenario(tmp_path: Path, scenario_runner):
    yaml_text = """
scenario: timer_fires
narrative: "timer expires after 10s of virtual time"
setup:
  - create: Counter("c1")
    context: { count: 0 }
steps:
  - fire: Counter("c1").start
  - advance_time: 5
  - assert:
      states:
        Counter("c1"): counting
  - advance_time: 6
expect:
  states:
    Counter("c1"): timed_out
"""
    scenario_file = tmp_path / "timer.yaml"
    scenario_file.write_text(yaml_text)
    scenario = ScenarioParser().parse(scenario_file)
    result = scenario_runner.run(scenario)
    assert result.passed, result.failure_reason


def test_context_assertion_passes(tmp_path: Path, scenario_runner):
    yaml_text = """
scenario: ctx_match
narrative: ""
setup:
  - create: Counter("c1")
    context: { count: 0 }
steps:
  - fire: Counter("c1").start
  - fire: Counter("c1").bump
  - fire: Counter("c1").bump
  - fire: Counter("c1").bump
expect:
  context:
    Counter("c1"):
      count: 3
"""
    scenario_file = tmp_path / "ctx.yaml"
    scenario_file.write_text(yaml_text)
    scenario = ScenarioParser().parse(scenario_file)
    result = scenario_runner.run(scenario)
    assert result.passed, result.failure_reason


def test_context_assertion_fails_with_message(tmp_path: Path, scenario_runner):
    yaml_text = """
scenario: ctx_mismatch
narrative: ""
setup:
  - create: Counter("c1")
    context: { count: 0 }
steps:
  - fire: Counter("c1").start
  - fire: Counter("c1").bump
expect:
  context:
    Counter("c1"):
      count: 99
"""
    scenario_file = tmp_path / "ctx2.yaml"
    scenario_file.write_text(yaml_text)
    scenario = ScenarioParser().parse(scenario_file)
    result = scenario_runner.run(scenario)
    assert not result.passed
    assert "count" in result.failure_reason
    assert "99" in result.failure_reason


def test_advance_time_step_with_units(tmp_path: Path, scenario_runner):
    yaml_text = """
scenario: time_units
narrative: ""
setup:
  - create: Counter("c1")
    context: { count: 0 }
steps:
  - fire: Counter("c1").start
  - advance_time: { minutes: 1 }
expect:
  states:
    Counter("c1"): timed_out
"""
    scenario_file = tmp_path / "units.yaml"
    scenario_file.write_text(yaml_text)
    scenario = ScenarioParser().parse(scenario_file)
    result = scenario_runner.run(scenario)
    assert result.passed, result.failure_reason
