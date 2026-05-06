"""HTTP inbound adapter for the subscription_billing example.

Two layers:

1. **Pure handler functions** that take a ``runner`` and a ``payload`` dict
   and return ``(status_code, body_dict)``. These contain the
   adapter-level translation (extract id, fire transition, build response).
   They are tested directly without going through HTTP.

2. **A thin ``http.server`` wrapper** that translates HTTP requests into
   handler calls and back. No domain logic; just plumbing.

Run as a script::

    PYTHONPATH=src:examples/subscription_billing \\
        python examples/subscription_billing/adapters/inbound/http_api.py

Default port is 8080. Open http://localhost:8080/ for the demo UI.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Path setup so this file can be run directly.
_THIS_FILE = Path(__file__).resolve()
EXAMPLE_ROOT = _THIS_FILE.parent.parent.parent
SDDX_ROOT = EXAMPLE_ROOT.parent.parent
sys.path.insert(0, str(EXAMPLE_ROOT))
sys.path.insert(0, str(SDDX_ROOT / "src"))

from sddx import SimulationRunner  # noqa: E402

from machines.fake_payment_provider import FakePaymentProvider  # noqa: E402
from machines.operation_log import OperationLog  # noqa: E402
from machines.payment_attempt import PaymentAttempt  # noqa: E402
from machines.subscription_lifecycle import SubscriptionLifecycle  # noqa: E402
from sddx_project import register_resolvers  # noqa: E402


# --- Runner construction --------------------------------------------------

def build_runner() -> SimulationRunner:
    """Build a fresh runner with all machines registered.

    The single shared ``FakePaymentProvider("provider")`` is created here
    so subsequent transitions can find it via the resolver.
    """
    runner = SimulationRunner()
    runner.register(SubscriptionLifecycle)
    runner.register(PaymentAttempt)
    runner.register(FakePaymentProvider)
    runner.register(OperationLog)
    register_resolvers(runner)
    runner.create(FakePaymentProvider, "provider", {
        "outcomes": [],
        "served": 0,
    })
    return runner


# --- Pure handler functions -----------------------------------------------
#
# Each handler takes ``(runner, payload, path_params)`` and returns
# ``(status_code, body)``. They never raise on bad input; instead they
# return 4xx with a structured error.

def _err(status: int, message: str, **extra) -> tuple[int, dict]:
    return status, {"error": message, **extra}


def list_subscriptions(runner: SimulationRunner) -> tuple[int, dict]:
    """GET /subscriptions — return all SubscriptionLifecycle instances."""
    out = []
    for (cls, instance_id), state in runner.machine_states().items():
        if cls is SubscriptionLifecycle:
            instance = runner.get(cls, instance_id)
            ctx = instance.context
            cycles = [
                cycle_id for (other_cls, cycle_id) in runner.machine_states()
                if other_cls is PaymentAttempt
                and cycle_id.startswith(f"{instance_id}:cycle")
            ]
            out.append({
                "subscription_id": instance_id,
                "state": state,
                "customer_id": ctx.get("customer_id"),
                "plan_id": ctx.get("plan_id"),
                "amount": ctx.get("amount"),
                "cycles_started": len(cycles),
            })
    out.sort(key=lambda s: s["subscription_id"])
    return 200, {"subscriptions": out}


def create_subscription(
    runner: SimulationRunner, payload: dict
) -> tuple[int, dict]:
    """POST /subscriptions — create a subscription and run its first charge."""
    sub_id = payload.get("subscription_id")
    if not sub_id or not isinstance(sub_id, str):
        return _err(400, "subscription_id is required")
    if runner.get(SubscriptionLifecycle, sub_id) is not None:
        return _err(409, f"subscription {sub_id!r} already exists")

    customer_id = payload.get("customer_id", "anonymous")
    plan_id = payload.get("plan_id", "default")
    amount = float(payload.get("amount", 29.99))
    grace_seconds = float(payload.get("grace_period_seconds", 7 * 86400))
    outcomes = payload.get("outcomes", ["succeed"])
    if not isinstance(outcomes, list):
        return _err(400, "outcomes must be a list")

    runner.create(SubscriptionLifecycle, sub_id, {
        "subscription_id": sub_id,
        "customer_id": customer_id,
        "plan_id": plan_id,
        "amount": amount,
        "billing_period_days": 30,
        "grace_period_seconds": grace_seconds,
    })
    runner.create(OperationLog, sub_id, {
        "subscription_id": sub_id,
        "entries": [],
    })

    # Append the user-specified outcomes for the first cycle to the provider.
    provider = runner.get(FakePaymentProvider, "provider")
    provider._context.setdefault("outcomes", []).extend(outcomes)

    cycle_id = f"{sub_id}:cycle1"
    runner.create(PaymentAttempt, cycle_id, {
        "retry_id": cycle_id,
        "subscription_id": sub_id,
        "cycle": 1,
        "amount": amount,
        "max_attempts": int(payload.get("max_attempts", 3)),
        "base_delay": float(payload.get("base_delay", 60.0)),
        "attempts": 0,
    })
    runner.fire(cycle_id, PaymentAttempt, "start")

    sub = runner.get(SubscriptionLifecycle, sub_id)
    return 201, {
        "subscription_id": sub_id,
        "state": sub.current_state,
        "cycle_id": cycle_id,
        "cycle_state": runner.get(PaymentAttempt, cycle_id).current_state,
    }


def get_subscription(
    runner: SimulationRunner, sub_id: str
) -> tuple[int, dict]:
    """GET /subscriptions/{id}."""
    sub = runner.get(SubscriptionLifecycle, sub_id)
    if sub is None:
        return _err(404, f"subscription {sub_id!r} not found")
    cycles = []
    for (cls, iid), state in runner.machine_states().items():
        if cls is PaymentAttempt and iid.startswith(f"{sub_id}:cycle"):
            instance = runner.get(cls, iid)
            cycles.append({
                "cycle_id": iid,
                "state": state,
                "attempts": instance.context.get("attempts"),
            })
    cycles.sort(key=lambda c: c["cycle_id"])
    log_state = "absent"
    log = runner.get(OperationLog, sub_id)
    if log is not None:
        log_state = log.current_state
    return 200, {
        "subscription_id": sub_id,
        "state": sub.current_state,
        "context": sub.context,
        "cycles": cycles,
        "log_state": log_state,
    }


def cancel_subscription(
    runner: SimulationRunner, sub_id: str, payload: dict
) -> tuple[int, dict]:
    """DELETE /subscriptions/{id}."""
    sub = runner.get(SubscriptionLifecycle, sub_id)
    if sub is None:
        return _err(404, f"subscription {sub_id!r} not found")
    reason = payload.get("reason", "user_request") if payload else "user_request"
    result = runner.fire(
        sub_id, SubscriptionLifecycle, "cancel",
        cancellation_reason=reason,
    )
    if result.errors:
        return _err(409, result.errors[0].message)
    return 200, {
        "subscription_id": sub_id,
        "state": sub.current_state,
    }


def start_cycle(
    runner: SimulationRunner, sub_id: str, payload: dict
) -> tuple[int, dict]:
    """POST /subscriptions/{id}/cycles — kick off the next billing cycle."""
    sub = runner.get(SubscriptionLifecycle, sub_id)
    if sub is None:
        return _err(404, f"subscription {sub_id!r} not found")
    if sub.is_final:
        return _err(409, f"subscription is in final state {sub.current_state!r}")

    existing = [
        int(iid.split(":cycle", 1)[1])
        for (cls, iid) in runner.machine_states()
        if cls is PaymentAttempt and iid.startswith(f"{sub_id}:cycle")
    ]
    next_n = max(existing, default=0) + 1
    cycle_id = f"{sub_id}:cycle{next_n}"

    outcomes = payload.get("outcomes", ["succeed"]) if payload else ["succeed"]
    if not isinstance(outcomes, list):
        return _err(400, "outcomes must be a list")
    provider = runner.get(FakePaymentProvider, "provider")
    provider._context.setdefault("outcomes", []).extend(outcomes)

    runner.create(PaymentAttempt, cycle_id, {
        "retry_id": cycle_id,
        "subscription_id": sub_id,
        "cycle": next_n,
        "amount": sub.context.get("amount", 29.99),
        "max_attempts": int(payload.get("max_attempts", 3)) if payload else 3,
        "base_delay": float(payload.get("base_delay", 60.0)) if payload else 60.0,
        "attempts": 0,
    })
    runner.fire(cycle_id, PaymentAttempt, "start")
    return 202, {
        "subscription_id": sub_id,
        "cycle_id": cycle_id,
        "cycle_state": runner.get(PaymentAttempt, cycle_id).current_state,
        "subscription_state": sub.current_state,
    }


def list_events(
    runner: SimulationRunner, query: dict
) -> tuple[int, dict]:
    """GET /events?since=N&limit=M — recent events."""
    try:
        since = float(query.get("since", 0.0)) if "since" in query else None
    except (TypeError, ValueError):
        return _err(400, "since must be numeric")
    try:
        limit = int(query.get("limit", 100))
    except (TypeError, ValueError):
        return _err(400, "limit must be an integer")

    events = list(runner.event_log)
    if since is not None:
        events = [e for e in events if e.timestamp > since]
    events = events[-limit:]
    return 200, {
        "events": [
            {
                "name": e.name,
                "payload": e.payload,
                "source_machine": e.source_machine,
                "source_instance": e.source_instance,
                "timestamp": e.timestamp,
            }
            for e in events
        ],
        "current_time": runner.clock.now(),
    }


def get_clock(runner: SimulationRunner) -> tuple[int, dict]:
    """GET /clock — virtual clock state."""
    pending = runner.clock.pending()
    return 200, {
        "now": runner.clock.now(),
        "pending_timers": [
            {
                "deadline": s.deadline,
                "event_name": s.event_name,
                "source_instance": s.source_instance,
            }
            for s in pending
        ],
    }


def advance_time(
    runner: SimulationRunner, payload: dict
) -> tuple[int, dict]:
    """POST /admin/advance_time — drive the virtual clock forward."""
    try:
        seconds = float(payload.get("seconds", 0))
    except (TypeError, ValueError):
        return _err(400, "seconds must be numeric")
    if seconds < 0:
        return _err(400, "seconds must be non-negative")
    fired = runner.advance_time(seconds)
    return 200, {
        "advanced_by": seconds,
        "now": runner.clock.now(),
        "events_fired": [{"name": e.name, "timestamp": e.timestamp} for e in fired],
    }


# --- HTTP server wrapper --------------------------------------------------

# Routes: (method, regex, handler-name). Handler-name is dispatched in do_*.
_ROUTES: list[tuple[str, re.Pattern, str]] = [
    ("GET",    re.compile(r"^/$"),                     "ui_index"),
    ("GET",    re.compile(r"^/static/(?P<path>.+)$"),  "ui_static"),
    ("GET",    re.compile(r"^/subscriptions$"),        "list_subs"),
    ("POST",   re.compile(r"^/subscriptions$"),        "create_sub"),
    ("GET",    re.compile(r"^/subscriptions/(?P<sid>[^/]+)$"),         "get_sub"),
    ("DELETE", re.compile(r"^/subscriptions/(?P<sid>[^/]+)$"),         "cancel_sub"),
    ("POST",   re.compile(r"^/subscriptions/(?P<sid>[^/]+)/cycles$"),  "start_cyc"),
    ("GET",    re.compile(r"^/events$"),               "events"),
    ("GET",    re.compile(r"^/clock$"),                "clock"),
    ("POST",   re.compile(r"^/admin/advance_time$"),   "advance"),
]

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
}


class BillingHTTPHandler(BaseHTTPRequestHandler):
    """http.server adapter — translates HTTP to pure-handler calls."""

    runner: SimulationRunner   # set by the server class

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Quieter logs.
        sys.stderr.write(f"[http] {format % args}\n")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urllib.parse.urlparse(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        flat_query = {k: v[0] for k, v in query.items() if v}

        for route_method, pattern, name in _ROUTES:
            if route_method != method:
                continue
            match = pattern.match(path)
            if not match:
                continue
            return self._handle(name, match.groupdict(), flat_query)

        self._send_json(404, {"error": f"no route for {method} {path}"})

    def _handle(self, name: str, params: dict, query: dict) -> None:
        if name == "ui_index":
            return self._serve_static("index.html")
        if name == "ui_static":
            return self._serve_static(params["path"])

        body = self._read_json_body()
        if isinstance(body, tuple):  # error
            return self._send_json(*body)

        if name == "list_subs":
            return self._send_json(*list_subscriptions(self.runner))
        if name == "create_sub":
            return self._send_json(*create_subscription(self.runner, body))
        if name == "get_sub":
            return self._send_json(*get_subscription(self.runner, params["sid"]))
        if name == "cancel_sub":
            return self._send_json(*cancel_subscription(self.runner, params["sid"], body))
        if name == "start_cyc":
            return self._send_json(*start_cycle(self.runner, params["sid"], body))
        if name == "events":
            return self._send_json(*list_events(self.runner, query))
        if name == "clock":
            return self._send_json(*get_clock(self.runner))
        if name == "advance":
            return self._send_json(*advance_time(self.runner, body))

        self._send_json(500, {"error": f"unrouted handler {name!r}"})

    def _read_json_body(self) -> dict | tuple[int, dict]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return (400, {"error": f"invalid JSON: {e}"})
        if not isinstance(data, dict):
            return (400, {"error": "request body must be a JSON object"})
        return data

    def _send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _serve_static(self, rel_path: str) -> None:
        # Prevent directory traversal.
        safe = (_STATIC_DIR / rel_path).resolve()
        if _STATIC_DIR not in safe.parents and safe != _STATIC_DIR:
            return self._send_json(403, {"error": "forbidden"})
        if not safe.is_file():
            return self._send_json(404, {"error": f"not found: {rel_path}"})
        if safe.suffix not in _MIME_TYPES:
            return self._send_json(415, {"error": f"unsupported type {safe.suffix}"})
        body = safe.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _MIME_TYPES[safe.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str = "127.0.0.1", port: int = 8080) -> HTTPServer:
    """Build an HTTP server bound to a fresh runner."""
    runner = build_runner()

    class _BoundHandler(BillingHTTPHandler):
        pass
    _BoundHandler.runner = runner

    return HTTPServer((host, port), _BoundHandler)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="sddx subscription billing demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    server = make_server(args.host, args.port)
    print(f"Demo running at http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
