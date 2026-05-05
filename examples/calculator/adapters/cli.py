"""CLI adapter — thin keystroke-to-transition translator over sddx core."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sddx import SimulationRunner  # noqa: E402

from machines.calculator import Calculator  # noqa: E402
from machines.memory_register import MemoryRegister  # noqa: E402
from machines.operation_log import OperationLog  # noqa: E402


SESSION_ID = "cli"


def _initial_context(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "accumulator": 0.0,
        "pending_operator": None,
        "current_operand": "",
        "display": "0",
    }


def _build_runner() -> SimulationRunner:
    runner = SimulationRunner()
    runner.register(Calculator)
    runner.register(MemoryRegister)
    runner.register(OperationLog)
    by_session = lambda e: e.payload.get("session_id", "")
    runner.register_resolver(Calculator, by_session)
    runner.register_resolver(MemoryRegister, by_session)
    runner.register_resolver(OperationLog, by_session)
    return runner


HELP = """\
Calculator (sddx). Type one token at a time:
  0-9       enter a digit
  + - * /   choose an operator
  =         compute
  c         clear
  ms / m+ / m- / mr / mc   memory ops
  q         quit
  ?         show this help
"""

_MEMORY_TOKENS = {
    "ms": "request_memory_set",
    "m+": "request_memory_add",
    "m-": "request_memory_subtract",
    "mr": "request_memory_recall",
    "mc": "request_memory_clear",
}


def main() -> int:
    runner = _build_runner()
    calc = runner.create(Calculator, SESSION_ID, _initial_context(SESSION_ID))
    runner.create(MemoryRegister, SESSION_ID, {"session_id": SESSION_ID, "memory": 0.0})
    runner.create(OperationLog, SESSION_ID, {"session_id": SESSION_ID, "entries": []})

    print(HELP)
    print(f"= {calc.context['display']}")

    while not calc.is_final:
        try:
            token = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not token:
            continue
        if token == "?":
            print(HELP)
            continue

        token_lower = token.lower()
        if token == "q":
            if calc.current_state in ("idle", "result_shown", "error"):
                runner.fire(SESSION_ID, Calculator, "close")
            else:
                runner.fire(SESSION_ID, Calculator, "clear")
                runner.fire(SESSION_ID, Calculator, "close")
            break
        if token == "c":
            runner.fire(SESSION_ID, Calculator, "clear")
        elif token == "=":
            result = runner.fire(SESSION_ID, Calculator, "compute")
            if result.errors:
                print(f"  ! {result.errors[0].message}")
        elif token in ("+", "-", "*", "/"):
            result = runner.fire(SESSION_ID, Calculator, "select_operator", operator=token)
            if result.errors:
                print(f"  ! {result.errors[0].message}")
        elif token_lower in _MEMORY_TOKENS:
            result = runner.fire(SESSION_ID, Calculator, _MEMORY_TOKENS[token_lower])
            if result.errors:
                print(f"  ! {result.errors[0].message}")
        elif len(token) == 1 and token.isdigit():
            result = runner.fire(SESSION_ID, Calculator, "input_digit", digit=token)
            if result.errors:
                print(f"  ! {result.errors[0].message}")
        else:
            print(f"  ! unknown token: {token!r} (type ? for help)")
            continue

        mem = runner.get(MemoryRegister, SESSION_ID)
        mem_marker = " [M]" if mem and mem.current_state == "holding" else ""
        print(f"= {calc.context['display']}{mem_marker}")

    log = runner.get(OperationLog, SESSION_ID)
    if log is not None:
        entries = log.context.get("entries", [])
        compute_count = sum(1 for e in entries if e.get("type") == "compute")
        error_count = sum(1 for e in entries if e.get("type") == "error")
        print(f"\nSession ended. {compute_count} computations, {error_count} errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
