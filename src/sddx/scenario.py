"""Scenario parser and runner for sddx.

Backward-compatible port of vanilla SDD's scenario module with two
previously-stubbed features now implemented:

- ``advance_time`` steps drive the runner's ``VirtualClock``, firing any
  scheduled events whose deadlines have passed.
- Context assertions in ``assert`` steps and the final ``expect`` block
  compare specific machine context fields against expected values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import yaml

if TYPE_CHECKING:
    from sddx.events import Event, EventLog
    from sddx.invariants import InvariantReport
    from sddx.runner import SimulationRunner, StepResult


class ScenarioParseError(Exception):
    def __init__(self, message: str, file: str | None = None, line: int | None = None):
        self.file = file
        self.line = line
        location = ""
        if file:
            location = f" in {file}"
            if line:
                location += f" at line {line}"
        super().__init__(f"{message}{location}")


INSTANCE_REF_PATTERN = re.compile(r'^(\w+)\("([^"]+)"\)$')
FIRE_PATTERN = re.compile(r'^(\w+)\("([^"]+)"\)\.(\w+)$')


def parse_instance_ref(ref: str) -> tuple[str, str]:
    match = INSTANCE_REF_PATTERN.match(ref)
    if not match:
        raise ScenarioParseError(f"Invalid instance reference: {ref}")
    return match.group(1), match.group(2)


def parse_fire_ref(ref: str) -> tuple[str, str, str]:
    match = FIRE_PATTERN.match(ref)
    if not match:
        raise ScenarioParseError(f"Invalid fire reference: {ref}")
    return match.group(1), match.group(2), match.group(3)


@dataclass
class CreateStep:
    machine: str
    instance_id: str
    context: dict = field(default_factory=dict)


@dataclass
class FireStep:
    machine: str
    instance_id: str
    transition: str
    args: dict = field(default_factory=dict)
    expect_failure: str | None = None


@dataclass
class AdvanceTimeStep:
    seconds: float = 0.0


@dataclass
class AssertStep:
    states: dict[str, str] | None = None
    events_emitted: list[str] | None = None
    events_not_emitted: list[str] | None = None
    context: dict[str, dict[str, Any]] | None = None


Step = CreateStep | FireStep | AdvanceTimeStep | AssertStep


@dataclass
class ExpectBlock:
    states: dict[str, str] | None = None
    events_emitted: list[str] | None = None
    events_not_emitted: list[str] | None = None
    context: dict[str, dict[str, Any]] | None = None


@dataclass
class Scenario:
    name: str
    narrative: str
    setup: list[Step] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    expect: ExpectBlock = field(default_factory=ExpectBlock)


class ScenarioParser:
    def parse(self, yaml_path: Path | str) -> Scenario:
        path = Path(yaml_path)
        if not path.exists():
            raise ScenarioParseError(f"Scenario file not found: {path}", file=str(path))
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ScenarioParseError(f"Invalid YAML: {e}", file=str(path))
        if not isinstance(data, dict):
            raise ScenarioParseError("Scenario must be a YAML mapping", file=str(path))
        return self._parse_scenario(data, str(path))

    def parse_directory(self, directory: Path | str) -> list[Scenario]:
        path = Path(directory)
        if not path.is_dir():
            raise ScenarioParseError(f"Directory not found: {path}")
        scenarios = []
        for yaml_file in sorted(path.glob("**/*.yaml")):
            scenarios.append(self.parse(yaml_file))
        for yaml_file in sorted(path.glob("**/*.yml")):
            scenarios.append(self.parse(yaml_file))
        return scenarios

    def _parse_scenario(self, data: dict[str, Any], file: str) -> Scenario:
        if "scenario" not in data:
            raise ScenarioParseError("Missing required field: 'scenario'", file=file)
        name = data["scenario"]
        narrative = data.get("narrative", "")

        setup = []
        if "setup" in data:
            for i, step_data in enumerate(data["setup"]):
                try:
                    setup.append(self._parse_step(step_data))
                except ScenarioParseError as e:
                    raise ScenarioParseError(
                        f"Error in setup step {i}: {e}", file=file
                    )
        steps = []
        if "steps" in data:
            for i, step_data in enumerate(data["steps"]):
                try:
                    steps.append(self._parse_step(step_data))
                except ScenarioParseError as e:
                    raise ScenarioParseError(f"Error in step {i}: {e}", file=file)

        expect = ExpectBlock()
        if "expect" in data:
            expect = self._parse_expect_block(data["expect"], file)

        return Scenario(
            name=name, narrative=narrative,
            setup=setup, steps=steps, expect=expect,
        )

    def _parse_step(self, step_data: dict[str, Any]) -> Step:
        if not isinstance(step_data, dict):
            raise ScenarioParseError(
                f"Step must be a mapping, got {type(step_data).__name__}"
            )
        if "create" in step_data:
            return self._parse_create_step(step_data)
        if "fire" in step_data:
            return self._parse_fire_step(step_data)
        if "advance_time" in step_data:
            return self._parse_advance_time_step(step_data)
        if "assert" in step_data:
            return self._parse_assert_step(step_data)
        raise ScenarioParseError(
            "Unknown step type. Expected 'create', 'fire', 'advance_time', or 'assert'"
        )

    def _parse_create_step(self, step_data: dict[str, Any]) -> CreateStep:
        machine, instance_id = parse_instance_ref(step_data["create"])
        return CreateStep(
            machine=machine, instance_id=instance_id,
            context=step_data.get("context", {}),
        )

    def _parse_fire_step(self, step_data: dict[str, Any]) -> FireStep:
        machine, instance_id, transition = parse_fire_ref(step_data["fire"])
        args = step_data.get("args", {})
        return FireStep(
            machine=machine, instance_id=instance_id, transition=transition,
            args=args if args else {},
            expect_failure=step_data.get("expect_failure"),
        )

    def _parse_advance_time_step(self, step_data: dict[str, Any]) -> AdvanceTimeStep:
        time_data = step_data["advance_time"]
        if isinstance(time_data, (int, float)):
            return AdvanceTimeStep(seconds=float(time_data))
        if not isinstance(time_data, dict):
            raise ScenarioParseError(
                "advance_time must be a number or a mapping with seconds/minutes/hours/days"
            )
        seconds = float(time_data.get("seconds", 0))
        seconds += 60 * float(time_data.get("minutes", 0))
        seconds += 3600 * float(time_data.get("hours", 0))
        seconds += 86400 * float(time_data.get("days", 0))
        return AdvanceTimeStep(seconds=seconds)

    def _parse_assert_step(self, step_data: dict[str, Any]) -> AssertStep:
        assert_data = step_data["assert"]
        if not isinstance(assert_data, dict):
            raise ScenarioParseError("assert must be a mapping")
        return AssertStep(
            states=assert_data.get("states"),
            events_emitted=assert_data.get("events_emitted"),
            events_not_emitted=assert_data.get("events_not_emitted"),
            context=assert_data.get("context"),
        )

    def _parse_expect_block(self, expect_data: dict[str, Any], file: str) -> ExpectBlock:
        if not isinstance(expect_data, dict):
            raise ScenarioParseError("expect must be a mapping", file=file)
        return ExpectBlock(
            states=expect_data.get("states"),
            events_emitted=expect_data.get("events_emitted"),
            events_not_emitted=expect_data.get("events_not_emitted"),
            context=expect_data.get("context"),
        )


@dataclass
class ScenarioResult:
    scenario_name: str
    passed: bool
    steps_executed: int
    step_results: list["StepResult"] = field(default_factory=list)
    failure_step: int | None = None
    failure_reason: str | None = None
    final_states: dict[str, str] = field(default_factory=dict)
    events_emitted: list["Event"] = field(default_factory=list)
    invariant_results: "InvariantReport | None" = None


class ScenarioRunner:
    def __init__(self, runner: "SimulationRunner") -> None:
        self._runner = runner

    def run(
        self,
        scenario: Scenario,
        invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] | None = None,
    ) -> ScenarioResult:
        self._runner.reset()

        step_results: list["StepResult"] = []
        steps_executed = 0
        failure_step = None
        failure_reason = None

        all_steps = scenario.setup + scenario.steps
        for i, step in enumerate(all_steps):
            try:
                result = self._execute_step(step)
                steps_executed += 1
                if result is not None:
                    step_results.append(result)
                    if isinstance(step, FireStep):
                        has_errors = len(result.errors) > 0
                        if step.expect_failure:
                            if not has_errors:
                                failure_step = i
                                failure_reason = (
                                    f"Expected failure '{step.expect_failure}' "
                                    f"but transition succeeded"
                                )
                                break
                            actual = result.errors[0].error_type if result.errors else None
                            if actual != step.expect_failure:
                                failure_step = i
                                failure_reason = (
                                    f"Expected failure '{step.expect_failure}' "
                                    f"but got '{actual}'"
                                )
                                break
                        else:
                            if has_errors:
                                error = result.errors[0]
                                failure_step = i
                                failure_reason = (
                                    f"Transition '{step.transition}' failed: "
                                    f"{error.error_type} - {error.message}"
                                )
                                break
            except AssertionError as e:
                failure_step = i
                failure_reason = str(e)
                break
            except Exception as e:
                failure_step = i
                failure_reason = str(e)
                break

        final_states: dict[str, str] = {}
        for (machine_class, instance_id), state in self._runner.machine_states().items():
            key = f'{machine_class.__name__}("{instance_id}")'
            final_states[key] = state

        events_emitted = list(self._runner.event_log)

        if failure_step is None:
            expect_failure = self._verify_expect_block(
                scenario.expect, final_states, events_emitted,
            )
            if expect_failure:
                failure_step = steps_executed
                failure_reason = expect_failure

        invariant_results: "InvariantReport | None" = None
        if invariants:
            from sddx.invariants import check_invariants
            states_for_invariants: dict[tuple[str, str], str] = {}
            for (machine_class, instance_id), state in self._runner.machine_states().items():
                states_for_invariants[(machine_class.__name__, instance_id)] = state
            invariant_results = check_invariants(
                invariants, self._runner.event_log, states_for_invariants,
            )
            if failure_step is None and not invariant_results.all_passed:
                failure_step = steps_executed
                first_failure = invariant_results.failures[0]
                failure_reason = (
                    f"Invariant '{first_failure.invariant_name}' violated: "
                    f"{first_failure.violation_details}"
                )

        return ScenarioResult(
            scenario_name=scenario.name,
            passed=(failure_step is None),
            steps_executed=steps_executed,
            step_results=step_results,
            failure_step=failure_step,
            failure_reason=failure_reason,
            final_states=final_states,
            events_emitted=events_emitted,
            invariant_results=invariant_results,
        )

    def run_all(
        self,
        scenarios: list[Scenario],
        invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] | None = None,
    ) -> list[ScenarioResult]:
        return [self.run(scenario, invariants=invariants) for scenario in scenarios]

    def _execute_step(self, step: Step) -> "StepResult | None":
        if isinstance(step, CreateStep):
            self._execute_create_step(step)
            return None
        if isinstance(step, FireStep):
            return self._execute_fire_step(step)
        if isinstance(step, AdvanceTimeStep):
            self._execute_advance_time_step(step)
            return None
        if isinstance(step, AssertStep):
            self._execute_assert_step(step)
            return None
        raise ScenarioParseError(f"Unknown step type: {type(step)}")

    def _execute_create_step(self, step: CreateStep) -> None:
        machine_class = self._runner.get_machine_class(step.machine)
        if machine_class is None:
            raise ScenarioParseError(
                f"Unknown machine class: {step.machine}. "
                f"Make sure to register it with the runner."
            )
        self._runner.create(machine_class, step.instance_id, step.context)

    def _execute_fire_step(self, step: FireStep) -> "StepResult":
        machine_class = self._runner.get_machine_class(step.machine)
        if machine_class is None:
            raise ScenarioParseError(
                f"Unknown machine class: {step.machine}. "
                f"Make sure to register it with the runner."
            )
        return self._runner.fire(
            step.instance_id, machine_class, step.transition, **step.args
        )

    def _execute_advance_time_step(self, step: AdvanceTimeStep) -> None:
        self._runner.advance_time(step.seconds)

    def _execute_assert_step(self, step: AssertStep) -> None:
        if step.states:
            for ref, expected_state in step.states.items():
                machine, instance_id = parse_instance_ref(ref)
                machine_class = self._runner.get_machine_class(machine)
                if machine_class is None:
                    raise AssertionError(
                        f"Unknown machine class in assertion: {machine}"
                    )
                instance = self._runner.get(machine_class, instance_id)
                if instance is None:
                    raise AssertionError(
                        f"Instance {ref} not found for state assertion"
                    )
                if instance.current_state != expected_state:
                    raise AssertionError(
                        f"State assertion failed for {ref}: expected "
                        f"'{expected_state}', got '{instance.current_state}'"
                    )

        if step.events_emitted:
            emitted_names = {e.name for e in self._runner.event_log}
            for event_name in step.events_emitted:
                if event_name not in emitted_names:
                    raise AssertionError(
                        f"Event assertion failed: expected '{event_name}' to be emitted"
                    )

        if step.events_not_emitted:
            emitted_names = {e.name for e in self._runner.event_log}
            for event_name in step.events_not_emitted:
                if event_name in emitted_names:
                    raise AssertionError(
                        f"Event assertion failed: expected '{event_name}' to NOT be emitted"
                    )

        if step.context:
            self._verify_context(step.context)

    def _verify_context(self, context_assertions: dict[str, dict[str, Any]]) -> None:
        """Check specific context fields on machine instances."""
        for ref, expected in context_assertions.items():
            machine, instance_id = parse_instance_ref(ref)
            machine_class = self._runner.get_machine_class(machine)
            if machine_class is None:
                raise AssertionError(
                    f"Unknown machine class in context assertion: {machine}"
                )
            instance = self._runner.get(machine_class, instance_id)
            if instance is None:
                raise AssertionError(
                    f"Instance {ref} not found for context assertion"
                )
            ctx = instance.context
            for field_name, expected_value in expected.items():
                actual = ctx.get(field_name)
                if actual != expected_value:
                    raise AssertionError(
                        f"Context assertion failed for {ref}.{field_name}: "
                        f"expected {expected_value!r}, got {actual!r}"
                    )

    def _verify_expect_block(
        self,
        expect: ExpectBlock,
        final_states: dict[str, str],
        events: list["Event"],
    ) -> str | None:
        if expect.states:
            for ref, expected_state in expect.states.items():
                if ref not in final_states:
                    return f"Expected state for {ref} but instance not found"
                if final_states[ref] != expected_state:
                    return (
                        f"Expected state '{expected_state}' for {ref}, "
                        f"got '{final_states[ref]}'"
                    )

        if expect.events_emitted:
            emitted_names = {e.name for e in events}
            for event_name in expect.events_emitted:
                if event_name not in emitted_names:
                    return f"Expected event '{event_name}' was not emitted"

        if expect.events_not_emitted:
            emitted_names = {e.name for e in events}
            for event_name in expect.events_not_emitted:
                if event_name in emitted_names:
                    return f"Event '{event_name}' was emitted but should not have been"

        if expect.context:
            try:
                self._verify_context(expect.context)
            except AssertionError as e:
                return str(e)

        return None
