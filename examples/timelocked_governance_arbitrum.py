"""Schedule, wait out and execute a timelocked ATOMIST call on a live Arbitrum vault.

Every other example grants its roles with ``Period(0)``: no execution delay, so
each restricted call goes out directly. Production vaults put execution delays
on their governance roles. A holder with a delay cannot send a restricted call
directly: it schedules the call, waits out the delay, then executes it. This
example walks through that flow with the SDK, on a live vault whose ATOMIST has
a 3-day delay:

  1. read both actors' roles and execution delays from the access manager;
  2. ask ``AccessManager.read_call_plan`` how the ATOMIST gets a
     ``setTotalSupplyCap`` through: the plan is ``schedule``, and its
     ``next_call`` is the ``schedule(...)`` call to send;
  3. send that ``schedule`` and read when the operation becomes executable;
  4. send the cap change an hour later: it reverts with ``AccessManagerNotReady``;
  5. wait out the delay;
  6. send the cap change again: the vault consumes the schedule and the cap
     changes;
  7. schedule a second cap change;
  8. the GUARDIAN cancels it before the delay has passed;
  9. wait out the delay and send the cancelled change: it reverts with
     ``AccessManagerNotScheduled``, and the cap stays where step 6 set it.

Role layout: one multisig holds OWNER (14-day delay) and ATOMIST (3-day delay)
on this vault, and a separate guardian account holds GUARDIAN (no delay).
Sharing one key between OWNER and ATOMIST is this vault's setup and is not
recommended: the IPOR docs require OWNER keys separate from the operational
roles' (ipor.io/docs -> build-on-fusion -> atomists ->
vault-configuration-step-by-step -> access-management, "Strict Key
Separation"). Do not copy it. The example acts as the ATOMIST and, in step 8,
as the GUARDIAN.

The recommended delays per role and the 7-day expiry of a scheduled operation
are IPOR policy: ipor.io/docs -> build-on-fusion -> atomists ->
vault-configuration-step-by-step -> timelocks-and-execution-delays. The example
reads the vault's delays from the chain rather than hardcoding them. A guardian
cancels operations scheduled by OWNER, ATOMIST or FUSE_MANAGER, but not
``grantRole``, ``revokeRole`` or ``setGrantDelay`` (access-management, and the
developer reference of timelocks-and-execution-delays, "Cancelling an
Operation").

Part 1 (steps 1-2) reads the live chain at a pinned block. Part 2 (steps 3-9)
runs as one ``eth_simulateV1`` batch. Steps 4 and 9 are *meant* to revert, so
the returned ``SimulationResult`` carries failed calls, and its ``all_success``
is ``False`` on a completely successful run. Do not copy
``if result.all_success:`` from here; this example checks that exactly the
expected calls failed, each for the expected reason.

Nothing is signed or broadcast. In production the ATOMIST multisig signs the
``.calldata`` of each of its steps, the GUARDIAN signs the cancel of step 8,
and a later ``read_call_plan`` (not a simulation) tells the ATOMIST when the
delay has passed.

Run it (the shell snippets assume a POSIX shell -- bash or zsh):

    export ARBITRUM_PROVIDER_URL="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
    uv run python examples/timelocked_governance_arbitrum.py

The provider must be an archive node that implements ``eth_simulateV1`` (Alchemy
and other geth/reth-based providers do).

The docs sum up the delays as "tightening security is fast; loosening security
is slow": raising a delay takes effect at once, and lowering one takes the full
notice period (timelocks-and-execution-delays, as above).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping

from web3 import Web3

from ipor_fusion import (
    AccessManager,
    Call,
    Multicall3,
    PlasmaVault,
    Roles,
    ScheduleId,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    is_simulate_v1_supported,
    parse_access_manager_address,
    parse_checksum_address,
    parse_plasma_vault_address,
)
from ipor_fusion.types import ChainId, Period, Shares

log = logging.getLogger("timelocked_governance_arbitrum")

# ── Live Arbitrum vault and actors (do NOT change) ───────────────────────────
# This is the only example that works on an existing vault instead of a fresh
# clone. read_call_plan reads the chain with eth_call, and a vault cloned inside
# an eth_simulateV1 batch exists only there, so the plan could not see it.

ARBITRUM_CHAIN_ID = ChainId(42161)

# "IPOR USDC Prime" on Arbitrum. It is not in the ipor-abi registry; the source
# is the IPOR vaults API (api.ipor.io/v2/fusion/vaults) and its app page
# (app.ipor.io/fusion/arbitrum/0xd855296f9868ff659f3f359c90b7d005ec049228).
VAULT = parse_plasma_vault_address("0xd855296f9868ff659f3f359c90b7d005ec049228")

# The vault's access manager, from VAULT.getAccessManagerAddress() at
# PINNED_BLOCK. A constant keeps the builders offline; run_simulation checks
# that the vault still answers it.
ACCESS_MANAGER = parse_access_manager_address(
    "0xC5987a10A9E5b48943a5b3342516964592d9b989"
)

# The ATOMIST: the IPOR DAO multisig (a contract), with a 3-day ATOMIST
# execution delay (hasRole at PINNED_BLOCK). On this vault it also holds OWNER,
# and sharing one key between OWNER and ATOMIST is not recommended: the docs
# require separate OWNER keys (access-management, "Strict Key Separation").
# With separate keys, a compromised atomist key cannot also change the roles
# and their delays. In the other examples owner and atomist coincide only
# during bootstrap: a fresh clone forces OWNER to self-grant ATOMIST, and in
# production the atomist may be delegated to a separate address. This vault
# keeps both roles on one multisig by choice; the example uses its actual
# holders because it needs a live vault.
ATOMIST = parse_checksum_address("0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569")

# A guardian: one of the vault's GUARDIAN holders, with no execution delay and
# neither OWNER nor ATOMIST (hasRole at PINNED_BLOCK). The docs recommend a zero
# delay for a guardian, so that it can pause the vault (updateTargetClosed) at
# once. `cancel` ignores the caller's delay, so step 8 needs none. A guardian
# must not share a key with the atomist whose operations it cancels.
GUARDIAN = parse_checksum_address("0x1d682c7eDC203B8A17dC9b8B96cbf1e2ef5B5645")

# The docs' recommended execution delays for the three roles this example reads
# (timelocks-and-execution-delays). Printed for comparison, never asserted: the
# policy belongs to the docs and the delays to the vault.
DOCS_RECOMMENDED_DELAYS: Mapping[Roles, Period] = {
    Roles.OWNER_ROLE: Period(14 * Period.DAY),
    Roles.ATOMIST_ROLE: Period(3 * Period.DAY),
    Roles.GUARDIAN_ROLE: Period(0),
}

# ── Simulation parameters (safe to adjust) ───────────────────────────────────

# Pinned Arbitrum block. Pinning makes the run deterministic and immune to
# mainnet state drift. It requires an archive node that can serve state at this
# height -- if your provider cannot (an error mentioning missing trie/state, or
# a pruned block), BUMP THIS to a recent Arbitrum block your node can reach, and
# re-check the actors' roles there.
PINNED_BLOCK = 525_340_000  # Arbitrum, 2026-11-18

# The supply cap the ATOMIST sets, in vault shares. Any value other than the
# vault's cap at PINNED_BLOCK shows the change landing.
NEW_CAP = Shares(200_000_000_000_000)

# The cap of the second change, which the GUARDIAN cancels. It differs from
# NEW_CAP and from the vault's cap at PINNED_BLOCK, so a cancelled change that
# still lands shows in the cap.
CANCELLED_CAP = Shares(300_000_000_000_000)

# How long after the schedule the premature execution of step 4 is sent.
EARLY_BY_SECONDS = Period.HOUR

# The calls the run expects to revert, by label, with the custom error each
# must revert with: step 4 (too early) and step 9 (cancelled).
EXPECTED_FAILURES: Mapping[str, str] = {
    "early_execute": "AccessManagerNotReady",
    "cancelled_execute": "AccessManagerNotScheduled",
}


# ── Inlined helpers (kept local so this file is self-contained) ──────────────


def _check(condition: bool, message: str) -> None:
    """Fail loud unconditionally -- unlike ``assert``, survives ``python -O``."""
    if not condition:
        raise AssertionError(message)


def _assert_only_expected_failures(
    result: SimulationResult, expected: Mapping[str, str]
) -> None:
    """Assert the batch failed on exactly the labeled demos, each with the
    custom error ``expected`` names for its label.

    The premature and the cancelled executions are *meant* to revert, so the
    batch cannot be checked with ``result.all_success``. Everything else still
    must succeed: a failure anywhere else means the happy path broke and the
    run is not a success, whatever the demos did.
    """
    failed = {c.label: c.revert_reason or "" for c in result.failed_calls}
    if set(failed) != set(expected) or len(result.failed_calls) != len(expected):
        raise AssertionError(
            f"expected exactly the failed calls {sorted(expected)}, got "
            f"{[(c.label, c.error) for c in result.failed_calls]}"
        )
    for label, error_name in expected.items():
        _check(
            failed[label].startswith(f"{error_name}("),
            f"{label!r} reverted for the wrong reason: {failed[label]}",
        )


def _connected_web3() -> Web3:
    """Build a Web3 client from ARBITRUM_PROVIDER_URL, or exit with a clear message."""
    url = os.environ.get("ARBITRUM_PROVIDER_URL")
    if not url:
        sys.exit(
            "ARBITRUM_PROVIDER_URL is not set -- export your Arbitrum RPC URL first."
        )
    web3 = Web3(Web3.HTTPProvider(url))
    if not web3.is_connected():
        sys.exit("cannot reach ARBITRUM_PROVIDER_URL -- check the endpoint.")
    if not is_simulate_v1_supported(web3):
        sys.exit(
            "the provider does not implement eth_simulateV1 -- use an archive node."
        )
    return web3


# ── Pure builders (no chain access -- safe to import and unit-test offline) ───


def cap_call(cap: Shares) -> Call[None]:
    """The ATOMIST's restricted call: ``setTotalSupplyCap(cap)`` on the vault."""
    return PlasmaVault(VAULT).set_total_supply_cap(cap)


def schedule_calldata() -> bytes:
    """The ``schedule`` of the ``NEW_CAP`` change, built offline.

    It is the call ``read_call_plan`` returns as ``next_call`` for a delayed
    caller with nothing scheduled: ``when=0`` means as soon as the caller's
    delay allows. ``run_simulation`` checks the two agree.
    """
    manager = AccessManager(ACCESS_MANAGER)
    return manager.schedule(VAULT, cap_call(NEW_CAP).data).calldata


def cancel_call() -> Call[None]:
    """The GUARDIAN's cancel of the ATOMIST's scheduled ``CANCELLED_CAP`` change.

    ``cancel`` names the operation by its scheduler, target and calldata, the
    inputs of ``operation_id``.
    """
    manager = AccessManager(ACCESS_MANAGER)
    return manager.cancel(ATOMIST, VAULT, cap_call(CANCELLED_CAP).data)


def operation_id(data: bytes) -> ScheduleId:
    """The id under which the manager records the ATOMIST's schedule of ``data``
    on the vault, computed offline as the manager computes it."""
    return AccessManager.hash_operation(ATOMIST, VAULT, data)


# ── Simulation (live reads, then one eth_simulateV1 batch; no broadcast) ─────


def _log_delays(ctx: Web3Context, manager: AccessManager) -> Period:
    """Step 1: log each actor's roles and delays; return the ATOMIST's delay."""
    actors = {"ATOMIST": ATOMIST, "GUARDIAN": GUARDIAN}
    keys = [(name, role) for name in actors for role in DOCS_RECOMMENDED_DELAYS]
    statuses = Multicall3(ctx).aggregate(
        [manager.has_role(role, actors[name]) for name, role in keys]
    )
    held = dict(zip(keys, statuses, strict=True))
    for (name, role), status in held.items():
        # hasRole answers a delay of 0 for a non-member; it means nothing there.
        if not status.is_member:
            log.info("%s does not hold %s", name, role.name)
            continue
        log.info(
            "%s holds %s with a delay of %ss (docs recommend %ss)",
            name,
            role.name,
            status.execution_delay,
            DOCS_RECOMMENDED_DELAYS[role],
        )
    atomist = held[("ATOMIST", Roles.ATOMIST_ROLE)]
    _check(atomist.is_member, "ATOMIST does not hold ATOMIST_ROLE")
    _check(atomist.execution_delay > 0, "ATOMIST has no execution delay")
    guardian = held[("GUARDIAN", Roles.GUARDIAN_ROLE)]
    _check(guardian.is_member, "GUARDIAN does not hold GUARDIAN_ROLE")
    for role in (Roles.OWNER_ROLE, Roles.ATOMIST_ROLE):
        _check(
            not held[("GUARDIAN", role)].is_member, f"GUARDIAN also holds {role.name}"
        )
    return atomist.execution_delay


def run_simulation(web3: Web3) -> SimulationResult:
    """Plan the ATOMIST's cap change on the live chain, then schedule, wait out
    and execute it in one eth_simulateV1 batch, in which the GUARDIAN also
    cancels a second change."""
    ctx = Web3Context(web3=web3, chain_id=ARBITRUM_CHAIN_ID).at_block(PINNED_BLOCK)
    vault = PlasmaVault(VAULT)
    manager = AccessManager(ACCESS_MANAGER)
    _check(
        vault.get_access_manager_address().call(ctx) == ACCESS_MANAGER,
        "the vault answers a different access manager",
    )

    # ── Part 1: the live chain ───────────────────────────────────────────────
    # 1. The delays come from the chain, not from this file.
    delay = _log_delays(ctx, manager)
    # Step 4 must land inside the delay to show the early call reverting.
    _check(
        delay > EARLY_BY_SECONDS,
        f"the ATOMIST's delay {delay}s is not longer than EARLY_BY_SECONDS",
    )

    # 2. The plan: what the ATOMIST sends now. With a delay and nothing
    #    scheduled, it is `schedule`, and next_call is the schedule itself.
    change = cap_call(NEW_CAP)
    plan = manager.read_call_plan(ctx, change, caller=ATOMIST)
    _check(plan.kind == "schedule", f"expected a schedule plan, got {plan.kind}")
    _check(
        plan.permission.delay == delay,
        f"the plan's delay {plan.permission.delay} differs from hasRole's {delay}",
    )
    schedule = plan.next_call
    if schedule is None:
        raise AssertionError("a schedule plan carries no next_call")
    _check(
        schedule.to == ACCESS_MANAGER and schedule.data == schedule_calldata(),
        "the plan's next_call differs from schedule_calldata()",
    )
    _check(
        plan.operation == operation_id(change.data),
        "the plan's operation id differs from operation_id()",
    )
    log.info(
        "plan: %s, delay %ss, operation 0x%s", plan.kind, delay, plan.operation.hex()
    )
    log.info(
        "the multisig signs a call to %s with calldata 0x%s",
        schedule.to,
        schedule.calldata.hex(),
    )

    # ── Part 2: one eth_simulateV1 batch ─────────────────────────────────────
    # Each simulator primitive stands in for a production action, read as
    # "simulation -> production":
    #   - sim.add_call(call=X, from_=ATOMIST) -> a multisig transaction with
    #     X.calldata, signed by the multisig's owners.
    #   - read = sim.observe(X), then read.value -> X.call(ctx), a plain read.
    # SIMULATION ONLY (impersonation): `from_` sends as the multisig without
    # its signatures, because the simulator runs with validation disabled.
    # Impossible in production.
    sim = VaultSimulator(web3=web3, block=PINNED_BLOCK)
    steps: list[str] = []

    # 3. Schedule. The operation becomes executable `delay` seconds after the
    #    block the schedule lands in; current_time is that block's time.
    sim.add_call(call=schedule, from_=ATOMIST, label="schedule")
    scheduled_in = sim.current_time
    executable_at = sim.observe(manager.get_schedule(plan.operation))
    steps.append("ATOMIST schedules the cap change")

    # SIMULATION ONLY (time advancement): next_block shifts block.timestamp;
    # production waits. The same holds for the later next_block calls below.
    sim.next_block(time_shift_seconds=EARLY_BY_SECONDS)

    # 4. Execute too early: the vault reverts with AccessManagerNotReady. In
    #    production nothing is sent here: read_call_plan answers `waiting`.
    sim.add_call(call=change, from_=ATOMIST, label="early_execute")
    steps.append("ATOMIST sends the cap change an hour later (reverts)")

    # 5. Wait out the delay. The schedule landed EARLY_BY_SECONDS before this
    #    block, and an operation is executable once getSchedule(id) <=
    #    block.timestamp, so a jump of exactly `delay` is enough.
    sim.next_block(time_shift_seconds=delay)

    # 6. Execute: the ATOMIST sends the call itself, and the vault consumes the
    #    schedule (the vault still sees the ATOMIST as the sender).
    # SIMULATION ONLY (plan stand-in): the get_schedule reads stand in for
    # re-running read_call_plan, which cannot see simulated state. That is
    # possible in production but wrong there: production
    # calls read_call_plan again at a later block, and sends plan.next_call
    # once the plan is `ready` (until the schedule expires).
    sim.add_call(call=change, from_=ATOMIST, label="execute")
    cap_after = sim.observe(vault.get_total_supply_cap())
    schedule_after = sim.observe(manager.get_schedule(plan.operation))
    steps.append("ATOMIST sends the cap change after the delay")

    # 7. Schedule a second change, the one the GUARDIAN cancels. Its operation
    #    id differs from the first one's, since the calldata differs.
    cancelled = cap_call(CANCELLED_CAP)
    cancelled_id = operation_id(cancelled.data)
    _check(cancelled_id != plan.operation, "both changes share one operation id")
    sim.add_call(
        call=manager.schedule(VAULT, cancelled.data),
        from_=ATOMIST,
        label="schedule_cancelled",
    )
    second_scheduled_in = sim.current_time
    second_executable_at = sim.observe(manager.get_schedule(cancelled_id))
    steps.append("ATOMIST schedules a second cap change")

    # 8. Cancel. `cancel` needs only the GUARDIAN's role, so the GUARDIAN
    #    cancels at once, while the ATOMIST's delay still runs.
    sim.add_call(call=cancel_call(), from_=GUARDIAN, label="cancel")
    cancelled_after = sim.observe(manager.get_schedule(cancelled_id))
    steps.append("GUARDIAN cancels the second change")

    # 9. Wait out the delay and send the cancelled change. Without the cancel
    #    it would be executable here, as in step 6; with it, the manager has no
    #    schedule left and reverts with AccessManagerNotScheduled.
    sim.next_block(time_shift_seconds=delay)
    sim.add_call(call=cancelled, from_=ATOMIST, label="cancelled_execute")
    cap_final = sim.observe(vault.get_total_supply_cap())
    steps.append("ATOMIST sends the cancelled change after the delay (reverts)")

    log.info("transaction plan:")
    for i, step in enumerate(steps, start=1):
        log.info("  %d. %s", i, step)

    result = sim.run()
    _assert_only_expected_failures(result, EXPECTED_FAILURES)

    # ── Simulation done. Everything above only *described* the batch; sim.run()
    # is the single eth_simulateV1 call that executed it. From here we read the
    # recorded observations back and verify the outcome. ──────────────────────
    _check(
        executable_at.value == scheduled_in + delay,
        f"the operation is executable at {executable_at.value}, "
        f"not {scheduled_in} + {delay}",
    )
    _check(cap_after.value == NEW_CAP, f"the cap is {cap_after.value}, not NEW_CAP")
    _check(schedule_after.value == 0, "the executed operation is still scheduled")
    _check(
        second_executable_at.value == second_scheduled_in + delay,
        f"the second change is executable at {second_executable_at.value}, "
        f"not {second_scheduled_in} + {delay}",
    )
    _check(cancelled_after.value == 0, "the cancelled operation is still scheduled")
    _check(
        cap_final.value == NEW_CAP,
        f"the cap is {cap_final.value} after the cancelled change, not NEW_CAP",
    )

    log.info(
        "OK -- cap set to %s after a %ss delay; the GUARDIAN cancelled the "
        "change to %s",
        cap_after.value,
        delay,
        CANCELLED_CAP,
    )
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_simulation(_connected_web3())


if __name__ == "__main__":
    main()
