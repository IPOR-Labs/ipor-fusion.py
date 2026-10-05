"""Opt-in, source-verified HyperCore EVM simulation on a pinned vault block."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from _hypercore_model import (
    HYPERCORE_SHADOW_CORE_WRITER,
    HyperCoreSimulationModel,
    hypercore_shadow_address,
)
from _hypercore_shadow import (
    _assert_only_address_redirects,
    compile_hypercore_shadow_runtimes,
)
from eth_abi import decode
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion import (
    ERC20,
    HyperCoreAccountMarginSummary,
    HyperCoreDepositFuse,
    HyperCoreOrderFuse,
    HyperCorePerpAssetInfo,
    HyperCoreReader,
    HyperCoreSendFuse,
    HyperCoreSpotBalance,
    HyperCoreTokenInfo,
    PlasmaVault,
    TimeInForce,
    VaultSimulator,
    Web3Context,
    erc20_balance_slot,
)
from ipor_fusion.fuses.hypercore import SPOT_DEX
from ipor_fusion.types import MarketId

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_market55.json").read_text()
)
DEPLOYMENT = FIXTURE["new_vault_deployment"]
BLOCK = DEPLOYMENT["snapshot_block"]
VAULT = Web3.to_checksum_address(DEPLOYMENT["vault"])
OPERATOR = Web3.to_checksum_address(DEPLOYMENT["operator"])
USDC = Web3.to_checksum_address(DEPLOYMENT["underlying"])
ADDRESSES = {
    name: Web3.to_checksum_address(item["address"])
    for name, item in FIXTURE["contracts"].items()
}
ADDRESSES["HyperCorePendingActionPreHook"] = Web3.to_checksum_address(
    DEPLOYMENT["pending_action_hook"]
)


def _model() -> HyperCoreSimulationModel:
    zero = HyperCoreAccountMarginSummary(0, 0, 0, 0)
    return HyperCoreSimulationModel(
        l1_block_number=1_000_000_000,
        core_users={VAULT: True},
        spot_balances={(VAULT, 0): HyperCoreSpotBalance(1_500_000_000, 0, 0)},
        account_summaries={(0, VAULT): zero, (1, VAULT): zero},
        token_infos={
            0: HyperCoreTokenInfo(
                name="USDC",
                spots=(),
                deployer_trading_fee_share=0,
                deployer=Web3.to_checksum_address(
                    "0x0000000000000000000000000000000000000000"
                ),
                evm_contract=Web3.to_checksum_address(
                    "0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24"
                ),
                sz_decimals=8,
                wei_decimals=8,
                evm_extra_wei_decimals=-2,
            )
        },
        perp_asset_infos={10_002: HyperCorePerpAssetInfo("xyz:NVDA", 20, 3, 20, False)},
    )


def test_shadow_runtime_guard_rejects_other_instruction_changes() -> None:
    metadata = bytes.fromhex("a00001")
    original = bytes.fromhex("61080100") + metadata
    _assert_only_address_redirects(original, bytes.fromhex("61090100") + metadata)
    with pytest.raises(ValueError, match="unexpected shadow immediate"):
        _assert_only_address_redirects(original, bytes.fromhex("61090200") + metadata)
    with pytest.raises(ValueError, match="shadow opcode changed"):
        _assert_only_address_redirects(original, bytes.fromhex("60080100") + metadata)


def test_market55_vault_flow_with_explicit_hypercore_model() -> None:
    source_dir = os.environ.get("IPOR_FUSION_HYPERCORE_SOURCE_DIR")
    url = os.environ.get("HYPEREVM_PROVIDER_URL")
    if not source_dir or not url:
        pytest.skip("HyperCore source checkout and HYPEREVM_PROVIDER_URL required")
    web3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
    if not web3.is_connected() or web3.eth.chain_id != 999:
        pytest.skip("HYPEREVM_PROVIDER_URL is not a reachable HyperEVM node")
    remappings = tuple(
        item
        for item in os.environ.get("IPOR_FUSION_FOUNDRY_REMAPPINGS", "").split(";")
        if item
    )
    runtimes = compile_hypercore_shadow_runtimes(
        Path(source_dir), web3, ADDRESSES, BLOCK, remappings
    )
    ctx = Web3Context(web3, 999)
    ctx.default_block = BLOCK
    vault = PlasmaVault(ctx, VAULT)
    usdc = ERC20(ctx, USDC)
    sim = VaultSimulator(web3, vault=VAULT, alpha=OPERATOR, block=BLOCK)
    for address, code in runtimes.items():
        sim.with_state_override(address, code="0x" + code.hex())
    model = _model()
    sim.with_state_override_provider(model.provider())
    balance_fuse = ADDRESSES["HyperCoreBalanceFuse"]
    sim.observe("balance_before", vault.balance_fuse_value(balance_fuse))
    l1_call = replace(
        HyperCoreReader().l1_block_number(), to=hypercore_shadow_address(0x809)
    )
    sim.observe("l1_before", l1_call)
    sim.with_erc20_balance(
        USDC, OPERATOR, 20_000_000, slot=erc20_balance_slot(web3, USDC, block=BLOCK)
    )
    sim.add_call(usdc.approve(VAULT, 15_000_000), from_=OPERATOR, label="approve")
    sim.add_call(vault.deposit(15_000_000, OPERATOR), from_=OPERATOR, label="deposit")
    sim.observe("after_deposit", vault.total_assets())

    sim.next_block()
    sim.execute(
        [
            HyperCoreDepositFuse(ADDRESSES["HyperCoreDepositFuse"]).enter(
                token_index=0, amount=10_000_000
            )
        ]
    )
    sim.next_block(60)
    sim.with_state_override_provider(
        replace(
            model,
            spot_balances={(VAULT, 0): HyperCoreSpotBalance(2_500_000_000, 0, 0)},
        ).provider()
    )
    sim.execute(
        [
            HyperCoreSendFuse(ADDRESSES["HyperCoreSendFuse"]).send_asset(
                destination=VAULT,
                source_dex=SPOT_DEX,
                destination_dex=1,
                token_index=0,
                amount_wei=1_000_000_000,
            )
        ]
    )
    sim.next_block(60)
    sim.with_state_override_provider(
        replace(
            model,
            account_summaries={
                (0, VAULT): HyperCoreAccountMarginSummary(0, 0, 0, 0),
                (1, VAULT): HyperCoreAccountMarginSummary(10_000_000, 0, 0, 10_000_000),
            },
        ).provider()
    )
    sim.execute(
        [
            HyperCoreOrderFuse(ADDRESSES["HyperCoreOrderFuse"]).enter(
                asset=110_002,
                is_buy=True,
                limit_px=229_92_000_000,
                sz=5_000_000,
                tif=TimeInForce.IOC,
                cloid=0x7101,
            )
        ]
    )
    sim.next_block(40)
    sim.observe("balance_after", vault.balance_fuse_value(balance_fuse))
    sim.observe("l1_after", l1_call)
    sim.execute_call(vault.update_markets_balances([MarketId(55)]))

    result = sim.run()
    assert result.all_success, [call.revert_reason for call in result.failed_calls]
    assert result.get("after_deposit") == 30_000_000
    assert result.get("balance_after") > result.get("balance_before")
    assert result.get("l1_after") == result.get("l1_before") + 4
    writer_logs = [
        log
        for log in result.execute_logs
        if log["address"].lower() == HYPERCORE_SHADOW_CORE_WRITER.lower()
    ]
    assert len(writer_logs) == 2
    selector = Web3.keccak(text="sendRawAction(bytes)")[:4]
    action_ids = []
    for log in writer_logs:
        calldata = bytes(HexBytes(log["data"]))
        assert calldata[:4] == selector
        payload = decode(["bytes"], calldata[4:])[0]
        action_ids.append(int.from_bytes(payload[1:4], "big"))
    assert action_ids == [13, 1]
