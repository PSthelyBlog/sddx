"""sddx — Simulation-Driven Development, eXtended.

A backward-compatible superset of vanilla SDD with cleaner ergonomics, real
timer support, runner-level snapshot/restore, and an opt-in machine stdlib.

Public API:

    from sddx import State, StateMachine
    from sddx import SimulationRunner
    from sddx import Scenario, ScenarioParser, ScenarioRunner
    from sddx import VirtualClock
    from sddx.std import Barrier, Retry
"""

from sddx.events import Event, EventBus, EventLog
from sddx.invariants import (
    InvariantReport,
    InvariantResult,
    check_invariant,
    check_invariants,
    load_invariants,
    load_invariants_from_module,
)
from sddx.protocol import (
    State,
    StateGroup,
    StateMachine,
    Transition,
    TransitionBranch,
    TransitionRecord,
    TransitionResult,
)
from sddx.runner import (
    SimulationRunner,
    StepError,
    StepResult,
    StructuralReport,
)
from sddx.scenario import (
    Scenario,
    ScenarioParser,
    ScenarioParseError,
    ScenarioResult,
    ScenarioRunner,
)
from sddx.timing import ScheduledEvent, VirtualClock

__version__ = "0.1.0"

__all__ = [
    # protocol
    "State", "StateGroup", "StateMachine",
    "Transition", "TransitionBranch", "TransitionRecord", "TransitionResult",
    # events
    "Event", "EventBus", "EventLog",
    # runner
    "SimulationRunner", "StepError", "StepResult", "StructuralReport",
    # scenario
    "Scenario", "ScenarioParser", "ScenarioParseError",
    "ScenarioResult", "ScenarioRunner",
    # invariants
    "InvariantReport", "InvariantResult",
    "check_invariant", "check_invariants",
    "load_invariants", "load_invariants_from_module",
    # timing
    "VirtualClock", "ScheduledEvent",
]
