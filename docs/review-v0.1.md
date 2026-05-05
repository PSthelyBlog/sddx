# Final Review — sddx v0.1

*Snapshot review of the v0.1 release. Read-only audit, no code changes.*

## Top-line health

- **Tests:** 49/49 passing in 0.03s
- **Example:** Calculator converges 14/14 scenarios, 9 invariants pass on each (126/126 invariant checks)
- **Git:** 56 files, 2 commits, pushed to https://github.com/PSthelyBlog/sddx
- **Docs:** 12 files, ~2,340 lines, complete cross-references, migration path documented
- **Backward compatibility:** Verified — `tests/test_protocol.py::test_hooks_without_source_param_still_work` confirms vanilla SDD machines run unchanged

## What's solid

1. **Every claimed enhancement is demonstrated by code + tests.** `.loop()`, source-aware hooks, per-source guards, pattern subscriptions, telemetry events, virtual clock, `set_timer`, runner snapshot/restore, working `advance_time`, working context assertions, the `python -m sddx` CLI — each has dedicated tests AND is exercised by the calculator example.
2. **The class-identity bug found during integration is fixed.** The CLI prepends project root to `sys.path` and uses natural package imports so `from machines.X import X` resolves to the same class as auto-discovery loaded. This was the kind of subtle bug that would have wasted hours; it's now under test.
3. **Docs match code.** Spot-checked: every documented method exists with the documented signature; the YAML grammar in `08-scenario-language.md` matches the parser in `scenario.py`.
4. **Honest scope boundaries.** `README.md` and the architecture docs flag what's deferred to v0.2 (Pydantic schemas, more stdlib, sdd_agent) consistently — no hand-waving.

## Design observations worth flagging

1. **`Retry.on_enter_backing_off` has dead code.** `delay = base * (2 ** (attempts - 2)) if attempts > 1 else base` — by the time this hook fires, `on_transition_record_failure` has already incremented `attempts` to ≥2, so the `else base` branch never executes. Current behavior is correct, but the conditional is misleading. Worth simplifying when v0.2 touches `std/`.
2. **`advance_time` collapses timestamps.** Every event fired during a single `advance(seconds)` call gets `timestamp = clock.now()` (the post-advance value), not its individual deadline. Relative ordering is preserved (the clock sorts by deadline before firing), but absolute deadlines are lost in the event log. Acceptable for most uses; if you need precise deadlines for analysis, this matters.
3. **`Barrier` requires explicit `start()`.** The `pending → waiting` transition exists because `on_enter` doesn't fire on the initial state — but a user who instantiates a `Barrier` and forgets to fire `start()` will silently sit in `pending` forever. Documented in `09-stdlib.md` but easy to miss. A factory helper (`Barrier.create(runner, ...)` that creates and starts in one call) would be friendlier.
4. **Dead-letter pattern detection assumes `*`.** `runner.dead_letters()` filters subscribed patterns with `"*" in s` to identify them as patterns. Patterns using only `?` or `[seq]` (rare in practice) would be matched as exact event names. Minor edge.
5. **Snapshot doesn't preserve subscriptions.** Documented as intentional, but if a user serializes a runner, deserializes into a runner with *different* registrations, behavior diverges silently. A snapshot-version check or registration-fingerprint would catch this — currently absent.

## Test coverage gaps

Honest gaps for a v0.1:

- `runner.snapshot()/restore()` round-trips clock state, but the test only checks instance state and event log presence, not specifically `clock.now()` or pending-timer preservation.
- `on_exit_*` source/target injection isn't tested directly. Only `on_enter` and `on_transition` are verified to receive them.
- Telemetry-marked events: there's a test confirming they're excluded from dead-letter warnings, but none verifying they're still deliverable to subscribers when a subscription does exist.
- The documented kwarg/context collision footgun has no test.
- No test exercising near-max cascade depth (the test uses `max_cascade_depth=3` and confirms `RuntimeError`, but doesn't verify the bus recovers cleanly afterward — the `_delivering` flag and queue should reset, but it's untested).
- No test for `runner.register()` called twice with the same class (would produce duplicate subscriptions).

None of these are blockers. They're the second pass before v1.0.

## Loose ends

- **`LICENSE` is still `TBD — fill in your chosen license here.`** The repo is public on GitHub, so anyone forking sees the placeholder. Intentional per repo setup, but flagging for visibility.
- **No CHANGELOG, no CONTRIBUTING, no CI config.** Standard for v0.1; not accidentally missing.
- **`pyproject.toml` declares the `sddx` console script** but the wheel hasn't been built or published. Currently the framework runs from source (`PYTHONPATH=src`). Standard for early-stage; flag for when you decide to publish to PyPI.

## What's surprising in a good way

- The calculator example demonstrates the bidirectional event pattern (`MR` round-trip: Calculator emits → MemoryRegister → Calculator) end-to-end with no direct references between machines. Strongest validation of the events-only-coupling principle.
- The migration guide is genuinely useful — the `diff` blocks make the conversion concrete and quick to skim.
- 49 tests in 30ms. The framework is fast.

## Recommendation

**Ship as v0.1.** The surface is coherent, the tests are honest, the example is real, and the docs stand alone. The known gaps are the kind that get filled in by actual users hitting the seams, which is where the framework will earn its design choices.

**Before v1.0**, in priority order:

1. Pick up the test coverage gaps above (especially clock snapshot/restore and the cascade-recovery edge).
2. Decide on Pydantic schemas vs. a lighter alternative for v0.2; that decision shapes the rest of the v0.2 surface.
3. Fix the `LICENSE`.
4. Prove the agent angle. Build a small `sdd_agent` package against an LM Studio instance — even a minimal `AgentSession + LLMRequest + ToolCall` slice. That's the validation step for whether the framework's bones are right for that workload, and it's the cheapest way to find missing primitives before they're frozen.
5. Decide on a release strategy (PyPI? leave as source-install? semver discipline?).

The framework is in good shape. Ship it.
