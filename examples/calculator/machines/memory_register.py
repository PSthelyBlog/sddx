"""MemoryRegister state machine - the M+/M-/MS/MR/MC store."""

from sddx import State, StateMachine


class MemoryRegister(StateMachine):
    """One numeric register that backs M+/M-/MS/MR/MC."""

    empty = State(initial=True)
    holding = State()
    closed = State(final=True)

    set_value = empty.to(holding) | holding.to(holding)
    add_value = empty.to(holding) | holding.to(holding)
    subtract_value = empty.to(holding) | holding.to(holding)
    recall_value = (empty | holding).loop()
    clear_value = empty.loop() | holding.to(empty)
    close = (empty | holding).to(closed)

    def on_transition_set_value(self) -> None:
        incoming = self._incoming_value()
        self._context["memory"] = incoming
        self.emit("memory.updated", {
            "session_id": self._context.get("session_id"),
            "value": incoming,
            "operation": "set",
        })

    def on_transition_add_value(self) -> None:
        incoming = self._incoming_value()
        new_memory = self._stored_value() + incoming
        self._context["memory"] = new_memory
        self.emit("memory.updated", {
            "session_id": self._context.get("session_id"),
            "value": new_memory,
            "operation": "add",
        })

    def on_transition_subtract_value(self) -> None:
        incoming = self._incoming_value()
        new_memory = self._stored_value() - incoming
        self._context["memory"] = new_memory
        self.emit("memory.updated", {
            "session_id": self._context.get("session_id"),
            "value": new_memory,
            "operation": "subtract",
        })

    def on_transition_recall_value(self) -> None:
        self.emit("memory.recalled", {
            "session_id": self._context.get("session_id"),
            "value": self._stored_value(),
        })

    def on_transition_clear_value(self) -> None:
        self._context["memory"] = 0.0
        self.emit("memory.cleared", {
            "session_id": self._context.get("session_id"),
        })

    def _stored_value(self) -> float:
        try:
            return float(self._context.get("memory", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _incoming_value(self) -> float:
        try:
            return float(self._context.get("value", 0.0))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "calc.memory_set_requested": "set_value",
            "calc.memory_add_requested": "add_value",
            "calc.memory_subtract_requested": "subtract_value",
            "calc.memory_recall_requested": "recall_value",
            "calc.memory_clear_requested": "clear_value",
            "calc.closed": "close",
        }
