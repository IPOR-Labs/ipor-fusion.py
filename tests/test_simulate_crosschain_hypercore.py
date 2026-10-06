"""The composed flow: an Arbitrum hub vault supplies USDC over CCIP to its
dispatcher on HyperEVM, the dispatcher deposits into the market-55 HyperCore
vault, that vault's Alpha trades on HyperCore, unwinds, and the hub recalls and
claims the USDC.

Hub and transport are the live v3 canary deployment (``CANARY_V3``); the spoke
vault is the market-55 HyperCore test vault instead of the canary's plain spoke
vault. The wiring the composition needs is simulated governance on live state:
the HyperCore vault as a ``REMOTE_VAULT`` substrate on the hub, on the
dispatcher's allowlist (an ``UPDATE_VAULTS`` command over CCIP) and with
``WHITELIST_ROLE`` for the dispatcher.

HyperCore itself is a model: the HyperCore fuses and pre-hooks run as
source-compiled runtimes with their precompile addresses redirected to
ordinary shadow addresses (``_hypercore_shadow``), answered by
``_hypercore_model``. Core never applies anything here; balances move because
the test sets them, and the EVM credit of a Core -> EVM transfer is an ERC-20
balance override. What the test proves is the EVM side: every call validates,
every CCIP delivery fits the gas its route gives it, and the hub ends with every
bucket at zero.

Needs ``ARBITRUM_PROVIDER_URL``, ``HYPEREVM_PROVIDER_URL`` and
``IPOR_FUSION_HYPERCORE_SOURCE_DIR`` (a contracts checkout at the revision whose
executable code matches the broadcast HyperCore set).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from _crosschain import CANARY_V3, CANARY_V3_ARBITRUM, CANARY_V3_HYPEREVM, Spoke
from _crosschain_lifecycle import (
    Run,
    _attest_residue,
    _deposit,
    attest,
    claim,
    prepare_deployed_run,
    redeem_and_recall,
    supply,
)
from _hypercore_model import market55_model
from _hypercore_shadow import compile_hypercore_shadow_runtimes
from web3 import Web3

from ipor_fusion import (
    AccessManager,
    CcipLane,
    CrosschainSubstrateLib,
    CrosschainTransportKind,
    HyperCoreDepositFuse,
    HyperCoreOrderFuse,
    HyperCoreSendFuse,
    PlasmaVault,
    Roles,
    TimeInForce,
    Web3Context,
    erc20_balance_slot,
)
from ipor_fusion.fuses.hypercore import SPOT_DEX, USDC_SYSTEM_ADDRESS
from ipor_fusion.readers.hypercore import (
    HyperCoreAccountMarginSummary,
    HyperCoreSpotBalance,
)
from ipor_fusion.types import Amount, ChainId, MarketId

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_market55.json").read_text()
)
HYPERCORE_VAULT = Web3.to_checksum_address(FIXTURE["new_vault_deployment"]["vault"])
HYPERCORE_ADDRESSES = {
    name: Web3.to_checksum_address(item["address"])
    for name, item in FIXTURE["contracts"].items()
}
HYPERCORE_ADDRESSES["HyperCorePendingActionPreHook"] = Web3.to_checksum_address(
    FIXTURE["new_vault_deployment"]["pending_action_hook"]
)
HYPERCORE_MARKET = MarketId(55)
XYZ_DEX = 1
XYZ_NVDA = 110_002

# After the HyperCore vault's deployment and first round trip; the hub five
# seconds after the spoke. The v3 hub buckets are all zero here.
SPOKE_BLOCK = 47_738_500
HUB_BLOCK = 511_957_255
DEPLOYMENT = replace(
    CANARY_V3,
    hub=replace(CANARY_V3_ARBITRUM, block=HUB_BLOCK),
    spokes=(
        replace(
            CANARY_V3.spokes[0], chain=replace(CANARY_V3_HYPEREVM, block=SPOKE_BLOCK)
        ),
    ),
)
SPOKE: Spoke = DEPLOYMENT.spokes[0]
TRANSPORT = CrosschainTransportKind.CHAINLINK_CCIP
#: 15 USDC: Hyperliquid's minimum order is 10 USD and the HIP-3 grant caps the
#: notional at 15 USD.
AMOUNT = Amount(15_000_000)
CORE_WEI_PER_USDC_UNIT = 100  # 8-decimal Core wei vs 6-decimal EVM USDC
_L1_START = 0
#: ``HyperCorePendingLib.PENDING_SLOT`` (ERC-7201 ``io.ipor.hypercore.pending``).
PENDING_SLOT = int(
    "ff3841d45e92b4b113213ebf16e5e18527d87d1b739008d460c4a8565edaf200", 16
)


def _source_dir() -> Path:
    source_dir = os.environ.get("IPOR_FUSION_HYPERCORE_SOURCE_DIR")
    if not source_dir:
        pytest.skip("IPOR_FUSION_HYPERCORE_SOURCE_DIR is not set")
    return Path(source_dir)


def _remappings() -> tuple[str, ...]:
    raw = os.environ.get("IPOR_FUSION_FOUNDRY_REMAPPINGS", "")
    return tuple(item for item in raw.split(";") if item)


def _wire(run: Run, web3_hyperevm) -> PlasmaVault:
    """Simulated governance that points the lane at the HyperCore vault."""
    lane = run.lane
    assert isinstance(lane, CcipLane)
    hub_vault = run.vault
    granted = list(hub_vault.get_market_substrates(run.market_id).call())
    granted.append(
        CrosschainSubstrateLib.remote_vault_substrate(
            run.spoke_chain_id, HYPERCORE_VAULT
        )
    )
    run.hub.add_call(
        hub_vault.grant_market_substrates(run.market_id, granted),
        from_=run.owner,
        label="grant_hypercore_remote_vault",
    )
    run.hub.execute(
        [
            lane.command_fuse.update_vaults(
                executor=lane.executor_address,
                chain_id=run.spoke_chain_id,
                plasma_vaults=[SPOKE.remote_vault, HYPERCORE_VAULT],
                send=lane.default_command_send(),
            )
        ]
    )
    spoke_ctx = Web3Context(web3_hyperevm, chain_id=ChainId(999))
    spoke_ctx.default_block = SPOKE_BLOCK
    hypercore_vault = PlasmaVault(spoke_ctx, HYPERCORE_VAULT)
    access = AccessManager(
        spoke_ctx, hypercore_vault.get_access_manager_address().call()
    )
    run.spoke_sim.add_call(
        access.grant_role(Roles.WHITELIST_ROLE, lane.executor_address, 0),
        from_=run.owner,
        label="whitelist_dispatcher_on_hypercore_vault",
    )
    run.relay()
    run.csim.observe(
        run.spoke_chain_id,
        "hypercore_vault_allowed",
        lane.dispatcher.is_allowed_vault(HYPERCORE_VAULT),
    )
    results = run.relay()
    assert results[run.spoke_chain_id].get("hypercore_vault_allowed") is True
    return hypercore_vault


def _install_hypercore_model(run: Run, web3_hyperevm) -> None:
    global _L1_START
    runtimes = compile_hypercore_shadow_runtimes(
        _source_dir(), web3_hyperevm, HYPERCORE_ADDRESSES, SPOKE_BLOCK, _remappings()
    )
    for address, code in runtimes.items():
        run.spoke_sim.with_state_override(address, code="0x" + code.hex())
    # The vault's pending record holds the real L1 block of its last live
    # action; the model's L1 clock must start past it, or that action reads as
    # pending forever. Every reader of that record calls the L1-block
    # precompile, which does not answer for a historical block, so read the
    # record's first storage word: ``pendingUntil`` (bits 0-63) then
    # ``enqueuedL1Block`` (bits 64-127) of ``HyperCorePendingLib.PendingAction``.
    word = int.from_bytes(
        web3_hyperevm.eth.get_storage_at(
            HYPERCORE_VAULT, PENDING_SLOT, block_identifier=SPOKE_BLOCK
        ),
        "big",
    )
    _L1_START = (word >> 64) & (2**64 - 1)
    assert _L1_START > 0, "the vault has no recorded HyperCore action at the pin"
    # The vault's Core account exists (it ran a round trip) and is empty.
    _set_core_state(run, spot_usdc_wei=0, xyz_value_usd6=0)


def _set_core_state(run: Run, *, spot_usdc_wei: int, xyz_value_usd6: int) -> None:
    model = market55_model(HYPERCORE_VAULT)
    run.spoke_sim.with_state_override_provider(
        replace(
            model,
            l1_block_number=_L1_START + 1,
            spot_balances={
                (HYPERCORE_VAULT, 0): HyperCoreSpotBalance(spot_usdc_wei, 0, 0)
            },
            account_summaries={
                (0, HYPERCORE_VAULT): HyperCoreAccountMarginSummary(0, 0, 0, 0),
                (XYZ_DEX, HYPERCORE_VAULT): HyperCoreAccountMarginSummary(
                    xyz_value_usd6, 0, 0, xyz_value_usd6
                ),
            },
        ).provider()
    )


def _alpha(run: Run, vault: PlasmaVault, label: str, action) -> None:
    run.spoke_sim.add_call(vault.execute([action]), from_=run.owner, label=label)
    run.relay()


def _trade_on_hypercore(run: Run, vault: PlasmaVault, web3_hyperevm, usdc: int) -> int:
    """The HyperCore vault's Alpha: bridge, move to xyz, buy, close, bring the
    USDC home. Returns what arrives back on the vault on HyperEVM."""
    deposit = HyperCoreDepositFuse(HYPERCORE_ADDRESSES["HyperCoreDepositFuse"])
    send = HyperCoreSendFuse(HYPERCORE_ADDRESSES["HyperCoreSendFuse"])
    order = HyperCoreOrderFuse(HYPERCORE_ADDRESSES["HyperCoreOrderFuse"])
    core_wei = usdc * CORE_WEI_PER_USDC_UNIT

    _alpha(run, vault, "bridge_to_core", deposit.enter(token_index=0, amount=usdc))
    run.advance(5)
    _set_core_state(run, spot_usdc_wei=core_wei, xyz_value_usd6=0)
    _alpha(
        run,
        vault,
        "spot_to_xyz",
        send.send_asset(
            destination=HYPERCORE_VAULT,
            source_dex=SPOT_DEX,
            destination_dex=XYZ_DEX,
            token_index=0,
            amount_wei=core_wei,
        ),
    )
    run.advance(5)
    _set_core_state(run, spot_usdc_wei=0, xyz_value_usd6=usdc)
    for label, is_buy, reduce_only, cloid in (
        ("buy", True, False, 0x8101),
        ("sell", False, True, 0x8102),
    ):
        _alpha(
            run,
            vault,
            label,
            order.enter(
                asset=XYZ_NVDA,
                is_buy=is_buy,
                limit_px=240_00_000_000 if is_buy else 230_00_000_000,
                sz=5_000_000,
                tif=TimeInForce.IOC,
                cloid=cloid,
                reduce_only=reduce_only,
            ),
        )
        run.advance(31)
    # Two taker fills of about 0.001 USDC each, as in the live round trip.
    after_fees = usdc - 2_134
    _set_core_state(run, spot_usdc_wei=0, xyz_value_usd6=after_fees)
    _alpha(
        run,
        vault,
        "xyz_to_spot",
        send.send_asset(
            destination=HYPERCORE_VAULT,
            source_dex=XYZ_DEX,
            destination_dex=SPOT_DEX,
            token_index=0,
            amount_wei=after_fees * CORE_WEI_PER_USDC_UNIT,
        ),
    )
    run.advance(5)
    # Core -> EVM leaves a reserve for the fee charged on top of the amount.
    reserve = 50_000
    home = after_fees - reserve
    _set_core_state(
        run, spot_usdc_wei=after_fees * CORE_WEI_PER_USDC_UNIT, xyz_value_usd6=0
    )
    _alpha(
        run,
        vault,
        "core_to_evm",
        send.send_asset(
            destination=USDC_SYSTEM_ADDRESS,
            source_dex=SPOT_DEX,
            destination_dex=SPOT_DEX,
            token_index=0,
            amount_wei=home * CORE_WEI_PER_USDC_UNIT,
        ),
    )
    run.advance(5)
    # The fee leaves the reserve; nothing else stays on Core. The EVM credit is
    # the transfer HyperCore would make, modelled as a balance override.
    _set_core_state(run, spot_usdc_wei=0, xyz_value_usd6=0)
    usdc_address = run.remote_vault.underlying_asset_address().call()
    run.spoke_sim.with_erc20_balance(
        usdc_address,
        HYPERCORE_VAULT,
        home,
        slot=erc20_balance_slot(web3_hyperevm, usdc_address, block=SPOKE_BLOCK),
    )
    run.spoke_sim.add_call(
        vault.update_markets_balances([HYPERCORE_MARKET]),
        from_=run.owner,
        label="refresh_home",
    )
    run.relay()
    return home


def _assert_deliveries_within_route_limits(run: Run) -> dict[str, int]:
    """Every delivery fits the gas limit stamped on it; returns the gas used by
    the hub -> spoke command deliveries, keyed by their order."""
    lane = run.lane
    assert isinstance(lane, CcipLane)
    hub, spoke = run.hub_chain_id, run.spoke_chain_id
    run.csim.observe(hub, "route_hub", lane.executor.ccip_route(spoke))
    run.csim.observe(spoke, "route_spoke", lane.dispatcher.ccip_route(hub))
    results = run.relay()
    routes = {
        spoke: results[hub].get("route_hub"),
        hub: results[spoke].get("route_spoke"),
    }
    commands: dict[str, int] = {}
    for index, message in enumerate(run.csim.delivered):
        route = routes[message.dst_chain_id]
        limit = (
            route.token_gas_limit if message.token_amount else route.message_gas_limit
        )
        label = f"ccip_receive:{message.message_id.hex()[:8]}"
        delivery = next(
            call for call in results[message.dst_chain_id].calls if call.label == label
        )
        run.log(
            "delivery gas",
            dst_chain_id=message.dst_chain_id,
            token_amount=message.token_amount,
            gas_used=delivery.gas_used,
            limit=limit,
        )
        assert 0 < delivery.gas_used <= limit
        if message.dst_chain_id == spoke and not message.token_amount:
            commands[f"{index}"] = delivery.gas_used
    return commands


def test_simulate_crosschain_supply_into_a_hypercore_vault(web3_arb, web3_hyperevm):
    _source_dir()
    run = prepare_deployed_run(web3_arb, web3_hyperevm, DEPLOYMENT, SPOKE, TRANSPORT)
    run.amount = AMOUNT
    # SIMULATION ONLY: the hub vault's USDC for a 15 USDC supply.
    hub_usdc = run.hub_asset.address
    run.hub.with_erc20_balance(
        hub_usdc,
        run.hub_vault_address,
        AMOUNT,
        slot=erc20_balance_slot(web3_arb, hub_usdc, block=HUB_BLOCK),
    )
    _install_hypercore_model(run, web3_hyperevm)
    hypercore_vault = _wire(run, web3_hyperevm)
    run.remote_vault = hypercore_vault

    credited = supply(run)
    attest(run, tag="initial", observation_label="observation_after_settle")
    shares = _deposit(run, credited)
    home = _trade_on_hypercore(run, hypercore_vault, web3_hyperevm, credited)

    idle = redeem_and_recall(run, shares, credited=credited)
    assert idle == home
    _attest_residue(run, idle)
    claim(run, idle)
    commands = _assert_deliveries_within_route_limits(run)
    # UPDATE_VAULTS, DEPOSIT (through the capital-flow hook's market-55
    # refresh), REDEEM and the recall request all carry the 2 M v3 route limit.
    assert len(commands) == 4
    run.log("command gas", **commands)
