"""Run a crosschain Plasma Vault on the released CCIP contracts: an Arbitrum hub, a HyperEVM spoke.

A crosschain vault keeps its accounting on one chain (the hub) and puts capital
to work on another (the spoke). On the hub a per-vault *executor* holds the
crosschain market's buckets -- settled remote balance, outbound in flight, idle
returned -- and sends messages; on the spoke a *dispatcher* at the very same
address receives them, holds the asset and deposits it into a spoke vault. The
hub vault cannot see the spoke, so the executor's NAV is a two-key
*attestation*: a proposer reports the dispatcher's observed balance, an approver
confirms it, and the crosschain balance fuse values the attested figure into the
vault's NAV. The SDK's ``CrosschainLane`` builds every lifecycle step.

This example uses the released IPOR crosschain contracts as they are deployed
on both chains: the ``CcipCrosschainFactory`` (one CREATE3 address on both
chains), the CCIP supply and command fuses, the crosschain claim and balance
fuses and the ``CrosschainNavValidationPreHook``. The deployed factory pair is
not configured yet, so the example configures it first, the way its owner
would. It shows, end to end:

  1. connect a ``Web3Context`` to each chain, pinned to one block per chain;
  2. check the executor and dispatcher creation codes against the release
     hashes (the factory stores them write-once);
  3. configure the factory on both chains as its owner: store the creation
     codes, schedule USDC, the factory-to-factory route and the dispatcher
     deployment gas limit, wait out the factory's timelock, execute them and
     allow the vault owner to create executors;
  4. create the hub vault on Arbitrum and the spoke vault on HyperEVM, grant the
     roles, set the USDC price feeds;
  5. create the vault's executor and wire the crosschain market on the hub
     vault: the four fuses, the executor and spoke-vault substrates and the NAV
     pre-hook; whitelist the dispatcher on the spoke vault;
  6. a depositor puts USDC into the hub vault;
  7. the alpha registers the executor's route and requests its dispatcher: the
     hub factory sends a deployment ticket, the spoke factory deploys the
     dispatcher at the executor's address and acknowledges;
  8. the alpha supplies USDC to the spoke: a CCIP token transfer to the
     dispatcher, acknowledged by a settlement receipt back to the hub;
  9. the proposer and the approver attest the dispatcher's observation, and
     the alpha refreshes the vault's cached NAV;
 10. the alpha sends a DEPOSIT command: the dispatcher deposits into the spoke
     vault and acknowledges;
 11. the alpha sends a REDEEM command: the dispatcher redeems the shares;
 12. the attesters re-mark the redeemed amount before anything comes back, so a
     loss on the spoke is never left in the settled bucket;
 13. the alpha recalls the idle: a CCIP token transfer back to the hub;
 14. the attesters mark the (now empty) remote balance to zero and the alpha
     claims the returned USDC into the vault.

Everything runs inside ``eth_simulateV1`` against pinned blocks -- nothing is
signed or broadcast. The two chains are simulated separately and a relay
delivers each CCIP message to the other chain by impersonating the Router, which
is what Chainlink's committee and executors do live. Constructs that exist only
because this runs under simulation are flagged inline as SIMULATION ONLY, with
what production does instead; there are several, because a deployment and a
lifecycle that the factory owner, the vault owner, the alpha, a depositor and
two attesters drive over more than a day are compressed into one script.

One live fact this simulation does not reproduce: Chainlink's executors submit
into HyperEVM's 3 M-gas small blocks. A delivery that does not fit one needs a
manual execution through the permissionless ``OffRamp.execute``
(``ipor_fusion.crosschain.manual_execution``); the one-time dispatcher
deployment ticket, which carries 6 M of gas, always does. The relay here
delivers every message directly.

The creation codes are the release build of ``CcipCrosschainExecutor`` and
``CcipCrosschainDispatcher``, linked against the deployed libraries; a factory
stores them as given, so they are an input here, checked against their pinned
hashes. Build them from a checkout of the contracts at the release commit
(``RELEASE_COMMIT``) with Foundry; with both variables unset the script prints
the two ``forge inspect`` commands, which link the nine release libraries:

    git clone https://github.com/IPOR-Labs/ipor-fusion && cd ipor-fusion
    git checkout <RELEASE_COMMIT> && npm ci
    forge inspect ... CcipCrosschainExecutor bytecode > executor.hex
    forge inspect ... CcipCrosschainDispatcher bytecode > dispatcher.hex

Run it (the shell snippets assume a POSIX shell -- bash or zsh):

    export ARBITRUM_PROVIDER_URL="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
    export HYPEREVM_PROVIDER_URL="https://YOUR_HYPEREVM_ARCHIVE_NODE"
    export CCIP_EXECUTOR_CREATION_CODE_FILE=executor.hex
    export CCIP_DISPATCHER_CREATION_CODE_FILE=dispatcher.hex
    uv run python examples/crosschain_ccip_usdc_arbitrum_hyperevm.py

Both providers must be archive nodes that implement ``eth_simulateV1`` and serve
state at the pinned blocks below.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion import (
    ERC20,
    AccessManager,
    Call,
    FusionFactory,
    FusionInstance,
    PlasmaVault,
    PriceOracleMiddlewareManager,
    Roles,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    erc20_balance_slot,
    is_simulate_v1_supported,
    parse_fuse_address,
)
from ipor_fusion.crosschain import (
    CcipChain,
    CcipCommandType,
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
    CcipLane,
    CcipRouteConfig,
    CcipTransport,
    Command,
    CrosschainSimulator,
    CrosschainSubstrateLib,
    LaneFuses,
    LaneObservation,
    SafetyConfig,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import (
    Amount,
    AssetId,
    CcipChainSelector,
    ChainId,
    Decimals,
    Period,
    Shares,
)

log = logging.getLogger("crosschain_ccip_usdc_arbitrum_hyperevm")

# ── Live infrastructure (do NOT change) ──────────────────────────────────────
# Chainlink CCIP routers and chain selectors, from the CCIP directory
# (docs.chain.link -> CCIP -> Directory -> Arbitrum One / Hyperliquid).
ARBITRUM_CHAIN_ID = ChainId(42161)
HYPEREVM_CHAIN_ID = ChainId(999)
ARBITRUM_CCIP_SELECTOR = CcipChainSelector(4949039107694359620)
HYPEREVM_CCIP_SELECTOR = CcipChainSelector(2442541497099098535)
ARBITRUM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x141fa059441E0ca23ce184B6A78bafD2A517DdE8"
)
HYPEREVM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec"
)

# Native (Circle-issued) USDC on each chain, 6 decimals
# (developers.circle.com -> stablecoins -> USDC contract addresses). The USDC
# CCIP lane between them is a CCTP burn-and-mint lane, so the asset arrives as
# the same native token on the other side.
ARBITRUM_USDC = Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
HYPEREVM_USDC = Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f")
USDC_DECIMALS = Decimals(6)

# Chainlink USDC/USD feed proxies (data.chain.link -> feeds -> arbitrum ->
# mainnet -> usdc-usd, and hyperliquid -> hyperliquid -> usdc-usd; the standard
# feeds, not SVR). Each vault's price manager prices USDC from its chain's feed.
ARBITRUM_USDC_USD_FEED = Web3.to_checksum_address(
    "0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3"
)
HYPEREVM_USDC_USD_FEED = Web3.to_checksum_address(
    "0xA0Adc43ce7AfE3EE7d7eac3C994E178D0620223B"
)

# IporFusionFactoryProxy on each chain, from ipor-abi
# mainnet/mainnet-arbitrum-fusion/addresses.json and
# mainnet/mainnet-hyperevm-fusion/addresses.json. Their clone(...) creates the
# hub and the spoke vault.
ARBITRUM_FUSION_FACTORY = Web3.to_checksum_address(
    "0x134fCAce7a2C7Ef3dF2479B62f03ddabAEa922d5"
)
HYPEREVM_FUSION_FACTORY = Web3.to_checksum_address(
    "0x6bBc827F34b4e862d15215ABd4AC0E3886181665"
)

# The released crosschain contracts. Every one sits at the same address on
# Arbitrum and HyperEVM and is recorded under the name in its comment in
# ipor-abi (mainnet/mainnet-arbitrum-fusion/addresses.json and
# mainnet/mainnet-hyperevm-fusion/addresses.json). The SDK's live test of this
# example also proves each runtime is the build of RELEASE_COMMIT.
RELEASE_COMMIT = "10f0f8e4031980b6f130d4f6460c6f8edfbcab32"
# CcipCrosschainFactory: creates executors on the hub and dispatchers on the
# spoke, and carries the factory-to-factory deployment ticket.
CCIP_FACTORY = Web3.to_checksum_address("0xAd84580810A4e498F5F13Ad8506F73782FB472FA")
# CcipCrosschainSupplyFuse, CcipCrosschainCommandFuse, CrosschainClaimFuse and
# CrosschainBalanceFuse, all bound to the CROSSCHAIN market (54).
CCIP_SUPPLY_FUSE = parse_fuse_address("0x00a20FD711083CcE9C9c4292762aFf79f78C48eb")
CCIP_COMMAND_FUSE = parse_fuse_address("0x997F12b31a6B1011ba844Bd0BC5fbeBCC7604793")
CROSSCHAIN_CLAIM_FUSE = parse_fuse_address("0x77bE16F7b33332BE2589E8E0582b397c524cF0d9")
CROSSCHAIN_BALANCE_FUSE = parse_fuse_address(
    "0x5896f376F06faB3964B1e060F8F3A86131D639c2"
)
# CrosschainNavValidationPreHook: closes the vault's share-price-sensitive entry
# points while any of its executors fails its NAV check (a stale attestation,
# a blocked chain, a stale transfer).
CROSSCHAIN_NAV_PRE_HOOK = Web3.to_checksum_address(
    "0xA17c7A5Ed994aA5FE99C22B37DeCBCbfb32FaFe3"
)
# The libraries the executor and the dispatcher link (source path, name,
# address), in the order Foundry's ``--libraries`` takes them.
RELEASE_LIBRARIES: tuple[tuple[str, str, ChecksumAddress], ...] = tuple(
    (source, name, Web3.to_checksum_address(address))
    for source, name, address in (
        (
            "contracts/crosschain/ccip/lib/CcipRouteLib.sol",
            "CcipRouteLib",
            "0x2045A36f6f884A7e80B8f27BD9673066C5c34d9A",
        ),
        (
            "contracts/crosschain/ccip/lib/CcipSendLib.sol",
            "CcipSendLib",
            "0xf4CD217F464821822e069c8984168B723439B6Fb",
        ),
        (
            "contracts/crosschain/ccip/lib/CcipDecimalsLib.sol",
            "CcipDecimalsLib",
            "0x43bb2B17a1980c06d6039F08640Cac8FcA37F766",
        ),
        (
            "contracts/crosschain/ccip/lib/CcipExecutorAttestationLib.sol",
            "CcipExecutorAttestationLib",
            "0x0921B83330A54212094b9822a6D5697519B0DA9f",
        ),
        (
            "contracts/crosschain/ccip/lib/CcipExecutorCommandLib.sol",
            "CcipExecutorCommandLib",
            "0xFF29Cf09B706fD333A43A0EF3c8962f097a218Ff",
        ),
        (
            "contracts/crosschain/ccip/lib/CcipExecutorTransferLib.sol",
            "CcipExecutorTransferLib",
            "0x9AD0ED30fdd23c5e46F9043F13C7733eF932Ef2c",
        ),
        (
            "contracts/crosschain/lib/RescueLogicLib.sol",
            "RescueLogicLib",
            "0x38bbaab37b55d200e87d1a9B1524ADD1d4235E62",
        ),
        (
            "contracts/crosschain/lib/CrosschainVaultActionLib.sol",
            "CrosschainVaultActionLib",
            "0x385Aa46Dd127b6976B5803CFB448662647E3B6fC",
        ),
        (
            "contracts/crosschain/ccip/lib/CcipDispatcherLib.sol",
            "CcipDispatcherLib",
            "0x75E4311Bf1fe29673F690df34Db0e5f29FDf2566",
        ),
    )
)
# keccak256 of the two creation codes built from RELEASE_COMMIT and linked
# against RELEASE_LIBRARIES. The factory accepts any bytes once, so whoever
# configures it checks the code against the release hash first, as
# check_creation_codes() does; the factory then reports the executor's hash
# (executorCreationCodeHash), which this example reads back.
EXECUTOR_CREATION_CODE_HASH = bytes.fromhex(
    "d8a2e5cb449fb74dff09d6f7d14db3ff93ea0900a734d3267de701595962f5f4"
)
DISPATCHER_CREATION_CODE_HASH = bytes.fromhex(
    "4ac8dacf772d8b1c4315364fa98df60008c393395eac2ae871318395c0e54985"
)

# The factory's owner on both chains, read from CcipCrosschainFactory.owner()
# (a Safe multisig). Only it configures the factory and allows executor
# creators; the example checks the read against this constant.
FACTORY_OWNER = Web3.to_checksum_address("0xA74376157E78053275fdae4A6849B5C16F41A0B9")

# ``IporFusionMarkets.CROSSCHAIN``: the market the crosschain fuses are bound to.
CROSSCHAIN_MARKET = IporFusionMarkets.CROSSCHAIN

# ── Actors ───────────────────────────────────────────────────────────────────
# All of them are impersonated simulation actors: the simulator runs with
# validation disabled, so it acts as any address without its key. None of them
# is an account you control.
#
# They are distinct addresses on purpose, because they are distinct roles that
# must not share an address in production:
#   VAULT_OWNER -- owner/atomist of both vaults, the executor's creator and its
#                  rescue admin; can reconfigure everything. A cold key or a
#                  multisig.
#   ALPHA       -- the online operator; drives execute() and refreshes market
#                  balances, nothing else. Collapsing it into VAULT_OWNER means
#                  a compromised alpha key becomes full governance control.
#   DEPOSITOR   -- an end user; only ever holds and deposits funds.
#   BALANCE_PROPOSER / BALANCE_APPROVER -- the two attestation keys; the
#                  executor refuses to be created with one address for both,
#                  and neither may be the alpha, or the alpha could mark its
#                  own NAV.
#   RESCUE_GUARDIAN -- can veto the executor's timelocked rescue actions,
#                  nothing else; the executor refuses it equal to the rescue
#                  admin (VAULT_OWNER).
VAULT_OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
# Anvil accounts #0 to #4, well-known test addresses.
ALPHA = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
DEPOSITOR = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
BALANCE_PROPOSER = Web3.to_checksum_address(
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
)
BALANCE_APPROVER = Web3.to_checksum_address(
    "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
)
RESCUE_GUARDIAN = Web3.to_checksum_address("0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65")

# ── Adjustable simulation parameters ─────────────────────────────────────────
# Pinned after the released contracts were deployed, at the same timestamp on
# both chains. Bump both if your provider cannot serve state at these heights
# (an error mentioning missing trie/state or a pruned block); keep them at one
# timestamp, the attestation rejects an observation stamped in the hub's future.
ARBITRUM_PINNED_BLOCK = 517_467_000
HYPEREVM_PINNED_BLOCK = 49_291_263

# The executor's asset id: the factory's name for USDC on both chains.
USDC_ASSET_ID = AssetId(bytes(Web3.keccak(text="USDC")))
# The executor's CREATE3 salt; with the creator it fixes the executor address,
# which is also the dispatcher address on the spoke.
EXECUTOR_SALT = bytes(Web3.keccak(text="ipor-fusion.py crosschain CCIP example"))

# The factory route policy. Commands carry 2.7 M of gas, the minimum the CCIP
# README of the release sets for messageGasLimit; token deliveries 1 M; the
# dispatcher deployment ticket 6 M. The fee caps are per message, in the
# source chain's native token: 0.01 ETH on Arbitrum, 1 HYPE on HyperEVM.
MESSAGE_GAS_LIMIT = 2_700_000
TOKEN_GAS_LIMIT = 1_000_000
DISPATCHER_DEPLOYMENT_GAS_LIMIT = 6_000_000
ARBITRUM_ROUTE_MAX_FEE = 10**16
HYPEREVM_ROUTE_MAX_FEE = 10**18

# The executor's safety configuration, fixed at creation. Approvals at least an
# hour apart; NAV fails closed a week after the last approval; one approval may
# move the settled balance by at most 20 %.
RESCUE_DELAY = Period(3 * 24 * 60 * 60)
BALANCE_STALENESS_MAX = Period(7 * 24 * 60 * 60)
BIG_CHANGE_BPS = 2_000
MIN_UPDATE_INTERVAL = Period(60 * 60)
TRANSFER_STALENESS_MAX = Period(24 * 60 * 60)

# 10 USDC into the hub vault, 1 USDC of it to the spoke. Small on purpose: the
# point is the shape of the flow.
DEPOSIT_AMOUNT = Amount(10_000_000)
SUPPLY_AMOUNT = Amount(1_000_000)
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
# SIMULATION ONLY: the block gas limit of every simulated block. The creation
# codes (about 26 KB each, about 12 M gas to store) and a vault clone (about
# 9 M) need more than HyperEVM's 3 M small blocks: live, those transactions go
# into HyperEVM's 30 M big blocks, which the sending account opts into.
SIMULATED_BLOCK_GAS_LIMIT = 30_000_000
CONFIGURE_CREATION_CODES_GAS = 14_000_000

# The vault entry points the NAV pre-hook guards, as its NatSpec lists them.
# ``execute`` passes through the hook (crosschain fuses check NAV themselves, so
# recovery stays possible); ``updateMarketsBalances`` must not be wired.
NAV_GUARDED_FUNCTIONS = (
    "deposit(uint256,address)",
    "depositWithPermit(uint256,address,uint256,uint8,bytes32,bytes32)",
    "mint(uint256,address)",
    "withdraw(uint256,address,address)",
    "redeem(uint256,address,address)",
    "redeemFromRequest(uint256,address,address)",
    "execute((address,bytes)[])",
)

CREATION_CODE_ENV_VARS = (
    "CCIP_EXECUTOR_CREATION_CODE_FILE",
    "CCIP_DISPATCHER_CREATION_CODE_FILE",
)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _check(condition: bool, message: str) -> None:
    """Fail loud unconditionally -- unlike ``assert``, survives ``python -O``."""
    if not condition:
        raise AssertionError(message)


def _assert_relay_success(results: dict[ChainId, SimulationResult]) -> None:
    """Raise with a readable summary if any simulated call on either chain
    reverted; each entry names the call and decodes its own revert."""
    failed = [
        (int(chain_id), call.label, call.error)
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
    return Web3Context(web3, chain_id=chain_id).at_block(block)


# ── Pure builders (no chain access, unit-testable offline) ───────────────────
class CreationCodes(NamedTuple):
    """The two creation codes the factory stores: the executor's (created on
    the hub) and the dispatcher's (created on the spoke by the ticket)."""

    executor: bytes
    dispatcher: bytes


def check_creation_codes(codes: CreationCodes) -> None:
    """Refuse creation codes that are not the release build. The factory
    stores them write-once, so a wrong code would need a new factory."""
    _check(
        bytes(Web3.keccak(codes.executor)) == EXECUTOR_CREATION_CODE_HASH,
        "the executor creation code is not the release build",
    )
    _check(
        bytes(Web3.keccak(codes.dispatcher)) == DISPATCHER_CREATION_CODE_HASH,
        "the dispatcher creation code is not the release build",
    )


def forge_inspect_command(contract: str) -> list[str]:
    """The ``forge inspect`` command, run in a checkout of the contracts at
    RELEASE_COMMIT, that prints ``contract``'s creation code linked against
    RELEASE_LIBRARIES."""
    command = ["forge", "inspect"]
    for source, name, address in RELEASE_LIBRARIES:
        command += ["--libraries", f"{source}:{name}:{address}"]
    return [
        *command,
        f"contracts/crosschain/ccip/{contract}.sol:{contract}",
        "bytecode",
    ]


def transport() -> CcipTransport:
    """The CCIP transport the relay simulates: both chains' routers, selectors
    and the USDC each side credits. ``CrosschainSimulator`` uses it to turn a
    ``CCIPMessageSent`` log on one chain into a ``ccipReceive`` on the other."""
    return CcipTransport(
        (
            CcipChain(
                ARBITRUM_CHAIN_ID,
                ARBITRUM_CCIP_SELECTOR,
                ARBITRUM_CCIP_ROUTER,
                ARBITRUM_USDC,
                USDC_DECIMALS,
            ),
            CcipChain(
                HYPEREVM_CHAIN_ID,
                HYPEREVM_CCIP_SELECTOR,
                HYPEREVM_CCIP_ROUTER,
                HYPEREVM_USDC,
                USDC_DECIMALS,
            ),
        )
    )


def factory_route(peer_selector: CcipChainSelector, max_fee: int) -> CcipRouteConfig:
    """The factory-to-factory route toward ``peer_selector``: the peer is the
    factory itself, at the same address on the other chain. Executors copy the
    delivery policy from it."""
    return CcipRouteConfig(
        chain_selector=peer_selector,
        peer=CCIP_FACTORY,
        # The zero address pays CCIP fees in the native token.
        fee_token=Web3.to_checksum_address("0x" + "00" * 20),
        message_gas_limit=MESSAGE_GAS_LIMIT,
        token_gas_limit=TOKEN_GAS_LIMIT,
        max_fee=max_fee,
        enabled=True,
    )


def executor_route(executor: ChecksumAddress) -> CcipRouteConfig:
    """The executor's route to HyperEVM: the factory's policy, with the
    dispatcher at the executor's own address as the peer."""
    return replace(
        factory_route(HYPEREVM_CCIP_SELECTOR, ARBITRUM_ROUTE_MAX_FEE), peer=executor
    )


def safety_config() -> SafetyConfig:
    """The executor's immutable safety configuration."""
    return SafetyConfig(
        rescue_admin=VAULT_OWNER,
        rescue_guardian=RESCUE_GUARDIAN,
        balance_proposer=BALANCE_PROPOSER,
        balance_approver=BALANCE_APPROVER,
        rescue_delay=RESCUE_DELAY,
        balance_staleness_max=BALANCE_STALENESS_MAX,
        big_change_bps=BIG_CHANGE_BPS,
        min_update_interval=MIN_UPDATE_INTERVAL,
        transfer_staleness_max=TRANSFER_STALENESS_MAX,
    )


def lane_fuses() -> LaneFuses:
    """The functional fuses the lane encodes for: supply, command, claim."""
    return LaneFuses(
        supply=CCIP_SUPPLY_FUSE,
        command=CCIP_COMMAND_FUSE,
        claim=CROSSCHAIN_CLAIM_FUSE,
    )


def crosschain_substrates(
    executor: ChecksumAddress, spoke_vault: ChecksumAddress
) -> list[bytes]:
    """The crosschain market's grants on the hub vault: the executor it may
    drive and the one spoke vault the executor may deposit into. Nothing else
    is granted, so the fuses refuse any other executor or remote vault."""
    return [
        CrosschainSubstrateLib.executor_substrate(executor),
        CrosschainSubstrateLib.remote_vault_substrate(HYPEREVM_CHAIN_ID, spoke_vault),
    ]


def nav_guarded_selectors() -> list[bytes]:
    """The 4-byte selectors of NAV_GUARDED_FUNCTIONS."""
    return [
        bytes(Web3.keccak(text=signature)[:4]) for signature in NAV_GUARDED_FUNCTIONS
    ]


def deposit_command(spoke_vault: ChecksumAddress, amount: Amount) -> Command:
    """DEPOSIT ``amount`` of the dispatcher's idle USDC into the spoke vault.

    ``amount`` is in the asset's shared decimals, which for USDC equal its
    local 6 decimals on both chains."""
    return Command.deposit(spoke_vault, amount)


def redeem_command(spoke_vault: ChecksumAddress, shares: Shares) -> Command:
    """REDEEM ``shares`` of the spoke vault back into the dispatcher's idle."""
    return Command.redeem(spoke_vault, shares)


def hub_clone(factory: FusionFactory) -> Call[FusionInstance]:
    """The hub vault's FusionFactory.clone call; the preview and the
    simulated create use this same call, so they yield the same addresses."""
    return factory.clone(
        asset_name="IPOR USDC Crosschain Hub (example)",
        asset_symbol="ipUSDCxc",
        underlying_token=ARBITRUM_USDC,
        redemption_delay_seconds=Period(1),
        owner=VAULT_OWNER,
    )


def spoke_clone(factory: FusionFactory) -> Call[FusionInstance]:
    """The spoke vault's FusionFactory.clone call. Its 1 s redemption delay is
    why the REDEEM below goes into a later block than the DEPOSIT."""
    return factory.clone(
        asset_name="IPOR USDC Crosschain Spoke (example)",
        asset_symbol="ipUSDCxs",
        underlying_token=HYPEREVM_USDC,
        redemption_delay_seconds=Period(1),
        owner=VAULT_OWNER,
    )


# ── The simulated deployment and lifecycle ───────────────────────────────────
class _Flow(NamedTuple):
    """Handles the phases share: the lane, both simulators and the vault
    wrappers on each side, and the numbers the run reports."""

    lane: CcipLane
    simulator: CrosschainSimulator
    hub: VaultSimulator
    spoke: VaultSimulator
    hub_vault: PlasmaVault
    spoke_vault: PlasmaVault
    hub_usdc: ERC20
    numbers: dict[str, int]

    def advance(self, seconds: int) -> None:
        # SIMULATION ONLY: the next calls land in a later block on both chains,
        # ``seconds`` later. Live, time passes on its own.
        self.hub.next_block(time_shift_seconds=seconds).with_block_override(
            gasLimit=SIMULATED_BLOCK_GAS_LIMIT
        )
        self.spoke.next_block(time_shift_seconds=seconds).with_block_override(
            gasLimit=SIMULATED_BLOCK_GAS_LIMIT
        )

    def relay(self) -> dict[ChainId, SimulationResult]:
        results = self.simulator.relay()
        _assert_relay_success(results)
        return results


def _configure_factory(
    chain: VaultSimulator,
    factory: CcipCrosschainFactory,
    codes: CreationCodes,
    *,
    token: ChecksumAddress,
    peer_chain_id: ChainId,
    route: CcipRouteConfig,
    config_delay: int,
) -> None:
    """Step 3 on one chain: the factory owner's governance. The asset, the route
    and the deployment gas limit go through the factory's own timelock
    (schedule, wait ``config_delay``, execute); the creation codes and the
    creator allowlist take effect at once."""
    # SIMULATION ONLY: FACTORY_OWNER is a multisig, impersonated here. Live,
    # each call below is a Safe transaction its signers approve (hand them
    # ``.calldata``), on each chain separately.
    chain.add_call(
        factory.configure_creation_codes(codes.executor, codes.dispatcher),
        from_=FACTORY_OWNER,
        label="configure_creation_codes",
        gas=CONFIGURE_CREATION_CODES_GAS,
    )
    chain.add_call(
        factory.schedule_asset(USDC_ASSET_ID, token, USDC_DECIMALS),
        from_=FACTORY_OWNER,
        label="schedule_asset",
    )
    chain.add_call(
        factory.schedule_factory_route(peer_chain_id, route),
        from_=FACTORY_OWNER,
        label="schedule_factory_route",
    )
    chain.add_call(
        factory.schedule_dispatcher_deployment_gas_limit(
            peer_chain_id, DISPATCHER_DEPLOYMENT_GAS_LIMIT
        ),
        from_=FACTORY_OWNER,
        label="schedule_dispatcher_deployment_gas_limit",
    )
    # SIMULATION ONLY (time advancement): the timelock elapses inside one run.
    # Live, the owner sends the execute calls once the delay has passed.
    chain.next_block(time_shift_seconds=config_delay + 1).with_block_override(
        gasLimit=SIMULATED_BLOCK_GAS_LIMIT
    )
    chain.add_call(
        factory.execute_asset(USDC_ASSET_ID, token, USDC_DECIMALS),
        from_=FACTORY_OWNER,
        label="execute_asset",
    )
    chain.add_call(
        factory.execute_factory_route(peer_chain_id, route),
        from_=FACTORY_OWNER,
        label="execute_factory_route",
    )
    chain.add_call(
        factory.execute_dispatcher_deployment_gas_limit(
            peer_chain_id, DISPATCHER_DEPLOYMENT_GAS_LIMIT
        ),
        from_=FACTORY_OWNER,
        label="execute_dispatcher_deployment_gas_limit",
    )
    # Executor creation is restricted to allowed creators: each executor's
    # dispatcher deployment is paid from the factory's own fee budget.
    chain.add_call(
        factory.set_creator_allowed(VAULT_OWNER, True),
        from_=FACTORY_OWNER,
        label="allow_executor_creator",
    )


def _configure_factories(
    simulator: CrosschainSimulator,
    hub: VaultSimulator,
    spoke: VaultSimulator,
    hub_ctx: Web3Context,
    spoke_ctx: Web3Context,
    codes: CreationCodes,
) -> None:
    """Step 3 on both chains, then read the configuration back."""
    factory = CcipCrosschainFactory(CCIP_FACTORY)
    # Plain reads of the live factory at the pinned blocks: its owner and its
    # timelock, the same on both chains.
    for ctx in (hub_ctx, spoke_ctx):
        _check(
            factory.owner().call(ctx) == FACTORY_OWNER,
            f"the factory owner on chain {ctx.chain_id} is not FACTORY_OWNER",
        )
        _check(
            factory.creation_codes_configured().call(ctx) is False,
            f"the factory on chain {ctx.chain_id} is configured already; "
            "pin an earlier block",
        )
    # The unsigned payload of the owner's first transaction on each chain, for
    # its multisig signers: ``.calldata`` needs no provider.
    payload = factory.configure_creation_codes(codes.executor, codes.dispatcher)
    log.info(
        "factory owner payload: to=%s selector=0x%s (%d bytes of calldata)",
        payload.to,
        payload.calldata[:4].hex(),
        len(payload.calldata),
    )
    config_delay = int(factory.config_delay().call(hub_ctx))
    _check(
        factory.config_delay().call(spoke_ctx) == config_delay,
        "the factories' timelocks differ",
    )
    _configure_factory(
        hub,
        factory,
        codes,
        token=ARBITRUM_USDC,
        peer_chain_id=HYPEREVM_CHAIN_ID,
        route=factory_route(HYPEREVM_CCIP_SELECTOR, ARBITRUM_ROUTE_MAX_FEE),
        config_delay=config_delay,
    )
    _configure_factory(
        spoke,
        factory,
        codes,
        token=HYPEREVM_USDC,
        peer_chain_id=ARBITRUM_CHAIN_ID,
        route=factory_route(ARBITRUM_CCIP_SELECTOR, HYPEREVM_ROUTE_MAX_FEE),
        config_delay=config_delay,
    )
    reads = {
        chain_id: (
            simulator.observe(chain_id, factory.executor_creation_code_hash()),
            simulator.observe(chain_id, factory.asset_config(USDC_ASSET_ID)),
            simulator.observe(chain_id, factory.ccip_route(peer)),
            simulator.observe(chain_id, factory.is_allowed_creator(VAULT_OWNER)),
        )
        for chain_id, peer in (
            (ARBITRUM_CHAIN_ID, HYPEREVM_CHAIN_ID),
            (HYPEREVM_CHAIN_ID, ARBITRUM_CHAIN_ID),
        )
    }
    _assert_relay_success(simulator.relay())
    for chain_id, token, peer in (
        (ARBITRUM_CHAIN_ID, ARBITRUM_USDC, CCIP_FACTORY),
        (HYPEREVM_CHAIN_ID, HYPEREVM_USDC, CCIP_FACTORY),
    ):
        code_hash, asset, route, allowed = reads[chain_id]
        _check(
            code_hash.value == EXECUTOR_CREATION_CODE_HASH,
            f"chain {chain_id}: the factory stored another executor code",
        )
        _check(
            asset.value == (token, USDC_DECIMALS, True),
            f"chain {chain_id}: USDC is not enabled on the factory",
        )
        _check(
            route.value.enabled and route.value.peer == peer,
            f"chain {chain_id}: the factory route is not live",
        )
        _check(allowed.value is True, f"chain {chain_id}: creator not allowed")
    log.info("factories configured on both chains after a %d s timelock", config_delay)


def _hub_roles(hub: VaultSimulator, access_manager: AccessManager) -> None:
    """Step 4 on the hub: the role bootstrap. A fresh clone grants VAULT_OWNER
    only OWNER_ROLE; it grants itself ATOMIST first, which administers the
    operational roles."""
    grants = (
        (Roles.ATOMIST_ROLE, VAULT_OWNER),
        # FUSE_MANAGER installs fuses, and a malicious fuse can drain the vault:
        # keep it on a key as protected as the owner's.
        (Roles.FUSE_MANAGER_ROLE, VAULT_OWNER),
        (Roles.PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE, VAULT_OWNER),
        # Administered by OWNER_ROLE: the pre-hooks gate every deposit and
        # withdrawal.
        (Roles.PRE_HOOKS_MANAGER_ROLE, VAULT_OWNER),
        # ALPHA must stay a separate address from VAULT_OWNER (see the actors).
        (Roles.ALPHA_ROLE, ALPHA),
        (Roles.UPDATE_MARKETS_BALANCES_ROLE, ALPHA),
        # A fresh clone is WHITELIST-gated (private), so the depositor needs it.
        (Roles.WHITELIST_ROLE, DEPOSITOR),
    )
    for role, account in grants:
        hub.add_call(
            access_manager.grant_role(role, account, Period(0)),
            from_=VAULT_OWNER,
            label=f"hub_grant_{role.name.lower()}",
        )


def _set_up_vaults(
    hub: VaultSimulator,
    spoke: VaultSimulator,
    hub_instance: FusionInstance,
    spoke_instance: FusionInstance,
    executor: ChecksumAddress,
    web3_arb: Web3,
) -> None:
    """Steps 4 to 6: both vaults, the executor, the crosschain market on the
    hub, the depositor's deposit."""
    hub_vault = PlasmaVault(hub_instance.plasma_vault)
    hub.add_call(
        hub_clone(FusionFactory(ARBITRUM_FUSION_FACTORY)),
        from_=VAULT_OWNER,
        label="clone_hub_vault",
    )
    spoke.add_call(
        spoke_clone(FusionFactory(HYPEREVM_FUSION_FACTORY)),
        from_=VAULT_OWNER,
        label="clone_spoke_vault",
    )
    # SIMULATION ONLY: the spoke's configuration starts in a later block than
    # its clone, so the clone has a whole simulated block to itself. Live,
    # every call is its own transaction anyway.
    spoke.next_block(time_shift_seconds=1).with_block_override(
        gasLimit=SIMULATED_BLOCK_GAS_LIMIT
    )

    _hub_roles(hub, AccessManager(hub_instance.access_manager))
    hub.add_call(
        PriceOracleMiddlewareManager(
            hub_instance.price_manager
        ).set_assets_price_sources([ARBITRUM_USDC], [ARBITRUM_USDC_USD_FEED]),
        from_=VAULT_OWNER,
        label="hub_usdc_price_feed",
    )

    # The spoke vault: its atomist sets the price feed and whitelists the
    # dispatcher -- the only depositor the spoke vault has.
    spoke_access = AccessManager(spoke_instance.access_manager)
    for role, account in (
        (Roles.ATOMIST_ROLE, VAULT_OWNER),
        (Roles.PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE, VAULT_OWNER),
        (Roles.WHITELIST_ROLE, executor),
    ):
        spoke.add_call(
            spoke_access.grant_role(role, account, Period(0)),
            from_=VAULT_OWNER,
            label=f"spoke_grant_{role.name.lower()}",
        )
    spoke.add_call(
        PriceOracleMiddlewareManager(
            spoke_instance.price_manager
        ).set_assets_price_sources([HYPEREVM_USDC], [HYPEREVM_USDC_USD_FEED]),
        from_=VAULT_OWNER,
        label="spoke_usdc_price_feed",
    )

    # Step 5: the executor, bound to the hub vault for life.
    hub.add_call(
        CcipCrosschainFactory(CCIP_FACTORY).create_executor(
            EXECUTOR_SALT, USDC_ASSET_ID, hub_instance.plasma_vault, safety_config()
        ),
        from_=VAULT_OWNER,
        label="create_executor",
    )
    # The crosschain market: the three functional fuses, the balance fuse that
    # values the executor into NAV, and only this executor and spoke vault.
    hub.add_call(
        hub_vault.add_fuses(
            [CCIP_SUPPLY_FUSE, CCIP_COMMAND_FUSE, CROSSCHAIN_CLAIM_FUSE]
        ),
        from_=VAULT_OWNER,
        label="add_crosschain_fuses",
    )
    hub.add_call(
        hub_vault.add_balance_fuse(CROSSCHAIN_MARKET, CROSSCHAIN_BALANCE_FUSE),
        from_=VAULT_OWNER,
        label="add_crosschain_balance_fuse",
    )
    hub.add_call(
        hub_vault.grant_market_substrates(
            CROSSCHAIN_MARKET,
            crosschain_substrates(executor, spoke_instance.plasma_vault),
        ),
        from_=VAULT_OWNER,
        label="grant_crosschain_substrates",
    )
    selectors = nav_guarded_selectors()
    hub.add_call(
        hub_vault.set_pre_hook_implementations(
            selectors,
            [CROSSCHAIN_NAV_PRE_HOOK] * len(selectors),
            [[] for _ in selectors],
        ),
        from_=VAULT_OWNER,
        label="wire_nav_pre_hook",
    )

    # Step 6: the depositor. SIMULATION ONLY: the depositor's USDC is a storage
    # override at the token's balance slot. Live, the depositor already holds
    # USDC. The approve and deposit are what a real depositor signs; the
    # deposit runs through the NAV pre-hook.
    hub.with_erc20_balance(
        ARBITRUM_USDC,
        DEPOSITOR,
        DEPOSIT_AMOUNT,
        slot=erc20_balance_slot(web3_arb, ARBITRUM_USDC, block=ARBITRUM_PINNED_BLOCK),
    )
    hub.add_call(
        ERC20(ARBITRUM_USDC).approve(hub_instance.plasma_vault, DEPOSIT_AMOUNT),
        from_=DEPOSITOR,
        label="approve_hub_deposit",
    )
    hub.add_call(
        hub_vault.deposit(DEPOSIT_AMOUNT, DEPOSITOR),
        from_=DEPOSITOR,
        label="deposit_hub_vault",
    )


def _request_dispatcher(flow: _Flow, executor: ChecksumAddress) -> None:
    """Step 7: register the executor's route and request its dispatcher. The
    hub factory sends the deployment ticket, the spoke factory deploys the
    dispatcher at ``executor`` and acknowledges."""
    lane = flow.lane
    flow.hub.execute(
        [
            lane.command_fuse.enter(
                CcipCommandType.REGISTER_ROUTE,
                executor=executor,
                chain_id=HYPEREVM_CHAIN_ID,
                route=executor_route(executor),
            ),
            lane.command_fuse.create_dispatcher(
                executor=executor,
                chain_id=HYPEREVM_CHAIN_ID,
                plasma_vaults=[flow.spoke_vault.address],
                send=lane.default_command_send(),
            ),
        ]
    )
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    dispatcher_ready = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.executor.has_dispatcher(HYPEREVM_CHAIN_ID)
    )
    dispatcher_registered = flow.simulator.observe(
        HYPEREVM_CHAIN_ID, CcipCrosschainFactory(CCIP_FACTORY).is_dispatcher(executor)
    )
    hub_shares = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, flow.hub_vault.balance_of(DEPOSITOR)
    )
    vault_usdc = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, flow.hub_usdc.balance_of(flow.hub_vault.address)
    )
    guarded = flow.simulator.observe(
        ARBITRUM_CHAIN_ID,
        flow.hub_vault.get_pre_hook_implementation(nav_guarded_selectors()[0]),
    )
    flow.relay()
    _check(dispatcher_ready.value is True, "the hub has no dispatcher ACK")
    _check(
        dispatcher_registered.value is True,
        "the spoke factory did not deploy the dispatcher",
    )
    _check(hub_shares.value > 0, "the depositor received no hub shares")
    _check(
        vault_usdc.value == DEPOSIT_AMOUNT, "the hub vault does not hold the deposit"
    )
    _check(
        guarded.value == CROSSCHAIN_NAV_PRE_HOOK,
        "deposit() is not behind the NAV pre-hook",
    )
    log.info(
        "dispatcher deployed at %s on HyperEVM; hub vault holds %d USDC units",
        executor,
        vault_usdc.value,
    )
    flow.numbers["vault_usdc_after_deposit"] = int(vault_usdc.value)


def _open(web3_arb: Web3, web3_hype: Web3, codes: CreationCodes) -> _Flow:
    """Steps 1 to 7: everything up to a lane with a deployed dispatcher."""
    hub_ctx = _pinned_context(web3_arb, ARBITRUM_CHAIN_ID, ARBITRUM_PINNED_BLOCK)
    spoke_ctx = _pinned_context(web3_hype, HYPEREVM_CHAIN_ID, HYPEREVM_PINNED_BLOCK)
    check_creation_codes(codes)

    # Previews (eth_call, no state change): the vault stacks the two clones
    # create and the executor's CREATE3 address. Nothing else touches either
    # FusionFactory in between, so the simulated clones land at these
    # addresses. The executor address depends only on the factory, the creator
    # and the salt, so the live factory answers it before any configuration.
    hub_instance = hub_clone(FusionFactory(ARBITRUM_FUSION_FACTORY)).call(hub_ctx)
    spoke_instance = spoke_clone(FusionFactory(HYPEREVM_FUSION_FACTORY)).call(spoke_ctx)
    executor = (
        CcipCrosschainFactory(CCIP_FACTORY)
        .compute_executor_address(VAULT_OWNER, EXECUTOR_SALT)
        .call(hub_ctx)
    )
    log.info(
        "hub vault=%s spoke vault=%s executor/dispatcher=%s",
        hub_instance.plasma_vault,
        spoke_instance.plasma_vault,
        executor,
    )

    simulator = CrosschainSimulator(transport())
    hub = simulator.add_chain(
        ARBITRUM_CHAIN_ID,
        web3_arb,
        block=ARBITRUM_PINNED_BLOCK,
        vault=hub_instance.plasma_vault,
        alpha=ALPHA,
    )
    spoke = simulator.add_chain(
        HYPEREVM_CHAIN_ID, web3_hype, block=HYPEREVM_PINNED_BLOCK
    )
    # SIMULATION ONLY: the hub's simulated clock starts a minute ahead of the
    # spoke's, so nothing read on the spoke during the setup is stamped in the
    # hub's future; the clocks are aligned before the lifecycle starts.
    hub.with_block_time_shift(60).with_block_override(
        gasLimit=SIMULATED_BLOCK_GAS_LIMIT
    )
    spoke.with_block_override(gasLimit=SIMULATED_BLOCK_GAS_LIMIT)

    _configure_factories(simulator, hub, spoke, hub_ctx, spoke_ctx, codes)

    hub.next_block(time_shift_seconds=1).with_block_override(
        gasLimit=SIMULATED_BLOCK_GAS_LIMIT
    )
    spoke.next_block(time_shift_seconds=1).with_block_override(
        gasLimit=SIMULATED_BLOCK_GAS_LIMIT
    )
    _set_up_vaults(hub, spoke, hub_instance, spoke_instance, executor, web3_arb)

    route = executor_route(executor)
    # SIMULATION ONLY: the native balances that pay CCIP fees. The hub factory
    # pays the deployment ticket, the spoke factory its ACK, the executor and
    # the dispatcher every message they send. Live, each holds a native float
    # someone tops up with a plain transfer; the simulation sets it instead.
    simulator.fund_native(ARBITRUM_CHAIN_ID, CCIP_FACTORY, route.max_fee)
    simulator.fund_native(HYPEREVM_CHAIN_ID, CCIP_FACTORY, HYPEREVM_ROUTE_MAX_FEE)
    simulator.fund_native(ARBITRUM_CHAIN_ID, executor, 10**18)
    simulator.fund_native(HYPEREVM_CHAIN_ID, executor, 10**18)

    lane = CcipLane(
        hub_ctx=hub_ctx,
        spoke_ctx=spoke_ctx,
        executor=CcipCrosschainExecutor(executor),
        dispatcher=CcipCrosschainDispatcher(executor),
        fuses=lane_fuses(),
        route=route,
        # USDC has 6 decimals on both chains: one local unit per shared unit.
        decimal_conversion_rate=1,
    )
    flow = _Flow(
        lane=lane,
        simulator=simulator,
        hub=hub,
        spoke=spoke,
        hub_vault=PlasmaVault(hub_instance.plasma_vault),
        spoke_vault=PlasmaVault(spoke_instance.plasma_vault),
        hub_usdc=ERC20(ARBITRUM_USDC),
        numbers={},
    )
    _request_dispatcher(flow, executor)

    # SIMULATION ONLY: one clock for both chains from here on. The executor
    # accepts an observation only if it was taken after its last value-moving
    # receipt and not after its own block time; live, the proposer reads the
    # spoke after the receipt is visible on the hub and the clocks agree.
    lag = hub.current_time - spoke.current_time
    if lag > 0:
        spoke.next_block(time_shift_seconds=lag).with_block_override(
            gasLimit=SIMULATED_BLOCK_GAS_LIMIT
        )
    return flow


def _supply(flow: _Flow) -> tuple[Amount, LaneObservation]:
    """Step 8: the alpha supplies the spoke. Returns the amount the dispatcher
    was credited with and its observation once the transfer settled."""
    lane = flow.lane
    # The supply fuse moves USDC vault -> executor -> CCIP. CCIP has no
    # destination-side floor, so ``min_received`` must be 0 here; the
    # settlement receipt that comes back carries the amount actually credited.
    flow.hub.execute(
        [lane.supply(asset=ARBITRUM_USDC, amount=SUPPLY_AMOUNT, min_received=Amount(0))]
    )
    outbound_after_send = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.outbound_in_flight()
    )
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    _check(
        outbound_after_send.value == SUPPLY_AMOUNT,
        "the supply did not enter the outbound bucket",
    )

    # One block later: the token leg landed on the spoke, the settlement
    # receipt landed back on the hub, the outbound bucket is empty again.
    flow.advance(1)
    settled_after_supply = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.settled_remote_balance()
    )
    outbound_after_supply = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.outbound_in_flight()
    )
    observation_after_supply = flow.simulator.observe(
        HYPEREVM_CHAIN_ID, lane.observation()
    )
    flow.relay()
    observation = observation_after_supply.value
    credited = Amount(observation.tracked_idle)
    _check(0 < credited <= SUPPLY_AMOUNT, "nothing was credited on the spoke")
    _check(
        settled_after_supply.value == credited,
        "the hub's settled bucket disagrees with the spoke's credit",
    )
    _check(outbound_after_supply.value == 0, "the outbound bucket did not clear")
    log.info("supply: sent=%d credited=%d", SUPPLY_AMOUNT, credited)
    flow.numbers.update({"sent": int(SUPPLY_AMOUNT), "credited": int(credited)})
    return credited, observation


def _attest(
    flow: _Flow, tag: str, observation: LaneObservation, *, hub_idle: int = 0
) -> int:
    """Steps 9, 12 and 14: the two-key attestation of a spoke ``observation``,
    then the alpha's NAV refresh. Returns the attested settled balance."""
    lane, hub = flow.lane, flow.hub
    # The executor refuses an observation that does not follow its last
    # value-moving receipt; the proposer reads that time from the hub.
    receipt_at = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.last_value_moving_receipt_at()
    )
    flow.relay()
    # The proposal is the dispatcher snapshot at the spoke block it was read
    # in, stamped with that block's number and time; it must still match the
    # dispatcher's state version when approved.
    proposal = lane.attestation(
        observation,
        remote_block=flow.spoke.current_block_number,
        remote_timestamp=Timestamp(flow.spoke.current_time),
        expiry=Timestamp(flow.hub.current_time + PROPOSAL_TTL),
        receipt_at=receipt_at.value,
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
    executor_nav = flow.simulator.observe(ARBITRUM_CHAIN_ID, lane.get_balance())
    market_nav = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, flow.hub_vault.total_assets_in_market(CROSSCHAIN_MARKET)
    )
    flow.relay()
    attested = int(observation.accounted_balance)
    executor_balance = executor_nav.value
    market_total = market_nav.value
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
    """Step 10: DEPOSIT the dispatcher's idle into the spoke vault."""
    lane = flow.lane
    flow.hub.execute(
        [lane.send_command(deposit_command(flow.spoke_vault.address, credited))]
    )
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    spoke_shares = flow.simulator.observe(
        HYPEREVM_CHAIN_ID, flow.spoke_vault.balance_of(lane.executor_address)
    )
    observation_after_deposit = flow.simulator.observe(
        HYPEREVM_CHAIN_ID, lane.observation()
    )
    active_after_deposit = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.has_active_command()
    )
    flow.relay()
    shares = spoke_shares.value
    observation = observation_after_deposit.value
    _check(shares > 0, "the spoke vault issued no shares to the dispatcher")
    _check(
        observation.tracked_idle == 0, "the dispatcher still holds idle after DEPOSIT"
    )
    # The command slot is free again only once the hub processed the ACK.
    _check(
        active_after_deposit.value is False,
        "the DEPOSIT command is still active on the hub",
    )
    log.info("deposit: shares=%d", shares)
    flow.numbers["spoke_shares"] = int(shares)
    return shares


def _redeem_from_spoke_vault(
    flow: _Flow, shares: Shares
) -> tuple[Amount, LaneObservation]:
    """Step 11: REDEEM the shares; the proceeds become the dispatcher's idle.
    Returns that idle and the dispatcher's observation after the REDEEM."""
    lane = flow.lane
    # A later spoke block: the spoke vault's redemption delay separates the
    # deposit and the redeem.
    flow.advance(60)
    flow.hub.execute(
        [lane.send_command(redeem_command(flow.spoke_vault.address, shares))]
    )
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    flow.advance(1)
    observation_after_redeem = flow.simulator.observe(
        HYPEREVM_CHAIN_ID, lane.observation()
    )
    spoke_shares_after_redeem = flow.simulator.observe(
        HYPEREVM_CHAIN_ID, flow.spoke_vault.balance_of(lane.executor_address)
    )
    active_after_redeem = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.has_active_command()
    )
    flow.relay()
    remote_idle = observation_after_redeem.value.tracked_idle
    _check(spoke_shares_after_redeem.value == 0, "shares remain after REDEEM")
    _check(remote_idle > 0, "REDEEM returned nothing to the dispatcher")
    _check(
        active_after_redeem.value is False,
        "the REDEEM command is still active on the hub",
    )
    log.info("redeem: remote_idle=%d", remote_idle)
    flow.numbers["remote_idle"] = int(remote_idle)
    return remote_idle, observation_after_redeem.value


def _recall_and_claim(flow: _Flow, remote_idle: Amount) -> None:
    """Steps 13 and 14: recall the idle to the hub, attest the empty remote
    balance, claim the USDC into the vault."""
    lane = flow.lane
    # ``min_return`` is enforced on the spoke against the amount it sends; the
    # hub credits whatever arrives after the token pool's fee and only emits
    # ReturnBelowMinimumCredited when that is below the floor.
    flow.hub.execute([lane.recall(amount=remote_idle, min_return=remote_idle)])
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    flow.advance(1)
    idle_after_return = flow.simulator.observe(ARBITRUM_CHAIN_ID, lane.idle_ledger())
    settled_after_return = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.settled_remote_balance()
    )
    pending_after_return = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, lane.pending_transfer_count()
    )
    observation_after_return = flow.simulator.observe(
        HYPEREVM_CHAIN_ID, lane.observation()
    )
    flow.relay()
    idle = idle_after_return.value
    settled = settled_after_return.value
    _check(0 < idle <= remote_idle, "the recall credited nothing on the hub")
    _check(pending_after_return.value == 0, "a transfer is still pending")
    # The executor debits the settled bucket by the amount the spoke sent, so
    # after a full recall nothing is left in it: the re-attestation of step 12
    # already marked any spoke-side loss, and a bridge fee is a realized loss
    # that lands in the idle, never in the settled bucket.
    _check(settled == 0, f"settled bucket not empty after the recall: {settled}")
    log.info("recall: idle=%d settled=%d", idle, settled)
    flow.numbers.update({"idle": idle, "settled_after_return": settled})

    # Step 14a: the dispatcher observes zero; the attesters mark it. The
    # executor's NAV is then exactly the idle it holds. The executor refuses
    # an approval sooner than MIN_UPDATE_INTERVAL after the previous one.
    flow.advance(MIN_UPDATE_INTERVAL)
    _attest(flow, "residue", observation_after_return.value, hub_idle=idle)

    # Step 14b: the claim pulls the idle from the executor into the vault.
    flow.hub.execute([lane.claim(Amount(idle))])
    flow.hub.add_call(
        flow.hub_vault.update_markets_balances([CROSSCHAIN_MARKET]),
        from_=ALPHA,
        label="update_balances_final",
    )
    hub_vault = flow.hub_vault.address
    vault_usdc_after = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, flow.hub_usdc.balance_of(hub_vault)
    )
    executor_usdc_after = flow.simulator.observe(
        ARBITRUM_CHAIN_ID, flow.hub_usdc.balance_of(lane.executor_address)
    )
    idle_after_claim = flow.simulator.observe(ARBITRUM_CHAIN_ID, lane.idle_ledger())
    nav_final = flow.simulator.observe(ARBITRUM_CHAIN_ID, lane.get_balance())
    market_final = flow.simulator.observe(
        ARBITRUM_CHAIN_ID,
        flow.hub_vault.total_assets_in_market(CROSSCHAIN_MARKET),
    )
    flow.relay()
    vault_after_deposit = flow.numbers["vault_usdc_after_deposit"]
    vault_after = vault_usdc_after.value
    # The vault holds what it held after the deposit, minus whatever the round
    # trip cost (bridge fees, the spoke vault's fee); the executor keeps nothing
    # and the market values to zero.
    _check(
        vault_after == vault_after_deposit - SUPPLY_AMOUNT + idle,
        f"vault USDC {vault_after} != {vault_after_deposit} - {SUPPLY_AMOUNT} sent + {idle} back",
    )
    _check(executor_usdc_after.value == 0, "the executor still holds USDC")
    _check(idle_after_claim.value == 0, "the idle ledger is not empty")
    _check(nav_final.value == 0, "the executor NAV is not zero")
    _check(market_final.value == 0, "the crosschain market is not zero")
    flow.numbers.update(
        {
            "vault_usdc_after": vault_after,
            "round_trip_cost": int(SUPPLY_AMOUNT) - idle,
        }
    )


def run_simulation(
    web3_arb: Web3, web3_hype: Web3, codes: CreationCodes
) -> dict[str, int]:
    """Configure the factories, build both vaults and the lane, run the whole
    lifecycle and return its numbers (all in USDC units)."""
    log.info("transaction plan:")
    for i, step in enumerate(
        (
            "factory owner, each chain: configureCreationCodes, schedule asset/route/"
            "ticket gas, (timelock) execute them, setCreatorAllowed(vault owner)",
            "vault owner: clone the hub (Arbitrum) and spoke (HyperEVM) vaults, roles, "
            "USDC price feeds, whitelist the dispatcher on the spoke",
            "vault owner: createExecutor; add the crosschain fuses, substrates and "
            "the NAV pre-hook to the hub vault",
            "depositor: approve + deposit USDC into the hub vault",
            "alpha: execute([REGISTER_ROUTE, CREATE_DISPATCHER]) -> ticket, "
            "dispatcher deployed on HyperEVM, ACK",
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

    flow = _open(web3_arb, web3_hype, codes)
    credited, observation_after_supply = _supply(flow)
    _attest(flow, "initial", observation_after_supply)
    shares = _deposit_into_spoke_vault(flow, credited)
    remote_idle, observation_after_redeem = _redeem_from_spoke_vault(flow, shares)
    # Step 12: mark the redeemed amount before recalling it. The dispatcher
    # carries a deposit at cost and the spoke vault's fee or a loss shows only
    # at REDEEM; attesting it now keeps it out of the settled bucket, where a
    # full recall could otherwise leave an un-attestable residue.
    flow.advance(MIN_UPDATE_INTERVAL)
    _attest(flow, "redeemed", observation_after_redeem)
    _recall_and_claim(flow, remote_idle)
    return flow.numbers


def _load_creation_codes() -> CreationCodes:
    """The creation codes from the two files the environment names, or exit
    with the commands that build them."""
    paths = [os.environ.get(name) for name in CREATION_CODE_ENV_VARS]
    if not all(paths):
        commands = "\n".join(
            " ".join(forge_inspect_command(contract)) + f" > {file}"
            for contract, file in (
                ("CcipCrosschainExecutor", "executor.hex"),
                ("CcipCrosschainDispatcher", "dispatcher.hex"),
            )
        )
        sys.exit(
            f"set {' and '.join(CREATION_CODE_ENV_VARS)}; build the files in a "
            f"checkout of ipor-fusion at {RELEASE_COMMIT} (after npm ci) with:\n"
            f"{commands}"
        )
    executor_path, dispatcher_path = (Path(str(path)) for path in paths)
    return CreationCodes(
        executor=bytes.fromhex(executor_path.read_text().strip().removeprefix("0x")),
        dispatcher=bytes.fromhex(
            dispatcher_path.read_text().strip().removeprefix("0x")
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    codes = _load_creation_codes()
    web3_arb = _connected_web3("ARBITRUM_PROVIDER_URL", ARBITRUM_CHAIN_ID)
    web3_hype = _connected_web3("HYPEREVM_PROVIDER_URL", HYPEREVM_CHAIN_ID)
    numbers = run_simulation(web3_arb, web3_hype, codes)
    log.info("done: %s", numbers)
    log.info(
        "round trip: %d USDC units out, %d back, cost %d",
        numbers["sent"],
        numbers["idle"],
        numbers["round_trip_cost"],
    )


if __name__ == "__main__":
    main()
