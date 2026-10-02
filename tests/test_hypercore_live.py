"""HyperCore read side and dry runs against the first HyperEVM test vault.

Opt-in: needs ``HYPEREVM_PROVIDER_URL`` (loaded from ``.env`` by
``conftest.py``); skipped otherwise. Every read is pinned to one block. No
``eth_simulateV1``: the HyperCore precompiles fail inside it, so an action is
dry-run as an independent ``eth_call`` plus ``eth_estimateGas`` from the run's
signer at the latest state. Such a call carries no earlier EVM state and
cannot settle a Core action, so a revert caused by the vault's *current*
pending state is a state fact, not an SDK regression (the test skips then),
and the full cycle stays a live-run gap.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import (
    ERC20,
    HyperCoreCancelFuse,
    HyperCoreDepositFuse,
    HyperCoreOrderFuse,
    HyperCorePendingReader,
    HyperCoreReader,
    HyperCoreSendFuse,
    PlasmaVault,
    TimeInForce,
    read_hypercore_nav,
    read_hypercore_vault_state,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.fuses.hypercore import SPOT_DEX, USDC_SYSTEM_ADDRESS, read_index_of
from ipor_fusion.types import MarketId

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_hip3_flow.json").read_text()
)
CREATED = {
    row["contract"]: Web3.to_checksum_address(row["created"])
    for row in FIXTURE["txs"]
    if row["kind"] == "create"
}
VAULT = Web3.to_checksum_address(FIXTURE["vault"])
USDC = Web3.to_checksum_address(FIXTURE["usdc"])
SIGNER = Web3.to_checksum_address(FIXTURE["signer"])
#: The vault keys HyperCore as 54 until its migration to 55.
LIVE_MARKET = MarketId(FIXTURE["market_id"])
XYZ_DEX = 1
XYZ_NVDA = 110_002

deposit_fuse = HyperCoreDepositFuse(CREATED["HyperCoreDepositFuse"])
send_fuse = HyperCoreSendFuse(CREATED["HyperCoreSendFuse"])
order_fuse = HyperCoreOrderFuse(CREATED["HyperCoreOrderFuse"])
cancel_fuse = HyperCoreCancelFuse(CREATED["HyperCoreCancelFuse"])


@pytest.fixture(scope="module")
def ctx() -> Web3Context:
    url = os.environ.get("HYPEREVM_PROVIDER_URL")
    if not url:
        pytest.skip("HYPEREVM_PROVIDER_URL not set")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
    if not w3.is_connected() or w3.eth.chain_id != 999:
        pytest.skip("HYPEREVM_PROVIDER_URL is not a reachable HyperEVM node")
    context = Web3Context(w3, 999, signer=SIGNER)
    context.default_block = w3.eth.block_number
    return context


@pytest.fixture(scope="module")
def vault(ctx: Web3Context) -> PlasmaVault:
    return PlasmaVault(ctx, VAULT)


@pytest.fixture(scope="module")
def pending(ctx: Web3Context, vault: PlasmaVault) -> HyperCorePendingReader:
    return HyperCorePendingReader(vault, CREATED["HyperCorePendingReader"])


def test_nav_identity_matches_the_balance_fuse(ctx: Web3Context, vault: PlasmaVault):
    nav = read_hypercore_nav(ctx, VAULT, LIVE_MARKET)
    on_chain = vault.balance_fuse_value(CREATED["HyperCoreBalanceFuse"]).call()
    assert nav.value_wad == on_chain
    assert nav.perp_dex_bitmap == 1 << XYZ_DEX
    assert [leg.token_index for leg in nav.spot] == [0]
    assert [leg.dex for leg in nav.hip3] == [XYZ_DEX]


def test_vault_state_reads_everything_at_one_block(ctx: Web3Context):
    state = read_hypercore_vault_state(
        ctx,
        VAULT,
        CREATED["HyperCoreBalanceFuse"],
        LIVE_MARKET,
        pending_reader=CREATED["HyperCorePendingReader"],
    )
    assert state.nav_matches_balance_fuse is True
    assert state.pending is not None and state.pending.l1_block_available
    (nvda,) = state.perp_markets
    assert (nvda.asset, nvda.dex, nvda.read_index) == (XYZ_NVDA, XYZ_DEX, 10_002)
    assert nvda.coin is not None and nvda.coin.startswith("xyz:")


def test_pending_state_reads_through_the_vault(pending: HyperCorePendingReader):
    state = pending.pending_state().call()
    assert state.l1_block_available and state.current_l1_block > 0
    assert state.current_timestamp > 0
    assert state.pending == pending.is_pending().call()
    assert state.pending or state.settled


def test_precompiles_batch_through_multicall3(ctx: Web3Context):
    reader = HyperCoreReader(ctx)
    nvda = read_index_of(XYZ_NVDA)
    reads = cast(
        list[Call[Any]],
        [
            reader.l1_block_number(),
            reader.core_user_exists(VAULT),
            reader.spot_balance(VAULT, 0),
            reader.account_margin_summary(XYZ_DEX, VAULT),
            reader.perp_asset_info(nvda),
            reader.position(VAULT, nvda),
            reader.oracle_px(nvda),
        ],
    )
    l1_block, exists, usdc, xyz, nvda_info, position, oracle_px = Multicall3(
        ctx
    ).aggregate(reads)
    assert l1_block > 0 and exists is True
    assert usdc.total >= usdc.hold
    assert isinstance(xyz.account_value, int)
    assert nvda_info.coin.startswith("xyz:") and nvda_info.max_leverage > 0
    assert position.leverage > 0 and oracle_px > 0


def _dry_run(ctx: Web3Context, call: Call) -> int:
    """``eth_call`` then ``eth_estimateGas`` from the signer at the pinned
    block; the estimate is the figure a real send would start from."""
    tx = {"from": SIGNER, "to": call.to, "data": call.calldata}
    ctx.web3.eth.call(tx, block_identifier=ctx.default_block)
    return ctx.web3.eth.estimate_gas(tx, block_identifier=ctx.default_block)


def _skip_if_pending(pending: HyperCorePendingReader) -> None:
    if pending.is_pending().call():
        pytest.skip("the vault has a pending HyperCore action (state, not the SDK)")


ActionBuilder = Callable[[PlasmaVault, Web3Context], Call]


def _deposit_evm_to_core(vault: PlasmaVault, ctx: Web3Context) -> Call:
    idle = ERC20(ctx, USDC).balance_of(VAULT).call()
    if idle == 0:
        pytest.skip("the vault holds no EVM USDC to bridge")
    return vault.execute([deposit_fuse.enter(token_index=0, amount=min(idle, 1_000))])


def _send_core_to_evm(vault: PlasmaVault, _ctx: Web3Context) -> Call:
    return vault.execute(
        [
            send_fuse.send_asset(
                destination=USDC_SYSTEM_ADDRESS,
                source_dex=SPOT_DEX,
                destination_dex=SPOT_DEX,
                token_index=0,
                amount_wei=100_000,
            )
        ]
    )


def _send_spot_to_xyz(vault: PlasmaVault, _ctx: Web3Context) -> Call:
    return vault.execute(
        [
            send_fuse.send_asset(
                destination=VAULT,
                source_dex=SPOT_DEX,
                destination_dex=XYZ_DEX,
                token_index=0,
                amount_wei=100_000,
            )
        ]
    )


def _order_ioc_buy(vault: PlasmaVault, _ctx: Web3Context) -> Call:
    return vault.execute(
        [
            order_fuse.enter(
                asset=XYZ_NVDA,
                is_buy=True,
                limit_px=229_92_000_000,
                sz=5_000_000,
                tif=TimeInForce.IOC,
                cloid=0x7101,
            )
        ]
    )


def _cancel_by_cloid(vault: PlasmaVault, _ctx: Web3Context) -> Call:
    return vault.execute([cancel_fuse.cancel_by_cloid(asset=XYZ_NVDA, cloid=0x5102)])


def _refresh(vault: PlasmaVault, _ctx: Web3Context) -> Call:
    return vault.update_markets_balances([LIVE_MARKET])


# The actions of the run's long cycle that are valid on the current state
# without a prior step of their own (an order needs no fill to be accepted;
# Core decides asynchronously). Measured 2026-10-02 at block 47453068:
# 272,662 / 464,875 / 442,726 / 423,054 / 440,126 / 414,648 gas.
CYCLE_ACTIONS: dict[str, ActionBuilder] = {
    "refresh": _refresh,
    "deposit_evm_to_core": _deposit_evm_to_core,
    "send_core_to_evm": _send_core_to_evm,
    "send_spot_to_xyz": _send_spot_to_xyz,
    "order_ioc_buy": _order_ioc_buy,
    "cancel_by_cloid": _cancel_by_cloid,
}


@pytest.mark.parametrize("name", list(CYCLE_ACTIONS))
def test_cycle_actions_dry_run_from_the_signer(
    name: str, ctx: Web3Context, vault: PlasmaVault, pending: HyperCorePendingReader
):
    _skip_if_pending(pending)
    gas = _dry_run(ctx, CYCLE_ACTIONS[name](vault, ctx))
    assert 100_000 < gas < 1_000_000


def test_redeem_pays_only_from_evm_usdc(ctx: Web3Context, vault: PlasmaVault):
    """Market 55 has no instant-withdraw fuse: a redeem worth more than the
    vault's EVM USDC reverts in the ERC-20 transfer, however much sits on
    Core. The spoke unwinds Core before anyone redeems."""
    shares = vault.balance_of(SIGNER).call()
    idle = ERC20(ctx, USDC).balance_of(VAULT).call()
    if shares == 0 or vault.convert_to_assets(shares).call() <= idle:
        pytest.skip("the signer's position is covered by the vault's EVM USDC")
    with pytest.raises(ContractLogicError, match="exceeds balance"):
        _dry_run(ctx, vault.redeem(shares, SIGNER, SIGNER))
