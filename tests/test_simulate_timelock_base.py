"""A timelocked governance call on a live Base vault, end to end in one
eth_simulateV1 run: the plan says schedule, the scheduled call is not executable
before the delay, and runs through the manager after it. Skipped without an
archive ``BASE_PROVIDER_URL`` that serves ``eth_simulateV1``."""

from _live import archive_context
from web3 import Web3

from ipor_fusion import AccessManager, PlasmaVault, Shares, VaultSimulator
from ipor_fusion.types import parse_plasma_vault_address

VAULT = parse_plasma_vault_address("0xc4c00d8b323f37527eeda27c87412378be9f68ec")
# Holds the vault's ATOMIST role with a three-day execution delay.
ATOMIST = Web3.to_checksum_address("0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569")
BLOCK = 52973779
NEW_CAP = Shares(10**30)


def test_schedule_wait_and_execute_a_delayed_call(web3_base):
    ctx = archive_context("BASE_PROVIDER_URL", BLOCK, VAULT)
    vault = PlasmaVault(VAULT)
    manager = AccessManager(vault.get_access_manager_address().call(ctx))
    cap = vault.set_total_supply_cap(NEW_CAP)

    plan = manager.read_call_plan(ctx, cap, caller=ATOMIST)
    assert plan.kind == "schedule"
    assert plan.permission.delay == 3 * 24 * 3600
    assert plan.next_call is not None

    early = VaultSimulator(web3_base, block=BLOCK)
    early.add_call(plan.next_call, from_=ATOMIST, label="schedule")
    early.next_block(3600)
    early.add_call(cap, from_=ATOMIST, label="early")
    scheduled, too_early = early.run().calls
    assert scheduled.success
    assert not too_early.success
    assert too_early.error is not None
    assert "0x18cb6b7a" in too_early.error  # AccessManagerNotReady(bytes32)

    # Once ready, the vault consumes the schedule on a direct call from the
    # atomist, or the manager relays it.
    for ready in (cap, manager.execute(cap.to, cap.data)):
        sim = VaultSimulator(web3_base, block=BLOCK)
        sim.add_call(plan.next_call, from_=ATOMIST)
        when = sim.observe(manager.get_schedule(plan.operation))
        sim.next_block(int(plan.permission.delay) + 1)
        sim.add_call(ready, from_=ATOMIST)
        after = sim.observe(vault.get_total_supply_cap())
        assert sim.run().all_success
        assert when.value > 0
        assert after.value == NEW_CAP
