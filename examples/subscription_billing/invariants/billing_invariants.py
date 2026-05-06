"""Property invariants for the subscription_billing system.

Cross-machine assertions that must hold across every scenario.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sddx.events import EventLog


def invariant_active_implies_at_least_one_capture(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """A subscription in `active` must have produced at least one
    payment.captured event for one of its PaymentAttempts."""
    for (machine, instance), state in states.items():
        if machine != "SubscriptionLifecycle" or state != "active":
            continue
        captures_for_subscription = [
            e for e in log.filter(name="payment.captured")
            if e.payload.get("retry_id", "").startswith(f"{instance}:")
        ]
        assert captures_for_subscription, (
            f"Subscription {instance} is active but no payment.captured "
            f"event was recorded for any of its cycles"
        )


def invariant_suspended_implies_retry_exhausted(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """`suspended` should only follow a `retry.exhausted` event for one of
    the subscription's PaymentAttempts."""
    for (machine, instance), state in states.items():
        if machine != "SubscriptionLifecycle" or state != "suspended":
            continue
        exhausted_for_subscription = [
            e for e in log.filter(name="retry.exhausted")
            if e.payload.get("retry_id", "").startswith(f"{instance}:")
        ]
        assert exhausted_for_subscription, (
            f"Subscription {instance} is suspended but no retry.exhausted "
            f"event was recorded"
        )


def invariant_churned_implies_grace_expired(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """A churned subscription must have received a subscription.grace_expired
    event after entering suspended."""
    for (machine, instance), state in states.items():
        if machine != "SubscriptionLifecycle" or state != "churned":
            continue
        grace_events = list(log.filter(
            name="subscription.grace_expired", source_instance=instance,
        ))
        assert grace_events, (
            f"Subscription {instance} churned but no grace_expired event found"
        )


def invariant_cancelled_after_cancel_only(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """Cancelled subscriptions must have a subscription.cancelled event."""
    for (machine, instance), state in states.items():
        if machine != "SubscriptionLifecycle" or state != "cancelled":
            continue
        cancel_events = list(log.filter(
            name="subscription.cancelled", source_instance=instance,
        ))
        assert cancel_events, (
            f"Subscription {instance} is cancelled but no cancelled event found"
        )


def invariant_no_capture_after_subscription_final(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """Once a subscription emits cancelled or churned, no further
    payment.captured events for that subscription are valid."""
    for (machine, instance), state in states.items():
        if machine != "SubscriptionLifecycle":
            continue
        if state not in ("cancelled", "churned"):
            continue
        final_events = list(log.filter(source_instance=instance))
        if not final_events:
            continue
        final_time = max(e.timestamp for e in final_events)
        late_captures = [
            e for e in log.filter(name="payment.captured")
            if e.payload.get("retry_id", "").startswith(f"{instance}:")
            and e.timestamp > final_time
        ]
        assert not late_captures, (
            f"Subscription {instance} is {state} but {len(late_captures)} "
            f"payment.captured event(s) followed its final transition"
        )


def invariant_provider_serves_each_attempt_once(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """For each retry.attempt_requested, the provider must produce exactly
    one corresponding response event (captured or declined)."""
    requests = list(log.filter(name="retry.attempt_requested"))
    for req in requests:
        retry_id = req.payload.get("retry_id")
        attempt = req.payload.get("attempt")
        responses = [
            e for e in log
            if e.name in ("payment.captured", "payment.declined")
            and e.payload.get("retry_id") == retry_id
            and e.payload.get("attempt") == attempt
        ]
        assert len(responses) == 1, (
            f"retry.attempt_requested for {retry_id} attempt {attempt} "
            f"got {len(responses)} responses (expected 1)"
        )


def invariant_payment_attempt_count_matches_provider_served(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """The total number of retry.attempt_requested events should equal the
    number of provider responses (captured + declined)."""
    requests = len(list(log.filter(name="retry.attempt_requested")))
    captures = len(list(log.filter(name="payment.captured")))
    declines = len(list(log.filter(name="payment.declined")))
    assert requests == captures + declines, (
        f"{requests} attempts requested but {captures} captures + "
        f"{declines} declines = {captures + declines} responses"
    )


ALL_INVARIANTS = [
    invariant_active_implies_at_least_one_capture,
    invariant_suspended_implies_retry_exhausted,
    invariant_churned_implies_grace_expired,
    invariant_cancelled_after_cancel_only,
    invariant_no_capture_after_subscription_final,
    invariant_provider_serves_each_attempt_once,
    invariant_payment_attempt_count_matches_provider_served,
]
