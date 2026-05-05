# Introduction

This document is the entry point to sddx. It explains what the framework is, why it works the way it does, and how to navigate the rest of the documentation. Read this first.

## What sddx is

Simulation-Driven Development, eXtended (sddx) is a way of building systems where the production core is a set of state machines, verified through simulation before any infrastructure exists. You decompose a problem into domains, model each as a state machine, run scenarios against them, and only connect databases, HTTP endpoints, and queues once the core converges. The machines that come out of simulation are the machines that ship.

The shift from conventional development is this: domain logic is no longer scattered across controllers, services, and handlers, and discovered through bug reports. It is concentrated in states, transitions, guards, and events, and discovered through scenarios you can rerun.

sddx is a backward-compatible superset of the original SDD framework. The methodology is identical. The implementation surface is cleaner: real timer support, snapshot/restore at the runner level, source-aware hooks, per-source guards, pattern subscriptions, telemetry-marked events, and a built-in CLI. Existing SDD machines run unchanged.

## A common ground between humans and LLMs

sddx is built around artifacts that are equally legible to both parties:

- **Scenarios** (YAML) read as specs for humans and as executable prompts for LLMs.
- **State machines** are a formalism humans recognize from statecharts and UML, and LLMs can generate from a brief.
- **Event logs** trace causality in a form either side can diagnose.
- **Convergence criteria** replace subjective code review with objective pass/fail signals.

Neither party has a privileged view. A human reviewing an LLM-authored machine sees the same structural report the runner produces. An LLM iterating on a failing scenario reads the same `StepResult` a human would. Collaboration happens at the artifact level — not through prose, intent, or trust.

This is why the framework is unusually well-suited to LLM authorship without being defined by it. The same protocol that lets you safely dispatch subagents to write machines in parallel also lets you read, modify, and ship that code yourself.

## A worked example, in miniature

Suppose the task is *"handle order checkout with payment retries."*

1. **Decompose.** Two domains surface: `OrderLifecycle` (created → paid → fulfilled, with cancelled/failed branches) and `PaymentAttempt` (pending → succeeded/failed, with retry).
2. **Implement.** Each machine is a Python class declaring its states, transitions, guards, and the events it emits and consumes. No imports beyond `sddx` and the standard library.
3. **Simulate.** Write scenarios in YAML — *"customer pays on first try"*, *"first payment fails, second succeeds"*, *"payment fails three times"*. Run them through `python -m sddx run`.
4. **Converge.** The runner reports structural gaps (a state with no exit), scenario failures (a guard rejected a step), and invariant violations (an order paid twice). Fix and rerun.
5. **Adapt.** Once all three layers pass, write thin adapters: an HTTP endpoint that calls `OrderLifecycle.fire("checkout")`, a Stripe adapter that listens for `payment.attempted` events, a persistence adapter using `snapshot()`/`restore()`.

The machines do not change between step 4 and production. That guarantee is what the rest of the documentation defends.

## How to read the rest of the docs

The numbered docs build on each other. Read in order if you are new:

1. **[01-architecture](01-architecture.md)** — the three layers (core, inbound, outbound) and what belongs where.
2. **[02-state-machine-protocol](02-state-machine-protocol.md)** — the contract every machine must satisfy. Required before writing or reviewing any machine.
3. **[03-event-system](03-event-system.md)** — how machines communicate. Required before designing inter-machine flow.
4. **[04-simulation-runner](04-simulation-runner.md)** — how to operate machines and observe their behavior, including timer advancement and snapshot/restore.
5. **[05-convergence-criteria](05-convergence-criteria.md)** — what "correct" means in sddx: structural, scenario, invariant.
6. **[06-io-adapters](06-io-adapters.md)** — only after convergence: how to connect to the real world.
7. **[07-workflow-guide](07-workflow-guide.md)** — the end-to-end process, including subagent dispatch.
8. **[08-scenario-language](08-scenario-language.md)** — the YAML grammar for scenarios.
9. **[09-stdlib](09-stdlib.md)** — opt-in machines for common patterns: `Barrier`, `Retry`.
10. **[10-cli](10-cli.md)** — the `python -m sddx` reference.
11. **[migration](migration.md)** — porting a project from vanilla SDD.

Shortcuts by task:

- **Diagnosing a failing scenario** → start at **04** for the runner's output, then **05** for what each layer reports.
- **Reviewing LLM-generated machine code** → **02** is the protocol checklist; **05** is the mechanical correctness signal.
- **Connecting a finished core to a new piece of infrastructure** → **06** alone, assuming convergence already passed.
- **Briefing a subagent to author a machine** → **07** for the brief structure, then **02** for the contract you are holding them to.
- **Adding fan-in or retry logic** → **09** before rolling your own.

## The non-negotiables

A few rules cannot be relaxed without breaking the framework. They are worth seeing up front:

- **Zero infrastructure imports in machines.** A machine module imports only `sddx` and the standard library.
- **Events are the only inter-machine coupling.** Machines never reference each other directly.
- **Failures are explicit states.** Error handling is modeled, not caught.
- **Adapters contain no domain logic.** An `if` evaluating a business rule outside `machines/` is in the wrong place.
- **Convergence before adaptation.** No adapter is written until the three convergence layers pass.

These constraints are what make the artifacts exchangeable between humans and LLMs. Loosen them and the common ground disappears.
