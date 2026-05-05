"""sddx CLI — `python -m sddx check|run|converge`.

Auto-discovers project layout starting from the current directory:

- ``machines/*.py`` — each file's module is loaded and any subclass of
  ``StateMachine`` is registered.
- ``scenarios/**/*.yaml`` — discovered and parsed.
- ``invariants/*.py`` — functions named ``invariant_*`` are picked up.
- A ``register_resolvers(runner)`` callable in ``conftest_sddx.py``,
  ``sddx_project.py``, or ``project.py`` (first match) is invoked to wire
  resolvers for events that need them.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
from pathlib import Path

from sddx.invariants import load_invariants
from sddx.protocol import StateMachine
from sddx.runner import SimulationRunner
from sddx.scenario import ScenarioParser, ScenarioRunner


CONFIG_FILENAMES = ("sddx_project.py", "project.py", "conftest_sddx.py")


def _discover_machines(project_root: Path) -> list[type[StateMachine]]:
    """Import every module under ``machines/`` using real package paths.

    Project root must already be on sys.path. Using real imports (rather than
    spec_from_file_location with fake names) ensures that a class loaded here
    is identical to the one a project config gets via ``from machines.X``.
    """
    machines_dir = project_root / "machines"
    found: list[type[StateMachine]] = []
    seen: set[type[StateMachine]] = set()
    if not machines_dir.is_dir():
        return found
    for py_file in sorted(machines_dir.glob("**/*.py")):
        if py_file.name.startswith("__"):
            continue
        rel = py_file.relative_to(project_root).with_suffix("")
        module_name = ".".join(rel.parts)
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            print(f"  ! failed to import {module_name}: {e}", file=sys.stderr)
            continue
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                inspect.isclass(obj)
                and issubclass(obj, StateMachine)
                and obj is not StateMachine
                and obj not in seen
                and obj.__module__ == module_name
            ):
                found.append(obj)
                seen.add(obj)
    return found


def _load_project_config(project_root: Path):
    """Look for an optional config module exposing ``register_resolvers``."""
    for filename in CONFIG_FILENAMES:
        candidate = project_root / filename
        if candidate.is_file():
            module_name = f"_sddx_project_{candidate.stem}"
            spec = importlib.util.spec_from_file_location(module_name, candidate)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module
    return None


def _build_runner(project_root: Path) -> SimulationRunner:
    # Put the project root on sys.path so `machines.X`, `invariants.X`, and the
    # project config all resolve via the same import path → same class identity.
    project_str = str(project_root)
    if project_str not in sys.path:
        sys.path.insert(0, project_str)

    runner = SimulationRunner()
    for machine_class in _discover_machines(project_root):
        runner.register(machine_class)

    config = _load_project_config(project_root)
    if config is not None and hasattr(config, "register_resolvers"):
        config.register_resolvers(runner)
    return runner


def cmd_check(project_root: Path) -> int:
    runner = _build_runner(project_root)
    report = runner.check()
    print(f"Machines analyzed: {report.machines_analyzed}")
    print(
        f"Total states: {report.total_states}, "
        f"transitions: {report.total_transitions}"
    )
    if report.unreachable_states:
        print(f"Unreachable: {report.unreachable_states}")
    if report.terminal_states:
        print(f"Terminal:    {report.terminal_states}")
    if report.dead_letters:
        print(f"Dead letters: {report.dead_letters}")
    if report.phantom_subscriptions:
        print(f"Phantom subs: {report.phantom_subscriptions}")
    print(f"Structural valid: {report.is_valid}")
    return 0 if report.is_valid else 1


def cmd_run(project_root: Path, scenario_path: str | None) -> int:
    runner = _build_runner(project_root)
    scen_runner = ScenarioRunner(runner)
    parser = ScenarioParser()

    if scenario_path:
        path = Path(scenario_path)
        scenarios = (
            [parser.parse(path)] if path.is_file() else parser.parse_directory(path)
        )
    else:
        scenarios = parser.parse_directory(project_root / "scenarios")

    failures = 0
    for scenario in scenarios:
        result = scen_runner.run(scenario)
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.scenario_name}: {scenario.narrative}")
        if not result.passed:
            failures += 1
            print(f"    failed at step {result.failure_step}: {result.failure_reason}")
    print(f"\n{len(scenarios) - failures}/{len(scenarios)} scenarios passed")
    return 0 if failures == 0 else 1


def cmd_converge(project_root: Path) -> int:
    runner = _build_runner(project_root)
    report = runner.check()
    if not report.is_valid:
        print("Structural checks failed:")
        if report.unreachable_states:
            print(f"  unreachable: {report.unreachable_states}")
        if report.terminal_states:
            print(f"  terminal:    {report.terminal_states}")
        if report.dead_letters:
            print(f"  dead letters: {report.dead_letters}")
        if report.phantom_subscriptions:
            print(f"  phantom subs: {report.phantom_subscriptions}")
        return 1
    print("Layer 1: structural OK")

    scen_runner = ScenarioRunner(runner)
    parser = ScenarioParser()
    scenarios = parser.parse_directory(project_root / "scenarios")

    invariants_dir = project_root / "invariants"
    invariants = load_invariants(invariants_dir) if invariants_dir.is_dir() else []

    failures = 0
    for scenario in scenarios:
        result = scen_runner.run(scenario, invariants=invariants)
        status = "PASS" if result.passed else "FAIL"
        suffix = ""
        if result.invariant_results is not None:
            ir = result.invariant_results
            suffix = f" (invariants: {ir.total_passed}/{ir.total_checked})"
        print(f"[{status}] {result.scenario_name}{suffix}")
        if not result.passed:
            failures += 1
            print(f"    {result.failure_reason}")
    print(f"\n{len(scenarios) - failures}/{len(scenarios)} scenarios converged")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sddx")
    parser.add_argument(
        "-C", "--project-root", default=".",
        help="Project root (defaults to current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Run structural checks (Layer 1)")
    run_parser = sub.add_parser("run", help="Run scenarios (Layer 2)")
    run_parser.add_argument("scenario", nargs="?", help="Path to a scenario file or directory")
    sub.add_parser("converge", help="Full convergence (Layers 1+2+3)")

    args = parser.parse_args(argv)
    project_root = Path(args.project_root).resolve()

    if args.command == "check":
        return cmd_check(project_root)
    if args.command == "run":
        return cmd_run(project_root, args.scenario)
    if args.command == "converge":
        return cmd_converge(project_root)
    return 1


if __name__ == "__main__":
    sys.exit(main())
