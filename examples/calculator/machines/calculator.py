"""Calculator state machine - the interactive core.

Models a standard four-function calculator. Operations chain left-to-right.
Memory operations (M+, M-, MS, MR, MC) are exposed as transitions whose
on_transition handlers emit ``calc.memory_*_requested`` events for the
MemoryRegister machine to consume; when MemoryRegister responds with
``memory.recalled``, this machine's ``load_value`` transition pushes the
value into the operand position.

Demonstrates sddx affordances:
- ``(states).loop()`` for self-loop transitions (memory request set).
- Per-source guards to disambiguate ``select_operator`` branches by source
  state without inspecting ``self._current_state`` from within a guard.
- Source-aware ``on_enter_idle(source)`` to make the trigger explicit.
"""

from sddx import State, StateMachine


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


_STABLE_STATES = ("idle", "entering_first", "operator_pending", "entering_second", "result_shown")


class Calculator(StateMachine):
    """Tracks one calculator session."""

    # --- States ---
    idle = State(initial=True)
    entering_first = State()
    operator_pending = State()
    entering_second = State()
    result_shown = State()
    error = State()
    closed = State(final=True)

    # --- Transitions ---
    input_digit = (
        idle.to(entering_first)
        | entering_first.to(entering_first)
        | operator_pending.to(entering_second)
        | entering_second.to(entering_second)
        | result_shown.to(entering_first)
    )
    select_operator = (
        entering_first.to(operator_pending)
        | operator_pending.to(operator_pending)
        | entering_second.to(operator_pending)
        | entering_second.to(error)
        | result_shown.to(operator_pending)
    )
    compute = entering_second.to(result_shown) | entering_second.to(error)
    clear = (
        idle | entering_first | operator_pending
        | entering_second | result_shown | error
    ).to(idle)
    close = (idle | result_shown | error).to(closed)

    # Memory request transitions: self-loops on stable states.
    _stable = idle | entering_first | operator_pending | entering_second | result_shown
    request_memory_set = _stable.loop()
    request_memory_add = _stable.loop()
    request_memory_subtract = _stable.loop()
    request_memory_recall = _stable.loop()
    request_memory_clear = _stable.loop()

    # Recall round-trip: subscribed to memory.recalled.
    load_value = (
        idle.to(entering_first)
        | entering_first.to(entering_first)
        | operator_pending.to(entering_second)
        | entering_second.to(entering_second)
        | result_shown.to(entering_first)
    )

    # --- Guards (per-source where the same target needs different rules) ---

    def guard_select_operator_from_entering_second_to_operator_pending(self, **kwargs) -> bool:
        return not self._would_divide_by_zero()

    def guard_select_operator_from_entering_second_to_error(self, **kwargs) -> bool:
        return self._would_divide_by_zero()

    def guard_compute_to_result_shown(self, **kwargs) -> bool:
        return not self._would_divide_by_zero()

    def guard_compute_to_error(self, **kwargs) -> bool:
        return self._would_divide_by_zero()

    # --- Side effects ---

    def on_transition_input_digit(self, source: str) -> None:
        digit = str(self._context.get("digit", ""))
        if source in ("idle", "operator_pending"):
            self._context["current_operand"] = digit
        elif source == "result_shown":
            self._context["accumulator"] = 0.0
            self._context["pending_operator"] = None
            self._context["current_operand"] = digit
        else:
            self._context["current_operand"] = (
                self._context.get("current_operand", "") + digit
            )
        self._context["display"] = self._context["current_operand"]
        self.emit("calc.digit_appended", {
            "session_id": self._context.get("session_id"),
            "display": self._context["display"],
            "buffer": self._context["current_operand"],
        })

    def on_transition_select_operator(self, source: str, target: str) -> None:
        operator = str(self._context.get("operator", ""))
        if source == "entering_first":
            self._context["accumulator"] = self._operand_as_float()
        elif source == "entering_second":
            if target == "error":
                self._context["error_reason"] = "Division by zero"
                return
            self._context["accumulator"] = self._apply(
                self._context.get("accumulator", 0.0),
                self._context.get("pending_operator"),
                self._operand_as_float(),
            )
        # result_shown / operator_pending: no compute.
        self._context["pending_operator"] = operator
        self._context["current_operand"] = ""
        self._context["display"] = _format_number(
            self._context.get("accumulator", 0.0)
        )
        self.emit("calc.operator_selected", {
            "session_id": self._context.get("session_id"),
            "operator": operator,
            "accumulator": self._context.get("accumulator", 0.0),
        })

    def on_transition_compute(self, target: str) -> None:
        if target == "error":
            self._context["error_reason"] = "Division by zero"
            return
        accumulator = self._context.get("accumulator", 0.0)
        operator = self._context.get("pending_operator")
        operand = self._operand_as_float()
        result = self._apply(accumulator, operator, operand)
        expression = (
            f"{_format_number(accumulator)} {operator} {_format_number(operand)}"
        )
        self._context["accumulator"] = result
        self._context["pending_operator"] = None
        self._context["current_operand"] = ""
        self._context["display"] = _format_number(result)
        self.emit("calc.computed", {
            "session_id": self._context.get("session_id"),
            "result": result,
            "display": self._context["display"],
            "expression": expression,
        })

    def on_transition_clear(self) -> None:
        self._context["accumulator"] = 0.0
        self._context["pending_operator"] = None
        self._context["current_operand"] = ""
        self._context["error_reason"] = None
        self._context["display"] = "0"
        self.emit("calc.cleared", {
            "session_id": self._context.get("session_id"),
        })

    def on_transition_request_memory_set(self) -> None:
        self.emit("calc.memory_set_requested", {
            "session_id": self._context.get("session_id"),
            "value": self._displayed_value(),
        })

    def on_transition_request_memory_add(self) -> None:
        self.emit("calc.memory_add_requested", {
            "session_id": self._context.get("session_id"),
            "value": self._displayed_value(),
        })

    def on_transition_request_memory_subtract(self) -> None:
        self.emit("calc.memory_subtract_requested", {
            "session_id": self._context.get("session_id"),
            "value": self._displayed_value(),
        })

    def on_transition_request_memory_recall(self) -> None:
        self.emit("calc.memory_recall_requested", {
            "session_id": self._context.get("session_id"),
        })

    def on_transition_request_memory_clear(self) -> None:
        self.emit("calc.memory_clear_requested", {
            "session_id": self._context.get("session_id"),
        })

    def on_transition_load_value(self, source: str) -> None:
        try:
            value = float(self._context.get("value", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        if source == "result_shown":
            self._context["accumulator"] = 0.0
            self._context["pending_operator"] = None
        formatted = _format_number(value)
        self._context["current_operand"] = formatted
        self._context["display"] = formatted
        self.emit("calc.value_loaded", {
            "session_id": self._context.get("session_id"),
            "value": value,
            "display": formatted,
            "buffer": formatted,
        })

    def on_enter_error(self) -> None:
        if not self._context.get("error_reason"):
            self._context["error_reason"] = "Division by zero"
        self._context["display"] = "Error"
        self.emit("calc.errored", {
            "session_id": self._context.get("session_id"),
            "reason": self._context["error_reason"],
        })

    def on_enter_closed(self) -> None:
        self.emit("calc.closed", {
            "session_id": self._context.get("session_id"),
            "final_display": self._context.get("display", "0"),
        })

    # --- Helpers ---

    def _would_divide_by_zero(self) -> bool:
        if self._context.get("pending_operator") != "/":
            return False
        try:
            return self._operand_as_float() == 0.0
        except (TypeError, ValueError):
            return True

    def _operand_as_float(self) -> float:
        raw = self._context.get("current_operand", "0")
        if raw in ("", None):
            return 0.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    def _displayed_value(self) -> float:
        state = self._current_state
        if state in ("operator_pending", "result_shown"):
            return float(self._context.get("accumulator", 0.0))
        if state in ("entering_first", "entering_second"):
            return self._operand_as_float()
        return 0.0

    @staticmethod
    def _apply(a: float, op: str | None, b: float) -> float:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        return b

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "memory.recalled": "load_value",
        }
