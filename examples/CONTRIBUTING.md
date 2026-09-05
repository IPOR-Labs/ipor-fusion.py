# Writing an example for `examples`

Rules for adding or editing an example in this directory. This is scoped to *authoring examples* --
it is not the repository-wide contribution process. Read it before you write a new example or change
an existing one.

The current canonical example, [`simple_aave_v3_supply_base.py`](simple_aave_v3_supply_base.py), is
the worked reference for everything below: match its structure, its documentation density, and its
safety posture.

## 1. What belongs here: one-shot, not long-running

An example is a script you run **once** to perform a task and then it exits. Construct a vault,
supply, withdraw, claim rewards, rebalance, or simply read and print vault state -- all fine.

A script that runs **continuously** -- a loop, a scheduler, or a watcher that polls for a condition
and acts when it is met -- does **not** belong here. That is a bot, and it belongs in the
`ipor-fusion-alpha-example` repo.

Two clarifications, because the line is about the lifecycle, not the logic:

- A **one-shot conditional** is fine. "If the health factor is below X, deleverage, then exit" is a
  single run and belongs here. The thing that moves a script to `alpha-example` is the *loop or
  schedule*, not the presence of a decision.
- **Time-gated flows still count as one-shot.** A withdrawal with a cooldown, or interest that
  accrues over a year, is demonstrated in a single run because the simulation advances time
  (`VaultSimulator.next_block`). You never sleep or poll in an example.

Keep each example narrow: one coherent workflow, not a production strategy. A supply example does
not also demonstrate fees, governance, and three protocols -- split unrelated concerns into their
own examples.

## 2. Simulation-only, never broadcast

The default (and only) behavior is preview plus `eth_simulateV1` simulation. An example must not
sign or broadcast a transaction, and must not require a private key to run.

- Show the read-only path with `.call()`, the unsigned-payload path with `.calldata`, and simulate
  the whole flow with `VaultSimulator`.
- For "how to actually submit this on-chain", point the reader at their signer via `.calldata`, and
  at the deployment/operation repos -- do not add a runnable broadcast path.

## 3. One self-contained file

A reader (human or agent) should understand the whole example from a single file, without chasing
imports.

- Inline the small helpers each example needs (address padding, a fail-loud check, connection
  setup). Do **not** import them from a shared module or from `tests/`.
- This means the same few helpers recur across examples on purpose. Do not "DRY" them into a shared
  examples framework -- readability for a first-time reader outranks deduplication here. (This does
  not excuse accidental duplication elsewhere, e.g. between test files.)

## 4. Standard structure

The fastest correct start is to copy [`simple_aave_v3_supply_base.py`](simple_aave_v3_supply_base.py)
and adapt it in place -- it is the living template for this section, so there is no separate skeleton to
keep in sync. Keep its section order:

1. Module docstring -- what the example teaches, the ordered steps, and how to run it.
2. Imports.
3. Configuration, in two clearly labeled blocks: live infrastructure ("do not change") and
   adjustable simulation parameters. Every address carries a provenance comment (see rule 5).
4. Inlined helpers.
5. Pure builders -- functions that construct calldata / fuse actions with no chain access, so they
   can be unit-tested offline.
6. The simulation function -- builds, funds, executes and observes the flow in one batch, prints an
   ordered transaction plan, then asserts the outcome.
7. `main()` plus an `if __name__ == "__main__":` guard.

## 5. Real, provenance-verified addresses

Every hardcoded address is a live, canonical deployment, annotated with a comment, and **verified
against the IPOR ABI registry** (<https://github.com/IPOR-Labs/ipor-abi>) before you commit it.

- Group addresses in the "live infrastructure -- do not change" block; keep tunables (owner, deposit
  amount, pinned block) in the "adjustable parameters" block.
- The provenance comment must name where the address actually comes from. If it is in
  `mainnet/mainnet-<chain>-fusion/addresses.json`, say so; if it comes from somewhere else, say that
  instead. Do not claim a provenance you have not checked.
- Never copy an address out of an arbitrary vault without verifying and documenting where it was
  deployed.

## 6. Deterministic pinned block

Pin the simulation to a fixed block so runs are reproducible and immune to mainnet state drift.
Include a comment telling the reader to bump the block if their provider cannot serve state at that
height.

## 7. Fail loud, and assert what you claim

- Report failed calls and never print success on an incomplete simulation.
- Final-state checks must **not** be bare `assert` statements -- those are stripped under
  `python -O`, so the example could log "OK" on unverified state. Use a small local helper that
  raises unconditionally.
- Each assertion must actually verify the outcome its comment claims. If the narration says "NAV is
  preserved", compare against the pre-operation NAV, not merely against a lower bound that a real
  loss would still satisfy.

## 8. Import-safe

Importing the example module must have **no** chain side effects -- no network, no `Web3Context` at
import time. All connection and simulation lives behind functions and the `__main__` guard, so the
pure builders (rule 4.5) can be exercised offline.

## 9. Document generously; comment the "why"

Examples are teaching material; over-document rather than under-document.

- The module docstring lists the ordered steps and the run command.
- Section headers segment the flow; inline comments explain the non-obvious *why* -- role-grant
  order, the clone-must-be-first invariant, why a dust buffer is subtracted, and so on.
- Comments describe the code as it is now, in the present tense. No historical narrative ("used to",
  "previously", "now", "stale").
- Name distinct actors distinctly, and teach role separation. Give each role (owner/governance,
  alpha, depositor) its own named constant so the access-control story stays legible, even when the
  addresses coincide in simulation. Because examples teach good practice, add a brief comment where
  production should use *separate* addresses and why: the alpha is an online, least-privilege key
  that can only drive `execute()`, while the owner/atomist can reconfigure the whole vault and should
  be a cold key or multisig -- collapsing them means a compromised alpha key becomes full governance
  control. Say which roles must not share an address, in one line, at the point they are granted.
- Use the SDK's domain types (`Amount`, `MarketId`, `ChainId`, `Period`, ...) rather than bare ints,
  and import public symbols from the top-level `ipor_fusion` package.

## 10. Tests and CI

Every example ships with two tests so it cannot silently go stale:

- An **offline** test (`tests/test_examples_*.py`, carrying the `examples` marker) that imports the
  module and exercises its pure builders -- calldata construction, fuse-action encoding, static
  config -- with no provider. Decode and assert the encoded payloads; do not merely check that bytes
  are non-empty.
- An **RPC-gated** simulation test (`tests/test_simulate_examples_*.py`) that runs the example's
  simulation at its pinned block and skips cleanly when no provider is configured.

`ruff` and `pyright` must cover `examples/`, and the example must pass both.

## 11. Run and commit conventions

- The documented run command uses `uv run python examples/<name>.py` (the repo is
  uv-managed).
- Configuration comes from environment variables; never commit keys, RPC URLs, or other secrets.
- Commit with a `docs(sdk):` message.

## When the canonical example and these rules disagree

These rules describe the target. If you find the canonical example not yet matching a rule, the rule
wins -- fix the example, don't weaken the rule.
