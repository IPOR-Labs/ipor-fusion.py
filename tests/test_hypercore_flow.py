"""Byte-level parity with the HyperCore HIP-3 flow run on HyperEVM mainnet.

``tests/fixtures/hypercore_hip3_flow.json`` pins the 47 transactions of the
2026-09-28 run: the 11 creates, the market 47 -> 54 migration and one long
plus one short xyz:NVDA cycle (EVM -> Core deposit, spot -> HIP-3 dex,
IOC/GTC orders, cancel, reduce-only close, Core -> EVM, redeem). Every call
the SDK can express is rebuilt from typed inputs and compared with the
on-chain calldata; the creates are cross-checked against the calls that wire
them. No network: the on-chain inputs are the oracle.

The run used market id 54; the contracts team has since assigned 54 to the
crosschain market and moved HyperCore to 55, so the governance calls below
keep the historical id while the substrate layout is decoded under
``IporFusionMarkets.HYPERCORE``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    HyperCoreCancelFuse,
    HyperCoreConfigKey,
    HyperCoreDepositFuse,
    HyperCoreOrderFuse,
    HyperCoreSendFuse,
    HyperCoreSubstrates,
    PlasmaVault,
    SettlementMode,
    TimeInForce,
    decode_substrate,
)
from ipor_fusion.fuses.hypercore import SPOT_DEX, USDC_SYSTEM_ADDRESS
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import MarketId

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_hip3_flow.json").read_text()
)
TX = {row["nonce"]: row for row in FIXTURE["txs"]}
CREATED = {
    row["contract"]: Web3.to_checksum_address(row["created"])
    for row in FIXTURE["txs"]
    if row["kind"] == "create"
}

VAULT = Web3.to_checksum_address(FIXTURE["vault"])
ACCESS_MANAGER = Web3.to_checksum_address(FIXTURE["access_manager"])
USDC = Web3.to_checksum_address(FIXTURE["usdc"])
SIGNER = Web3.to_checksum_address(FIXTURE["signer"])
RUN_MARKET = MarketId(FIXTURE["market_id"])  # 54 at the time of the run
DOLOMITE = MarketId(47)  # the market the vault migrated away from

DEPOSIT_FUSE = CREATED["HyperCoreDepositFuse"]
SEND_FUSE = CREATED["HyperCoreSendFuse"]
ORDER_FUSE = CREATED["HyperCoreOrderFuse"]
CANCEL_FUSE = CREATED["HyperCoreCancelFuse"]
BALANCE_FUSE = CREATED["HyperCoreBalanceFuse"]
REPORTER = CREATED["HyperCoreSettlementReporter"]
PENDING_HOOK = CREATED["HyperCorePendingActionPreHook"]
CAPITAL_HOOK = CREATED["HyperCoreCapitalFlowPreHook"]
# CREATEd by the reporter's constructor, so it has no row of its own.
SETTLEMENT_FUSE = Web3.to_checksum_address("0x275F3940A0D12109Cf9909780b91881554e7b91f")
BUILDER = Web3.to_checksum_address("0xcee5c4272e246a424aede992c987966736e0f63b")
ALPHA_ROLE = 200

# Market 47 wiring the migration removed (never re-encoded by the SDK).
OLD_BALANCE_FUSE = Web3.to_checksum_address(
    "0x10ebdd680512323025cf9482b65db78a8ad5183b"
)
OLD_REPORTER = Web3.to_checksum_address("0xf9624b514a3ee0caf92eaae9e030a342786fa01c")
OLD_FUSES = [
    Web3.to_checksum_address(a)
    for a in (
        "0x4d35a7767ddeef4b86070fd0f9b18967f999e738",
        "0xcbcf8b926cbf718ddc3b792b20843b2c1c4ea157",
        "0x63a1ccd22c462ee043457bcf64d8e12b4d2a15ad",
        "0xd6277f12af2132ff4d51fb948c8ebca9fbc40394",
        "0x70fb8610f96386ae361646e34f9b2b6d36427d3d",
        "0xeb775bdf2fe4e632130f3d6c60966ae996482c1b",
        "0x14f6320675bcb5b91518b2f69e394aa1650bd2fe",
    )
]

XYZ_DEX = 1
XYZ_NVDA = 110_002
DEPOSIT = 15_000_000  # 15 USDC, ERC-20 units
TO_XYZ = 1_490_000_000  # 14.9 USDC in Core wei

vault = PlasmaVault.encoder(VAULT)
access = AccessManager.encoder(ACCESS_MANAGER)
usdc = ERC20.encoder(USDC)
deposit_fuse = HyperCoreDepositFuse(DEPOSIT_FUSE)
send_fuse = HyperCoreSendFuse(SEND_FUSE)
order_fuse = HyperCoreOrderFuse(ORDER_FUSE)
cancel_fuse = HyperCoreCancelFuse(CANCEL_FUSE)


def _onchain(nonce: int) -> bytes:
    return bytes.fromhex(TX[nonce]["input"][2:])


def _execute(action) -> bytes:
    return vault.execute([action]).calldata


def _order(
    *, is_buy: bool, px: int, sz: int, tif: TimeInForce, cloid: int, reduce_only=False
):
    return _execute(
        order_fuse.enter(
            asset=XYZ_NVDA,
            is_buy=is_buy,
            limit_px=px,
            sz=sz,
            tif=tif,
            cloid=cloid,
            reduce_only=reduce_only,
        )
    )


def _send(destination, source_dex: int, destination_dex: int, amount_wei: int):
    return _execute(
        send_fuse.send_asset(
            destination=destination,
            source_dex=source_dex,
            destination_dex=destination_dex,
            token_index=0,
            amount_wei=amount_wei,
        )
    )


def _bridge_in():
    return _execute(deposit_fuse.enter(token_index=0, amount=DEPOSIT))


MARKET_54_GRANTS = [
    HyperCoreSubstrates.spot_token(0, USDC),
    HyperCoreSubstrates.perp_market(XYZ_NVDA, 15_000_000),
    HyperCoreSubstrates.destination(VAULT),
    HyperCoreSubstrates.destination(USDC_SYSTEM_ADDRESS),
    HyperCoreSubstrates.send_cap(0, 100 * 10**8),
    HyperCoreSubstrates.config(HyperCoreConfigKey.WINDOW_TRANSFER_SECONDS, 2),
    HyperCoreSubstrates.config(HyperCoreConfigKey.WINDOW_ORDER_SECONDS, 30),
    HyperCoreSubstrates.config(
        HyperCoreConfigKey.MAX_USD_CLASS_TRANSFER_USD6, 40_000_000
    ),
    HyperCoreSubstrates.config(
        HyperCoreConfigKey.SETTLEMENT_MODE, SettlementMode.TIMING
    ),
    HyperCoreSubstrates.config(HyperCoreConfigKey.SPOT_SEND_BRIDGE_ENABLED, 1),
    HyperCoreSubstrates.config(HyperCoreConfigKey.PERP_DEX_IDS, 1 << XYZ_DEX),
    HyperCoreSubstrates.builder(BUILDER, 1),
]


# migration.7: the two HyperCore pre-hooks the run installed, in its order --
# the pending-action hook on the fuse entry points, the capital-flow hook on
# every deposit and exit entry point; no hook carries substrates.
PENDING_ACTION_PRE_HOOK = Web3.to_checksum_address(
    "0xbd1654f1932ef6869ce0c4e4a4d593c8678b17ec"
)
CAPITAL_FLOW_PRE_HOOK = Web3.to_checksum_address(
    "0xc8efdb4f33b7112f6d68b0d864ded0badc4c9029"
)
PRE_HOOKS = [
    ("execute((address,bytes)[])", PENDING_ACTION_PRE_HOOK),
    ("updateMarketsBalances(uint256[])", PENDING_ACTION_PRE_HOOK),
    ("deposit(uint256,address)", CAPITAL_FLOW_PRE_HOOK),
    (
        "depositWithPermit(uint256,address,uint256,uint8,bytes32,bytes32)",
        CAPITAL_FLOW_PRE_HOOK,
    ),
    ("mint(uint256,address)", CAPITAL_FLOW_PRE_HOOK),
    ("withdraw(uint256,address,address)", CAPITAL_FLOW_PRE_HOOK),
    ("redeem(uint256,address,address)", CAPITAL_FLOW_PRE_HOOK),
    ("redeemFromRequest(uint256,address,address)", CAPITAL_FLOW_PRE_HOOK),
]


def _set_pre_hooks() -> bytes:
    return vault.set_pre_hook_implementations(
        [function_signature_to_4byte_selector(sig) for sig, _hook in PRE_HOOKS],
        [hook for _sig, hook in PRE_HOOKS],
        [[] for _ in PRE_HOOKS],
    ).calldata


# nonce -> how the SDK expresses that transaction.
EXPECTED: dict[int, Callable[[], bytes]] = {
    # migration 47 -> 54
    67: lambda: (
        vault.add_fuses(
            [
                DEPOSIT_FUSE,
                CREATED["HyperCoreMarginFuse"],
                ORDER_FUSE,
                CANCEL_FUSE,
                SEND_FUSE,
                CREATED["HyperCoreBuilderFeeFuse"],
                SETTLEMENT_FUSE,
            ]
        ).calldata
    ),
    68: lambda: vault.add_balance_fuse(RUN_MARKET, BALANCE_FUSE).calldata,
    69: lambda: vault.grant_market_substrates(RUN_MARKET, MARKET_54_GRANTS).calldata,
    70: lambda: access.grant_role(ALPHA_ROLE, REPORTER, 0).calldata,
    71: lambda: vault.grant_market_substrates(DOLOMITE, []).calldata,
    72: lambda: vault.update_markets_balances([DOLOMITE, RUN_MARKET]).calldata,
    73: _set_pre_hooks,
    74: lambda: vault.remove_balance_fuse(DOLOMITE, OLD_BALANCE_FUSE).calldata,
    75: lambda: vault.remove_fuses(OLD_FUSES).calldata,
    76: lambda: access.revoke_role(ALPHA_ROLE, OLD_REPORTER).calldata,
    77: lambda: vault.update_markets_balances([RUN_MARKET]).calldata,
    # long cycle
    78: lambda: usdc.approve(VAULT, DEPOSIT).calldata,
    79: lambda: vault.deposit(DEPOSIT, SIGNER).calldata,
    80: _bridge_in,
    81: lambda: _send(VAULT, SPOT_DEX, XYZ_DEX, TO_XYZ),
    82: lambda: _order(
        is_buy=True, px=229_92_000_000, sz=5_000_000, tif=TimeInForce.IOC, cloid=0x5101
    ),
    83: lambda: vault.update_markets_balances([RUN_MARKET]).calldata,
    84: lambda: _order(
        is_buy=True, px=180_00_000_000, sz=6_000_000, tif=TimeInForce.GTC, cloid=0x5102
    ),
    85: lambda: _execute(cancel_fuse.cancel_by_cloid(asset=XYZ_NVDA, cloid=0x5102)),
    86: lambda: _order(
        is_buy=False,
        px=225_47_000_000,
        sz=5_000_000,
        tif=TimeInForce.IOC,
        cloid=0x5103,
        reduce_only=True,
    ),
    87: lambda: vault.update_markets_balances([RUN_MARKET]).calldata,
    88: lambda: _send(VAULT, XYZ_DEX, SPOT_DEX, 1_492_138_200),
    89: lambda: _send(USDC_SYSTEM_ADDRESS, SPOT_DEX, SPOT_DEX, 1_503_799_300),
    90: lambda: vault.update_markets_balances([RUN_MARKET]).calldata,
    91: lambda: vault.redeem(1_577_296_220, SIGNER, SIGNER).calldata,
    # short cycle
    92: lambda: usdc.approve(VAULT, DEPOSIT).calldata,
    93: lambda: vault.deposit(DEPOSIT, SIGNER).calldata,
    94: _bridge_in,
    95: lambda: _send(VAULT, SPOT_DEX, XYZ_DEX, TO_XYZ),
    96: lambda: _order(
        is_buy=False, px=227_65_000_000, sz=5_000_000, tif=TimeInForce.IOC, cloid=0x5201
    ),
    97: lambda: vault.update_markets_balances([RUN_MARKET]).calldata,
    98: lambda: _order(
        is_buy=True,
        px=232_55_000_000,
        sz=5_000_000,
        tif=TimeInForce.IOC,
        cloid=0x5203,
        reduce_only=True,
    ),
    99: lambda: _send(VAULT, XYZ_DEX, SPOT_DEX, 1_488_792_900),
    100: lambda: _send(USDC_SYSTEM_ADDRESS, SPOT_DEX, SPOT_DEX, 1_498_347_000),
    101: lambda: vault.update_markets_balances([RUN_MARKET]).calldata,
    102: lambda: vault.redeem(1_573_136_517, SIGNER, SIGNER).calldata,
}

_PARITY_CASES = [
    pytest.param(nonce, id=f"n{nonce} {TX[nonce]['step']}")
    for nonce in sorted(EXPECTED)
]


def test_fixture_covers_the_whole_run():
    nonces = [row["nonce"] for row in FIXTURE["txs"]]
    assert nonces == list(range(56, 103))
    assert all(row["gas_used"] > 0 for row in FIXTURE["txs"])
    calls = {row["nonce"] for row in FIXTURE["txs"] if row["kind"] == "call"}
    assert calls == set(EXPECTED)


@pytest.mark.parametrize("nonce", _PARITY_CASES)
def test_sdk_reproduces_the_onchain_calldata(nonce):
    row = TX[nonce]
    expected_target = {70: ACCESS_MANAGER, 76: ACCESS_MANAGER, 78: USDC, 92: USDC}.get(
        nonce, VAULT
    )
    assert Web3.to_checksum_address(row["to"]) == expected_target
    assert EXPECTED[nonce]() == _onchain(nonce)


def test_creates_are_what_the_migration_wired():
    fuses_added = decode(["address[]"], _onchain(67)[4:])[0]
    assert {Web3.to_checksum_address(a) for a in fuses_added} == {
        CREATED[name]
        for name in (
            "HyperCoreDepositFuse",
            "HyperCoreMarginFuse",
            "HyperCoreSendFuse",
            "HyperCoreOrderFuse",
            "HyperCoreCancelFuse",
            "HyperCoreBuilderFeeFuse",
        )
    } | {SETTLEMENT_FUSE}
    selectors, implementations, substrates = decode(
        ["bytes4[]", "address[]", "bytes32[][]"], _onchain(73)[4:]
    )
    hooks = dict(
        zip(
            [s.hex() for s in selectors],
            [Web3.to_checksum_address(a) for a in implementations],
            strict=True,
        )
    )
    # execute and updateMarketsBalances go through the pending-action hook,
    # every share-priced entry point through the capital-flow hook.
    assert hooks["baae8abf"] == hooks["e9a2e778"] == PENDING_HOOK
    assert {hooks[s] for s in ("6e553f65", "94bf804d", "b460af94", "ba087652")} == {
        CAPITAL_HOOK
    }
    assert all(len(s) == 0 for s in substrates)
    assert TX[73]["log_emitters"] == [VAULT.lower()] * len(TX[73]["log_emitters"])


def test_granted_words_decode_as_hypercore():
    market_id, words = decode(["uint256", "bytes32[]"], _onchain(69)[4:])
    assert market_id == RUN_MARKET
    infos = [
        decode_substrate(word, market_id=IporFusionMarkets.HYPERCORE) for word in words
    ]
    assert [i.type_label for i in infos] == [
        "SPOT_TOKEN",
        "PERP_MARKET",
        "DESTINATION",
        "DESTINATION",
        "SEND_CAP",
        "CONFIG",
        "CONFIG",
        "CONFIG",
        "CONFIG",
        "CONFIG",
        "CONFIG",
        "BUILDER",
    ]
    assert infos[0].address == USDC.lower()
    assert infos[1].extra == {
        "asset": str(XYZ_NVDA),
        "max_notional_usd6": "15000000",
        "reduce_only_required": "false",
    }
    assert [i.extra.get("key") for i in infos[5:11]] == [
        "WindowTransferSeconds",
        "WindowOrderSeconds",
        "MaxUsdClassTransferUsd6",
        "SettlementMode",
        "SpotSendBridgeEnabled",
        "PerpDexIds",
    ]
    assert infos[8].extra["mode"] == "TIMING"
    assert infos[10].extra["value"] == str(1 << XYZ_DEX)
    assert infos[11].address == BUILDER.lower()
    assert all(not i.is_error for i in infos)
