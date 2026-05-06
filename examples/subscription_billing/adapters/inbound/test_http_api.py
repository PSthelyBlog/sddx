"""Tests for the HTTP adapter's pure handler functions.

We don't go through the http.server wrapper here — that's mechanical
plumbing. These tests verify that the handlers correctly translate
parsed-request payloads into runner.fire calls and produce the right
response shapes and status codes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Path setup so the test runs from anywhere.
THIS_FILE = Path(__file__).resolve()
EXAMPLE_ROOT = THIS_FILE.parent.parent.parent
sys.path.insert(0, str(EXAMPLE_ROOT))
sys.path.insert(0, str(EXAMPLE_ROOT.parent.parent / "src"))

from adapters.inbound.http_api import (  # noqa: E402
    advance_time,
    build_runner,
    cancel_subscription,
    create_subscription,
    get_clock,
    get_subscription,
    list_events,
    list_subscriptions,
    start_cycle,
)
from machines.subscription_lifecycle import SubscriptionLifecycle  # noqa: E402


@pytest.fixture
def runner():
    return build_runner()


def test_list_subscriptions_empty(runner):
    status, body = list_subscriptions(runner)
    assert status == 200
    assert body == {"subscriptions": []}


def test_create_subscription_succeeds_and_activates(runner):
    status, body = create_subscription(runner, {
        "subscription_id": "sub_1",
        "customer_id": "cust_1",
        "plan_id": "pro",
        "amount": 19.99,
        "outcomes": ["succeed"],
    })
    assert status == 201
    assert body["subscription_id"] == "sub_1"
    assert body["state"] == "active"
    assert body["cycle_state"] == "succeeded"


def test_create_subscription_requires_id(runner):
    status, body = create_subscription(runner, {})
    assert status == 400
    assert "subscription_id" in body["error"]


def test_create_subscription_rejects_duplicate(runner):
    create_subscription(runner, {"subscription_id": "s", "outcomes": ["succeed"]})
    status, body = create_subscription(runner, {"subscription_id": "s"})
    assert status == 409


def test_get_subscription_404_for_missing(runner):
    status, _ = get_subscription(runner, "nope")
    assert status == 404


def test_get_subscription_returns_state_and_cycles(runner):
    create_subscription(runner, {
        "subscription_id": "s", "outcomes": ["succeed"],
    })
    status, body = get_subscription(runner, "s")
    assert status == 200
    assert body["state"] == "active"
    assert len(body["cycles"]) == 1
    assert body["cycles"][0]["state"] == "succeeded"


def test_cancel_subscription_transitions_to_cancelled(runner):
    create_subscription(runner, {
        "subscription_id": "s", "outcomes": ["succeed"],
    })
    status, body = cancel_subscription(runner, "s", {"reason": "churned"})
    assert status == 200
    assert body["state"] == "cancelled"


def test_cancel_subscription_404_for_missing(runner):
    status, _ = cancel_subscription(runner, "nope", {})
    assert status == 404


def test_start_cycle_increments_cycle_id(runner):
    create_subscription(runner, {
        "subscription_id": "s", "outcomes": ["succeed"],
    })
    status, body = start_cycle(runner, "s", {"outcomes": ["succeed"]})
    assert status == 202
    assert body["cycle_id"] == "s:cycle2"
    assert body["cycle_state"] == "succeeded"

    status, body = start_cycle(runner, "s", {"outcomes": ["succeed"]})
    assert body["cycle_id"] == "s:cycle3"


def test_start_cycle_rejects_final_subscription(runner):
    create_subscription(runner, {
        "subscription_id": "s", "outcomes": ["succeed"],
    })
    cancel_subscription(runner, "s", {})
    status, body = start_cycle(runner, "s", {"outcomes": ["succeed"]})
    assert status == 409
    assert "final state" in body["error"]


def test_advance_time_drives_retry_exhaustion(runner):
    """End-to-end: bad outcomes + advance_time → retry exhausts → suspended."""
    create_subscription(runner, {
        "subscription_id": "s",
        "outcomes": ["decline", "decline", "decline"],
        "max_attempts": 3,
        "base_delay": 60.0,
    })
    sub = runner.get(SubscriptionLifecycle, "s")
    assert sub.current_state == "pending"   # first attempt declined; retrying

    status, body = advance_time(runner, {"seconds": 61})
    assert status == 200
    assert sub.current_state == "pending"   # second attempt declined; retrying

    advance_time(runner, {"seconds": 121})
    assert sub.current_state == "suspended"


def test_advance_time_rejects_negative(runner):
    status, _ = advance_time(runner, {"seconds": -1})
    assert status == 400


def test_list_events_filters_by_since(runner):
    create_subscription(runner, {"subscription_id": "s", "outcomes": ["succeed"]})
    status, body = list_events(runner, {})
    assert status == 200
    total_events = len(body["events"])
    assert total_events > 0

    # Use the timestamp of the last event to filter.
    last_ts = body["events"][-1]["timestamp"]
    status, body = list_events(runner, {"since": str(last_ts)})
    assert status == 200
    assert body["events"] == []


def test_get_clock_reports_pending_timers_after_suspension(runner):
    create_subscription(runner, {
        "subscription_id": "s",
        "outcomes": ["decline", "decline", "decline"],
        "base_delay": 60.0,
    })
    advance_time(runner, {"seconds": 61})
    advance_time(runner, {"seconds": 121})
    status, body = get_clock(runner)
    assert status == 200
    assert body["now"] > 0
    pending = [t["event_name"] for t in body["pending_timers"]]
    assert "subscription.grace_expired" in pending
