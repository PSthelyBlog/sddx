"""FakePaymentProvider — deterministic outbound payment adapter for simulation.

In production this would be a non-state-machine adapter calling Stripe or
similar. For simulation, we model it as a state machine so it participates
in the runner's reset/snapshot lifecycle and stays deterministic across runs.

The provider listens for ``retry.attempt_requested`` (emitted by every
PaymentAttempt's ``attempting`` state) and responds with either
``payment.captured`` or ``payment.declined``. Outcome is determined by an
``outcomes`` list in the provider's context: each request pops the head and
emits the corresponding event. If the list is empty the provider declines
(simulating "the upstream is unavailable").
"""

from sddx import State, StateMachine


class FakePaymentProvider(StateMachine):
    """Scriptable mock of an external payment processor.

    Context fields:
        outcomes (list[str]): scripted responses, popped FIFO. Each entry is
            either "succeed" or "decline". Default behavior on empty list:
            emit payment.declined with reason "provider_unavailable".
        served (int): number of requests handled so far. Mutated by the machine.
    """

    ready = State(initial=True)
    retired = State(final=True)

    process_request = ready.loop()
    retire = ready.to(retired)

    def on_transition_process_request(self) -> None:
        retry_id = self._context.get("retry_id")
        attempt = self._context.get("attempt")
        outcomes = self._context.setdefault("outcomes", [])
        served = self._context.get("served", 0) + 1
        self._context["served"] = served

        if not outcomes:
            self.emit("payment.declined", {
                "retry_id": retry_id,
                "attempt": attempt,
                "reason": "provider_unavailable",
            })
            return

        decision = outcomes.pop(0)
        if decision == "succeed":
            self.emit("payment.captured", {
                "retry_id": retry_id,
                "attempt": attempt,
                "transaction_id": f"txn_{served:06d}",
            })
        else:
            self.emit("payment.declined", {
                "retry_id": retry_id,
                "attempt": attempt,
                "reason": "card_declined",
            })

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "retry.attempt_requested": "process_request",
        }
