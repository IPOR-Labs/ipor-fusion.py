"""Wire a HyperCore (market 55) vault from the official HyperEVM deployment and trade on Core.

A HyperCore vault is a HyperEVM Plasma Vault that is itself a Hyperliquid Core
account: delegatecalled fuses read Core through the HyperCore read precompiles
(0x800-0x813) and queue CoreWriter actions from the vault's own address. This
example uses only the official deployment published in ipor-abi
(``mainnet/mainnet-hyperevm-fusion/addresses.json``): the Fusion factory, the
seven HyperCore fuses, the two HyperCore pre-hooks and ``HyperCorePendingReader``.

No live vault runs on the official HyperCore contracts yet (no vault on HyperEVM
has added any of them), so the example builds one: it clones a USDC vault from
the official factory and has the simulated governance wire market 55. It shows,
in order:

  1. clone a USDC vault from the official Fusion factory and bootstrap its roles,
     including PRE_HOOKS_MANAGER and the vault's own UPDATE_MARKETS_BALANCES role
     (the HyperCore pre-hooks refresh market 55 through a self-call), and give
     USDC a price source in the vault's price manager;
  2. add the HyperCore deposit, send and order fuses and the market-55 balance
     fuse, and grant the market-55 substrates (spot token, perp market, the two
     destinations, the send cap and the config keys);
  3. install ``HyperCoreCapitalFlowPreHook`` on the six share-priced selectors and
     ``HyperCorePendingActionPreHook`` on ``execute`` and ``updateMarketsBalances``,
     then read every selector, role, fuse and substrate back (the contracts'
     post-deployment checklist);
  4. build the Core leg from the granted substrates: bridge USDC EVM -> Core, send
     it spot -> the xyz perp dex, buy xyz:NVDA with an IOC limit order, close it
     reduce-only, send the margin back to spot, and bridge Core -> EVM; print each
     action's unsigned ``execute`` calldata for a signer;
  5. with a HyperCore stand-in (see below), deposit, run those six actions through
     the vault, read ``HyperCorePendingReader`` and the NAV between them, and
     check the HyperCore events.

What is and is not simulated -- read this before trusting any output:

  * Steps 1-3 run in one ``eth_simulateV1`` batch at a pinned block against the
    deployed bytecode, as governance would send them (the gas settings in
    ``run_simulation`` are the only simulation-specific part).
  * Core execution is NEVER simulated. HyperCore runs a queued action in a later
    Core block, off the EVM; nothing here proves Core accepts, fills or settles
    it. What step 5 proves is EVM acceptance: the vault, the hooks and the fuses
    admit each action and emit it, given the Core answers the example assumes.
  * The read precompiles do not run inside ``eth_simulateV1`` (the node reports
    "out of gas ... during precompiled contract execution") and ignore code
    overrides, and an ``eth_call`` at a past block fails the same way: Core state
    is only readable at head. So step 5 needs a stand-in: the fuses, hooks and
    pending reader recompiled from their released source with the precompile and
    CoreWriter addresses redirected to ordinary addresses that answer from
    ``core_ledger()``. That stand-in needs Foundry and a contracts checkout, so it
    lives in this repository's tests, which pass it to ``run_simulation``; the
    default run stops after step 4 and says so.
  * ``main()`` also reads live Core metadata at head with ``HyperCoreReader`` (the
    precompiles answer a plain ``eth_call`` at the latest block) to check the
    constants below against Core.

Once such a vault is deployed, its live state is one read at head:
``read_hypercore_vault_state(ctx, vault, BALANCE_FUSE)`` (the NAV identity, the
balance fuse value, the pending state and the granted perp markets).

Run it (POSIX shell):

    export HYPEREVM_PROVIDER_URL="https://YOUR_HYPEREVM_ARCHIVE_NODE"
    uv run python examples/hypercore_vault_official_deployment_hyperevm.py

The provider must implement ``eth_simulateV1`` and serve state at the pinned
block. Status: preview, not production. The HyperCore surface is a preview
feature (``ipor_fusion.about.PREVIEW_FEATURES``): its names may change in a minor
release.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from typing import NamedTuple, Protocol, TypedDict

from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    FuseAction,
    FusionFactory,
    PlasmaVault,
    PriceOracleMiddlewareManager,
    Roles,
    SimulationObservation,
    SimulationResult,
    StateOverrideProvider,
    VaultSimulator,
    Web3Context,
    decode_substrate,
    erc20_balance_slot,
    is_simulate_v1_supported,
    parse_checksum_address,
    parse_fuse_address,
)
from ipor_fusion.hypercore import (
    HYPERCORE_PENDING_READERS,
    SPOT_DEX,
    USDC_SYSTEM_ADDRESS,
    USDC_TOKEN_INDEX,
    HyperCoreConfigKey,
    HyperCoreDepositFuse,
    HyperCoreEffectKind,
    HyperCoreOrderFuse,
    HyperCorePendingReader,
    HyperCorePendingState,
    HyperCorePerpAssetInfo,
    HyperCorePreHookWiring,
    HyperCoreReader,
    HyperCoreSendFuse,
    HyperCoreSubstrates,
    HyperCoreTokenInfo,
    TimeInForce,
    decode_hypercore_event,
    hip3_action_asset,
    precompile_index_of,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import (
    Amount,
    ChainId,
    HyperCoreClientOrderId,
    Period,
    PlasmaVaultAddress,
)

log = logging.getLogger("hypercore_vault_official_deployment_hyperevm")

# ── Live HyperEVM infrastructure (do NOT change) ─────────────────────────────
# Every IPOR address below is in ipor-abi
#   mainnet/mainnet-hyperevm-fusion/addresses.json (commit 5bb6913, 2026-10-22),
# deployed from the ipor-fusion release of 2026-10-21.

HYPEREVM_CHAIN_ID = ChainId(999)

# IporFusionFactoryProxy (implementation IporFusionFactoryImpl, factory version 10).
FUSION_FACTORY = parse_checksum_address("0x6bBc827F34b4e862d15215ABd4AC0E3886181665")

# BurnRequestFeeFuseV2: the fuse every factory clone starts with.
FACTORY_FEE_FUSE = parse_fuse_address("0xD62f398d36aeDE3992521EA9Fc501dEB418a50b4")

# The HyperCore market and its contracts (ipor-abi names in the comments).
HYPERCORE_MARKET = IporFusionMarkets.HYPERCORE  # 55
# HyperCoreDepositFuse: EVM -> Core deposit of a granted spot token.
DEPOSIT_FUSE = parse_fuse_address("0x5DBB60251Ff1F4BD57394976F4526987d05d1eB6")
# HyperCoreSendFuse: spotSend / sendAsset between Core accounts and back to EVM.
SEND_FUSE = parse_fuse_address("0x0C7b6788716eD1563De79743C2b7690D82f7C999")
# HyperCoreOrderFuse: perp limit orders (CoreWriter action 1).
ORDER_FUSE = parse_fuse_address("0x96f97c088E9610A036a2738B004DF04Ee9Cf17a2")
# HyperCoreBalanceFuse: values the vault's Core account for market 55.
BALANCE_FUSE = parse_fuse_address("0xbEd25202529DCD2a05f7D8944B10779b6dbc6Fa6")
# HyperCoreCapitalFlowPreHook and HyperCorePendingActionPreHook.
CAPITAL_FLOW_PRE_HOOK = parse_checksum_address(
    "0xbA999E8b50bDC2672CcE13A1F896aaD7Dc5BE547"
)
PENDING_ACTION_PRE_HOOK = parse_checksum_address(
    "0xd240690cf7C41dDD75863c4b4a9358ea62B20c85"
)
# HyperCorePendingReader: the stateless helper the vault delegatecalls to report
# its pending-action record. The SDK's default for chain 999 is this address.
PENDING_READER = parse_checksum_address("0x1EFFb12434f5fb59a636C54D8CBaC93b13DF1A9E")

# IporFusionPriceFeedUSD: a constant 1 USD price (8 decimals). The global price
# oracle middleware on HyperEVM has no USDC source, so the vault's own price
# manager must name one. A constant peg keeps this example's NAV arithmetic
# exact; a production vault picks its USDC feed in its oracle review (a market
# USDC/USD feed prices a depeg, a constant does not).
USD_PRICE_FEED = parse_checksum_address("0x15794119c5D99Ed7997D1CE80DE8599e188547c5")

# Native Circle USDC on HyperEVM (6 decimals). Core token 0 is USDC; its
# ``tokenInfo(0).evmContract`` is Circle's CoreDepositWallet, whose ``token()`` is
# this ERC-20, so the SpotToken grant names this address, not the wallet
# (contracts/fuses/hypercore/README.md in ipor-fusion).
HYPEREVM_USDC = parse_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f")

# xyz is HIP-3 perp dex 1, and xyz:NVDA is its market 2: action asset
# 100_000 + 1 * 10_000 + 2 in orders and grants, read index 10_002 for the
# precompiles (``precompile_index_of``).
XYZ_DEX = 1
XYZ_NVDA = hip3_action_asset(XYZ_DEX, 2)
XYZ_NVDA_COIN = "xyz:NVDA"
XYZ_NVDA_SZ_DECIMALS = 3

# ── Simulation parameters (safe to adjust) ───────────────────────────────────
#
# OWNER, ALPHA and DEPOSITOR are impersonated simulation actors (the simulator
# runs with validation disabled); there is nothing to sign here. They are three
# addresses on purpose: OWNER is governance (owner, atomist, fuse manager and
# pre-hooks manager in this bootstrap; a cold key or multisig, ideally behind a
# timelock), ALPHA is the online operator that can only drive execute() and
# refresh balances, and DEPOSITOR is an end user. OWNER and ALPHA must never
# share an address in production: a compromised alpha key would become
# governance, and on a HyperCore vault governance can re-wire the pre-hooks.
OWNER = parse_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
ALPHA = parse_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
DEPOSITOR = parse_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")

# Pinned HyperEVM block, after the official HyperCore contracts and the factory
# upgrade. If your provider cannot serve state at this height, BUMP IT to a
# later block it can reach.
PINNED_BLOCK = 49_297_000  # HyperEVM, 2026-10-23

# The gas limit of a HyperEVM big block (small blocks hold 3 M).
BIG_BLOCK_GAS_LIMIT = 30_000_000
CLONE_GAS = 12_000_000  # the clone uses about 9.3 M
# SIMULATION ONLY: without an explicit gas, this node splits the block's gas
# limit evenly across the block's calls, which starves a call in a crowded
# simulated block. A signer estimates each transaction's gas instead.
CALL_GAS = 1_000_000

# Amounts: EVM USDC has 6 decimals, Core USDC has 8 (Core wei), perp sizes and
# limit prices carry 8 implied decimals, USD figures in grants 6 (USD6).
CORE_WEI_PER_EVM_UNIT = 100  # tokenInfo(0).evmExtraWeiDecimals is -2
DEPOSIT_AMOUNT = Amount(20_000_000)  # 20 USDC into the vault
BRIDGE_AMOUNT = Amount(15_000_000)  # 15 USDC EVM -> Core
# The first credit to a new Core account pays a one-time activation fee (1 USDC
# on mainnet); the deposit fuse nets it through Config{ActivationFeeAllowance},
# whose default is that same 1 USDC in Core wei.
ACTIVATION_FEE_WEI = 100_000_000
TO_DEX_WEI = 1_000_000_000  # 10 USDC spot -> xyz dex
ORDER_SIZE = 5_000_000  # 0.05 xyz:NVDA (a multiple of the 10**(8 - 3) lot)
LIMIT_BUY_PX = 230 * 10**8  # pays at most 230 USD
LIMIT_SELL_PX = 220 * 10**8  # sells at no less than 220 USD
# Core -> EVM: a spot -> spot sendAsset to the USDC system address. The amount
# must be a multiple of 100 Core wei (representable in 6 EVM decimals); Core
# takes its transfer fee from what stays on spot.
CORE_FEE_RESERVE_WEI = 5_000_000  # 0.05 USDC
# Grants: per-order notional cap, per-transfer cap and the NAV-jump floor.
MAX_ORDER_NOTIONAL_USD6 = 15_000_000
SEND_CAP_WEI = 10_000_000_000  # 100 USDC
NAV_JUMP_FLOOR_USD6 = 1_000_000  # 1 USD; the runbook sizes it per vault TVL

# This deployment's two pre-hooks.
PRE_HOOKS = HyperCorePreHookWiring(
    capital_flow_hook=CAPITAL_FLOW_PRE_HOOK,
    pending_action_hook=PENDING_ACTION_PRE_HOOK,
)


# ── Inlined helpers (kept local so this file is self-contained) ──────────────


def _check(condition: bool, message: str) -> None:
    """Fail loud unconditionally -- unlike ``assert``, survives ``python -O``."""
    if not condition:
        raise AssertionError(message)


def _assert_all_success(result: SimulationResult) -> None:
    if result.all_success:
        return
    failed = [(c.label, c.revert_reason or c.error) for c in result.failed_calls]
    raise AssertionError(f"simulation calls failed: {failed}")


def _connected_web3() -> Web3:
    url = os.environ.get("HYPEREVM_PROVIDER_URL")
    if not url:
        sys.exit("HYPEREVM_PROVIDER_URL is not set -- export your HyperEVM RPC URL.")
    web3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 90}))
    if not web3.is_connected() or web3.eth.chain_id != HYPEREVM_CHAIN_ID:
        sys.exit("HYPEREVM_PROVIDER_URL is not a reachable HyperEVM (999) node.")
    if not is_simulate_v1_supported(web3):
        sys.exit("the provider does not implement eth_simulateV1.")
    return web3


# ── Pure builders (no chain access -- safe to import and unit-test offline) ───


class CloneKwargs(TypedDict):
    """The keyword arguments of ``FusionFactory.clone``."""

    asset_name: str
    asset_symbol: str
    underlying_token: ChecksumAddress
    redemption_delay_seconds: Period
    owner: ChecksumAddress
    dao_fee_package_index: int


def clone_args() -> CloneKwargs:
    """Arguments of the vault clone; the preview and the queued clone must match
    so the factory hands out the previewed addresses."""
    return {
        "asset_name": "IPOR USDC HyperCore Vault (example)",
        "asset_symbol": "ipUSDChc",
        "underlying_token": HYPEREVM_USDC,
        "redemption_delay_seconds": Period(0),
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def unsigned_clone_calldata() -> bytes:
    """Unsigned ``clone(...)`` calldata for a signer. The owner encoded here is
    the simulation OWNER: set your own in ``clone_args()`` before deploying."""
    return FusionFactory(FUSION_FACTORY).clone(**clone_args()).calldata


def market_substrates(vault: PlasmaVaultAddress) -> list[bytes]:
    """Every market-55 grant this vault needs, and nothing more.

    The grants bound what the fuses admit; they do not promise Core executes it.
    One perp market only: granting a HIP-3 market makes the order fuse require
    exactly one PerpMarket grant. The destinations are the only two the send fuse
    accepts for these routes: the vault itself (moves between its own spot and
    dex accounts) and USDC's system address (Core -> EVM).
    """
    return [
        HyperCoreSubstrates.spot_token(USDC_TOKEN_INDEX, HYPEREVM_USDC),
        HyperCoreSubstrates.perp_market(XYZ_NVDA, MAX_ORDER_NOTIONAL_USD6),
        HyperCoreSubstrates.destination(vault),
        HyperCoreSubstrates.destination(USDC_SYSTEM_ADDRESS),
        HyperCoreSubstrates.send_cap(USDC_TOKEN_INDEX, SEND_CAP_WEI),
        # Bit i enables HIP-3 perp dex i; bit 0 stays clear (the native dex is
        # always valued). Without this bit the xyz account is not in NAV.
        HyperCoreSubstrates.config(HyperCoreConfigKey.PERP_DEX_IDS, 1 << XYZ_DEX),
        # The NAV-jump breaker's absolute floor: without it, a donation larger
        # than MaxNavJumpBps of a small stored value freezes the vault.
        HyperCoreSubstrates.config(
            HyperCoreConfigKey.NAV_JUMP_FLOOR_USD6, NAV_JUMP_FLOOR_USD6
        ),
    ]


def core_to_evm_amount(spot_total_wei: int) -> int:
    """Core wei to bridge back: spot minus the fee reserve, rounded down to a
    multiple of 100 Core wei so the EVM amount is exact."""
    amount = max(spot_total_wei - CORE_FEE_RESERVE_WEI, 0)
    return amount - amount % CORE_WEI_PER_EVM_UNIT


class CoreStep(NamedTuple):
    """One Core action: its name and the fuse action the alpha executes."""

    name: str
    action: FuseAction


class CoreAnswers(NamedTuple):
    """What HyperCore reports to the precompile reads during one step.

    These are the example's assumptions about the Core ledger, not Core
    output: Core's own execution is never simulated. ``spot_usdc_wei`` is the
    spot total (nothing on hold), ``xyz_account_value_usd6`` the xyz dex
    account value, ``position_szi`` the xyz:NVDA position in ``szDecimals``
    units, and ``mark_px`` the precompile's raw perp price (``6 - szDecimals``
    decimals, so 225_000 is 225 USD).
    """

    account_exists: bool
    spot_usdc_wei: int
    xyz_account_value_usd6: int
    position_szi: int
    mark_px: int


def core_ledger() -> tuple[CoreAnswers, ...]:
    """The Core answers before each step of ``core_steps()``, plus one after
    the last move between Core accounts (index 6, read by the NAV refresh).

    Each entry assumes the previous action landed as sent: the bridge credits
    15 USDC minus the 1 USDC activation fee, the sends move exactly their
    amount, the IOC buy fills its full size and the close flattens it at no
    profit or loss.
    """
    bridged = BRIDGE_AMOUNT * CORE_WEI_PER_EVM_UNIT - ACTIVATION_FEE_WEI
    mark = 225_000
    on_dex = TO_DEX_WEI // 100  # Core wei -> USD6
    filled = ORDER_SIZE // 10 ** (8 - XYZ_NVDA_SZ_DECIMALS)
    return (
        CoreAnswers(False, 0, 0, 0, mark),  # before the bridge: no Core account
        CoreAnswers(True, bridged, 0, 0, mark),  # the bridge landed
        CoreAnswers(True, bridged - TO_DEX_WEI, on_dex, 0, mark),  # margin on xyz
        CoreAnswers(True, bridged - TO_DEX_WEI, on_dex, filled, mark),  # long
        CoreAnswers(True, bridged - TO_DEX_WEI, on_dex, 0, mark),  # closed
        CoreAnswers(True, bridged, 0, 0, mark),  # margin back on spot
        CoreAnswers(True, bridged, 0, 0, mark),  # unchanged before Core -> EVM
    )


def core_steps(vault: PlasmaVaultAddress) -> list[CoreStep]:
    """The six Core actions, each built from a granted substrate: USDC is the
    granted spot token, xyz:NVDA the granted market (under its notional cap),
    the vault and USDC's system address the granted destinations, every send
    within the USDC SendCap. A fuse method is a pure encoder."""
    deposit = HyperCoreDepositFuse(DEPOSIT_FUSE)
    send = HyperCoreSendFuse(SEND_FUSE)
    order = HyperCoreOrderFuse(ORDER_FUSE)
    spot_after_round_trip = core_ledger()[6].spot_usdc_wei
    return [
        CoreStep(
            "bridge EVM -> Core",
            deposit.enter(token_index=USDC_TOKEN_INDEX, amount=BRIDGE_AMOUNT),
        ),
        CoreStep(
            "spot -> xyz dex",
            send.send_asset(
                destination=vault,
                source_dex=SPOT_DEX,
                destination_dex=XYZ_DEX,
                token_index=USDC_TOKEN_INDEX,
                amount_wei=TO_DEX_WEI,
            ),
        ),
        CoreStep(
            "buy xyz:NVDA (IOC)",
            order.enter(
                asset=XYZ_NVDA,
                is_buy=True,
                limit_px=LIMIT_BUY_PX,
                sz=ORDER_SIZE,
                tif=TimeInForce.IOC,
                cloid=HyperCoreClientOrderId(1),
            ),
        ),
        CoreStep(
            "close xyz:NVDA (IOC, reduce-only)",
            order.enter(
                asset=XYZ_NVDA,
                is_buy=False,
                limit_px=LIMIT_SELL_PX,
                sz=ORDER_SIZE,
                tif=TimeInForce.IOC,
                cloid=HyperCoreClientOrderId(2),
                reduce_only=True,
            ),
        ),
        CoreStep(
            "xyz dex -> spot",
            send.send_asset(
                destination=vault,
                source_dex=XYZ_DEX,
                destination_dex=SPOT_DEX,
                token_index=USDC_TOKEN_INDEX,
                amount_wei=TO_DEX_WEI,
            ),
        ),
        CoreStep(
            "bridge Core -> EVM",
            send.send_asset(
                destination=USDC_SYSTEM_ADDRESS,
                source_dex=SPOT_DEX,
                destination_dex=SPOT_DEX,
                token_index=USDC_TOKEN_INDEX,
                amount_wei=core_to_evm_amount(spot_after_round_trip),
            ),
        ),
    ]


def check_core_metadata(
    usdc_info: HyperCoreTokenInfo,
    wallet_token: ChecksumAddress,
    nvda_info: HyperCorePerpAssetInfo,
) -> None:
    """The constants above against what Core reports: USDC's Core -> EVM scale,
    the CoreDepositWallet's ERC-20 (the address the SpotToken grant must
    name), and the market's coin and size decimals (the order lot)."""
    _check(
        10 ** (-usdc_info.evm_extra_wei_decimals) == CORE_WEI_PER_EVM_UNIT,
        f"USDC evmExtraWeiDecimals is {usdc_info.evm_extra_wei_decimals}, not -2",
    )
    _check(
        wallet_token == HYPEREVM_USDC,
        f"the CoreDepositWallet's token() is {wallet_token}, not {HYPEREVM_USDC}",
    )
    _check(
        nvda_info.coin == XYZ_NVDA_COIN,
        f"read index {precompile_index_of(XYZ_NVDA)} is {nvda_info.coin!r}",
    )
    _check(
        nvda_info.sz_decimals == XYZ_NVDA_SZ_DECIMALS,
        f"{XYZ_NVDA_COIN} szDecimals is {nvda_info.sz_decimals}",
    )
    _check(
        ORDER_SIZE % 10 ** (8 - nvda_info.sz_decimals) == 0,
        "the order size is not a whole number of lots",
    )


# ── Live Core metadata (head only: the precompiles have no history) ──────────


def read_core_metadata_at_head(web3: Web3) -> None:
    """Read Core at the latest block and check the constants against it."""
    ctx = Web3Context(web3, chain_id=HYPEREVM_CHAIN_ID)
    reader = HyperCoreReader()
    usdc_info = reader.token_info(USDC_TOKEN_INDEX).call(ctx)
    nvda_index = precompile_index_of(XYZ_NVDA)
    nvda_info = reader.perp_asset_info(nvda_index).call(ctx)
    mark = reader.mark_px(nvda_index).call(ctx)
    # ``token()`` of the CoreDepositWallet, as the deposit fuse checks it.
    raw = web3.eth.call(
        {
            "to": usdc_info.evm_contract,
            "data": function_signature_to_4byte_selector("token()"),
        }
    )
    wallet_token = Web3.to_checksum_address(raw[-20:])
    check_core_metadata(usdc_info, wallet_token, nvda_info)
    log.info(
        "Core at head: USDC deposit wallet %s -> token %s; %s szDecimals=%d mark=%s",
        usdc_info.evm_contract,
        wallet_token,
        nvda_info.coin,
        nvda_info.sz_decimals,
        mark / 10 ** (6 - nvda_info.sz_decimals),
    )


# ── Simulation ───────────────────────────────────────────────────────────────


class CoreStandIn(Protocol):
    """SIMULATION ONLY: answers the HyperCore precompiles inside eth_simulateV1.

    ``runtimes`` replaces the code of the deployed HyperCore fuses, hooks and
    pending reader with builds of the same source whose precompile and
    CoreWriter addresses are redirected to ordinary addresses; ``answers``
    returns the overrides that make those addresses report ``core``. Production
    has no equivalent: the real precompiles report the real Core account, and
    Core, not the EVM, decides what a queued action does.
    """

    def runtimes(self) -> Mapping[ChecksumAddress, bytes]: ...

    def answers(
        self, vault: PlasmaVaultAddress, core: CoreAnswers
    ) -> StateOverrideProvider: ...


class Outcome(NamedTuple):
    """What ``run_simulation`` returns: the vault, its Core leg and the result."""

    vault: PlasmaVaultAddress
    steps: list[CoreStep]
    result: SimulationResult


def run_simulation(
    web3: Web3, *, block: int = PINNED_BLOCK, core: CoreStandIn | None = None
) -> Outcome:
    """Clone, wire and check the vault in one eth_simulateV1 batch; with
    ``core``, also deposit and run the Core leg in later blocks."""
    ctx = Web3Context(web3, chain_id=HYPEREVM_CHAIN_ID).at_block(block)
    factory = FusionFactory(FUSION_FACTORY)
    _check(
        HYPERCORE_PENDING_READERS[HYPEREVM_CHAIN_ID] == PENDING_READER,
        "the SDK's default HyperCorePendingReader is not the published one",
    )

    # SIMULATION ONLY: the pinned replay has no other clone between this
    # preview and the simulated one, so the predicted stack is the created one.
    # Live, another clone in between moves it: send the clone first, take the
    # stack from factory.decode_clone_receipt(receipt) and build the
    # configuration and operation calldata from those addresses.
    preview = factory.clone(**clone_args()).call(ctx)
    vault_address = preview.plasma_vault
    log.info("predicted vault=%s (factory version %d)", vault_address, preview.version)
    log.info("unsigned clone calldata: 0x%s", unsigned_clone_calldata().hex())
    vault = PlasmaVault(vault_address)
    access_manager = AccessManager(preview.access_manager)
    price_manager = PriceOracleMiddlewareManager(preview.price_manager)

    # Simulation -> production: sim.add_call(X, from_=OWNER) is X.calldata
    # signed by governance; sim.execute([...]) is vault.execute([...]).calldata
    # signed by the alpha; sim.observe(X) then .value is X.call(ctx).
    sim = VaultSimulator(web3, vault=vault_address, alpha=ALPHA, block=block)
    # SIMULATION ONLY: HyperEVM alternates 3 M-gas small blocks with 30 M-gas
    # big blocks, and the pinned block is a small one. The clone deploys the
    # whole vault stack and does not fit 3 M gas, so the simulated blocks get
    # the big-block limit. In production the deployer opts into big blocks
    # (HyperCore action ``evmUserModify`` with ``usingBigBlocks``) before
    # sending the clone; each later call here uses well under 3 M gas.
    sim.with_block_override(gasLimit=BIG_BLOCK_GAS_LIMIT)
    if core is not None:
        # SIMULATION ONLY: see CoreStandIn. Not a recommended pattern.
        for address, code in core.runtimes().items():
            sim.with_state_override(address, code="0x" + code.hex())
        sim.with_state_override_provider(core.answers(vault_address, core_ledger()[0]))
    plan: list[str] = []

    # 1. The clone must be the first queued call: anything that bumps the
    #    factory index first changes the previewed addresses.
    sim.add_call(
        factory.clone(**clone_args()), from_=OWNER, label="clone", gas=CLONE_GAS
    )
    # The admin chain is OWNER -> ATOMIST -> the rest, so OWNER grants itself
    # ATOMIST first. FUSE_MANAGER installs fuses and PRE_HOOKS_MANAGER re-wires
    # the hooks the HyperCore fuses rely on: both are governance-grade keys
    # (the contracts' runbook puts PRE_HOOKS_MANAGER behind a timelock).
    # Period(0) keeps the simulation legible; production uses delays.
    for role, account in (
        (Roles.ATOMIST_ROLE, OWNER),
        (Roles.FUSE_MANAGER_ROLE, OWNER),
        (Roles.PRE_HOOKS_MANAGER_ROLE, OWNER),
        (Roles.PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE, OWNER),
        (Roles.ALPHA_ROLE, ALPHA),
        (Roles.WHITELIST_ROLE, DEPOSITOR),
        (Roles.UPDATE_MARKETS_BALANCES_ROLE, ALPHA),
        # The pre-hooks call updateMarketsBalances([55]) on the vault from the
        # vault itself; without this grant every hooked entry point reverts.
        (Roles.UPDATE_MARKETS_BALANCES_ROLE, vault_address),
    ):
        sim.add_call(
            access_manager.grant_role(role, account, Period(0)),
            from_=OWNER,
            label=f"grant {role.name}",
            gas=CALL_GAS,
        )
    plan.append("clone the vault and grant the roles")
    # The balance fuse values Core spot USDC through the vault's oracle, and the
    # vault converts that value back into its USDC underlying through it too.
    sim.add_call(
        price_manager.set_assets_price_sources([HYPEREVM_USDC], [USD_PRICE_FEED]),
        from_=OWNER,
        label="price USDC",
        gas=CALL_GAS,
    )
    plan.append("point the vault's price manager at a USDC price source")

    # 2. Market 55: the functional fuses this flow uses, the balance fuse, the
    #    grants. (The margin, cancel and builder-fee fuses join the same way.)
    sim.add_call(
        vault.add_fuses([DEPOSIT_FUSE, SEND_FUSE, ORDER_FUSE]),
        from_=OWNER,
        label="add fuses",
        gas=CALL_GAS,
    )
    sim.add_call(
        vault.add_balance_fuse(HYPERCORE_MARKET, BALANCE_FUSE),
        from_=OWNER,
        label="add balance fuse",
        gas=CALL_GAS,
    )
    sim.add_call(
        vault.grant_market_substrates(
            HYPERCORE_MARKET, market_substrates(vault_address)
        ),
        from_=OWNER,
        label="grant substrates",
        gas=CALL_GAS,
    )
    plan.append("add the HyperCore fuses and grant the market-55 substrates")

    # 3. The pre-hooks. The deposit fuse and opening orders revert unless all
    #    six share-priced selectors carry the capital-flow hook.
    sim.add_call(
        PRE_HOOKS.set_pre_hook_implementations(vault),
        from_=OWNER,
        label="set pre-hooks",
        gas=CALL_GAS,
    )
    plan.append("install the HyperCore pre-hooks on their eight selectors")

    # The post-deployment checklist, read back from the wired vault.
    wired_hooks = [
        (selector, expected, sim.observe(vault.get_pre_hook_implementation(selector)))
        for selector, expected in zip(
            PRE_HOOKS.selectors, PRE_HOOKS.implementations, strict=True
        )
    ]
    vault_refresh_role = sim.observe(
        access_manager.has_role(Roles.UPDATE_MARKETS_BALANCES_ROLE, vault_address)
    )
    fuses = sim.observe(vault.get_fuses())
    granted = sim.observe(vault.get_market_substrates(HYPERCORE_MARKET))
    usdc_price = sim.observe(price_manager.get_asset_price(HYPEREVM_USDC))
    plan.append("read the hooks, the vault's refresh role, fuses and grants back")

    steps = core_steps(vault_address)
    for step in steps:
        log.info(
            "%s calldata: 0x%s", step.name, vault.execute([step.action]).calldata.hex()
        )

    reads = None
    if core is not None:
        reads = _queue_core_leg(sim, web3, block, vault, steps, core, plan)
    else:
        plan.append(
            "STOP: the Core leg needs the read precompiles, which do not run in "
            "eth_simulateV1 (see CoreStandIn); its calldata is logged above"
        )

    log.info("transaction plan:")
    for index, step in enumerate(plan, start=1):
        log.info("  %d. %s", index, step)

    result = sim.run()
    _assert_all_success(result)

    for selector, expected, implementation in wired_hooks:
        _check(
            implementation.value == expected,
            f"selector 0x{selector.hex()} runs {implementation.value}, not {expected}",
        )
    _check(
        vault_refresh_role.value.is_member
        and vault_refresh_role.value.execution_delay == 0,
        "the vault cannot refresh its own balances without delay",
    )
    # The factory installs its own burn-request-fee fuse (ipor-abi
    # BurnRequestFeeFuseV2) in every clone; everything else is ours.
    _check(
        set(fuses.value) == {DEPOSIT_FUSE, SEND_FUSE, ORDER_FUSE, FACTORY_FEE_FUSE},
        f"unexpected fuses: {fuses.value}",
    )
    _check(
        {bytes(s) for s in granted.value}
        == {bytes(s) for s in market_substrates(vault_address)},
        "the granted market-55 substrates differ from market_substrates()",
    )
    for raw in granted.value:
        info = decode_substrate(raw, HYPERCORE_MARKET)
        log.info("  grant %s %s %s", info.type_label, info.address or "", info.extra)
    _check(usdc_price.value.amount > 0, "the vault's oracle cannot price USDC")

    if reads is not None:
        _check_core_leg(result, reads)
    log.info(
        "OK -- vault %s wired%s, gas used %d",
        vault_address,
        "" if reads is None else " and its Core leg admitted",
        sum(call.gas_used for call in result.calls),
    )
    return Outcome(vault_address, steps, result)


class CoreLegReads(NamedTuple):
    """The reads queued around the Core leg, resolved by ``sim.run()``."""

    nav_after_deposit: SimulationObservation[Amount]
    nav_bridge_pending: SimulationObservation[Amount]
    pending: list[SimulationObservation[HyperCorePendingState]]
    nav_round_trip: SimulationObservation[Amount]
    vault_usdc_end: SimulationObservation[Amount]


def _queue_core_leg(
    sim: VaultSimulator,
    web3: Web3,
    block: int,
    vault: PlasmaVault,
    steps: list[CoreStep],
    core: CoreStandIn,
    plan: list[str],
) -> CoreLegReads:
    """Deposit, then one block per Core action, with Core reporting
    ``core_ledger()`` before each one."""
    ledger = core_ledger()
    vault_address = vault.address
    sim.next_block(time_shift_seconds=60)
    usdc = ERC20(HYPEREVM_USDC)
    pending_reader = HyperCorePendingReader(vault_address, PENDING_READER)

    # SIMULATION ONLY: the depositor's USDC is a storage override; a real
    # depositor already holds it. The approve and deposit are what it signs.
    slot = erc20_balance_slot(web3, HYPEREVM_USDC, block=block)
    sim.with_erc20_balance(HYPEREVM_USDC, DEPOSITOR, DEPOSIT_AMOUNT, slot=slot)
    sim.add_call(
        usdc.approve(vault_address, DEPOSIT_AMOUNT), from_=DEPOSITOR, gas=CALL_GAS
    )
    sim.add_call(
        vault.deposit(DEPOSIT_AMOUNT, DEPOSITOR),
        from_=DEPOSITOR,
        label="deposit",
        gas=CALL_GAS,
    )
    nav_after_deposit = sim.observe(vault.total_assets())
    plan.append("deposit 20 USDC (the capital-flow hook refreshes market 55 first)")

    pending: list[SimulationObservation[HyperCorePendingState]] = []
    nav_bridge_pending = nav_round_trip = nav_after_deposit
    for index, step in enumerate(steps):
        # SIMULATION ONLY (time): each action waits for the previous transfer's
        # settlement window and a later Core block. Production waits for both,
        # and reads HyperCorePendingReader before sending.
        if index > 0:
            sim.next_block(time_shift_seconds=60)
            sim.with_state_override_provider(core.answers(vault_address, ledger[index]))
        sim.execute([step.action])
        pending.append(sim.observe(pending_reader.pending_state()))
        plan.append(f"alpha: {step.name}")
        if index == 0:
            nav_bridge_pending = sim.observe(vault.total_assets())
        if index == 4:
            # The margin is back on spot and settles in the next block: refresh
            # market 55 there, before the Core -> EVM send, to book the live NAV.
            sim.next_block(time_shift_seconds=60)
            sim.with_state_override_provider(core.answers(vault_address, ledger[6]))
            sim.add_call(
                vault.update_markets_balances([HYPERCORE_MARKET]),
                from_=ALPHA,
                label="refresh market 55",
                gas=CALL_GAS,
            )
            nav_round_trip = sim.observe(vault.total_assets())
            plan.append("alpha: refresh market 55 with the margin back on spot")
    return CoreLegReads(
        nav_after_deposit=nav_after_deposit,
        nav_bridge_pending=nav_bridge_pending,
        pending=pending,
        nav_round_trip=nav_round_trip,
        vault_usdc_end=sim.observe(usdc.balance_of(vault_address)),
    )


def _check_core_leg(result: SimulationResult, reads: CoreLegReads) -> None:
    """The fuses admitted every action and the vault booked what they report."""
    events = [
        event.name
        for call in result.calls
        for log_entry in call.logs
        if (event := decode_hypercore_event(log_entry)) is not None
    ]
    log.info("HyperCore events: %s", events)
    # Four transfers (two bridges, two sends) open a pending record; orders
    # never do. The hooks settle three of them before the next action (the
    # third inside the market-55 refresh); the Core -> EVM bridge is still in
    # flight at the end.
    _check(events.count("HyperCoreActionEnqueued") == 4, f"enqueued: {events}")
    _check(events.count("HyperCoreActionSettled") == 3, f"settled: {events}")
    _check(events.count("HyperCoreOrderFuseEnter") == 2, f"orders: {events}")
    _check(events.count("HyperCoreDepositFuseEnter") == 1, f"deposits: {events}")
    _check(events.count("HyperCoreSendFuseSendAsset") == 3, f"sends: {events}")

    deposited = int(DEPOSIT_AMOUNT)
    _check(reads.nav_after_deposit.value == deposited, "deposit not in NAV")
    # While the bridge is in flight the balance fuse reports the cached value
    # with the in-transit credit, so NAV does not dip.
    nav_pending = reads.nav_bridge_pending.value
    _check(
        abs(nav_pending - deposited) <= 1,
        f"NAV moved while the bridge was pending: {nav_pending}",
    )
    bridge = reads.pending[0].value
    _check(
        bridge.pending
        and bridge.effect_kind == HyperCoreEffectKind.CORE_SPOT
        and bridge.expected_balance
        == BRIDGE_AMOUNT * CORE_WEI_PER_EVM_UNIT - ACTIVATION_FEE_WEI,
        "the bridge is not pending on its Core credit net of the activation fee",
    )
    for index in (2, 3):
        _check(not reads.pending[index].value.pending, "an order left a record")
    # After the round trip NAV is the deposit minus the activation fee: 5 USDC
    # idle plus 14 USDC on Core spot.
    expected_nav = deposited - ACTIVATION_FEE_WEI // CORE_WEI_PER_EVM_UNIT
    nav_round_trip = reads.nav_round_trip.value
    _check(
        abs(nav_round_trip - expected_nav) <= 1,
        f"NAV after the round trip is {nav_round_trip}, not {expected_nav}",
    )
    bridge_back = reads.pending[5].value
    bridged_back = core_to_evm_amount(core_ledger()[6].spot_usdc_wei)
    expected_evm = reads.vault_usdc_end.value + bridged_back // CORE_WEI_PER_EVM_UNIT
    _check(
        bridge_back.pending
        and bridge_back.effect_kind == HyperCoreEffectKind.EVM_BALANCE
        and bridge_back.expected_balance == expected_evm,
        "the Core -> EVM bridge does not wait for its EVM credit",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    web3 = _connected_web3()
    read_core_metadata_at_head(web3)
    run_simulation(web3)


if __name__ == "__main__":
    main()
