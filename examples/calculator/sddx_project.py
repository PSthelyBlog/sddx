"""Project config for `python -m sddx`.

Wires resolvers between events and machine instances by session_id.
The CLI's auto-discovery loads ``machines/`` and ``invariants/`` automatically;
this file only needs to declare cross-cutting wiring.
"""

from machines.calculator import Calculator
from machines.memory_register import MemoryRegister
from machines.operation_log import OperationLog


def register_resolvers(runner) -> None:
    by_session = lambda e: e.payload.get("session_id", "")
    runner.register_resolver(Calculator, by_session)
    runner.register_resolver(MemoryRegister, by_session)
    runner.register_resolver(OperationLog, by_session)
