"""OperationLog state machine - records calculator activity.

Demonstrates pattern subscriptions: a single ``calc.memory_*_requested``
glob replaces five explicit subscriptions.
"""

from sddx import State, StateMachine


class OperationLog(StateMachine):
    """Append-only audit log for one calculator session."""

    recording = State(initial=True)
    closed = State(final=True)

    record_input = recording.loop()
    record_op_select = recording.loop()
    record_compute = recording.loop()
    record_error = recording.loop()
    record_clear = recording.loop()
    record_memory_request = recording.loop()
    record_memory_update = recording.loop()
    record_memory_recall = recording.loop()
    record_memory_clear = recording.loop()
    close_log = recording.to(closed)

    def on_transition_record_input(self) -> None:
        self._append({
            "type": "input",
            "buffer": self._context.get("buffer"),
            "display": self._context.get("display"),
        })

    def on_transition_record_op_select(self) -> None:
        self._append({
            "type": "operator",
            "operator": self._context.get("operator"),
            "accumulator": self._context.get("accumulator"),
        })

    def on_transition_record_compute(self) -> None:
        self._append({
            "type": "compute",
            "expression": self._context.get("expression"),
            "result": self._context.get("result"),
        })

    def on_transition_record_error(self) -> None:
        self._append({
            "type": "error",
            "reason": self._context.get("reason"),
        })

    def on_transition_record_clear(self) -> None:
        self._append({"type": "clear"})

    def on_transition_record_memory_request(self) -> None:
        self._append({
            "type": "memory_request",
            "value": self._context.get("value"),
        })

    def on_transition_record_memory_update(self) -> None:
        self._append({
            "type": "memory_update",
            "operation": self._context.get("operation"),
            "value": self._context.get("value"),
        })

    def on_transition_record_memory_recall(self) -> None:
        self._append({
            "type": "memory_recall",
            "value": self._context.get("value"),
        })

    def on_transition_record_memory_clear(self) -> None:
        self._append({"type": "memory_clear"})

    def on_enter_closed(self, source: str) -> None:
        self._append({
            "type": "closed",
            "from_state": source,
            "final_display": self._context.get("final_display"),
        })

    def _append(self, entry: dict) -> None:
        entries = self._context.setdefault("entries", [])
        entries.append(entry)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "calc.digit_appended": "record_input",
            "calc.value_loaded": "record_input",
            "calc.operator_selected": "record_op_select",
            "calc.computed": "record_compute",
            "calc.errored": "record_error",
            "calc.cleared": "record_clear",
            "calc.closed": "close_log",
            "memory.updated": "record_memory_update",
            "memory.recalled": "record_memory_recall",
            "memory.cleared": "record_memory_clear",
        }

    @classmethod
    def pattern_subscriptions(cls) -> dict[str, str]:
        # One line replaces 5 explicit calc.memory_*_requested subscriptions.
        return {
            "calc.memory_*_requested": "record_memory_request",
        }
