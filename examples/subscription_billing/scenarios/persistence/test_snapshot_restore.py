"""Snapshot/restore exercise for the subscription_billing example.

Demonstrates that a long-running subscription session can be paused and
resumed across process boundaries without losing state. This is a Python
test rather than a YAML scenario because snapshot/restore is a
runner-level operation, not a step in the scenario grammar.

Run with:
    PYTHONPATH=src:examples/subscription_billing pytest examples/subscription_billing/scenarios/persistence/

or as a one-shot:
    PYTHONPATH=src:examples/subscription_billing python examples/subscription_billing/scenarios/persistence/test_snapshot_restore.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow direct invocation: add sddx src and the example root to sys.path.
EXAMPLE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(EXAMPLE_ROOT))
sys.path.insert(0, str(EXAMPLE_ROOT.parent.parent / "src"))

from sddx import SimulationRunner  # noqa: E402

from machines.fake_payment_provider import FakePaymentProvider  # noqa: E402
from machines.operation_log import OperationLog  # noqa: E402
from machines.payment_attempt import PaymentAttempt  # noqa: E402
from machines.subscription_lifecycle import SubscriptionLifecycle  # noqa: E402
from sddx_project import register_resolvers  # noqa: E402


def _build_runner() -> SimulationRunner:
    runner = SimulationRunner()
    runner.register(SubscriptionLifecycle)
    runner.register(PaymentAttempt)
    runner.register(FakePaymentProvider)
    runner.register(OperationLog)
    register_resolvers(runner)
    return runner


def _make_subscription(runner: SimulationRunner) -> None:
    runner.create(SubscriptionLifecycle, "sub_1", {
        "subscription_id": "sub_1",
        "customer_id": "cust_1",
        "plan_id": "pro_monthly",
        "amount": 29.99,
        "billing_period_days": 30,
        "grace_period_seconds": 604800,
    })
    runner.create(OperationLog, "sub_1", {
        "subscription_id": "sub_1",
        "entries": [],
    })
    runner.create(FakePaymentProvider, "provider", {
        "outcomes": ["succeed", "succeed"],
        "served": 0,
    })
    runner.create(PaymentAttempt, "sub_1:cycle1", {
        "retry_id": "sub_1:cycle1",
        "subscription_id": "sub_1",
        "cycle": 1,
        "amount": 29.99,
        "max_attempts": 3,
        "base_delay": 60.0,
        "attempts": 0,
    })


def test_snapshot_restore_preserves_active_subscription():
    """Activate, snapshot, restore on a fresh runner, run a second cycle."""
    runner = _build_runner()
    _make_subscription(runner)
    runner.fire("sub_1:cycle1", PaymentAttempt, "start")

    sub = runner.get(SubscriptionLifecycle, "sub_1")
    log = runner.get(OperationLog, "sub_1")
    provider = runner.get(FakePaymentProvider, "provider")
    assert sub.current_state == "active"
    assert provider.context["served"] == 1
    entries_before = len(log.context["entries"])
    assert entries_before > 0

    snap = runner.snapshot()

    # Fresh runner — same registrations, no instances.
    fresh = _build_runner()
    fresh.restore(snap)

    fresh_sub = fresh.get(SubscriptionLifecycle, "sub_1")
    fresh_log = fresh.get(OperationLog, "sub_1")
    fresh_provider = fresh.get(FakePaymentProvider, "provider")
    assert fresh_sub.current_state == "active"
    assert fresh_provider.context["served"] == 1
    assert len(fresh_log.context["entries"]) == entries_before
    assert len(fresh.event_log) == len(runner.event_log)

    # Continue the simulation in the fresh runner — start cycle 2.
    fresh.create(PaymentAttempt, "sub_1:cycle2", {
        "retry_id": "sub_1:cycle2",
        "subscription_id": "sub_1",
        "cycle": 2,
        "amount": 29.99,
        "max_attempts": 3,
        "base_delay": 60.0,
        "attempts": 0,
    })
    fresh.fire("sub_1:cycle2", PaymentAttempt, "start")

    cycle2 = fresh.get(PaymentAttempt, "sub_1:cycle2")
    assert cycle2.current_state == "succeeded"
    assert fresh_sub.current_state == "active"
    assert fresh_provider.context["served"] == 2

    # Subscription emitted cycle_completed for the second cycle.
    cycle_completed = list(fresh.event_log.filter(name="subscription.cycle_completed"))
    assert len(cycle_completed) == 1


def test_snapshot_restore_preserves_pending_grace_timer():
    """Suspended subscription's grace timer survives snapshot/restore."""
    runner = _build_runner()
    _make_subscription(runner)
    # Override outcomes to force exhaustion.
    provider = runner.get(FakePaymentProvider, "provider")
    provider._context["outcomes"] = ["decline", "decline", "decline"]

    runner.fire("sub_1:cycle1", PaymentAttempt, "start")
    runner.advance_time(61)
    runner.advance_time(121)

    sub = runner.get(SubscriptionLifecycle, "sub_1")
    assert sub.current_state == "suspended"
    pending_timers_before = len(runner.clock.pending())
    assert pending_timers_before == 1   # the grace timer is queued
    clock_now_before = runner.clock.now()

    snap = runner.snapshot()

    fresh = _build_runner()
    fresh.restore(snap)

    assert fresh.clock.now() == clock_now_before
    assert len(fresh.clock.pending()) == 1
    assert fresh.get(SubscriptionLifecycle, "sub_1").current_state == "suspended"

    # Advance the grace period in the fresh runner — should fire the timer.
    fresh.advance_time(7 * 86400)
    assert fresh.get(SubscriptionLifecycle, "sub_1").current_state == "churned"


if __name__ == "__main__":
    test_snapshot_restore_preserves_active_subscription()
    print("test_snapshot_restore_preserves_active_subscription: PASS")
    test_snapshot_restore_preserves_pending_grace_timer()
    print("test_snapshot_restore_preserves_pending_grace_timer: PASS")
