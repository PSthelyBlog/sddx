"""Invariant checking for cross-machine property validation.

Direct port of vanilla SDD's invariant module.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from sddx.events import EventLog


@dataclass
class InvariantResult:
    passed: bool
    invariant_name: str
    violation_details: str | None = None


@dataclass
class InvariantReport:
    results: list[InvariantResult] = field(default_factory=list)
    total_checked: int = 0
    total_passed: int = 0
    total_failed: int = 0

    @property
    def all_passed(self) -> bool:
        return self.total_failed == 0

    @property
    def failures(self) -> list[InvariantResult]:
        return [r for r in self.results if not r.passed]


class InvariantFunction(Protocol):
    def __call__(
        self, log: "EventLog", states: dict[tuple[str, str], str]
    ) -> None: ...


def check_invariant(
    invariant: Callable[["EventLog", dict[tuple[str, str], str]], None],
    log: "EventLog",
    states: dict[tuple[str, str], str],
) -> InvariantResult:
    invariant_name = invariant.__name__
    try:
        invariant(log, states)
        return InvariantResult(passed=True, invariant_name=invariant_name)
    except AssertionError as e:
        return InvariantResult(
            passed=False,
            invariant_name=invariant_name,
            violation_details=str(e) if str(e) else "Invariant assertion failed",
        )


def check_invariants(
    invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]],
    log: "EventLog",
    states: dict[tuple[str, str], str],
) -> InvariantReport:
    report = InvariantReport()
    for invariant in invariants:
        result = check_invariant(invariant, log, states)
        report.results.append(result)
        report.total_checked += 1
        if result.passed:
            report.total_passed += 1
        else:
            report.total_failed += 1
    return report


def load_invariants(
    directory: Path | str,
) -> list[Callable[["EventLog", dict[tuple[str, str], str]], None]]:
    path = Path(directory)
    if not path.exists():
        raise FileNotFoundError(f"Invariants directory not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] = []
    for py_file in sorted(path.glob("**/*.py")):
        if py_file.name.startswith("__"):
            continue
        module_name = f"_invariants_{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            continue
        for attr_name in dir(module):
            if attr_name.startswith("invariant_"):
                attr = getattr(module, attr_name)
                if callable(attr):
                    invariants.append(attr)
    return invariants


def load_invariants_from_module(
    module,
) -> list[Callable[["EventLog", dict[tuple[str, str], str]], None]]:
    invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] = []
    for attr_name in dir(module):
        if attr_name.startswith("invariant_"):
            attr = getattr(module, attr_name)
            if callable(attr):
                invariants.append(attr)
    return invariants
