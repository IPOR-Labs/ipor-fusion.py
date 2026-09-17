"""Build a Fusion vault on Base that parks cash on an off-chain venue (market 50).

The external-state market is how a vault holds capital the chain cannot see --
the margin leg of a delta-neutral strategy sitting on a perp venue, for
instance. The vault's own accounting cannot observe that capital, so its value
is a *custodian attestation*: two independent custodians propose and confirm a
figure on a per-vault ExternalStateExecutor, and the market's balance fuse
values that figure into the vault's NAV.

This example shows the whole lifecycle an alpha drives, end to end:

  1. connect a ``Web3Context`` to Base;
  2. preview a vault deployment with ``FusionFactory.clone(...).call()`` and read
     its state-dependent CREATE addresses for the atomic simulation, plus the
     unsigned creation calldata via ``.calldata``;
  3. bootstrap the vault's access control -- five ``Roles`` grants across three
     addresses, each on the address that has to hold it;
  4. install the external-state operation fuse and its balance fuse;
  5. grant exactly the market-50 substrates the flow needs, and nothing more;
  6. a user deposits USDC;
  7. the alpha enters the market: USDC moves vault -> executor -> venue, and the
     executor books it against a balance account;
  8. custodian A proposes the off-venue value, custodian B confirms it, and the
     alpha refreshes the vault's cached NAV;
  9. the user files a scheduled-withdrawal request;
 10. the alpha exits the market -- the venue returns the cash, the executor hands
     it back to the vault -- and releases funds for that request;
 11. a mismatched confirmation is rejected, and the script reports it.

A scheduled withdrawal takes three steps and two parties: the user calls
``requestShares``, the alpha calls ``releaseFunds`` once the money is back, and
the user then calls ``redeemFromRequest`` to take it. Steps 9 and 10 above are
the first two. The two-step shape exists for exactly the situation modelled
here -- the vault's assets are not all in the vault, so the request is what
gives the alpha notice and time to unwind the venue position.

Out of scope on purpose: the user's own redemption, which is the third step and
the user's transaction to send rather than the alpha's, plus a real venue
integration and broadcasting.

Everything runs inside ``eth_simulateV1`` against a pinned Base block -- nothing
is signed or broadcast. To put a flow on-chain, hand the ``.calldata`` each
builder produces to your own signer, each step signed by the actor authorized for
it. Constructs that exist only because this runs under simulation are flagged
inline as SIMULATION ONLY, with what production does instead; there are more of
them here than in the simpler examples, because a lifecycle five separate
signers would drive over days is being compressed into one script.

Two departures from the simpler examples, both deliberate. The run makes **two**
``eth_simulateV1`` calls, not one: a discovery pass whose only job is to learn
two values the real batch needs as arguments (see ``_discovery_pass``), then the
lifecycle batch. And that batch ends on a call that is *meant* to revert -- the
rejected-confirmation demo -- so the returned ``SimulationResult`` carries one
failed call and its ``all_success`` is ``False`` on a completely successful run.
Do not copy ``if result.all_success:`` from here; this example checks that
exactly one call failed and that it failed for the expected reason.

Run it (the shell snippets assume a POSIX shell -- bash or zsh):

    export BASE_PROVIDER_URL="https://base-mainnet.g.alchemy.com/v2/YOUR_KEY"
    uv run python examples/external_state_margin_leg_base.py

The provider must be an archive node that implements ``eth_simulateV1`` (Alchemy
and other geth/reth-based providers do).
"""

from __future__ import annotations

import logging
import os
import sys

from eth_abi.abi import decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    ExternalStateExecutor,
    ExternalStateSubstrates,
    FuseAction,
    PlasmaVault,
    PriceOracleMiddlewareManager,
    Roles,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    WithdrawManager,
    is_simulate_v1_supported,
)
from ipor_fusion.core import FusionFactory
from ipor_fusion.core.fusion_factory import FusionInstance
from ipor_fusion.fuses import ExternalStateOperationFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId, Period, Shares

log = logging.getLogger("external_state_margin_leg_base")

# ── Live IPOR / Base infrastructure (do NOT change) ──────────────────────────
# Every address below is a live, canonical deployment. Provenance for all IPOR
# Fusion addresses: https://github.com/IPOR-Labs/ipor-abi
#   mainnet/mainnet-base-fusion/addresses.json

BASE_CHAIN_ID = ChainId(8453)

# IporFusionFactoryProxy on Base. Its clone(...) takes the six arguments
# assembled in clone_args() below.
BASE_FUSION_FACTORY = Web3.to_checksum_address(
    "0x1455717668fA96534f675856347A973fA907e922"
)

# Native (Circle-issued) USDC on Base, 6 decimals. Not the bridged USDbC, a
# distinct ERC-20 with its own price feed. It is the vault's underlying AND the
# asset parked at the venue -- perp venues margin in USDC, and a venue that
# credits by sender needs the executor itself to send it, so a second asset plus
# a swap would only add a market this example does not need.
BASE_USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# External-state market (IporFusionMarkets.EXTERNAL_STATE).
EXTERNAL_STATE_MARKET = MarketId(IporFusionMarkets.EXTERNAL_STATE)

# External-state *operation* fuse (enter/exit) -- the functional fuse. It also
# lazily deploys the per-vault ExternalStateExecutor on the first enter.
BASE_EXTERNAL_STATE_OPERATION_FUSE = Web3.to_checksum_address(
    "0x01f05736B79C7abcFbeeCe76B1C27e54f7A07D03"
)
# External-state *balance* fuse -- distinct from the operation fuse; this is what
# lets total_assets_in_market(EXTERNAL_STATE_MARKET) value the attested figure.
BASE_EXTERNAL_STATE_BALANCE_FUSE = Web3.to_checksum_address(
    "0xCDEb32ACF766587b5Eac9dbfB875145ec05E3517"
)

# ── Simulation parameters (safe to adjust) ───────────────────────────────────
#
# Every address below is an impersonated simulation actor: the simulator runs
# with validation disabled, so it can act as any of them without a key. They are
# NOT accounts you control -- there is nothing to replace and nothing to
# broadcast here.
#
# Five of them -- OWNER, ALPHA, DEPOSITOR, CUSTODIAN_A and CUSTODIAN_B -- are the
# actors this flow needs, kept on five distinct addresses because they must NOT
# share one in production. (The two venue addresses and BASE_USDC_WHALE are not
# actors in that sense: they stand in for an off-chain venue and for funding the
# depositor.) The vault authorizes them two different ways: OWNER, ALPHA and
# DEPOSITOR hold `Roles.*` grants in the AccessManager, while the two custodians
# hold no role at all -- they are authorized by a market-50 CUSTODIAN substrate,
# cached on the executor. The five:
#   OWNER      -- governance/atomist; can reconfigure the whole vault, including
#                 every substrate grant below. A cold key or multisig. (A fresh
#                 clone forces OWNER to self-grant ATOMIST, so owner and atomist
#                 coincide during bootstrap.)
#   ALPHA      -- the online operator; drives execute() and refreshes market
#                 balances, and nothing else. Collapsing it into OWNER means a
#                 compromised alpha key becomes full governance control -- and
#                 the TARGET grant below makes that especially costly here.
#   DEPOSITOR  -- an end user; only ever holds, deposits and requests funds.
#   CUSTODIAN_A/B -- the two attesting parties. The contract rejects a confirm
#                 from the proposer, so a single compromised custodian key
#                 cannot move the vault's NAV on its own. That property only
#                 holds while the two keys live in genuinely separate systems;
#                 two addresses driven by one process buy nothing.

# Governance/atomist. Any address; here it happens to be a random EOA.
OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")

# The alpha operator. Anvil account #0, a well-known test address.
ALPHA = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

# The depositor -- the end user putting funds in. Anvil account #1.
DEPOSITOR = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")

# The two custodians. Anvil accounts #2 and #3.
CUSTODIAN_A = Web3.to_checksum_address("0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
CUSTODIAN_B = Web3.to_checksum_address("0x90F79bf6EB2c4f870365E785982E1f101E93b906")

# SIMULATION ONLY (placeholder addresses). Two distinct things, easy to conflate.
#
# VENUE_DEPOSIT_ADDRESS is where the executor sends the margin. It appears only
# as an argument inside the transfer calldata and is NOT a substrate: the TARGET
# grant names the token contract and the selector, never a destination.
#
# VENUE_BALANCE_ACCOUNT is a label. No money is ever sent to it. It is the key of
# a mapping on the executor, granted as the BALANCE_ACCOUNT substrate, and the
# address the custodians name when they attest "this bucket holds 900 USDC".
# On a venue that credits deposits by sender, the natural label is the EXECUTOR's
# own address, since that is the identity the venue assigns -- which needs
# createExecutor() and syncSubstrates() before the first enter, so this example
# uses a plain third address instead.
#
# Both are deliberately obvious placeholders rather than real deployments: this
# example runs on Base, while the venue a production vault would fund -- e.g. the
# Hyperliquid Core deposit gateway -- lives on HyperEVM, a chain this SDK does
# not reach. Pasting that address here would give it borrowed provenance: on Base
# it is an unrelated account, and a reader who copied it would send funds
# nowhere.
VENUE_DEPOSIT_ADDRESS = Web3.to_checksum_address(
    "0x000000000000000000000000000000000000BEEF"
)
VENUE_BALANCE_ACCOUNT = Web3.to_checksum_address(
    "0x000000000000000000000000000000000000CAFE"
)

# A Base address holding ample USDC, used to fund the depositor via an
# impersonated transfer inside the simulation. Morpho Blue's core contract
# (~150M USDC) -- a protocol this example never touches, so funding cannot
# perturb the flow under test.
BASE_USDC_WHALE = Web3.to_checksum_address("0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb")

# Pinned Base block, after both external-state fuses were deployed. Pinning makes
# the simulation deterministic and immune to mainnet state drift. It requires an
# archive node that can serve state at this height -- if your provider cannot (an
# error mentioning missing trie/state, or a pruned block), BUMP THIS to a recent
# Base block your node can reach.
PINNED_BLOCK = 51169852  # Base, ~2026-09-11

DEPOSIT_AMOUNT = Amount(1_000_000_000)  # 1,000 USDC (6 decimals)
# What the alpha parks at the venue. The rest stays idle in the vault.
MARGIN_AMOUNT = Amount(900_000_000)  # 900 USDC
# What the depositor asks to withdraw on the scheduled path.
WITHDRAW_REQUEST_AMOUNT = Amount(500_000_000)  # 500 USDC
# NAV must be conserved across the round trip up to oracle-conversion dust.
NAV_DUST_TOLERANCE = Amount(100)  # 0.0001 USDC

# Market-50 guard values, mirroring the reference configuration in the contracts
# repo.
#
# READ THIS BEFORE COPYING THE SET: granting all four does NOT mean all four
# are enforced. Some are enforced by the executor, some only by the market's
# pause pre-hook (ExternalStatePausePreHook, a live Base deployment in the IPOR
# ABI registry), and THIS EXAMPLE DOES NOT REGISTER THAT HOOK -- installing a
# pre-hook is its own workflow, and an example covers one. Where that leaves
# each of the four:
#   DUST_THRESHOLD / MIN_UPDATE_INTERVAL -- fully enforced, by the executor
#                     itself, pre-hook or not.
#   STALENESS_MAX  -- HALF enforced. Its TTL job works: confirmBalance rejects
#                     a proposal older than this. Its other job -- gating user
#                     operations once the CONFIRMED value goes stale -- belongs
#                     to the pre-hook, so nothing here bounds how long a stale
#                     NAV may stand (see market_substrates()).
#   BIG_CHANGE_BPS -- INERT. The balance fuse writes a pause flag into vault
#                     storage on a breach, and without the pre-hook nothing
#                     reads it, so deposits and redemptions keep clearing.
# A production vault that skips the pre-hook has granted four guards and bought
# two and a half.
STALENESS_MAX = Period.DAY  # pending-proposal TTL; user-op gating needs the hook
BIG_CHANGE_BPS = 500  # a confirmed move above 5% raises the (unread) pause flag
DUST_THRESHOLD_PERCENT = 100  # executor may hold at most 1 whole token per asset
MIN_UPDATE_INTERVAL = Period.HOUR  # minimum gap between two confirmed updates

# SIMULATION ONLY (time advancement). Each of these is a gap standing in for real
# elapsed time between transactions that separate parties send on their own
# schedules. In production nothing advances time; you wait, and the timestamps
# come from whatever block each transaction lands in.
# The first two are plausible operational cadences: a deposit credits at the
# venue within minutes, but attestation services run on a schedule, and the
# second custodian has to query the venue independently before it can agree.
PROPOSE_DELAY = Period.HOUR  # venue confirms receipt, custodian A attests
CONFIRM_DELAY = 5 * Period.MINUTE  # custodian B independently verifies
# A user files whenever they like, so this one is arbitrary; it is only long
# enough to make clear the request is unrelated to the attestation before it.
REQUEST_DELAY = 1337 * Period.MINUTE  # the user files a withdrawal request
# Unwinding a perp margin leg is the slow step in reality: closing positions,
# the venue's own withdrawal processing, possibly a bridge -- hours to a day.
# Twelve hours is a realistic middle, and it has to stay inside the withdraw
# window, which the factory defaults to 24h from the request. The window is NOT
# what release_funds checks -- that call only requires a timestamp already in
# the past -- it is what keeps the released request REDEEMABLE, which is the
# `can_withdraw` that _assert_unwind reads and the condition the depositor's
# own redeemFromRequest needs later.
RELEASE_DELAY = 12 * Period.HOUR  # the venue returns the margin, alpha releases
DEMO_DELAY = Period.MINUTE  # the rejected-confirmation demo at the end

# The running sum of those gaps, as offsets from the pinned block's timestamp.
# This is the ONE representation of the schedule: every block is opened with
# `next_block().with_block_time_shift(<offset>)`, which sets that block's time to
# baseline + offset ABSOLUTELY, and the two calls that need a timestamp as an
# *argument* -- the confirm hash binds the propose's timestamp, release_funds
# takes the instant up to which requests are released -- read the same constants.
# Because each offset is absolute rather than a step from the block before it,
# inserting a step leaves every other block where it was.
PROPOSE_OFFSET = PROPOSE_DELAY
CONFIRM_OFFSET = PROPOSE_OFFSET + CONFIRM_DELAY
REQUEST_OFFSET = CONFIRM_OFFSET + REQUEST_DELAY
RELEASE_OFFSET = REQUEST_OFFSET + RELEASE_DELAY
DEMO_OFFSET = RELEASE_OFFSET + DEMO_DELAY

# A hash that is deliberately not the pending proposal's, for the rejection demo.
WRONG_PROPOSAL_HASH = b"\xde\xad" + b"\x00" * 30

# ── Derived from deployed signatures (NOT tunable) ───────────────────────────
# Each of these is keccak over a signature the deployed contracts declare.
# Editing one does not reconfigure anything -- it just stops matching, so they
# sit outside the adjustable block above.

# `transfer(address,uint256)` on the underlying: the one selector the executor is
# allowed to call, and how the margin reaches the venue. See market_substrates().
TRANSFER_SELECTOR = function_signature_to_4byte_selector("transfer(address,uint256)")

# The error the executor raises when the hash does not match the pending
# proposal: ExternalStateExecutorProposalHashMismatch(bytes32 expected,
# bytes32 given). Pinned as a selector so the demo below proves *which* guard
# fired, not merely that something reverted.
PROPOSAL_HASH_MISMATCH_SELECTOR = function_signature_to_4byte_selector(
    "ExternalStateExecutorProposalHashMismatch(bytes32,bytes32)"
)
# topic0 of ExternalStateExecutorDeployed(address executor, uint256 marketId),
# emitted in the vault's context when the first enter creates the executor.
EXECUTOR_DEPLOYED_TOPIC = Web3.keccak(
    text="ExternalStateExecutorDeployed(address,uint256)"
).to_0x_hex()


# ── Inlined helpers (kept local so this file is self-contained) ──────────────


def _check(condition: bool, message: str) -> None:
    """Fail loud unconditionally -- unlike ``assert``, survives ``python -O``."""
    if not condition:
        raise AssertionError(message)


def _assert_all_success(result: SimulationResult) -> None:
    """Raise with a readable summary if any simulated call reverted."""
    if result.all_success:
        return
    failed = [(c.label, c.error) for c in result.failed_calls]
    raise AssertionError(
        f"simulation calls failed: {failed} (reason={result.revert_reason})"
    )


def _assert_only_expected_failure(result: SimulationResult, label: str) -> bytes:
    """Assert the batch failed on exactly one call -- the labeled demo -- and
    return that call's revert payload.

    The rejected-confirmation demo is *meant* to revert, so the batch cannot be
    checked with ``_assert_all_success``. Everything else still must succeed:
    a second failure anywhere means the happy path broke and the run is not a
    success, whatever the demo did.
    """
    failed = result.failed_calls
    if len(failed) != 1 or failed[0].label != label:
        raise AssertionError(
            f"expected exactly one failed call ({label!r}), got "
            f"{[(c.label, c.error) for c in failed]}"
        )
    return bytes(failed[0].return_data)


def _connected_web3() -> Web3:
    """Build a Web3 client from BASE_PROVIDER_URL, or exit with a clear message."""
    url = os.environ.get("BASE_PROVIDER_URL")
    if not url:
        sys.exit("BASE_PROVIDER_URL is not set -- export your Base RPC URL first.")
    web3 = Web3(Web3.HTTPProvider(url))
    if not web3.is_connected():
        sys.exit("cannot reach the RPC at BASE_PROVIDER_URL -- check the endpoint.")
    if not is_simulate_v1_supported(web3):
        sys.exit(
            "the provider does not implement eth_simulateV1 -- use an archive node."
        )
    return web3


def _logs_of(result: SimulationResult, label: str) -> list[dict]:
    """Every log emitted by the labeled call in a finished simulation."""
    for call in result.calls:
        if call.label == label:
            return call.logs
    raise AssertionError(f"no simulated call labeled {label!r}")


def _executor_from_logs(logs: list[dict], vault: ChecksumAddress) -> ChecksumAddress:
    """Read the executor address out of the ExternalStateExecutorDeployed log
    that `vault` emitted.

    Both event fields are unindexed, so the address is the first word of `data`.
    The emitter to match is the VAULT, not the executor -- the library that emits
    this runs in a delegatecall from the vault -- and matching it is not
    optional: this is handed the logs of every ``execute`` call in the batch,
    here the enter and its whole call tree, which emits under three addresses --
    the VAULT (the fuse runs by delegatecall, so it never emits under its own),
    the freshly created EXECUTOR, and the TOKEN on the transfer out. A
    topic-only match would accept a same-signature event from any of them and
    hand back whatever address it named.

    Decoded by hand because the SDK has no public reader for this event that
    checks the emitter and accepts simulation-shaped rows, and because
    ``ExternalStateExecutor`` needs an executor address before it can read
    anything -- this log is how that address is learned. The one check available
    for the propose event and not for this one is the hash check
    ``find_balance_proposed`` makes: ``ExternalStateExecutorDeployed`` carries no
    hash to verify itself against.

    A log missing either field is skipped rather than fatal, matching what the
    SDK's readers do with one it cannot attribute: unattributable is not the same
    as foreign, but it is equally not proof the log is ours.
    """
    for entry in logs:
        topics = entry.get("topics") or ()
        if not topics or topics[0] != EXECUTOR_DEPLOYED_TOPIC:
            continue
        emitter = entry.get("address")
        if emitter is None or Web3.to_checksum_address(emitter) != vault:
            continue
        executor, _market_id = decode(
            ["address", "uint256"], bytes.fromhex(entry["data"][2:])
        )
        return Web3.to_checksum_address(executor)
    raise AssertionError(
        f"no ExternalStateExecutorDeployed log from vault {vault} in the enter"
    )


# ── Pure builders (no chain access -- safe to import and unit-test offline) ───


def clone_args() -> dict:
    """Arguments for FusionFactory.clone(...).

    The preview and the in-batch create MUST use identical args: same args plus
    the same factory state yields the same CREATE addresses in one simulation.
    """
    return {
        "asset_name": "IPOR USDC External-State Vault (example)",
        "asset_symbol": "ipUSDCes",
        "underlying_token": BASE_USDC,
        "redemption_delay_seconds": 0,
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def unsigned_clone_calldata() -> bytes:
    """Unsigned creation calldata (selector + ABI-encoded args) for the deploy.

    Built with a ctx-less encoder, so it needs no provider. Hand these bytes to
    an external signer / multisig instead of calling ``.send()``. NOTE: the owner
    encoded here is the simulation ``OWNER`` (a throwaway EOA) -- set your real
    owner in ``clone_args()`` before deploying, or the vault is owned by it.
    """
    return FusionFactory.encoder(BASE_FUSION_FACTORY).clone(**clone_args()).calldata


def market_substrates() -> list[bytes]:
    """The complete market-50 substrate grant, as packed bytes32 values.

    Market 50 substrates are typed: a one-byte tag plus a payload, built by
    ``ExternalStateSubstrates`` so the layout matches the on-chain library. All
    eight tags the market defines appear here, and the set is deliberately
    minimal -- one asset, one call the executor may make, one balance account,
    two custodians, and the four guards.

    The TARGET grant is the one to read carefully.

    It permits the executor to call ``transfer(address,uint256)`` on USDC, which
    is what funding a venue *is*: the executor must be the sender, because venues
    that credit by sender identify the account by who paid. That makes the
    granted TARGET contract the same address as the granted ASSET.

    The operation fuse's own source names that overlap and warns against it: a
    substrate-grant review is told to confirm ``target`` is not among the granted
    assets. The reason is that the two together let the alpha move the asset
    directly, by a call the executor is authorized to make, while the executor's
    tracked balance -- the figure market 50 reports as NAV -- stays where it was.

    That rule is written for the common case, where the overlap is avoidable: a
    venue with a deposit contract is funded by granting TARGET on *that* contract
    and its deposit selector, leaving the token itself ungranted. Where the only
    way in is a bare token transfer, as here, the overlap cannot be avoided. Such
    a deployment is not passing a clean review -- it is accepting a documented
    risk, and it owes the compensating controls the chain will not provide: a
    market limit capping the exposure, monitoring of the executor's outgoing
    transfers, and strict alpha-key hygiene.

    What the grant permits is far broader than what this example does with it.
    ``enter`` transfers its ``amount`` from the vault to the executor *before*
    the actions run, so a single ``execute`` batch can route the vault's entire
    idle underlying to an arbitrary destination -- not merely whatever the
    executor happened to be holding. A substrate cannot narrow that: a TARGET is
    ``(contract, selector)`` and has no destination field, so "only to the venue"
    is not expressible on-chain. Nor does this example set a market-50 limit,
    which is the one on-chain bound that would cap the amount.

    Nor would NAV notice, and not merely for a while. This market's value is a
    custodian attestation, not an on-chain balance, so a transfer to the wrong
    address leaves the reported figure untouched until a custodian reports a
    different one -- and nothing on-chain compels them to. STALENESS_MAX is not
    the bound it looks like: without the pause pre-hook this vault does not
    register, it expires PENDING PROPOSALS and gates nothing else (see the
    guard-constant block above). A vault that wants a bound on how long NAV may
    stay wrong has to register the pre-hook and monitor off-chain; this one has
    neither.

    That is the concrete reason the alpha key here is hot, least-privilege, and
    must never share an address with the owner/atomist: the alpha can move the
    parked asset, and only the atomist decides which asset that is.

    (Margining in an asset the vault does not otherwise hold would narrow the
    blast radius to whatever is on hand in that asset -- but only while no
    granted fuse can produce it. ``execute`` takes a batch, so a swap fuse for
    that pair lets the alpha mint the transferable asset and forward it in the
    same transaction, and the cap is gone.)
    """
    return [
        # The asset that may move vault <-> executor and be accounted for.
        ExternalStateSubstrates.asset(BASE_USDC),
        # The single call the executor may make. See the caveats above.
        ExternalStateSubstrates.target(BASE_USDC, TRANSFER_SELECTOR),
        # The bucket enter/exit book against, and the label the custodians
        # attest to. The enter's balance_account, this grant and the
        # propose/confirm account must all be the same address, or the
        # attestation overwrites a bucket the enter never credited. Note it is
        # NOT the transfer destination -- that never appears as a substrate.
        ExternalStateSubstrates.balance_account(VENUE_BALANCE_ACCOUNT),
        # Two custodians: proposing and confirming must come from different
        # addresses, so one grant would make the market unusable.
        ExternalStateSubstrates.custodian(CUSTODIAN_A),
        ExternalStateSubstrates.custodian(CUSTODIAN_B),
        # Guards. STALENESS_MAX and BIG_CHANGE_BPS are mandatory: the executor
        # reads a zero as "unset" and refuses to build its substrate cache,
        # which would make the very first enter revert.
        ExternalStateSubstrates.staleness_max(STALENESS_MAX),
        ExternalStateSubstrates.big_change_bps(BIG_CHANGE_BPS),
        ExternalStateSubstrates.dust_threshold(DUST_THRESHOLD_PERCENT),
        ExternalStateSubstrates.min_update_interval(MIN_UPDATE_INTERVAL),
    ]


def venue_transfer_calldata(amount: Amount) -> bytes:
    """``USDC.transfer(VENUE_DEPOSIT_ADDRESS, amount)`` calldata, run from the executor.

    Encoded with a ctx-less ERC20 wrapper -- pure bytes, no chain access.
    """
    return ERC20.encoder(BASE_USDC).transfer(VENUE_DEPOSIT_ADDRESS, amount).calldata


def build_enter_action(amount: Amount) -> FuseAction:
    """Encode the alpha's entry into the external-state market.

    One fuse call does three things in order: move ``amount`` USDC from the vault
    to the executor, book it against ``VENUE_BALANCE_ACCOUNT``, then run the actions from
    the executor's context -- here, forwarding the same amount to the venue.

    The forwarding action is not optional decoration. Propose and confirm both
    run a dust check that rejects an executor still holding more than
    DUST_THRESHOLD_PERCENT of a token, so an enter that parked funds on the
    executor would make the custodians' next call revert.
    """
    return ExternalStateOperationFuse(BASE_EXTERNAL_STATE_OPERATION_FUSE).enter(
        asset=BASE_USDC,
        amount=amount,
        balance_account=VENUE_BALANCE_ACCOUNT,
        actions=[(BASE_USDC, venue_transfer_calldata(amount))],
    )


def build_exit_action(amount: Amount) -> FuseAction:
    """Encode the alpha's exit from the external-state market.

    The mirror of the enter, and the order inverts: actions first, then the
    tracked balance is decremented and ``amount`` USDC is pulled executor ->
    vault. The exit carries no actions here, because the funds have to be back on
    the executor before it can hand them over, and it is the venue -- not the
    vault -- that sends them (see the staging step in ``run_simulation``).

    Exiting the exact amount that was entered works only because the whole run
    reads one block's state: ``next_block`` moves ``block.timestamp`` but not the
    price feed's stored answer, so enter and exit convert through an identical
    USDC price and the tracked balance matches to the unit. Against a real chain
    the two conversions straddle a price update, the exit's value can round above
    what the enter booked, and ``removeBalance`` reverts -- so production exits
    the tracked balance it reads back, or leaves a small buffer.
    """
    return ExternalStateOperationFuse(BASE_EXTERNAL_STATE_OPERATION_FUSE).exit(
        asset=BASE_USDC,
        amount=amount,
        balance_account=VENUE_BALANCE_ACCOUNT,
        actions=[],
    )


# ── Simulation (the whole build + lifecycle, no broadcast) ───────────────────


def _queue_bootstrap(
    sim: VaultSimulator,
    factory: FusionFactory,
    plasma_vault: PlasmaVault,
    access_manager: AccessManager,
    usdc: ERC20,
    plan: list[str],
) -> None:
    """Queue everything up to and including the alpha's enter.

    This setup sequence is queued twice: once by ``_discovery_pass``, then again
    by the lifecycle batch in ``run_simulation``, which appends the rest of the
    flow after it. Keeping it in one function is what guarantees the two are
    identical -- the executor address the discovery pass learns is only valid for
    the lifecycle batch if nothing before the enter differs.
    """
    # The clone MUST be the first queued call. Each component is deployed with
    # `Clones.clone` -- plain CREATE -- so its address is a function of the
    # deploying sub-factory's NONCE, not of the arguments: any earlier call that
    # made those factories deploy anything would shift every address previewed
    # in run_simulation.
    sim.add_call(call=factory.clone(**clone_args()), from_=OWNER, label="clone")
    plan.append("deploy the vault stack (clone)")

    # Role bootstrap order: a fresh clone grants OWNER only OWNER_ROLE. The admin
    # chain is OWNER -> ATOMIST -> {ALPHA, FUSE_MANAGER, WHITELIST,
    # UPDATE_MARKETS_BALANCES}, so OWNER must self-grant ATOMIST before it can
    # grant the rest. OWNER is its OWN admin -- nothing sits above it, ADMIN_ROLE
    # included -- which is what makes the owner key the root of the vault and
    # worth the 14-day delay production puts on it.
    #
    # Every grant here passes Period(0) -- no execution delay -- to keep the
    # simulation legible. Production differs per role: 14 days on OWNER, 3 days
    # on ATOMIST, 0 on GUARDIAN so it can cancel a queued malicious action
    # instantly. Delays can be raised at once but only lowered after the full
    # notice period. Figures from docs.ipor.io -> build-on-fusion -> atomists ->
    # vault-configuration-step-by-step -> timelocks-and-execution-delays.
    #
    # A delayed role does not merely wait: it must schedule() an operation and
    # execute() it later, so adding delays here would turn each setup call into
    # three. Configuring them is its own workflow, not this example's.
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, OWNER, Period(0)),
        from_=OWNER,
    )
    # FUSE_MANAGER installs fuses, and a malicious fuse can drain the vault -- it
    # is as sensitive as ATOMIST. Colocating it on the owner adds no power the
    # owner lacks, which is why it is safe here; the documented preference is
    # still a dedicated time-locked account, since fuses shape the strategy.
    # Either way, never an online key.
    sim.add_call(
        call=access_manager.grant_role(Roles.FUSE_MANAGER_ROLE, OWNER, Period(0)),
        from_=OWNER,
    )
    # ALPHA drives execute() and, with UPDATE_MARKETS_BALANCES, refreshes the
    # cached NAV after a confirmation. It must stay a separate address from OWNER
    # in production. Its Period(0) is NOT a simulation simplification: the alpha
    # carries no timelock by design, because it can only move funds inside the
    # limits and substrates the slower roles pre-approved. How much that
    # constrains the alpha depends entirely on what those roles approved -- and
    # in THIS vault it constrains very little, since the TARGET grant is an
    # unbounded transfer of the underlying and no market-50 limit is set. See
    # market_substrates(); the untimelocked alpha is safe only in proportion to
    # the care taken there.
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ALPHA, Period(0)),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(
            Roles.UPDATE_MARKETS_BALANCES_ROLE, ALPHA, Period(0)
        ),
        from_=OWNER,
    )
    # A fresh clone is WHITELIST-gated (private), so the depositor needs WHITELIST.
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, DEPOSITOR, Period(0)),
        from_=OWNER,
    )
    plan.append(
        "grant roles: ATOMIST, FUSE_MANAGER, ALPHA, UPDATE_MARKETS_BALANCES, WHITELIST"
    )

    # Wire the market: operation fuse + balance fuse + the substrate grant.
    sim.add_call(
        call=plasma_vault.add_fuses([BASE_EXTERNAL_STATE_OPERATION_FUSE]), from_=OWNER
    )
    sim.add_call(
        call=plasma_vault.add_balance_fuse(
            EXTERNAL_STATE_MARKET, BASE_EXTERNAL_STATE_BALANCE_FUSE
        ),
        from_=OWNER,
    )
    # Every substrate must be granted BEFORE the first enter, because the first
    # enter is what deploys the executor and the executor is built from the
    # grants standing at that moment.
    #
    # Two mechanisms, and the difference decides what a revocation actually
    # does. The operation fuse reads TARGET, ASSET and BALANCE_ACCOUNT LIVE from
    # the vault on every call, so those grants take effect and stop taking
    # effect the instant the atomist changes them. The executor additionally
    # keeps its own CACHE -- the custodian list, the balance accounts, the
    # dust-checked assets, the guard values -- populated at deployment and
    # refreshed only by syncSubstrates() on the executor.
    #
    # Adding is uniformly slow: a newly granted custodian or balance account is
    # unusable until that sync runs. Revoking is ASYMMETRIC, which is the part
    # to keep in a runbook. A revoked balance account is refused immediately,
    # because propose/confirm check the vault before consulting the cache. A
    # revoked CUSTODIAN is not: `onlyCustodian` reads the cache alone, so a
    # compromised key keeps proposing and confirming until someone syncs.
    # Revoke and sync in the same step.
    sim.add_call(
        call=plasma_vault.grant_market_substrates(
            EXTERNAL_STATE_MARKET, market_substrates()
        ),
        from_=OWNER,
    )
    plan.append("add the external-state operation + balance fuses, grant substrates")

    sim.observe(
        "granted_substrates", plasma_vault.get_market_substrates(EXTERNAL_STATE_MARKET)
    )

    # Fund the depositor, then deposit. SIMULATION ONLY: the whale -> depositor
    # transfer is impersonated and has NO production equivalent -- a real
    # depositor already holds their USDC. The approve + deposit that follow are
    # exactly what a real depositor signs.
    sim.add_call(call=usdc.transfer(DEPOSITOR, DEPOSIT_AMOUNT), from_=BASE_USDC_WHALE)
    sim.add_call(
        call=usdc.approve(plasma_vault.address, DEPOSIT_AMOUNT), from_=DEPOSITOR
    )
    sim.add_call(call=plasma_vault.deposit(DEPOSIT_AMOUNT, DEPOSITOR), from_=DEPOSITOR)
    plan.append("fund the depositor and deposit 1,000 USDC")

    sim.observe("total_assets_after_deposit", plasma_vault.total_assets())
    sim.observe("vault_usdc_after_deposit", usdc.balance_of(plasma_vault.address))
    # The withdrawal request further down is denominated in shares, so the
    # depositor has to convert first. Reading it rather than assuming a share
    # price keeps the example correct for a vault that has already accrued -- and
    # reading it *here*, inside the setup sequence, is what lets the discovery
    # pass hand the value back before the request is encoded (see
    # _discovery_pass).
    sim.observe(
        "requested_shares", plasma_vault.convert_to_shares(WITHDRAW_REQUEST_AMOUNT)
    )
    # The venue's starting balance: a placeholder address on a live chain may
    # already hold the token, so the checks below compare deltas, not totals.
    sim.observe("venue_usdc_before_enter", usdc.balance_of(VENUE_DEPOSIT_ADDRESS))

    # The alpha parks the margin at the venue. This single execute() deploys the
    # executor, moves the USDC out through it, and books the amount against the
    # balance account.
    sim.execute([build_enter_action(MARGIN_AMOUNT)])
    plan.append("alpha parks 900 USDC at the venue (enter)")


def _discovery_pass(
    web3: Web3,
    preview: FusionInstance,
    factory: FusionFactory,
    plasma_vault: PlasmaVault,
    access_manager: AccessManager,
    usdc: ERC20,
) -> tuple[ChecksumAddress, Shares, SimulationResult]:
    """SIMULATION ONLY (runs the setup sequence an extra time): learn two values
    the rest of the batch needs as call arguments.

    The executor is created by the first enter, with plain CREATE from the vault,
    so its address exists only once that call has run -- and every call in an
    ``eth_simulateV1`` batch is encoded before any of them execute. The share
    count behind the withdrawal request has the same problem from the other end:
    the vault does not exist at the pinned block, so nothing can convert an asset
    amount for it yet. The way out is to run the setup sequence twice: this pass
    reads the executor out of the ``ExternalStateExecutorDeployed`` log and the
    shares out of an observation, and ``run_simulation`` then replays the
    identical sequence with the remaining calls appended.

    Production never does this. The alpha sends the enter and reads the executor
    from that transaction's receipt -- or, at any point afterwards, from
    ``ExternalStateExecutor.for_vault(ctx, vault)``, which resolves it out of the
    vault's own storage and is what a service restarting with nothing but the
    vault address uses. The depositor's client calls
    ``convert_to_shares(...).call()`` against the live vault. One transaction and
    one read, no replay. (``for_vault`` is no help here: it is a live storage
    read, not a queued ``Call``, and at the pinned block the vault does not exist
    yet.) Nothing else depends on the repetition: identical calls on an identical
    base block produce an identical vault nonce, hence an identical executor
    address.

    Returns the executor address, the share count, and this pass's own result.
    Its ``gas_used`` is the ENTER's gas, not the pass's: ``SimulationResult``
    sums only ``execute`` calls, so the clone, the grants and the deposit --
    all ``add_call`` -- contribute nothing to it.
    """
    sim = VaultSimulator(
        web3=web3, vault=preview.plasma_vault, alpha=ALPHA, block=PINNED_BLOCK
    )
    # The plan this pass builds is discarded: run_simulation prints the one from
    # its own replay, and two copies would double every line.
    _queue_bootstrap(sim, factory, plasma_vault, access_manager, usdc, plan=[])
    result = sim.run()
    _assert_all_success(result)
    return (
        _executor_from_logs(result.execute_logs, preview.plasma_vault),
        result.get("requested_shares"),
        result,
    )


def run_simulation(web3: Web3) -> SimulationResult:
    """Build the vault, park the margin, attest it, unwind it.

    Runs two ``eth_simulateV1`` batches: the discovery pass, then the lifecycle
    batch whose result this returns. That result deliberately carries ONE failed
    call -- the rejected-confirmation demo -- so its ``all_success`` is ``False``
    even though the run succeeded. Callers should check the outcome the way this
    function does (exactly one failure, with the expected revert selector), not
    with ``result.all_success``.
    """
    ctx = Web3Context(web3=web3, chain_id=BASE_CHAIN_ID, signer=OWNER)
    ctx.default_block = PINNED_BLOCK

    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)

    # 1. Preview the deterministic addresses (eth_call, no gas, no state change).
    preview = factory.clone(**clone_args()).call()
    log.info(
        "predicted vault=%s access_manager=%s withdraw_manager=%s (index=%d)",
        preview.plasma_vault,
        preview.access_manager,
        preview.withdraw_manager,
        preview.index,
    )
    # 2. The unsigned creation calldata (external-signer path).
    log.info("unsigned clone calldata: 0x%s", unsigned_clone_calldata().hex())

    # Built once, and shared with the discovery pass: it replays this exact
    # bootstrap, so the same wrappers must drive both.
    plasma_vault = PlasmaVault(ctx, preview.plasma_vault)
    access_manager = AccessManager(ctx, preview.access_manager)
    usdc = ERC20(ctx, BASE_USDC)

    # 3. Discovery pass -- see _discovery_pass for why this runs at all.
    executor_address, requested_shares, discovery = _discovery_pass(
        web3, preview, factory, plasma_vault, access_manager, usdc
    )
    log.info(
        "discovery pass: executor=%s requested_shares=%s (gas_used=%s)",
        executor_address,
        requested_shares,
        discovery.gas_used,
    )

    price_manager = PriceOracleMiddlewareManager(ctx, preview.price_manager)
    withdraw_manager = WithdrawManager(ctx, preview.withdraw_manager)
    # Only constructible now: the discovery pass is what learned the address.
    executor = ExternalStateExecutor(ctx, executor_address)

    # The pinned block's timestamp, the origin every *_OFFSET is measured from.
    # Read once here; two calls further down need it as an argument.
    baseline_timestamp = int(web3.eth.get_block(PINNED_BLOCK)["timestamp"])

    # Each simulator primitive stands in for a production action, read as
    # "simulation -> production":
    #   - sim.add_call(call=X, from_=OWNER) -> X.send() by the owner/atomist
    #     (or X.calldata to a multisig / timelock).
    #   - sim.execute([action]) -> plasma_vault.execute([action]).send() by the alpha.
    #   - sim.observe("key", X) -> X.call(), a plain read.
    # SIMULATION ONLY (per-call senders): `from_` lets one process act as all five
    # actors, because in simulation they are addresses with no keys. In production
    # they are five separate signers on separate systems, and that separation is
    # the entire security argument -- especially for the two custodians.
    sim = VaultSimulator(
        web3=web3, vault=preview.plasma_vault, alpha=ALPHA, block=PINNED_BLOCK
    )
    plan: list[str] = []

    _queue_bootstrap(sim, factory, plasma_vault, access_manager, usdc, plan)

    # Oracle coverage: the market cannot value anything the vault cannot price.
    # The enter converts the parked amount to underlying through this feed, and
    # the balance fuse values the attested figure through it too.
    sim.observe("usdc_price", price_manager.get_asset_price(BASE_USDC))
    sim.observe("total_assets_after_enter", plasma_vault.total_assets())
    sim.observe("vault_usdc_after_enter", usdc.balance_of(preview.plasma_vault))
    sim.observe("venue_usdc_after_enter", usdc.balance_of(VENUE_DEPOSIT_ADDRESS))
    sim.observe("executor_usdc_after_enter", usdc.balance_of(executor_address))
    sim.observe(
        "market_value_after_enter",
        plasma_vault.total_assets_in_market(EXTERNAL_STATE_MARKET),
    )
    # The raw figure the executor tracks for the balance account -- booked by the
    # enter, since no custodian has attested anything yet -- read straight off the
    # executor rather than through the vault's cached market value, so the two
    # can be compared.
    sim.observe("tracked_after_enter", executor.balances(VENUE_BALANCE_ACCOUNT))

    # ── Custodian A proposes the off-venue value ─────────────────────────────
    # Two calls, from two addresses, is the production shape: custodian A's
    # service proposes, custodian B's service confirms, and neither can do the
    # other's half. That is the entire security property of this market.
    #
    # SIMULATION ONLY: the part that is not production is that BOTH keys are
    # driven from this one process. In a real deployment they live in separate
    # systems -- if one process can send both, a single compromise moves the
    # vault's NAV and the two-party design has bought nothing.
    #
    # ExternalStateExecutor.mark_nav runs the propose, the confirm and -- when
    # handed a vault -- the refresh in one call. It cannot be used here for a
    # mechanical reason: it .send()s each leg, and nothing is broadcast under
    # eth_simulateV1. It is also not what a production deployment wants, for the
    # reason above: one process holding both custodian keys is one key wearing
    # two hats.
    sim.next_block().with_block_time_shift(PROPOSE_OFFSET)
    sim.add_call(
        call=executor.propose_balance(VENUE_BALANCE_ACCOUNT, MARGIN_AMOUNT),
        from_=CUSTODIAN_A,
        label="propose",
    )
    plan.append("custodian A proposes the off-venue value (900 USDC)")

    # ── Custodian B confirms it ──────────────────────────────────────────────
    # The confirm quotes the proposal's hash, which binds the executor, the
    # chain, the account, the value, the proposer, the timestamp and the nonce.
    # SIMULATION ONLY (offline hash): the proposal hash cannot be read here,
    # because the propose has not executed yet when this call is encoded, so the
    # example recomputes it from the same inputs. The two must agree, and the
    # assertions below check exactly that against the emitted log rather than
    # trusting the arithmetic.
    #
    # Production has two reads, and they are equally safe. Both return the same
    # BalanceProposal; both reject anything that did not come from this executor
    # or whose hash is not the hash of its own fields; and confirmBalance
    # re-derives that hash from the pending slot regardless. So neither route can
    # make B attest to a proposal A did not actually put on-chain:
    #   pending_proposals(account)     -- reads the slot, so B needs nothing from
    #                                     A but the account address.
    #   find_balance_proposed(logs, account) -- reads the same proposal out of the
    #                                     propose receipt. That couples B to A's
    #                                     transaction hash, which is an
    #                                     operational dependency, not a trust
    #                                     one: B can fetch the receipt from its
    #                                     own node.
    # Picking between them is therefore plumbing. The security control is not a
    # read at all: confirming asserts that B independently agrees the venue holds
    # `value`, and nothing on-chain can check that, because the chain cannot see
    # the capital. A B that confirms whatever is pending has collapsed the design
    # to one custodian by either route, however separate the two keys are -- and
    # that is why a production deployment cannot drive both halves from one
    # process, whatever this example does under simulation.
    proposed_at = baseline_timestamp + PROPOSE_OFFSET
    proposal_hash = ExternalStateExecutor.proposal_hash(
        executor=executor_address,
        chain_id=BASE_CHAIN_ID,
        balance_account=VENUE_BALANCE_ACCOUNT,
        value=MARGIN_AMOUNT,
        proposer=CUSTODIAN_A,
        proposed_at=proposed_at,
        nonce=1,  # a fresh executor's first proposal; asserted against the log
    )
    sim.next_block().with_block_time_shift(CONFIRM_OFFSET)
    sim.add_call(
        call=executor.confirm_balance(VENUE_BALANCE_ACCOUNT, proposal_hash),
        from_=CUSTODIAN_B,
        label="confirm",
    )
    plan.append("custodian B confirms the proposal")

    # The confirm wrote to the EXECUTOR and nowhere else; the vault's totalAssets
    # reads a cached per-market value out of its own storage. Normally a vault
    # execute() refreshes the markets it touched in a post-execute hook -- which
    # is why the market value was already right after the enter -- but the confirm
    # is a direct call to the executor, so nothing tells the vault anything
    # changed. This is the call that makes it look.
    sim.add_call(
        call=plasma_vault.update_markets_balances([EXTERNAL_STATE_MARKET]), from_=ALPHA
    )
    sim.observe("executor_nonce", executor.nonce())
    sim.observe("tracked_after_confirm", executor.balances(VENUE_BALANCE_ACCOUNT))
    # Per-account, and the field MIN_UPDATE_INTERVAL is measured from -- not the
    # executor-wide stamp the balance fuse reads. Observing it is what ties that
    # constant to something this run can check.
    sim.observe(
        "last_updated_after_confirm", executor.last_updated(VENUE_BALANCE_ACCOUNT)
    )
    sim.observe(
        "market_value_after_confirm",
        plasma_vault.total_assets_in_market(EXTERNAL_STATE_MARKET),
    )
    sim.observe("total_assets_after_confirm", plasma_vault.total_assets())
    plan.append("alpha refreshes the cached market balance")

    # ── The user asks for a scheduled withdrawal ─────────────────────────────
    # Step 1 of 3 on the scheduled path: the user requests, the alpha releases
    # (below), and the user later redeems with redeemFromRequest -- that third
    # step is the user's own transaction and out of scope here. The request is
    # what gives the alpha notice to unwind the venue position in between.
    #
    # Requests are share-denominated, so the depositor asks for the shares that
    # 500 USDC bought, not for the 500 USDC itself. That is what makes the
    # request survive a share price that moves between filing and release.
    sim.next_block().with_block_time_shift(REQUEST_OFFSET)
    sim.add_call(
        call=withdraw_manager.request_shares(requested_shares), from_=DEPOSITOR
    )
    sim.observe("request_after_filing", withdraw_manager.request_info(DEPOSITOR))
    plan.append("user requests a scheduled withdrawal worth 500 USDC")

    # ── The alpha brings the margin back and releases it ─────────────────────
    # SIMULATION ONLY: this transfer impersonates the venue. The exit pulls USDC
    # from the executor to the vault, so the funds must be back on the executor
    # first -- and it is the venue that sends them. In production the alpha
    # requests a withdrawal at the venue and waits for it to land; nothing the
    # vault signs can make it happen.
    sim.next_block().with_block_time_shift(RELEASE_OFFSET)
    sim.add_call(
        call=usdc.transfer(executor_address, MARGIN_AMOUNT), from_=VENUE_DEPOSIT_ADDRESS
    )
    plan.append("the venue returns the margin to the executor")

    sim.execute([build_exit_action(MARGIN_AMOUNT)])
    sim.observe("vault_usdc_after_exit", usdc.balance_of(preview.plasma_vault))
    sim.observe("venue_usdc_after_exit", usdc.balance_of(VENUE_DEPOSIT_ADDRESS))
    sim.observe("tracked_after_exit", executor.balances(VENUE_BALANCE_ACCOUNT))
    sim.observe(
        "market_value_after_exit",
        plasma_vault.total_assets_in_market(EXTERNAL_STATE_MARKET),
    )
    sim.observe("total_assets_after_exit", plasma_vault.total_assets())
    plan.append("alpha exits the market (executor -> vault)")

    # release_funds marks every request filed up to `timestamp` as withdrawable,
    # for at most `shares` shares. "One second ago" is the SDK's own idiom for
    # "everything requested so far" (see WithdrawManager.get_pending_requests_info),
    # and the timestamp must be in the past. This is the last thing the alpha does
    # for a scheduled exit; the user's own redemption is theirs to send, and out
    # of scope here.
    sim.add_call(
        call=withdraw_manager.release_funds(
            timestamp=baseline_timestamp + RELEASE_OFFSET - 1, shares=requested_shares
        ),
        from_=ALPHA,
    )
    sim.observe("request_after_release", withdraw_manager.request_info(DEPOSITOR))
    sim.observe("shares_released", withdraw_manager.get_shares_to_release())
    plan.append("alpha releases funds for the request")

    # ── Failure case: a confirmation that does not match its proposal ────────
    # A successful confirm deletes the pending proposal, so the demo needs a
    # fresh one of its own -- confirming twice against the first would revert for
    # the wrong reason (no pending proposal) and pass a sloppy "it reverted"
    # check. The hash is checked before the rate limit, so no extra waiting is
    # needed for this to be the guard that fires. The proposed value is beside
    # the point -- the confirm never gets past the hash to look at it.
    sim.next_block().with_block_time_shift(DEMO_OFFSET)
    sim.add_call(
        call=executor.propose_balance(VENUE_BALANCE_ACCOUNT, Amount(0)),
        from_=CUSTODIAN_A,
        label="demo_propose",
    )
    sim.add_call(
        call=executor.confirm_balance(VENUE_BALANCE_ACCOUNT, WRONG_PROPOSAL_HASH),
        from_=CUSTODIAN_B,
        label="demo_confirm",
    )
    plan.append("demo: a mismatched confirmation is rejected")

    log.info("transaction plan:")
    for i, step in enumerate(plan, start=1):
        log.info("  %d. %s", i, step)

    result = sim.run()

    # ── Simulation done. Everything above only *described* the batch; one
    # eth_simulateV1 call, this sim.run(), executed all of it. (The discovery
    # pass made the run's other one.) From here we read the recorded
    # observations back and verify the outcome, failing loud on any
    # mismatch. ─────────────────────────────────────────────────────────────
    revert_data = _assert_only_expected_failure(result, "demo_confirm")
    _check(
        revert_data[:4] == PROPOSAL_HASH_MISMATCH_SELECTOR,
        f"the demo confirm reverted for the wrong reason: 0x{revert_data[:4].hex()}",
    )
    log.info("confirm rejected as expected: ExternalStateExecutorProposalHashMismatch")

    _assert_configuration(result)
    _assert_margin_parked(result)
    _assert_attestation(
        result,
        executor,
        proposal_hash,
        proposed_at,
        confirmed_at=baseline_timestamp + CONFIRM_OFFSET,
    )
    _assert_unwind(result)

    log.info(
        "OK -- NAV %s -> %s (market 50: %s -> %s -> %s), gas_used=%s",
        result.get("total_assets_after_deposit"),
        result.get("total_assets_after_exit"),
        result.get("market_value_after_enter"),
        result.get("market_value_after_confirm"),
        result.get("market_value_after_exit"),
        result.gas_used,
    )
    return result


def _assert_configuration(result: SimulationResult) -> None:
    """The vault can price its underlying, and holds exactly the grants above."""
    _check(
        result.get("usdc_price").amount > 0,
        "USDC has no oracle price -- the market cannot value the parked margin",
    )
    # No unrelated permissions: the granted set is EXACTLY what market_substrates
    # builds. Compare as sets (grant order is not guaranteed).
    granted = {bytes(s) for s in result.get("granted_substrates")}
    _check(
        granted == set(market_substrates()),
        f"unexpected market substrates granted: {sorted(s.hex() for s in granted)}",
    )
    # Exact, not a lower bound: a fresh clone starts empty, and every later check
    # here (idle USDC after the enter, the vault balance after the exit) already
    # assumes the deposit is the vault's whole balance.
    _check(
        result.get("total_assets_after_deposit") == DEPOSIT_AMOUNT,
        f"deposit not credited to NAV: {result.get('total_assets_after_deposit')}",
    )
    _check(
        result.get("vault_usdc_after_deposit") == DEPOSIT_AMOUNT,
        f"vault holds {result.get('vault_usdc_after_deposit')} USDC after the deposit",
    )


def _assert_margin_parked(result: SimulationResult) -> None:
    """The enter moved the margin all the way out and accounted for it."""
    margin = int(MARGIN_AMOUNT)
    _check(
        result.get("vault_usdc_after_enter") == DEPOSIT_AMOUNT - margin,
        f"unexpected idle USDC after the enter: {result.get('vault_usdc_after_enter')}",
    )
    venue_delta = result.get("venue_usdc_after_enter") - result.get(
        "venue_usdc_before_enter"
    )
    _check(
        venue_delta == margin,
        f"the venue received {venue_delta} USDC, expected {margin}",
    )
    # Nothing may sit on the executor: propose and confirm both run a dust check
    # against it, so a stranded balance would break the attestation entirely.
    _check(
        result.get("executor_usdc_after_enter") == 0,
        f"executor holds {result.get('executor_usdc_after_enter')} USDC after the enter",
    )
    _check(
        result.get("tracked_after_enter") == margin,
        f"executor booked {result.get('tracked_after_enter')}, expected {margin}",
    )
    _check(
        result.get("market_value_after_enter") == margin,
        f"market 50 values the position at {result.get('market_value_after_enter')}",
    )
    # NAV is preserved across the enter: USDC left the vault and reappeared as
    # market-50 value, 1:1. Compare against the pre-enter NAV, not a lower bound.
    _check(
        abs(
            result.get("total_assets_after_enter")
            - result.get("total_assets_after_deposit")
        )
        <= NAV_DUST_TOLERANCE,
        f"the enter moved NAV: {result.get('total_assets_after_deposit')} -> "
        f"{result.get('total_assets_after_enter')}",
    )


def _assert_attestation(
    result: SimulationResult,
    executor: ExternalStateExecutor,
    proposal_hash: bytes,
    proposed_at: int,
    confirmed_at: int,
) -> None:
    """The custodians' figure is what the contract recorded, and NAV held."""
    # The SDK's own reader, over the propose call's logs. It accepts only logs
    # this executor emitted, and only a payload whose proposal_hash is the hash
    # of its own other fields, so what comes back is a self-consistent proposal
    # from the right contract. What it does NOT answer is the question below:
    # whether the hash the confirm was encoded with is this one.
    logged = executor.find_balance_proposed(
        _logs_of(result, "propose"), VENUE_BALANCE_ACCOUNT
    )
    # The hash equality below is the check that matters, and the four field
    # comparisons before it are diagnostics rather than extra verification:
    # find_balance_proposed already rejects a log whose proposal_hash is not the
    # hash of its own fields, so once the hashes match, collision resistance
    # makes the fields match too. Comparing them first is what turns "the hashes
    # differ" into "the nonce was the input that drifted".
    _check(
        logged.nonce == 1, f"first proposal carried nonce {logged.nonce}, expected 1"
    )
    _check(
        logged.value == MARGIN_AMOUNT,
        f"custodian A proposed {logged.value}, expected {MARGIN_AMOUNT}",
    )
    _check(
        logged.proposer == CUSTODIAN_A,
        f"the proposal came from {logged.proposer}, not custodian A",
    )
    _check(
        logged.proposed_at == proposed_at,
        f"propose landed at {logged.proposed_at}, the confirm hash assumed "
        f"{proposed_at}",
    )
    _check(
        logged.proposal_hash == proposal_hash,
        f"recomputed proposal hash 0x{proposal_hash.hex()} != logged "
        f"0x{logged.proposal_hash.hex()}",
    )
    _check(
        result.get("executor_nonce") == 1,
        f"executor nonce is {result.get('executor_nonce')} after one proposal",
    )
    # The stamp moves on the CONFIRM, not the propose.
    _check(
        result.get("last_updated_after_confirm") == confirmed_at,
        f"the account's last update reads "
        f"{result.get('last_updated_after_confirm')}, expected {confirmed_at}",
    )
    # The custodians attested exactly what was parked, so the confirmed value
    # does not move NAV. (It also stays inside BIG_CHANGE_BPS, but this vault
    # would not act on a breach -- see the guard-constant note above -- so that
    # is not asserted here.)
    _check(
        result.get("tracked_after_confirm") == MARGIN_AMOUNT,
        f"confirmed balance is {result.get('tracked_after_confirm')}",
    )
    _check(
        result.get("market_value_after_confirm") == MARGIN_AMOUNT,
        f"market 50 values the attested position at "
        f"{result.get('market_value_after_confirm')}",
    )
    _check(
        abs(
            result.get("total_assets_after_confirm")
            - result.get("total_assets_after_deposit")
        )
        <= NAV_DUST_TOLERANCE,
        f"the attestation moved NAV: {result.get('total_assets_after_deposit')} -> "
        f"{result.get('total_assets_after_confirm')}",
    )


def _assert_unwind(result: SimulationResult) -> None:
    """The margin came home, the market emptied, and the request is releasable."""
    _check(
        result.get("vault_usdc_after_exit") == DEPOSIT_AMOUNT,
        f"vault holds {result.get('vault_usdc_after_exit')} USDC after the exit",
    )
    _check(
        result.get("venue_usdc_after_exit") == result.get("venue_usdc_before_enter"),
        f"the venue kept USDC back: {result.get('venue_usdc_before_enter')} -> "
        f"{result.get('venue_usdc_after_exit')}",
    )
    _check(
        result.get("tracked_after_exit") == 0,
        f"executor still books {result.get('tracked_after_exit')} for the venue",
    )
    _check(
        result.get("market_value_after_exit") == 0,
        f"market 50 still values {result.get('market_value_after_exit')}",
    )
    _check(
        abs(
            result.get("total_assets_after_exit")
            - result.get("total_assets_after_deposit")
        )
        <= NAV_DUST_TOLERANCE,
        f"the round trip moved NAV: {result.get('total_assets_after_deposit')} -> "
        f"{result.get('total_assets_after_exit')}",
    )
    # The request survives the round trip: it is filed before the margin comes
    # home and still carries the same shares after, only now withdrawable. The
    # depositor's own redemption is the next step, and out of scope here.
    filed = result.get("request_after_filing")
    released = result.get("request_after_release")
    _check(filed.shares > 0, "the withdrawal request registered no shares")
    _check(
        filed.shares == released.shares,
        f"the request changed size: {filed.shares} -> {released.shares}",
    )
    _check(
        not filed.can_withdraw,
        "the request was withdrawable before release_funds ran",
    )
    _check(
        released.can_withdraw,
        "release_funds did not make the request withdrawable",
    )
    _check(
        result.get("shares_released") == released.shares,
        f"released {result.get('shares_released')} shares, requested {released.shares}",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_simulation(_connected_web3())


if __name__ == "__main__":
    main()
