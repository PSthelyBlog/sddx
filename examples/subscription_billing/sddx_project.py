"""Project config for `python -m sddx`.

Wires resolvers between events and machine instances.

Resolution rules:
  - SubscriptionLifecycle resolves to the subscription_id either directly
    in payload or extracted from a "<subscription_id>:cycle<n>" retry_id.
  - PaymentAttempt resolves to its retry_id (always present in retry.* and
    payment.* event payloads).
  - FakePaymentProvider has a single shared instance "provider".
  - OperationLog resolves to the subscription_id (one log per subscription).
"""

from machines.fake_payment_provider import FakePaymentProvider
from machines.operation_log import OperationLog
from machines.payment_attempt import PaymentAttempt
from machines.subscription_lifecycle import SubscriptionLifecycle


def _subscription_id(event) -> str:
    """Extract a subscription id from any event the SubscriptionLifecycle
    or OperationLog might receive."""
    payload = event.payload
    if "subscription_id" in payload:
        return payload["subscription_id"]
    retry_id = payload.get("retry_id", "")
    if ":" in retry_id:
        return retry_id.split(":", 1)[0]
    return retry_id


def register_resolvers(runner) -> None:
    runner.register_resolver(SubscriptionLifecycle, _subscription_id)
    runner.register_resolver(OperationLog, _subscription_id)
    runner.register_resolver(
        PaymentAttempt, lambda e: e.payload.get("retry_id", ""),
    )
    runner.register_resolver(FakePaymentProvider, lambda e: "provider")
