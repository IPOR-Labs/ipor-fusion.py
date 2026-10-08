"""The composed flow: an Arbitrum hub funds a HyperCore vault on HyperEVM and trades on Core.

Two preview-status pieces of IPOR Fusion joined end to end: a crosschain lane
(hub executor on Arbitrum, dispatcher on HyperEVM, Chainlink CCIP between them,
market 54) whose remote vault is a HyperCore vault (a HyperEVM Plasma Vault that
is itself a Hyperliquid Core account, market 55). The hub supplies USDC to the
dispatcher, the dispatcher deposits it into the HyperCore vault, the vault's
alpha trades on Core, and the capital comes back the same way: redeem, recall,
claim, with a two-key attestation of the remote balance after every change.

This example follows the second live cycle of that flow (2026-10-07/08, 15 USDC)
and splits it by what a node can do without signing anything:

  A. the Core leg is PREVIEWED at head on HyperEVM. ``eth_simulateV1`` cannot
     run the HyperCore read precompiles (the RPC ignores code overrides at
     0x800-0x813), but ``eth_call`` and ``eth_estimateGas`` execute them, and
     this RPC accepts state overrides. So each Core action -- bridge EVM -> Core,
     spot -> perp dex, a limit order, its reduce-only close, dex -> spot,
     Core -> EVM -- is built from the vault's own substrate grants, previewed
     from the alpha and reported with its gas or its decoded revert. Sends that
     need a Core balance are sized to the vault's live Core inventory, because
     Core-side balances cannot be overridden from the EVM;
  B. the hub legs that never touch the HyperCore vault are SIMULATED in
     ``CrosschainSimulator`` at blocks pinned inside the live cycle: the REDEEM
     command sized by the vault's EVM exit ceiling behind the executor's fee
     gate, then the recall, the token return, the residual attestation and the
     claim, replayed over the relay exactly as cycle 2 ran them.

The two spoke deliveries that execute the HyperCore vault's own deposit and
redeem (DEPOSIT and REDEEM commands) are the one thing neither tool reproduces:
their live receipts are cited instead.

Run it (POSIX shell):

    export ARBITRUM_PROVIDER_URL="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
    export HYPEREVM_PROVIDER_URL="https://YOUR_HYPEREVM_ARCHIVE_NODE"
    uv run python examples/composed_hypercore_flow_arbitrum_hyperevm.py

The Arbitrum provider must be an archive node with ``eth_simulateV1`` serving the
pinned blocks; the HyperEVM provider must serve ``eth_call`` with state
overrides at head and state at its pinned block.

Status: preview, not production. The contracts behind both markets are under
active development, the mainnet deployments are a proof of concept and IPOR
Labs canaries, and interfaces may change between minor versions; see
``ipor_fusion.about.PREVIEW_FEATURES``.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, NamedTuple

from eth_abi.abi import encode as abi_encode
from eth_utils import keccak
from web3 import Web3

from ipor_fusion import (
    ERC20,
    CcipChain,
    CcipCrosschainExecutor,
    CcipTransport,
    Command,
    CrosschainLane,
    CrosschainSimulator,
    PlasmaVault,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    ccip_events,
    decode_custom_error,
    decode_substrate,
    erc20_balance_slot,
    open_lane,
    quote_ccip_native_fee,
    read_hypercore_evm_exit_ceiling,
    read_hypercore_vault_state,
)
from ipor_fusion.fuses import (
    HyperCoreDepositFuse,
    HyperCoreOrderFuse,
    HyperCoreSendFuse,
)
from ipor_fusion.fuses.hypercore import (
    SPOT_DEX,
    USDC_SYSTEM_ADDRESS,
    TimeInForce,
    read_index_of,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.hypercore import HyperCoreReader
from ipor_fusion.types import Amount, ChainId, MarketId, Period, Shares

log = logging.getLogger("composed_hypercore_flow_arbitrum_hyperevm")

# ── Live infrastructure (do NOT change) ──────────────────────────────────────
ARBITRUM_CHAIN_ID = ChainId(42161)
HYPEREVM_CHAIN_ID = ChainId(999)
# Chainlink CCIP routers and chain selectors (docs.chain.link -> CCIP -> Directory).
ARBITRUM_CCIP_SELECTOR = 4949039107694359620
HYPEREVM_CCIP_SELECTOR = 2442541497099098535
ARBITRUM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x141fa059441E0ca23ce184B6A78bafD2A517DdE8"
)
HYPEREVM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec"
)
# Native USDC on each chain (6 decimals); the lane between them is CCTP burn-and-mint.
ARBITRUM_USDC = Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
HYPEREVM_USDC = Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f")

# The composed test deployment the live cycles drove (created by the IPOR SDK
# team against the v3 CCIP crosschain factory pair). Not in the ipor-abi
# registry; verify with ``discover_deployment`` on the hub vault.
HUB_VAULT = Web3.to_checksum_address("0x8BeED16DB1f354B0BFa31eF7e747102e26eb8FbF")
# The executor on Arbitrum and the dispatcher on HyperEVM: one CREATE3 address.
EXECUTOR = Web3.to_checksum_address("0xcb83A78BD8e8c284d5704B62945EF2C454266dB0")
# The remote vault: a HyperCore vault (market 55) on HyperEVM, and the Core
# account the trade runs in.
HYPERCORE_VAULT = Web3.to_checksum_address("0xB8De01Ca5ca57A14671E1c696337134a02B4A478")
HYPERCORE_BALANCE_FUSE = Web3.to_checksum_address(
    "0xaC7404Ec2Bc3F2d3c0853633e0c3edfA25DC50F9"
)
HYPERCORE_MARKET = 55
CROSSCHAIN_MARKET = MarketId(int(IporFusionMarkets.CROSSCHAIN))

# ── Actors ───────────────────────────────────────────────────────────────────
# One EOA holds the alpha role on both vaults in this test deployment; the two
# attesters are separate keys. Production separates every role.
ALPHA = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
BALANCE_PROPOSER = Web3.to_checksum_address(
    "0xd122DCF446bC200D1f19AD45eB669a5Ef273dBC8"
)
BALANCE_APPROVER = Web3.to_checksum_address(
    "0x64D345FE416EE6b841B544F27277d9B61b61A02a"
)

# ── Pinned blocks inside the live cycle (bump only if your provider cannot
# serve them; keep each hub block a little AFTER its spoke block) ─────────────
# Just before the REDEEM command of 2026-10-08 09:53:33 UTC: the dispatcher
# holds 1,606,344,423 shares of the HyperCore vault, the executor's float was
# topped up above the route cap after the first attempt reverted.
ARBITRUM_BLOCK_BEFORE_REDEEM = 512_845_819
HYPEREVM_BLOCK_BEFORE_REDEEM = 47_983_100
# Just before the recall of 10:16:39 UTC: the REDEEM is acknowledged, the
# dispatcher holds 14,965,393 USDC idle plus 238,862 residual shares, and the
# hub's settled remote balance reads 14,967,618 (attestation 8).
ARBITRUM_BLOCK_BEFORE_RECALL = 512_850_829
HYPEREVM_BLOCK_BEFORE_RECALL = 47_984_500
PROPOSAL_TTL = Period(30 * 60)
APPROVAL_DELAY = Period(5 * 60)
BRIDGE_LATENCY = Period(2 * 60)

# ── The trade the live cycle ran ─────────────────────────────────────────────
# xyz is HIP-3 perp dex 1; xyz:NVDA is action asset 100_000 + 1 * 10_000 + 2.
XYZ_DEX = 1
XYZ_NVDA_ASSET = 110_002
# Core amounts are Core wei: 8 decimals for USDC; sizes carry 8 implied
# decimals too (0.05 NVDA = 5_000_000), prices 8 implied decimals.
CORE_WEI = 10**8
BRIDGE_AMOUNT_USDC = Amount(14_000_000)  # 14 USDC in EVM units (6 decimals)
ORDER_SIZE = 5_000_000  # 0.05 xyz:NVDA
# The Core -> EVM route charges a variable Core fee on top of the amount; the
# live cycle kept 0.05 USDC on spot and paid 0.001749 from it.
CORE_FEE_RESERVE_WEI = 5_000_000  # 0.05 USDC in Core wei
LIMIT_SLIPPAGE_BPS = 100  # a limit 1 % through the mark fills like a market order


# ── Helpers ──────────────────────────────────────────────────────────────────
def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _connected_web3(env_var: str, chain_id: ChainId) -> Web3:
    url = os.environ.get(env_var)
    if not url:
        sys.exit(f"set {env_var} to an RPC endpoint for chain {chain_id}")
    web3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 90}))
    _check(web3.eth.chain_id == chain_id, f"{env_var} is not chain {chain_id}")
    return web3


def _usdc_balance_override(
    web3: Web3, holder: str, amount: int, *, block: int | str = "latest"
) -> dict[str, Any]:
    """An ``eth_call`` state override that credits ``holder`` with ``amount``
    USDC on HyperEVM: the balance slot is found by probing, never assumed."""
    slot = erc20_balance_slot(web3, HYPEREVM_USDC, block=block)
    key = keccak(abi_encode(["address", "uint256"], [holder, slot]))
    return {
        HYPEREVM_USDC: {
            "stateDiff": {"0x" + key.hex(): "0x" + amount.to_bytes(32, "big").hex()}
        }
    }


def _to_hex(data: bytes | str) -> str:
    return data if isinstance(data, str) else "0x" + bytes(data).hex()


class Preview(NamedTuple):
    """One Core action as the alpha would send it, and what the node says."""

    step: str
    calldata: str
    gas: int | None  # None when the preview reverted
    revert: str | None  # the decoded custom error, or None
    note: str


def _preview(
    web3: Web3,
    vault: PlasmaVault,
    step: str,
    action: Any,
    *,
    override: dict[str, Any] | None,
    note: str,
) -> Preview:
    """``eth_call`` + ``eth_estimateGas`` of ``execute([action])`` from the
    alpha at head. A revert is decoded by name through the SDK registry
    (``decode_custom_error``) and reported, never raised: at head the vault's
    Core inventory is whatever the last live cycle left, and a preview that
    reverts on a balance is information, not a failure of the example."""
    calldata = _to_hex(vault.execute([action]).calldata)
    params: list[Any] = [
        {"from": ALPHA, "to": HYPERCORE_VAULT, "data": calldata},
        "latest",
    ]
    if override is not None:
        params.append(override)
    response = web3.provider.make_request("eth_estimateGas", params)
    if "result" in response:
        return Preview(step, calldata, int(response["result"], 16), None, note)
    error = response.get("error") or {}
    data = str(error.get("data") or "")
    decoded = None
    if data.startswith("0x") and len(data) >= 10:
        raw = bytes.fromhex(data[2:])
        decoded = decode_custom_error(raw[:4], raw[4:]) or data[:10]
    return Preview(step, calldata, None, decoded or str(error)[:120], note)


# ── Part A: the Core leg, previewed at head ──────────────────────────────────
def core_leg_previews(web3_hype: Web3) -> list[Preview]:
    """Build the six Core actions of the live cycle from the vault's own
    substrate grants and preview each one from the alpha at head."""
    ctx = Web3Context(web3_hype, chain_id=HYPEREVM_CHAIN_ID)
    vault = PlasmaVault(ctx, HYPERCORE_VAULT)

    # 1. What the vault is allowed to do on Core: every parameter below comes
    #    from a granted substrate, not from this script.
    grants = [
        decode_substrate(raw, HYPERCORE_MARKET)
        for raw in vault.get_market_substrates(HYPERCORE_MARKET).call()
    ]
    by_type: dict[str, list[Any]] = {}
    for info in grants:
        by_type.setdefault(info.type_label, []).append(info)
    spot_tokens = {
        int(i.extra["token_index"]): i.address for i in by_type.get("SPOT_TOKEN", [])
    }
    perp_markets = {int(i.extra["asset"]): i for i in by_type.get("PERP_MARKET", [])}
    destinations = {
        Web3.to_checksum_address(i.address) for i in by_type.get("DESTINATION", [])
    }
    send_caps = {
        int(i.extra["token_index"]): int(i.extra["max_wei"])
        for i in by_type.get("SEND_CAP", [])
    }
    config = {i.extra["key"]: int(i.extra["value"]) for i in by_type.get("CONFIG", [])}
    _check(
        0 in spot_tokens and spot_tokens[0].lower() == HYPEREVM_USDC.lower(),
        "USDC (Core token 0) is not a granted spot token",
    )
    _check(XYZ_NVDA_ASSET in perp_markets, "xyz:NVDA is not a granted perp market")
    _check(
        HYPERCORE_VAULT in destinations
        and Web3.to_checksum_address(USDC_SYSTEM_ADDRESS) in destinations,
        "the vault or the USDC system address is not a granted destination",
    )
    _check(0 in send_caps, "USDC has no SendCap: it cannot be sent")
    _check(
        config.get("PerpDexIds", 0) >> XYZ_DEX & 1 == 1,
        "perp dex 1 (xyz) is not enabled in PerpDexIds",
    )
    market = perp_markets[XYZ_NVDA_ASSET]
    max_notional_usd6 = int(market.extra["max_notional_usd6"])
    log.info(
        "grants: spot tokens %s, perp markets %s (xyz:NVDA cap %d USD6, reduce-only %s), send cap USDC %d wei, config %s",
        sorted(spot_tokens),
        sorted(perp_markets),
        max_notional_usd6,
        market.extra["reduce_only_required"],
        send_caps[0],
        config,
    )

    # 2. The fuses the vault registered for market 55, read from the vault.
    fuses = {Web3.to_checksum_address(f) for f in vault.get_fuses().call()}
    deposit_fuse = HyperCoreDepositFuse(
        Web3.to_checksum_address("0xDbE31Da504ac89B618664bB02e1521c335a41E73")
    )
    send_fuse = HyperCoreSendFuse(
        Web3.to_checksum_address("0x984D84183008C52d9A645C3ba4863F0A29b4785A")
    )
    order_fuse = HyperCoreOrderFuse(
        Web3.to_checksum_address("0x8Dc849FC55D9195cc987CA4ddFF5eA022651c173")
    )
    for fuse in (deposit_fuse, send_fuse, order_fuse):
        _check(
            fuse.address in fuses, f"fuse {fuse.address} is not registered on the vault"
        )

    # 3. Live Core inventory and the pending gate: only one Core action may be
    #    pending per vault, and execute() reverts while one is.
    state = read_hypercore_vault_state(ctx, HYPERCORE_VAULT, HYPERCORE_BALANCE_FUSE)
    reader = HyperCoreReader(ctx)
    pending = bool(state.pending and state.pending.pending)
    spot = reader.spot_balance(HYPERCORE_VAULT, 0).call()
    spot_wei = int(spot.total)
    read_index = read_index_of(XYZ_NVDA_ASSET)
    # The precompile quotes a perp price with ``6 - szDecimals`` decimals; the
    # order fuse takes 8 implied decimals. Read the market's szDecimals rather
    # than assuming it, then rescale.
    sz_decimals = int(reader.perp_asset_info(read_index).call().sz_decimals)
    mark_raw = int(reader.mark_px(read_index).call())
    mark = mark_raw * 10 ** (2 + sz_decimals)
    _check(
        10 * CORE_WEI < mark < 100_000 * CORE_WEI,
        f"mark {mark} (8 dp) is not a sane NVDA price",
    )
    position = reader.position(HYPERCORE_VAULT, read_index).call()
    withdrawable = int(reader.withdrawable(HYPERCORE_VAULT).call())
    log.info(
        "live: nav_wad=%s balance_fuse_wad=%s pending=%s spot=%d wei position_szi=%d (szDecimals %d) mark=%d (8 dp) withdrawable=%d",
        state.nav.value_wad,
        state.balance_fuse_value_wad,
        pending,
        spot_wei,
        position.szi,
        sz_decimals,
        mark,
        withdrawable,
    )
    _check(not pending, "a Core action is pending; previews would all revert")

    previews: list[Preview] = []
    # A1. EVM -> Core: the vault holds little USDC at head, so the preview
    #     credits it through a state override (a holder-less way to fund any
    #     address in a read-only call).
    override = _usdc_balance_override(
        web3_hype, HYPERCORE_VAULT, int(BRIDGE_AMOUNT_USDC) + 1_000_000
    )
    previews.append(
        _preview(
            web3_hype,
            vault,
            "bridge_to_core",
            deposit_fuse.enter(token_index=0, amount=BRIDGE_AMOUNT_USDC),
            override=override,
            note="14 USDC EVM -> Core spot through the CoreDepositWallet; vault USDC balance overridden for the preview",
        )
    )
    # A2. spot -> xyz dex: the fuse checks the live Core spot balance through
    #     the precompile, which no override reaches; size to what is there.
    to_dex = max(min(spot_wei - CORE_FEE_RESERVE_WEI, send_caps[0]), 0)
    if to_dex > 0:
        previews.append(
            _preview(
                web3_hype,
                vault,
                "spot_to_xyz",
                send_fuse.send_asset(
                    destination=HYPERCORE_VAULT,
                    source_dex=SPOT_DEX,
                    destination_dex=XYZ_DEX,
                    token_index=0,
                    amount_wei=to_dex,
                ),
                override=None,
                note=f"self-send of {to_dex} Core wei USDC from spot to dex {XYZ_DEX}, sized to the live spot balance minus the fee reserve",
            )
        )
    else:
        previews.append(
            Preview(
                "spot_to_xyz",
                "",
                None,
                None,
                f"skipped: live spot balance {spot_wei} wei leaves nothing above the {CORE_FEE_RESERVE_WEI} reserve",
            )
        )
    # A3/A4. A limit order through the mark (fills like a market order) and
    #        its reduce-only close. The order fuse validates the market grant,
    #        the notional cap and the order window on the EVM side.
    limit_buy = mark * (10_000 + LIMIT_SLIPPAGE_BPS) // 10_000
    limit_sell = mark * (10_000 - LIMIT_SLIPPAGE_BPS) // 10_000
    notional_usd6 = ORDER_SIZE * limit_buy // CORE_WEI // 100  # 8+8 dp -> 6 dp
    _check(
        notional_usd6 <= max_notional_usd6,
        f"order notional {notional_usd6} above the market cap {max_notional_usd6}",
    )
    previews.append(
        _preview(
            web3_hype,
            vault,
            "buy",
            order_fuse.enter(
                asset=XYZ_NVDA_ASSET,
                is_buy=True,
                limit_px=limit_buy,
                sz=ORDER_SIZE,
                tif=TimeInForce.IOC,
                cloid=1,
            ),
            override=None,
            note=f"buy 0.05 xyz:NVDA, limit {limit_buy} (mark +1 %), notional {notional_usd6} USD6 under the cap {max_notional_usd6}",
        )
    )
    previews.append(
        _preview(
            web3_hype,
            vault,
            "sell",
            order_fuse.enter(
                asset=XYZ_NVDA_ASSET,
                is_buy=False,
                limit_px=limit_sell,
                sz=ORDER_SIZE,
                tif=TimeInForce.IOC,
                cloid=2,
                reduce_only=True,
            ),
            override=None,
            note="reduce-only close of the same size, limit mark -1 %",
        )
    )
    # A5. xyz dex -> spot: needs withdrawable margin on the dex.
    if withdrawable > 0:
        previews.append(
            _preview(
                web3_hype,
                vault,
                "xyz_to_spot",
                send_fuse.send_asset(
                    destination=HYPERCORE_VAULT,
                    source_dex=XYZ_DEX,
                    destination_dex=SPOT_DEX,
                    token_index=0,
                    amount_wei=withdrawable,
                ),
                override=None,
                note=f"self-send of the dex's withdrawable {withdrawable} wei back to spot",
            )
        )
    else:
        previews.append(
            Preview(
                "xyz_to_spot",
                "",
                None,
                None,
                "skipped: nothing withdrawable on the xyz dex at head",
            )
        )
    # A6. Core -> EVM: a spot -> spot send to the USDC system address, which
    #     needs Config{SpotSendBridgeEnabled}; keep the fee reserve.
    to_evm = max(min(spot_wei - CORE_FEE_RESERVE_WEI, send_caps[0]), 0)
    if to_evm > 0:
        previews.append(
            _preview(
                web3_hype,
                vault,
                "core_to_evm",
                send_fuse.send_asset(
                    destination=Web3.to_checksum_address(USDC_SYSTEM_ADDRESS),
                    source_dex=SPOT_DEX,
                    destination_dex=SPOT_DEX,
                    token_index=0,
                    amount_wei=to_evm,
                ),
                override=None,
                note=f"{to_evm} Core wei to the USDC system address; Core charges its fee from the reserve left behind",
            )
        )
    else:
        previews.append(
            Preview(
                "core_to_evm",
                "",
                None,
                None,
                "skipped: live spot balance leaves nothing above the fee reserve",
            )
        )

    for p in previews:
        log.info(
            "  %-13s %s  %s",
            p.step,
            f"gas {p.gas}"
            if p.gas is not None
            else (f"revert {p.revert}" if p.revert else "skipped"),
            p.note,
        )
    # The REDEEM the hub will send is sized by the vault's EVM exit ceiling,
    # not by the shares: shares whose value sits on Core cannot leave through
    # the EVM in one step, and the remainder stays attested as residual shares.
    ceiling = read_hypercore_evm_exit_ceiling(ctx, HYPERCORE_VAULT, EXECUTOR)
    log.info("EVM exit ceiling for the dispatcher at head: %s", ceiling)
    return previews


# ── Part B: the hub legs, simulated at pinned blocks ─────────────────────────
def _assert_relay_success(results: dict[ChainId, SimulationResult]) -> None:
    failed = [
        f"{chain}:{call.label}:{call.revert_reason}"
        for chain, result in results.items()
        for call in result.calls
        if not call.success
    ]
    _check(not failed, f"simulation calls failed: {failed}")


def _transport() -> CcipTransport:
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


def _pinned(web3: Web3, chain_id: ChainId, block: int) -> Web3Context:
    ctx = Web3Context(web3, chain_id=chain_id)
    ctx.default_block = block
    return ctx


class _Tail(NamedTuple):
    lane: CrosschainLane
    simulator: CrosschainSimulator
    hub: VaultSimulator
    spoke: VaultSimulator
    hub_vault: PlasmaVault
    hub_usdc: ERC20
    numbers: dict[str, int]

    def advance(self, seconds: int) -> None:
        self.hub.next_block(time_shift_seconds=seconds)
        self.spoke.next_block(time_shift_seconds=seconds)

    def relay(self) -> dict[ChainId, SimulationResult]:
        results = self.simulator.relay()
        _assert_relay_success(results)
        return results


def redeem_command_preview(web3_arb: Web3, web3_hype: Web3) -> dict[str, int]:
    """B1. The REDEEM the hub sends, sized by the HyperCore vault's EVM exit
    ceiling, behind the executor's fee gate; the hub side is simulated at the
    block before the live send. The spoke delivery executes the HyperCore
    vault's redeem (precompile reads) and is not simulated: live it was
    HyperEVM tx 0xf11a551a…bab08b, acknowledged on the hub in 0xb2a578a4…21dc."""
    hub_ctx = _pinned(web3_arb, ARBITRUM_CHAIN_ID, ARBITRUM_BLOCK_BEFORE_REDEEM)
    spoke_ctx = _pinned(web3_hype, HYPEREVM_CHAIN_ID, HYPEREVM_BLOCK_BEFORE_REDEEM)
    lane = open_lane(hub_ctx, spoke_ctx, executor=EXECUTOR, market_id=CROSSCHAIN_MARKET)
    hub_vault = PlasmaVault(hub_ctx, HUB_VAULT)
    remote = PlasmaVault(spoke_ctx, HYPERCORE_VAULT)

    # Size by what can actually leave the HyperCore vault through the EVM in
    # one step: its idle underlying, not the dispatcher's share value. The
    # remainder stays as shares and is attested as residual later.
    ceiling = read_hypercore_evm_exit_ceiling(spoke_ctx, HYPERCORE_VAULT, EXECUTOR)
    shares = Shares(
        int(remote.convert_to_shares(Amount(ceiling.upper_bound_assets)).call())
    )
    _check(
        0 < shares <= ceiling.shares, "exit ceiling sizing left no redeemable shares"
    )
    expected = int(remote.convert_to_assets(shares).call())
    min_assets = Amount(expected * 99 // 100)
    command = Command.redeem(HYPERCORE_VAULT, shares, min_assets)
    send = hub_vault.execute([lane.send_command(command)])

    # The fee gate the planner applies before any CCIP step: the executor pays
    # the CCIP fee from its own native balance, the quote moves between
    # samples, so the balance must cover the route's maxFee (the contract cap),
    # not merely today's quote.
    route = (
        CcipCrosschainExecutor(hub_ctx, EXECUTOR).ccip_route(HYPEREVM_CHAIN_ID).call()
    )
    quote = quote_ccip_native_fee(
        web3_arb, send, payer=EXECUTOR, from_=ALPHA, block=ARBITRUM_BLOCK_BEFORE_REDEEM
    )
    balance = web3_arb.eth.get_balance(
        EXECUTOR, block_identifier=ARBITRUM_BLOCK_BEFORE_REDEEM
    )
    log.info(
        "REDEEM: shares=%d (of %d) expected=%d min_assets=%d | fee quote=%d wei, route maxFee=%d, executor balance=%d",
        shares,
        ceiling.shares,
        expected,
        min_assets,
        quote,
        route.max_fee,
        balance,
    )
    _check(
        quote <= route.max_fee,
        "the quote is above the route cap: the executor would refuse",
    )
    _check(
        balance >= route.max_fee,
        "executor float below the route maxFee: do not send (cycle 2's first attempt did, and reverted)",
    )

    # The hub side alone, in one simulated block at the pin: the spoke
    # delivery is the HyperCore vault's own redeem and is not reproducible
    # here (see the docstring).
    hub = VaultSimulator(
        web3_arb, vault=HUB_VAULT, alpha=ALPHA, block=ARBITRUM_BLOCK_BEFORE_REDEEM
    )
    hub.add_call(send, from_=ALPHA, label="redeem_command")
    result = hub.run()
    _assert_relay_success({ARBITRUM_CHAIN_ID: result})
    sent = next(c for c in result.calls if c.label == "redeem_command")
    events = {e.name: dict(e.values) for e in ccip_events(sent.logs)}
    _check(
        "CommandSent" in events and "CcipMessageSent" in events,
        f"expected CommandSent + CcipMessageSent, got {sorted(events)}",
    )
    fee_paid = int(events["CcipMessageSent"]["fee"])
    _check(
        fee_paid == quote, f"the simulated send paid {fee_paid}, the quote said {quote}"
    )
    log.info(
        "REDEEM simulated on the hub: CommandSent seq %s, CcipMessageSent fee %d wei (the live send of 09:53:33 UTC paid 1,732,275,459,170,300)",
        events["CommandSent"].get("sequence"),
        fee_paid,
    )
    return {
        "redeem_shares": int(shares),
        "redeem_expected_assets": expected,
        "fee_quote": quote,
        "fee_paid_simulated": fee_paid,
        "route_max_fee": route.max_fee,
        "executor_balance": balance,
    }


def _attest(flow: _Tail, tag: str, observation_label: str, *, hub_idle: int = 0) -> int:
    lane, hub = flow.lane, flow.hub
    observation = flow.simulator.results[HYPEREVM_CHAIN_ID].get(observation_label)
    proposal = lane.attestation(
        observation,
        remote_block=flow.spoke.current_block_number,
        remote_timestamp=flow.spoke.current_time,
        expiry=flow.hub.current_time + PROPOSAL_TTL,
    )
    # SIMULATION ONLY: two keyless addresses driven from one process; live
    # they are two signers, which is the point of the two-key design.
    hub.add_call(
        lane.propose_balance(proposal), from_=BALANCE_PROPOSER, label=f"propose_{tag}"
    )
    results = flow.relay()
    proposed = next(
        c for c in results[ARBITRUM_CHAIN_ID].calls if c.label == f"propose_{tag}"
    )
    proposal_id = lane.proposal_id_from_logs(proposed.logs)
    flow.advance(APPROVAL_DELAY)
    hub.add_call(
        lane.approve_balance(proposal_id),
        from_=BALANCE_APPROVER,
        label=f"approve_{tag}",
    )
    # The market-54 refresh after every approval: the vault's cached NAV
    # follows the attested figure only when someone refreshes it.
    hub.add_call(
        flow.hub_vault.update_markets_balances([CROSSCHAIN_MARKET]),
        from_=ALPHA,
        label=f"refresh_54_{tag}",
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
    executor_balance = int(results[ARBITRUM_CHAIN_ID].get(f"executor_balance_{tag}"))
    market_total = int(results[ARBITRUM_CHAIN_ID].get(f"market_total_{tag}"))
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


def recall_attest_claim(web3_arb: Web3, web3_hype: Web3) -> dict[str, int]:
    """B2. From the block before the live recall: recall the dispatcher's idle,
    relay the token return to the hub, attest the residual (the 238,862
    shares still in the HyperCore vault), claim. None of these touch the
    HyperCore vault, so the relay reproduces them exactly."""
    hub_ctx = _pinned(web3_arb, ARBITRUM_CHAIN_ID, ARBITRUM_BLOCK_BEFORE_RECALL)
    spoke_ctx = _pinned(web3_hype, HYPEREVM_CHAIN_ID, HYPEREVM_BLOCK_BEFORE_RECALL)
    lane = open_lane(hub_ctx, spoke_ctx, executor=EXECUTOR, market_id=CROSSCHAIN_MARKET)
    simulator = CrosschainSimulator(_transport())
    hub = simulator.add_chain(
        ARBITRUM_CHAIN_ID,
        web3_arb,
        block=ARBITRUM_BLOCK_BEFORE_RECALL,
        vault=HUB_VAULT,
        alpha=ALPHA,
    )
    spoke = simulator.add_chain(
        HYPEREVM_CHAIN_ID, web3_hype, block=HYPEREVM_BLOCK_BEFORE_RECALL
    )
    hub.with_block_time_shift(
        60
    )  # SIMULATION ONLY: the hub clock a minute ahead of the spoke's
    spoke.with_block_override(
        gasLimit=30_000_000
    )  # SIMULATION ONLY: several deliveries in one simulated block
    simulator.fund_native(
        ARBITRUM_CHAIN_ID, EXECUTOR, 10**18
    )  # SIMULATION ONLY: fee floats
    simulator.fund_native(HYPEREVM_CHAIN_ID, EXECUTOR, 10**18)
    flow = _Tail(
        lane,
        simulator,
        hub,
        spoke,
        PlasmaVault(hub_ctx, HUB_VAULT),
        ERC20(hub_ctx, ARBITRUM_USDC),
        {},
    )

    # Where the live cycle stood: the settled bucket carries attestation 8, the
    # dispatcher holds the redeemed idle and the residual shares.
    settled_before = int(lane.settled_remote_balance().call())
    observation_before = lane.observation().call()
    remote_idle = Amount(int(observation_before.tracked_idle))
    residual_shares = int(
        PlasmaVault(spoke_ctx, HYPERCORE_VAULT).balance_of(EXECUTOR).call()
    )
    vault_usdc_before = int(flow.hub_usdc.balance_of(HUB_VAULT).call())
    log.info(
        "before the recall: settled=%d dispatcher idle=%d accounted=%d residual shares=%d hub vault USDC=%d",
        settled_before,
        remote_idle,
        observation_before.accounted_balance,
        residual_shares,
        vault_usdc_before,
    )
    _check(remote_idle > 0, "nothing idle on the dispatcher at this pin")

    hub.execute([lane.recall(amount=remote_idle, min_return=remote_idle)])
    flow.advance(BRIDGE_LATENCY)
    flow.relay()
    simulator.observe(ARBITRUM_CHAIN_ID, "idle_after_return", lane.idle_ledger())
    simulator.observe(
        ARBITRUM_CHAIN_ID, "settled_after_return", lane.settled_remote_balance()
    )
    simulator.observe(
        ARBITRUM_CHAIN_ID, "pending_after_return", lane.pending_transfer_count()
    )
    simulator.observe(HYPEREVM_CHAIN_ID, "observation_after_return", lane.observation())
    results = flow.relay()
    idle = int(results[ARBITRUM_CHAIN_ID].get("idle_after_return"))
    settled = int(results[ARBITRUM_CHAIN_ID].get("settled_after_return"))
    _check(
        idle == int(remote_idle),
        f"the hub credited {idle}, the spoke sent {remote_idle} (no token fee on this lane)",
    )
    _check(
        results[ARBITRUM_CHAIN_ID].get("pending_after_return") == 0,
        "a transfer is still pending",
    )
    # The settled bucket keeps exactly the value of the residual shares; the
    # recall debited the sent amount from it.
    _check(
        settled == settled_before - int(remote_idle),
        f"settled {settled} != {settled_before} - {remote_idle}",
    )
    log.info("recall: idle=%d settled=%d (the residual's value)", idle, settled)
    flow.numbers.update({"recalled": idle, "settled_after_return": settled})

    flow.advance(int(lane.executor.min_update_interval().call()))
    residual = _attest(flow, "residual", "observation_after_return", hub_idle=idle)
    _check(
        residual == settled, f"the residual attestation {residual} != settled {settled}"
    )

    hub.execute([lane.claim(Amount(idle))])
    hub.add_call(
        flow.hub_vault.update_markets_balances([CROSSCHAIN_MARKET]),
        from_=ALPHA,
        label="refresh_54_final",
    )
    simulator.observe(
        ARBITRUM_CHAIN_ID, "vault_usdc_after", flow.hub_usdc.balance_of(HUB_VAULT)
    )
    simulator.observe(ARBITRUM_CHAIN_ID, "idle_after_claim", lane.idle_ledger())
    simulator.observe(ARBITRUM_CHAIN_ID, "nav_final", lane.get_balance())
    simulator.observe(
        ARBITRUM_CHAIN_ID,
        "market_final",
        flow.hub_vault.total_assets_in_market(CROSSCHAIN_MARKET),
    )
    results = flow.relay()
    vault_after = int(results[ARBITRUM_CHAIN_ID].get("vault_usdc_after"))
    _check(
        vault_after == vault_usdc_before + idle,
        f"hub vault USDC {vault_after} != {vault_usdc_before} + {idle}",
    )
    _check(
        results[ARBITRUM_CHAIN_ID].get("idle_after_claim") == 0,
        "the idle ledger is not empty after the claim",
    )
    _check(
        int(results[ARBITRUM_CHAIN_ID].get("nav_final")) == residual,
        "executor NAV != the residual",
    )
    _check(
        int(results[ARBITRUM_CHAIN_ID].get("market_final")) == residual,
        "market 54 total != the residual",
    )
    log.info(
        "claim: hub vault USDC %d -> %d; market 54 = %d (residual shares %d)",
        vault_usdc_before,
        vault_after,
        residual,
        residual_shares,
    )
    flow.numbers.update(
        {
            "vault_usdc_before": vault_usdc_before,
            "vault_usdc_after": vault_after,
            "residual": residual,
            "residual_shares": residual_shares,
        }
    )
    return flow.numbers


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    web3_hype = _connected_web3("HYPEREVM_PROVIDER_URL", HYPEREVM_CHAIN_ID)
    web3_arb = _connected_web3("ARBITRUM_PROVIDER_URL", ARBITRUM_CHAIN_ID)
    log.info("A. the Core leg, previewed at HyperEVM head (nothing is sent):")
    core_leg_previews(web3_hype)
    log.info(
        "B1. the REDEEM command, hub side simulated at the block before the live send:"
    )
    numbers = redeem_command_preview(web3_arb, web3_hype)
    log.info(
        "B2. recall, return, residual attestation and claim, replayed from the block before the live recall:"
    )
    numbers.update(recall_attest_claim(web3_arb, web3_hype))
    log.info("done: %s", numbers)


if __name__ == "__main__":
    main()
