"""Drive a crosschain Plasma Vault: an Arbitrum hub, a HyperEVM spoke, CCIP in between.

A crosschain vault keeps its accounting on one chain (the hub) and puts capital
to work on another (the spoke). On the hub a per-vault *executor* holds the
crosschain market's buckets -- settled remote balance, outbound in flight, idle
returned -- and sends messages; on the spoke a *dispatcher* at the very same
address receives them, holds the asset and deposits it into a spoke vault. The
hub vault cannot see the spoke, so the executor's NAV is a two-key
*attestation*: a proposer reports the dispatcher's observed balance, an approver
confirms it, and the crosschain balance fuse values the attested figure into the
vault's NAV. Every step below is one ``PlasmaVault.execute`` on the hub or one
attestation call; the SDK's ``CrosschainLane`` builds all of them.

This example shows the whole lifecycle an alpha drives, end to end, on a live
test deployment:

  1. connect a ``Web3Context`` to each chain, pinned to one block per chain;
  2. open the lane from the hub vault's executor: the SDK discovers the fuses
     from the vault's market grants and reads the CCIP route from the executor;
  3. a depositor puts USDC into the hub vault;
  4. the alpha supplies USDC to the spoke: a CCIP token transfer to the
     dispatcher, acknowledged by a settlement receipt back to the hub;
  5. the proposer and the approver attest the dispatcher's observation, and
     the alpha refreshes the vault's cached NAV;
  6. the alpha sends a DEPOSIT command: the dispatcher deposits into the spoke
     vault and acknowledges;
  7. the alpha sends a REDEEM command: the dispatcher redeems the shares;
  8. the attesters re-mark the redeemed amount before anything comes back, so a
     loss on the spoke is never left in the settled bucket;
  9. the alpha recalls the idle: a CCIP token transfer back to the hub;
 10. the attesters mark the (now empty) remote balance to zero and the alpha
     claims the returned USDC into the vault.

Everything runs inside ``eth_simulateV1`` against pinned blocks -- nothing is
signed or broadcast. The two chains are simulated separately and a relay
delivers each CCIP message to the other chain by impersonating the Router, which
is exactly what Chainlink's committee and executors do live. Constructs that
exist only because this runs under simulation are flagged inline as SIMULATION
ONLY, with what production does instead; there are several, because a
lifecycle four separate signers drive over an hour is compressed into one
script.

One live fact this simulation does not reproduce: on this lane Chainlink's
executors submit into HyperEVM's 3 M-gas small blocks, so a hub -> spoke
message whose gas limit exceeds that is never executed automatically and has to
be executed by anyone through the permissionless ``OffRamp.execute``
(``ipor_fusion.manual_execution``). The relay here delivers directly.

Run it (the shell snippets assume a POSIX shell -- bash or zsh):

    export ARBITRUM_PROVIDER_URL="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
    export HYPEREVM_PROVIDER_URL="https://YOUR_HYPEREVM_ARCHIVE_NODE"
    uv run python examples/crosschain_ccip_usdc_arbitrum_hyperevm.py

Both providers must be archive nodes that implement ``eth_simulateV1`` and serve
state at the pinned blocks below.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import NamedTuple

from web3 import Web3

from ipor_fusion import (
    ERC20,
    CcipChain,
    CcipTransport,
    Command,
    CrosschainLane,
    CrosschainSimulator,
    PlasmaVault,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    erc20_balance_slot,
    is_simulate_v1_supported,
    open_lane,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId, Period, Shares

log = logging.getLogger("crosschain_ccip_usdc_arbitrum_hyperevm")

# ── Live infrastructure (do NOT change) ──────────────────────────────────────
# Chainlink CCIP routers and chain selectors, from the CCIP directory
# (docs.chain.link -> CCIP -> Directory -> Arbitrum One / Hyperliquid).
ARBITRUM_CHAIN_ID = ChainId(42161)
HYPEREVM_CHAIN_ID = ChainId(999)
ARBITRUM_CCIP_SELECTOR = 4949039107694359620
HYPEREVM_CCIP_SELECTOR = 2442541497099098535
ARBITRUM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x141fa059441E0ca23ce184B6A78bafD2A517DdE8"
)
HYPEREVM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec"
)

# Native (Circle-issued) USDC on each chain, 6 decimals; the USDC CCIP lane
# between them is a CCTP burn-and-mint lane, so the asset arrives as the same
# native token on the other side.
ARBITRUM_USDC = Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
HYPEREVM_USDC = Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f")

# The crosschain test deployment this example drives. It is NOT in the ipor-abi
# registry: these are test vaults and a test executor created by the IPOR SDK
# team on 2026-10-02 against the CCIP crosschain factory, and they stay live
# as a regression fixture (the SDK's own tests pin the same addresses). Verify
# them yourself with ``discover_deployment(hub_ctx, HUB_VAULT, CROSSCHAIN_MARKET)``,
# which reads the executor, its fuses and its spoke from the vault's grants.
HUB_VAULT = Web3.to_checksum_address("0x9257e35FEcF601fD009d35060C8f555af5A655EA")
# The executor on Arbitrum and the dispatcher on HyperEVM: one CREATE3 address.
EXECUTOR = Web3.to_checksum_address("0xb5B4Cb6aB855fee75C98D5F4E7dB2927EDB2d211")
SPOKE_VAULT = Web3.to_checksum_address("0x35606C49360f94fD0310F4bAE4f0a1155FC00D1a")
# ``IporFusionMarkets.CROSSCHAIN``: the market the four crosschain fuses are
# registered under on the hub vault.
CROSSCHAIN_MARKET = MarketId(int(IporFusionMarkets.CROSSCHAIN))

# ── Actors ───────────────────────────────────────────────────────────────────
# Role separation: the alpha is an online, least-privilege key that can only
# drive execute(); the two attesters hold nothing but their attestation role;
# the owner/atomist that can reconfigure the vault should be a cold key or a
# multisig. None of these should share an address with another -- a compromised
# alpha key must not be able to approve its own balance proposals. On this test
# deployment one EOA holds the owner, atomist and alpha roles and the
# whitelist (the depositor), while the proposer and the approver are two
# separate keys, as the executor contract requires.
ALPHA = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
# The hub vault only accepts deposits from whitelisted addresses, and the
# whitelist of this test vault holds one address -- the same EOA. A production
# vault grants WHITELIST_ROLE to its depositors, or runs with an open deposit.
DEPOSITOR = ALPHA
BALANCE_PROPOSER = Web3.to_checksum_address(
    "0xd122DCF446bC200D1f19AD45eB669a5Ef273dBC8"
)
BALANCE_APPROVER = Web3.to_checksum_address(
    "0x64D345FE416EE6b841B544F27277d9B61b61A02a"
)

# ── Adjustable simulation parameters ─────────────────────────────────────────
# Pinned right after the deployment's last live transaction, with every
# executor bucket at zero. Bump both if your provider cannot serve state at
# these heights; keep the hub block a few seconds AFTER the spoke block, the
# attestation rejects an observation stamped in the hub's future.
ARBITRUM_PINNED_BLOCK = 510_934_861
HYPEREVM_PINNED_BLOCK = 47_452_389
# 1 USDC. Small on purpose: the whole point is the shape of the flow.
DEPOSIT_AMOUNT = Amount(1_000_000)
# How long the proposer gives the approver before a proposal expires; the
# executor refuses anything shorter than its minimum approval window.
PROPOSAL_TTL = Period(30 * 60)
# The approver acts in a later block than the proposer, as two signers would.
APPROVAL_DELAY = Period(5 * 60)
# SIMULATION ONLY: the modeled time between a CCIP send and its delivery, so
# the simulated clocks resemble the lane's. Live, Arbitrum -> HyperEVM takes
# Arbitrum's source finality (about 15 minutes) plus verification; HyperEVM ->
# Arbitrum lands in about a minute.
BRIDGE_LATENCY = Period(2 * 60)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _check(condition: bool, message: str) -> None:
    """Fail loud unconditionally -- unlike ``assert``, survives ``python -O``."""
    if not condition:
        raise AssertionError(message)


def _assert_relay_success(results: dict[ChainId, SimulationResult]) -> None:
    """Raise with a readable summary if any simulated call on either chain
    reverted; each entry names the call and decodes its own revert."""
    failed = [
        (int(chain_id), call.label, call.revert_reason or call.error)
        for chain_id, result in results.items()
        for call in result.calls
        if not call.success
    ]
    if failed:
        raise AssertionError(f"simulation calls failed: {failed}")


def _connected_web3(env_var: str, chain_id: ChainId) -> Web3:
    """Build a Web3 client from ``env_var``, or exit with a clear message that
    never echoes the URL (it embeds the provider key)."""
    url = os.environ.get(env_var)
    if not url:
        sys.exit(f"{env_var} is not set -- export your RPC URL first.")
    web3 = Web3(Web3.HTTPProvider(url))
    if not web3.is_connected():
        sys.exit(f"cannot reach the RPC at {env_var} -- check the endpoint.")
    if web3.eth.chain_id != chain_id:
        sys.exit(f"{env_var} does not serve chain {int(chain_id)}.")
    if not is_simulate_v1_supported(web3):
        sys.exit(
            f"the provider at {env_var} does not implement eth_simulateV1 -- "
            "use an archive node."
        )
    return web3


def _pinned_context(web3: Web3, chain_id: ChainId, block: int) -> Web3Context:
    ctx = Web3Context(web3, chain_id=chain_id)
    ctx.default_block = block
    return ctx


# ── Pure builders (no chain access, unit-testable offline) ───────────────────
def transport() -> CcipTransport:
    """The CCIP transport the relay simulates: both chains' routers, selectors
    and the USDC each side credits. This is what ``CrosschainSimulator`` uses
    to turn a ``CCIPMessageSent`` log on one chain into a ``ccipReceive`` on
    the other."""
    return CcipTransport(
        (
            CcipChain(
                ARBITRUM_CHAIN_ID,
                ARBITRUM_CCIP_SELECTOR,
                ARBITRUM_CCIP_ROUTER,
                ARBITRUM_USDC,
                6,
            ),
            CcipChain(
                HYPEREVM_CHAIN_ID,
                HYPEREVM_CCIP_SELECTOR,
                HYPEREVM_CCIP_ROUTER,
                HYPEREVM_USDC,
                6,
            ),
        )
    )


def deposit_command(amount: Amount) -> Command:
    """DEPOSIT ``amount`` of the dispatcher's idle USDC into the spoke vault.

    ``amount`` is in the asset's shared decimals, which for USDC equal its
    local 6 decimals on both chains."""
    return Command.deposit(SPOKE_VAULT, amount)


def redeem_command(shares: Shares) -> Command:
    """REDEEM ``shares`` of the spoke vault back into the dispatcher's idle."""
    return Command.redeem(SPOKE_VAULT, shares)


# ── The simulated lifecycle ──────────────────────────────────────────────────
class _Flow(NamedTuple):
    """Handles the phases share: the lane, both simulators and the vault
    wrappers on each side."""

    lane: CrosschainLane
    simulator: CrosschainSimulator
    hub: VaultSimulator
    spoke: VaultSimulator
    hub_vault: PlasmaVault
    spoke_vault: PlasmaVault
    hub_usdc: ERC20
    min_update_interval: int
    numbers: dict[str, int]

    def advance(self, seconds: int) -> None:
        # SIMULATION ONLY: the next calls land in a later block on both chains,
        # ``seconds`` later. Live, time passes on its own.
        self.hub.next_block(time_shift_seconds=seconds)
        self.spoke.next_block(time_shift_seconds=seconds)

    def relay(self) -> dict[ChainId, SimulationResult]:
        results = self.simulator.relay()
        _assert_relay_success(results)
        return results


def _open(web3_arb: Web3, web3_hype: Web3) -> _Flow:
    hub_ctx = _pinned_context(web3_arb, ARBITRUM_CHAIN_ID, ARBITRUM_PINNED_BLOCK)
    spoke_ctx = _pinned_context(web3_hype, HYPEREVM_CHAIN_ID, HYPEREVM_PINNED_BLOCK)

    # The lane: the executor on the hub, its dispatcher on the spoke, the fuses
    # the hub vault registered for the crosschain market and the executor's
    # CCIP route to the spoke, all read from the chain.
    lane = open_lane(hub_ctx, spoke_ctx, executor=EXECUTOR, market_id=CROSSCHAIN_MARKET)
    _check(lane.executor_address == EXECUTOR, "the lane is not on the executor")
    log.info(
        "lane: executor=%s supply_fuse=%s command_fuse=%s claim_fuse=%s",
        lane.executor_address,
        lane.fuses.supply,
        lane.fuses.command,
        lane.fuses.claim,
    )
    # The executor refuses a new approval sooner than this after the previous
    # one; every attestation after the first waits it out.
    min_update_interval = int(lane.executor.min_update_interval().call())

    simulator = CrosschainSimulator(transport())
    hub = simulator.add_chain(
        ARBITRUM_CHAIN_ID,
        web3_arb,
        block=ARBITRUM_PINNED_BLOCK,
        vault=HUB_VAULT,
        alpha=ALPHA,
    )
    spoke = simulator.add_chain(
        HYPEREVM_CHAIN_ID, web3_hype, block=HYPEREVM_PINNED_BLOCK
    )
    # SIMULATION ONLY: the hub's simulated clock runs a minute ahead of the
    # spoke's so an observation read on the spoke is never stamped in the hub's
    # future (the executor rejects that). Live, the two chains' clocks are
    # within seconds and the proposer simply reads the spoke first.
    hub.with_block_time_shift(60)
    # SIMULATION ONLY: HyperEVM's small blocks carry 3 M gas; the deliveries
    # here run under a 30 M block, as a HyperEVM big block does. Live, token
    # legs fit a small block and Chainlink executes them; a message above the
    # small-block limit needs the manual ``OffRamp.execute`` named in the
    # module docstring.
    spoke.with_block_override(gasLimit=30_000_000)
    # SIMULATION ONLY: the executor and the dispatcher pay the CCIP fees of
    # what they send out of their own native balances; the live deployment
    # holds those floats, the simulation sets them.
    simulator.fund_native(ARBITRUM_CHAIN_ID, EXECUTOR, 10**18)
    simulator.fund_native(HYPEREVM_CHAIN_ID, EXECUTOR, 10**18)

    return _Flow(
        lane=lane,
        simulator=simulator,
        hub=hub,
        spoke=spoke,
        hub_vault=PlasmaVault(hub_ctx, HUB_VAULT),
        spoke_vault=PlasmaVault(spoke_ctx, SPOKE_VAULT),
        hub_usdc=ERC20(hub_ctx, ARBITRUM_USDC),
        min_update_interval=min_update_interval,
        numbers={},
    )


def _deposit_and_supply(flow: _Flow, web3_arb: Web3) -> Amount:
    """Steps 3 and 4: the depositor funds the hub vault, the alpha supplies the
    spoke. Returns the amount the dispatcher was credited with."""
    lane, hub = flow.lane, flow.hub
    # SIMULATION ONLY: the depositor's USDC is a storage override at the
    # token's balance slot. Live, the depositor already holds USDC.
    hub.with_erc20_balance(
        ARBITRUM_USDC,
        DEPOSITOR,
        DEPOSIT_AMOUNT,
        slot=erc20_balance_slot(web3_arb, ARBITRUM_USDC, block=ARBITRUM_PINNED_BLOCK),
    )
    hub.add_call(
        flow.hub_usdc.approve(HUB_VAULT, DEPOSIT_AMOUNT),
        from_=DEPOSITOR,
        label="approve_hub_deposit",
    )
    hub.add_call(
        flow.hub_vault.deposit(DEPOSIT_AMOUNT, DEPOSITOR),
        from_=DEPOSITOR,
        label="deposit_hub_vault",
    )
    # The vault's USDC once the deposit is in and before any of it leaves.
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID,
        "vault_usdc_after_deposit",
        flow.hub_usdc.balance_of(HUB_VAULT),
    )
    # The supply: the supply fuse moves USDC vault -> executor -> CCIP. CCIP has
    # no destination-side floor, so ``min_received`` must be 0 here; the
    # settlement receipt that comes back carries the amount actually credited.
    hub.execute(
        [
            lane.supply(
                asset=ARBITRUM_USDC, amount=DEPOSIT_AMOUNT, min_received=Amount(0)
            )
        ]
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "outbound_after_send", lane.outbound_in_flight()
    )
    flow.advance(BRIDGE_LATENCY)
    results = flow.relay()
    _check(
        results[ARBITRUM_CHAIN_ID].get("outbound_after_send") == DEPOSIT_AMOUNT,
        "the supply did not enter the outbound bucket",
    )

    # After the relay: the token leg landed on the spoke, the settlement
    # receipt landed back on the hub, the outbound bucket is empty again.
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "settled_after_supply", lane.settled_remote_balance()
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "outbound_after_supply", lane.outbound_in_flight()
    )
    flow.simulator.observe(
        HYPEREVM_CHAIN_ID, "observation_after_supply", lane.observation()
    )
    results = flow.relay()
    observation = results[HYPEREVM_CHAIN_ID].get("observation_after_supply")
    credited = Amount(observation.tracked_idle)
    vault_after_deposit = results[ARBITRUM_CHAIN_ID].get("vault_usdc_after_deposit")
    _check(0 < credited <= DEPOSIT_AMOUNT, "nothing was credited on the spoke")
    _check(
        results[ARBITRUM_CHAIN_ID].get("settled_after_supply") == credited,
        "the hub's settled bucket disagrees with the spoke's credit",
    )
    _check(
        results[ARBITRUM_CHAIN_ID].get("outbound_after_supply") == 0,
        "the outbound bucket did not clear",
    )
    log.info("supply: sent=%d credited=%d", DEPOSIT_AMOUNT, credited)
    flow.numbers.update(
        {
            "sent": int(DEPOSIT_AMOUNT),
            "credited": int(credited),
            "vault_usdc_after_deposit": int(vault_after_deposit),
        }
    )
    return credited


def _attest(flow: _Flow, tag: str, observation_label: str, *, hub_idle: int = 0) -> int:
    """Step 5 (and 8 and 10): the two-key attestation of the spoke observation
    recorded under ``observation_label``, then the alpha's NAV refresh.
    Returns the attested settled balance."""
    lane, hub = flow.lane, flow.hub
    observation = flow.simulator.results[HYPEREVM_CHAIN_ID].get(observation_label)
    # The proposal is the dispatcher snapshot at the spoke block it was read
    # in, stamped with that block's number and time; it must still match the
    # dispatcher's state version when approved.
    proposal = lane.attestation(
        observation,
        remote_block=flow.spoke.current_block_number,
        remote_timestamp=flow.spoke.current_time,
        expiry=flow.hub.current_time + PROPOSAL_TTL,
    )
    # SIMULATION ONLY: the proposer and the approver are two addresses with no
    # keys here, driven from one process. Live they are two separate signers,
    # and that separation is the whole point of the two-key design.
    hub.add_call(
        lane.propose_balance(proposal), from_=BALANCE_PROPOSER, label=f"propose_{tag}"
    )
    results = flow.relay()
    proposed = next(
        c for c in results[ARBITRUM_CHAIN_ID].calls if c.label == f"propose_{tag}"
    )
    # The proposal id comes from the executor's BalanceProposed event, not
    # from a counter kept on the client.
    proposal_id = lane.proposal_id_from_logs(proposed.logs)

    flow.advance(APPROVAL_DELAY)
    hub.add_call(
        lane.approve_balance(proposal_id),
        from_=BALANCE_APPROVER,
        label=f"approve_{tag}",
    )
    hub.add_call(
        flow.hub_vault.update_markets_balances([CROSSCHAIN_MARKET]),
        from_=ALPHA,
        label=f"update_balances_{tag}",
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, f"executor_balance_{tag}", lane.get_balance()
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID,
        f"market_total_{tag}",
        flow.hub_vault.total_assets_in_market(CROSSCHAIN_MARKET),
    )
    results = flow.relay()
    attested = int(observation.accounted_balance)
    executor_balance = results[ARBITRUM_CHAIN_ID].get(f"executor_balance_{tag}")
    market_total = results[ARBITRUM_CHAIN_ID].get(f"market_total_{tag}")
    # The executor's NAV is the attested remote balance plus whatever idle it
    # holds on the hub; the vault's market total follows it.
    _check(
        executor_balance == attested + hub_idle,
        f"{tag}: executor NAV {executor_balance} != attested {attested} + idle {hub_idle}",
    )
    _check(
        market_total == executor_balance,
        f"{tag}: market total {market_total} != executor NAV {executor_balance}",
    )
    log.info(
        "attestation %s: proposal=%d settled=%d executor_nav=%d",
        tag,
        proposal_id,
        attested,
        executor_balance,
    )
    flow.numbers[f"attested_{tag}"] = attested
    return attested


def _deposit_into_spoke_vault(flow: _Flow, credited: Amount) -> Shares:
    """Step 6: DEPOSIT the dispatcher's idle into the spoke vault."""
    lane, hub = flow.lane, flow.hub
    hub.execute([lane.send_command(deposit_command(credited))])
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    flow.simulator.observe(
        HYPEREVM_CHAIN_ID, "spoke_shares", flow.spoke_vault.balance_of(EXECUTOR)
    )
    flow.simulator.observe(
        HYPEREVM_CHAIN_ID, "observation_after_deposit", lane.observation()
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "active_after_deposit", lane.has_active_command()
    )
    results = flow.relay()
    shares = Shares(results[HYPEREVM_CHAIN_ID].get("spoke_shares"))
    observation = results[HYPEREVM_CHAIN_ID].get("observation_after_deposit")
    _check(shares > 0, "the spoke vault issued no shares to the dispatcher")
    _check(
        observation.tracked_idle == 0, "the dispatcher still holds idle after DEPOSIT"
    )
    # The command slot is free again only once the hub processed the ACK.
    _check(
        results[ARBITRUM_CHAIN_ID].get("active_after_deposit") is False,
        "the DEPOSIT command is still active on the hub",
    )
    log.info("deposit: shares=%d", shares)
    flow.numbers["spoke_shares"] = int(shares)
    return shares


def _redeem_from_spoke_vault(flow: _Flow, shares: Shares) -> Amount:
    """Step 7: REDEEM the shares; the proceeds become the dispatcher's idle."""
    lane, hub = flow.lane, flow.hub
    # A later spoke block: the spoke vault's redemption delay separates the
    # deposit and the redeem.
    flow.advance(60)
    hub.execute([lane.send_command(redeem_command(shares))])
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    flow.simulator.observe(
        HYPEREVM_CHAIN_ID, "observation_after_redeem", lane.observation()
    )
    flow.simulator.observe(
        HYPEREVM_CHAIN_ID,
        "spoke_shares_after_redeem",
        flow.spoke_vault.balance_of(EXECUTOR),
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "active_after_redeem", lane.has_active_command()
    )
    results = flow.relay()
    remote_idle = Amount(
        results[HYPEREVM_CHAIN_ID].get("observation_after_redeem").tracked_idle
    )
    _check(
        results[HYPEREVM_CHAIN_ID].get("spoke_shares_after_redeem") == 0,
        "shares remain after REDEEM",
    )
    _check(remote_idle > 0, "REDEEM returned nothing to the dispatcher")
    _check(
        results[ARBITRUM_CHAIN_ID].get("active_after_redeem") is False,
        "the REDEEM command is still active on the hub",
    )
    log.info("redeem: remote_idle=%d", remote_idle)
    flow.numbers["remote_idle"] = int(remote_idle)
    return remote_idle


def _recall_and_claim(flow: _Flow, remote_idle: Amount) -> None:
    """Steps 9 and 10: recall the idle to the hub, attest the empty remote
    balance, claim the USDC into the vault."""
    lane, hub = flow.lane, flow.hub
    # ``min_return`` is enforced on the spoke against the amount it sends; the
    # hub credits whatever arrives after the token pool's fee and only emits
    # ReturnBelowMinimumCredited when that is below the floor.
    hub.execute([lane.recall(amount=remote_idle, min_return=remote_idle)])
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    flow.simulator.observe(ARBITRUM_CHAIN_ID, "idle_after_return", lane.idle_ledger())
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "settled_after_return", lane.settled_remote_balance()
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "pending_after_return", lane.pending_transfer_count()
    )
    flow.simulator.observe(
        HYPEREVM_CHAIN_ID, "observation_after_return", lane.observation()
    )
    results = flow.relay()
    idle = int(results[ARBITRUM_CHAIN_ID].get("idle_after_return"))
    settled = int(results[ARBITRUM_CHAIN_ID].get("settled_after_return"))
    _check(0 < idle <= remote_idle, "the recall credited nothing on the hub")
    _check(
        results[ARBITRUM_CHAIN_ID].get("pending_after_return") == 0,
        "a transfer is still pending",
    )
    # The executor debits the settled bucket by the amount the spoke sent, so
    # after a full recall nothing is left in it: the re-attestation of step 8
    # already marked any spoke-side loss, and a bridge fee is a realized loss
    # that lands in the idle, never in the settled bucket.
    _check(settled == 0, f"settled bucket not empty after the recall: {settled}")
    log.info("recall: idle=%d settled=%d", idle, settled)
    flow.numbers.update({"idle": idle, "settled_after_return": settled})

    # Step 10a: the dispatcher observes zero; the attesters mark it. The
    # executor's NAV is then exactly the idle it holds.
    flow.advance(flow.min_update_interval)
    _attest(flow, "residue", "observation_after_return", hub_idle=idle)

    # Step 10b: the claim pulls the idle from the executor into the vault.
    hub.execute([lane.claim(Amount(idle))])
    hub.add_call(
        flow.hub_vault.update_markets_balances([CROSSCHAIN_MARKET]),
        from_=ALPHA,
        label="update_balances_final",
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "vault_usdc_after", flow.hub_usdc.balance_of(HUB_VAULT)
    )
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID, "executor_usdc_after", flow.hub_usdc.balance_of(EXECUTOR)
    )
    flow.simulator.observe(ARBITRUM_CHAIN_ID, "idle_after_claim", lane.idle_ledger())
    flow.simulator.observe(ARBITRUM_CHAIN_ID, "nav_final", lane.get_balance())
    flow.simulator.observe(
        ARBITRUM_CHAIN_ID,
        "market_final",
        flow.hub_vault.total_assets_in_market(CROSSCHAIN_MARKET),
    )
    results = flow.relay()
    vault_after_deposit = flow.numbers["vault_usdc_after_deposit"]
    vault_after = int(results[ARBITRUM_CHAIN_ID].get("vault_usdc_after"))
    # The vault holds what it held after the deposit, minus whatever the round
    # trip cost (bridge fees, the spoke vault's fee); the executor keeps nothing
    # and the market values to zero.
    _check(
        vault_after == vault_after_deposit - DEPOSIT_AMOUNT + idle,
        f"vault USDC {vault_after} != {vault_after_deposit} - {DEPOSIT_AMOUNT} sent + {idle} back",
    )
    _check(
        results[ARBITRUM_CHAIN_ID].get("executor_usdc_after") == 0,
        "the executor still holds USDC",
    )
    _check(
        results[ARBITRUM_CHAIN_ID].get("idle_after_claim") == 0,
        "the idle ledger is not empty",
    )
    _check(
        results[ARBITRUM_CHAIN_ID].get("nav_final") == 0, "the executor NAV is not zero"
    )
    _check(
        results[ARBITRUM_CHAIN_ID].get("market_final") == 0,
        "the crosschain market is not zero",
    )
    flow.numbers.update(
        {
            "vault_usdc_after": vault_after,
            "round_trip_cost": int(DEPOSIT_AMOUNT) - idle,
        }
    )


def run_simulation(web3_arb: Web3, web3_hype: Web3) -> dict[str, int]:
    """Run the whole lifecycle and return its numbers (all in USDC units)."""
    flow = _open(web3_arb, web3_hype)
    log.info(
        "transaction plan (hub transactions, each one PlasmaVault.execute or one attestation call):"
    )
    for i, step in enumerate(
        (
            "depositor: approve + deposit USDC into the hub vault",
            "alpha: execute([supply]) -> CCIP token leg to the dispatcher, settlement receipt back",
            "proposer: proposeBalance; approver: approveBalance; alpha: updateMarketsBalances",
            "alpha: execute([DEPOSIT command]) -> dispatcher deposits into the spoke vault, ACK",
            "alpha: execute([REDEEM command]) -> dispatcher redeems, ACK",
            "proposer + approver: re-attest the redeemed amount; alpha: updateMarketsBalances",
            "alpha: execute([recall]) -> dispatcher returns the idle over CCIP",
            "proposer + approver: attest the empty remote balance; alpha: updateMarketsBalances",
            "alpha: execute([claim]) -> the USDC is back in the vault",
        ),
        start=1,
    ):
        log.info("  %d. %s", i, step)

    credited = _deposit_and_supply(flow, web3_arb)
    _attest(flow, "initial", "observation_after_supply")
    shares = _deposit_into_spoke_vault(flow, credited)
    remote_idle = _redeem_from_spoke_vault(flow, shares)
    # Step 8: mark the redeemed amount before recalling it. The dispatcher
    # carries a deposit at cost and the spoke vault's fee or a loss shows only
    # at REDEEM; attesting it now keeps it out of the settled bucket, where a
    # full recall could otherwise leave an un-attestable residue.
    flow.advance(flow.min_update_interval)
    _attest(flow, "redeemed", "observation_after_redeem")
    _recall_and_claim(flow, remote_idle)
    return flow.numbers


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    web3_arb = _connected_web3("ARBITRUM_PROVIDER_URL", ARBITRUM_CHAIN_ID)
    web3_hype = _connected_web3("HYPEREVM_PROVIDER_URL", HYPEREVM_CHAIN_ID)
    numbers = run_simulation(web3_arb, web3_hype)
    log.info("done: %s", numbers)
    log.info(
        "round trip: %d USDC units out, %d back, cost %d",
        numbers["sent"],
        numbers["idle"],
        numbers["round_trip_cost"],
    )


if __name__ == "__main__":
    main()
