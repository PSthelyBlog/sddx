"""SubscriptionLifecycle — the customer-facing subscription state machine.

Reacts to PaymentAttempt outcomes (retry.succeeded / retry.exhausted) by
moving between pending, active, suspended. Schedules a grace-period timer
on entering suspended; if the timer fires before the user pays or cancels,
the subscription churns.

This machine doesn't know how charges work — that's PaymentAttempt's job.
It only consumes the resolved outcome events.
"""

from sddx import State, StateMachine


_DEFAULT_GRACE_PERIOD_SECONDS = 7 * 86400  # 7 days


class SubscriptionLifecycle(StateMachine):
    """One customer subscription.

    Context fields:
        subscription_id (str): identifier (also the resolver key).
        customer_id (str): customer that owns the subscription.
        plan_id (str): catalog plan reference.
        amount (float): per-cycle charge amount.
        billing_period_days (int): days between cycles (informational here).
        grace_period_seconds (float): how long to wait in suspended before
            churning. Defaults to 7 days.
        cancellation_reason (str | None): set when the user cancels.
    """

    pending = State(initial=True)
    active = State()
    suspended = State()
    cancelled = State(final=True)
    churned = State(final=True)

    charge_succeeded = pending.to(active) | active.to(active)
    charge_exhausted = pending.to(suspended) | active.to(suspended)
    cancel = (pending | active | suspended).to(cancelled)
    churn = suspended.to(churned)

    # --- Side effects ---

    def on_transition_charge_succeeded(self, source: str) -> None:
        # Fired by retry.succeeded; we may be activating for the first time
        # or acknowledging a successful subsequent cycle.
        if source == "pending":
            self.emit("subscription.activated", {
                "subscription_id": self._context.get("subscription_id"),
                "customer_id": self._context.get("customer_id"),
                "plan_id": self._context.get("plan_id"),
            })
        else:
            self.emit("subscription.cycle_completed", {
                "subscription_id": self._context.get("subscription_id"),
            })

    def on_enter_suspended(self) -> None:
        self.emit("subscription.suspended", {
            "subscription_id": self._context.get("subscription_id"),
        })
        if self._clock is None:
            return
        grace = float(self._context.get(
            "grace_period_seconds", _DEFAULT_GRACE_PERIOD_SECONDS,
        ))
        self.set_timer("subscription.grace_expired", grace, {
            "subscription_id": self._context.get("subscription_id"),
        })

    def on_enter_cancelled(self) -> None:
        self.emit("subscription.cancelled", {
            "subscription_id": self._context.get("subscription_id"),
            "reason": self._context.get("cancellation_reason", "user_request"),
        })

    def on_enter_churned(self) -> None:
        self.emit("subscription.churned", {
            "subscription_id": self._context.get("subscription_id"),
        })

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "retry.succeeded": "charge_succeeded",
            "retry.exhausted": "charge_exhausted",
            "subscription.grace_expired": "churn",
        }
