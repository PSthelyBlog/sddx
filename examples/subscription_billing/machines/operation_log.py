"""OperationLog — append-only audit trail across all subscription events.

Subscribes to every meaningful event in the system. One instance per
subscription session. Closes when the subscription reaches a final state.
"""

from sddx import State, StateMachine


class OperationLog(StateMachine):
    """Append-only audit log for one subscription's lifecycle.

    Context fields:
        subscription_id (str): identifier; matches the SubscriptionLifecycle.
        entries (list[dict]): chronological list of recorded events.
    """

    recording = State(initial=True)
    closed = State(final=True)

    record_payment = recording.loop()
    record_retry = recording.loop()
    record_subscription = recording.loop()
    close_log = recording.to(closed)

    # --- Side effects ---

    def on_transition_record_payment(self) -> None:
        self._append({
            "type": "payment",
            "retry_id": self._context.get("retry_id"),
            "transaction_id": self._context.get("transaction_id"),
            "reason": self._context.get("reason"),
            "attempt": self._context.get("attempt"),
        })

    def on_transition_record_retry(self) -> None:
        self._append({
            "type": "retry",
            "retry_id": self._context.get("retry_id"),
            "attempts": self._context.get("attempts"),
            "attempt": self._context.get("attempt"),
        })

    def on_transition_record_subscription(self) -> None:
        self._append({
            "type": "subscription",
            "customer_id": self._context.get("customer_id"),
            "plan_id": self._context.get("plan_id"),
            "reason": self._context.get("reason"),
        })

    def on_enter_closed(self, source: str) -> None:
        self._append({"type": "closed", "from_state": source})

    def _append(self, entry: dict) -> None:
        entries = self._context.setdefault("entries", [])
        entries.append(entry)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "subscription.activated": "record_subscription",
            "subscription.suspended": "record_subscription",
            "subscription.cycle_completed": "record_subscription",
            # Final-state subscription events route to close_log; this is
            # registered AFTER record_subscription so for those event names
            # only one handler fires (the exact one).
            "subscription.cancelled": "close_log",
            "subscription.churned": "close_log",
        }

    @classmethod
    def pattern_subscriptions(cls) -> dict[str, str]:
        # Demonstrates pattern subscriptions: every payment.* event records
        # via the same transition; same for retry.*.
        return {
            "payment.*": "record_payment",
            "retry.*": "record_retry",
        }
