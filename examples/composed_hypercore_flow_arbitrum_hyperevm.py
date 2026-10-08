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
     0x800-0x813), but ``eth_estimateGas`` executes them, and this RPC accepts
     state overrides. So each Core action -- bridge EVM -> Core, spot -> perp
     dex, a limit order, its reduce-only close, dex -> spot, Core -> EVM -- is
     built from the vault's own substrate grants, estimated from the alpha and
     reported with its gas or its decoded revert. The six previews are
     independent reads against the live inventory: the bridge preview funds
     nothing for the next one, the buy preview opens no position for the
     close, and a gas estimate proves only that the EVM side accepts the
     action -- not that Core accepts it, fills it or changes a balance. Sends
     the fuse checks against a Core balance are sized to the vault's live Core
     inventory, because Core-side state cannot be overridden from the EVM;
  B. the hub legs that never touch the HyperCore vault are SIMULATED in
     ``CrosschainSimulator`` at blocks pinned inside the live cycle. B1 sizes
     a REDEEM command with the live planner's policy and simulates its hub
     side behind the executor's fee gate; B2 is a separate replay that starts
     after the live redeem had settled: recall, token return, residual
     attestation and claim, exactly as cycle 2 ran them.

The two spoke deliveries that execute the HyperCore vault's own deposit and
redeem are the one thing neither tool reproduces. Live they were, on HyperEVM,
DEPOSIT 0x3ff1586ed161310d1f23017bce6bb0b2b60e69c85d9958e57e764f161a3422e7
(block 47,896,918; acknowledged on Arbitrum in
0x8dce572918c2e1877e1b318c3fef877bcaecdc25a25ced98b27dc20b35be0f13, block
512,534,204) and REDEEM
0xf11a551a6a6e2ce1352ef6492307e65adc54d1046b81af3f8d1ec35198bab08b (block
47,984,367; acknowledged in
0xb2a578a440093a94ec28fa92d750e60ed6f849f845ce4e81af1af71a334a21dc, block
512,850,108).

Run it (POSIX shell):

    export ARBITRUM_PROVIDER_URL="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
    export HYPEREVM_PROVIDER_URL="https://YOUR_HYPEREVM_ARCHIVE_NODE"
    uv run python examples/composed_hypercore_flow_arbitrum_hyperevm.py

The Arbitrum provider must be an archive node with ``eth_simulateV1`` serving the
pinned blocks; the HyperEVM provider must serve ``eth_estimateGas`` with state
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
    register_custom_errors,
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

# The send fuse's balance checks, so a preview that trips them reads by name
# (contracts/fuses/hypercore/HyperCoreSendFuse.sol; selectors 0x9b94bcad and
# 0x99a5f02c). The spot check compares the requested Core wei with the spot
# balance's ``total - hold``; the HIP-3 check compares it with the dex account
# value (equity) scaled to USD6 -- equity, not withdrawable collateral.
HYPERCORE_SEND_FUSE_ERRORS = (
    "HyperCoreSendFuseInsufficientBalance(uint256,uint256)",
    "HyperCoreSendFuseInsufficientDexEquity(uint32,uint256,uint256)",
)
register_custom_errors(HYPERCORE_SEND_FUSE_ERRORS)

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


# ── Pure sizing rules (no chain access, unit-tested offline) ─────────────────
def _available_spot_wei(total: int, hold: int) -> int:
    """What the send fuse lets leave spot: ``total - hold`` (held funds back
    open orders and do not count)."""
    return max(int(total) - int(hold), 0)


def _size_send(
    available_wei: int, reserve_wei: int, cap_wei: int, *, step_wei: int = 1
) -> int:
    """Core wei a send may carry: the available balance minus the reserve,
    capped by the token's SendCap, rounded down to ``step_wei`` (the Core ->
    EVM route needs an amount representable in the EVM token's decimals:
    10 ** (Core wei decimals - EVM decimals), 100 for USDC). Zero when the cap
    is zero or nothing is above the reserve."""
    amount = min(max(int(available_wei) - int(reserve_wei), 0), max(int(cap_wei), 0))
    return amount - amount % max(int(step_wei), 1)


def _dex_equity_wei(account_value_usd6: int, cap_wei: int) -> int:
    """The HIP-3 leg of ``send_asset`` is gated on the dex account value in
    USD6 (100 Core wei per 1e-6 USD); the SendCap still applies. This is
    equity, not withdrawable collateral: a transfer Core refuses for margin
    reasons still passes this EVM check."""
    return min(max(int(account_value_usd6), 0) * 100, max(int(cap_wei), 0))


def _redeem_size(shares: int, share_assets: int, idle: int, max_withdraw: int) -> int:
    """The live planner's REDEEM sizing (composed_hypercore_trade_plan):
    everything the holder has when its value fits the vault's idle and
    ``maxWithdraw``; otherwise ``min(idle, maxWithdraw) - 1_000`` underlying
    units, converted to shares by the caller. The 99 % ``min_assets`` floor
    protects the proceeds; it is not liquidity headroom, this buffer is."""
    if min(shares, share_assets, idle, max_withdraw) <= 0:
        return 0
    if share_assets <= min(idle, max_withdraw):
        return shares
    return max(0, min(idle, max_withdraw) - 1_000)


def _fee_gate(quote_wei: int, max_fee_wei: int, balance_wei: int) -> tuple[bool, str]:
    """The planner's gate before any CCIP send: the executor's native balance
    must cover the route's maxFee (the contract cap), not merely today's quote,
    and the quote must be within the cap. Cycle 2's first REDEEM was gated on
    the quote alone and reverted when the fee moved before the broadcast."""
    if quote_wei > max_fee_wei:
        return (
            False,
            f"quote {quote_wei} above the route maxFee {max_fee_wei}: the executor would refuse",
        )
    if balance_wei < max_fee_wei:
        return False, (
            f"executor balance {balance_wei} below the route maxFee {max_fee_wei}: "
            f"do not send, even though the quote {quote_wei} fits"
        )
    return True, "balance covers the cap and the quote is within it"


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
    """``eth_estimateGas`` of ``execute([action])`` from the alpha at head: the
    node runs the fuse, the precompile reads included, and answers gas or the
    revert. A revert is decoded by name through the SDK registry
    (``decode_custom_error``) and reported, never raised: at head the vault's
    Core inventory is whatever the last live cycle left, and a preview that
    reverts on a balance is information, not a failure of the example. A gas
    figure proves EVM acceptance only; Core executes the queued action later."""
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
    substrate grants and estimate each one from the alpha at head. Six
    independent previews against the live inventory; none changes state."""
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
    send_cap = send_caps[0]
    log.info(
        "grants: spot tokens %s, perp markets %s (xyz:NVDA cap %d USD6, reduce-only %s), send cap USDC %d wei, config %s",
        sorted(spot_tokens),
        sorted(perp_markets),
        max_notional_usd6,
        market.extra["reduce_only_required"],
        send_cap,
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

    # 3. Live Core inventory, read the way the fuses read it: spot total and
    #    hold (the spot check uses total - hold), the dex-1 account value (the
    #    HIP-3 check uses equity in USD6), the token's Core wei decimals (the
    #    Core -> EVM amount must be representable on the EVM), the mark, and
    #    the pending gate: only one Core action may be pending per vault.
    state = read_hypercore_vault_state(ctx, HYPERCORE_VAULT, HYPERCORE_BALANCE_FUSE)
    reader = HyperCoreReader(ctx)
    pending = bool(state.pending and state.pending.pending)
    spot = reader.spot_balance(HYPERCORE_VAULT, 0).call()
    spot_available = _available_spot_wei(spot.total, spot.hold)
    usdc_info = reader.token_info(0).call()
    evm_step = 10 ** max(int(usdc_info.wei_decimals) - 6, 0)
    xyz_equity_usd6 = int(
        reader.account_margin_summary(XYZ_DEX, HYPERCORE_VAULT).call().account_value
    )
    read_index = read_index_of(XYZ_NVDA_ASSET)
    # The precompile quotes a perp price with ``6 - szDecimals`` decimals; the
    # order fuse takes 8 implied decimals. Read szDecimals, then rescale.
    sz_decimals = int(reader.perp_asset_info(read_index).call().sz_decimals)
    mark = int(reader.mark_px(read_index).call()) * 10 ** (2 + sz_decimals)
    _check(
        10 * CORE_WEI < mark < 100_000 * CORE_WEI,
        f"mark {mark} (8 dp) is not a sane NVDA price",
    )
    position = reader.position(HYPERCORE_VAULT, read_index).call()
    log.info(
        "live: nav_wad=%s balance_fuse_wad=%s pending=%s spot total=%d hold=%d (available %d wei) xyz account value=%d USD6 position_szi=%d (szDecimals %d) mark=%d (8 dp)",
        state.nav.value_wad,
        state.balance_fuse_value_wad,
        pending,
        spot.total,
        spot.hold,
        spot_available,
        xyz_equity_usd6,
        position.szi,
        sz_decimals,
        mark,
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
    # A2. spot -> xyz dex: the fuse checks the live spot balance (total minus
    #     hold) through the precompile, which no override reaches; size to it.
    to_dex = _size_send(spot_available, CORE_FEE_RESERVE_WEI, send_cap)
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
                note=f"self-send of {to_dex} Core wei USDC from spot to dex {XYZ_DEX}: available {spot_available} minus the {CORE_FEE_RESERVE_WEI} reserve, within the SendCap {send_cap}",
            )
        )
    else:
        previews.append(
            Preview(
                "spot_to_xyz",
                "",
                None,
                None,
                f"skipped: available spot {spot_available} wei (total {spot.total} - hold {spot.hold}) minus the {CORE_FEE_RESERVE_WEI} reserve, within the SendCap {send_cap}, leaves nothing to send",
            )
        )
    # A3/A4. A limit order through the mark (fills like a market order) and
    #        its reduce-only close. The order fuse validates the market grant,
    #        the notional cap and the order window on the EVM side; the close
    #        preview does not depend on the buy preview having run.
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
            note="reduce-only close of the same size, limit mark -1 % (independent of the buy preview)",
        )
    )
    # A5. xyz dex -> spot: the HIP-3 leg is gated on the dex account value
    #     (equity, accountMarginSummary for dex 1), not on anything
    #     withdrawable; what Core will actually release is a Core-side
    #     question the EVM preview cannot answer. The live planner sized this
    #     leg to the dex's withdrawable from the Core API instead.
    from_dex = _dex_equity_wei(xyz_equity_usd6, send_cap)
    if from_dex > 0:
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
                    amount_wei=from_dex,
                ),
                override=None,
                note=f"self-send of {from_dex} Core wei from dex {XYZ_DEX} to spot, sized to the dex account value {xyz_equity_usd6} USD6 (equity, not withdrawable collateral) within the SendCap",
            )
        )
    else:
        previews.append(
            Preview(
                "xyz_to_spot",
                "",
                None,
                None,
                f"skipped: dex {XYZ_DEX} account value {xyz_equity_usd6} USD6 at head (the EVM equity gate) within the SendCap {send_cap} wei leaves nothing to send",
            )
        )
    # A6. Core -> EVM: a spot -> spot send to the USDC system address. The
    #     amount must be representable on the EVM (a multiple of 100 Core wei
    #     for USDC) and Core charges its fee from what stays behind.
    #     ``Config{SpotSendBridgeEnabled}`` gates ``spotSend`` (action 6), not
    #     this ``sendAsset`` route.
    to_evm = _size_send(
        spot_available, CORE_FEE_RESERVE_WEI, send_cap, step_wei=evm_step
    )
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
                note=f"{to_evm} Core wei to the USDC system address (available {spot_available} minus the reserve, rounded down to {evm_step}-wei EVM precision, within the SendCap)",
            )
        )
    else:
        previews.append(
            Preview(
                "core_to_evm",
                "",
                None,
                None,
                f"skipped: available spot {spot_available} wei minus the {CORE_FEE_RESERVE_WEI} reserve, within the SendCap {send_cap} and rounded to {evm_step}-wei EVM precision, leaves nothing to send",
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
    """B1. A REDEEM command sized by the live planner's rule, its hub side
    simulated at the block before the live send, behind the executor's fee
    gate. The spoke delivery executes the HyperCore vault's own redeem
    (precompile reads) and is not simulated; this is a sizing and gating
    demonstration at the pin, not the exact live send (the live planner read
    the same rule one block later and sent 1,606,105,561 shares)."""
    hub_ctx = _pinned(web3_arb, ARBITRUM_CHAIN_ID, ARBITRUM_BLOCK_BEFORE_REDEEM)
    spoke_ctx = _pinned(web3_hype, HYPEREVM_CHAIN_ID, HYPEREVM_BLOCK_BEFORE_REDEEM)
    lane = open_lane(hub_ctx, spoke_ctx, executor=EXECUTOR, market_id=CROSSCHAIN_MARKET)
    hub_vault = PlasmaVault(hub_ctx, HUB_VAULT)
    remote = PlasmaVault(spoke_ctx, HYPERCORE_VAULT)

    # Everything the dispatcher holds when its value fits what can leave the
    # vault through the EVM (idle underlying and maxWithdraw), otherwise
    # min(idle, maxWithdraw) less a 1,000-unit buffer, converted to shares.
    # Whatever stays is attested as residual shares later.
    ceiling = read_hypercore_evm_exit_ceiling(spoke_ctx, HYPERCORE_VAULT, EXECUTOR)
    max_withdraw = int(remote.max_withdraw(EXECUTOR).call())
    sized = _redeem_size(
        ceiling.shares, ceiling.share_assets, ceiling.idle_underlying, max_withdraw
    )
    _check(sized > 0, "the sizing rule found nothing redeemable at this pin")
    if sized == ceiling.shares:
        shares = Shares(int(sized))
        branch = "all shares: their value fits idle and maxWithdraw"
    else:
        shares = Shares(int(remote.convert_to_shares(Amount(sized)).call()))
        branch = f"partial: min(idle {ceiling.idle_underlying}, maxWithdraw {max_withdraw}) - 1,000 = {sized} units"
    _check(0 < shares <= ceiling.shares, "sizing left no redeemable shares")
    expected = int(remote.convert_to_assets(shares).call())
    _check(
        expected <= min(ceiling.idle_underlying, max_withdraw),
        f"expected {expected} does not fit idle {ceiling.idle_underlying} / maxWithdraw {max_withdraw}",
    )
    min_assets = Amount(expected * 99 // 100)
    command = Command.redeem(HYPERCORE_VAULT, shares, min_assets)
    send = hub_vault.execute([lane.send_command(command)])

    # The fee gate: the executor pays the CCIP fee from its own native
    # balance and the quote moves between samples, so the balance must cover
    # the route's maxFee (the contract cap), not merely today's quote.
    route = (
        CcipCrosschainExecutor(hub_ctx, EXECUTOR).ccip_route(HYPEREVM_CHAIN_ID).call()
    )
    quote = quote_ccip_native_fee(
        web3_arb, send, payer=EXECUTOR, from_=ALPHA, block=ARBITRUM_BLOCK_BEFORE_REDEEM
    )
    balance = web3_arb.eth.get_balance(
        EXECUTOR, block_identifier=ARBITRUM_BLOCK_BEFORE_REDEEM
    )
    ready, why = _fee_gate(quote, route.max_fee, balance)
    log.info(
        "REDEEM: shares=%d of %d (%s) expected=%d min_assets=%d | fee quote=%d wei, route maxFee=%d, executor balance=%d -> %s",
        shares,
        ceiling.shares,
        branch,
        expected,
        min_assets,
        quote,
        route.max_fee,
        balance,
        why,
    )
    _check(ready, why)

    # The hub side alone, in one simulated block at the pin.
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
        "REDEEM simulated on the hub: CommandSent seq %s, CcipMessageSent fee %d wei (the live send of 09:53:33 UTC paid 1,732,275,459,170,300; a matching fee says nothing about the spoke redemption, which this preview does not run)",
        events["CommandSent"].get("sequence"),
        fee_paid,
    )
    return {
        "redeem_shares": int(shares),
        "redeem_all_shares": int(ceiling.shares),
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
        "B1. a REDEEM command sized by the live rule, hub side simulated at the block before the live send:"
    )
    numbers = redeem_command_preview(web3_arb, web3_hype)
    log.info(
        "B2. separate replay from the block before the live recall (after the live redeem settled): recall, return, residual attestation, claim:"
    )
    numbers.update(recall_attest_claim(web3_arb, web3_hype))
    log.info("done: %s", numbers)


if __name__ == "__main__":
    main()
