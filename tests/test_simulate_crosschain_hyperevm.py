"""Pinned Arbitrum-to-HyperEVM CCIP factory and deployment rehearsal.

The lifecycle relay impersonates the CCIP Router, and synthetic storage funding
stands in for destination token-pool release. It proves that the IPOR contracts, fuses
and SDK compose end to end; pinned readiness tests separately verify the live
CCIP lanes and pools, but this simulation does not prove their liveness.

The module requires both RPCs and skips in CI until ``HYPEREVM_PROVIDER_URL``
is configured there.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from _crosschain_lifecycle import (
    BIG_CHANGE_EXCEEDED,
    Run,
    assert_reverted,
    attest,
    run_lifecycle,
    supply,
)
from _crosschain_pilot import PILOT_BUILD, assert_pilot_code, assert_pilot_deployment
from _crosschain_recovery import (
    run_failed_deposit_recovery,
    run_unfunded_return_recovery,
)
from _foundry import (
    FoundryContract,
    SolidityArtifact,
    compile_foundry_contracts,
    git_revision,
)
from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    CcipChain,
    CcipCommandType,
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
    CcipLane,
    CcipTransport,
    CrosschainSimulator,
    CrosschainSubstrateLib,
    CrosschainTransportKind,
    LaneFuses,
    OutboundMessage,
    PlasmaVault,
    PriceOracleMiddlewareManager,
    Roles,
    SafetyConfig,
    VaultSimulator,
    Web3Context,
    ccip_token_lane,
    erc20_balance_slot,
)
from ipor_fusion.core.contract import Call
from ipor_fusion.core.fusion_factory import FusionFactory
from ipor_fusion.crosschain.logs import log_bytes, log_topic0
from ipor_fusion.types import Amount, ChainId, MarketId, Shares

ARBITRUM = ChainId(42161)
HYPEREVM = ChainId(999)
ARBITRUM_BLOCK = 509_772_283
HYPEREVM_BLOCK = 47_131_895
ARBITRUM_SELECTOR = 4949039107694359620
HYPEREVM_SELECTOR = 2442541497099098535
ARBITRUM_ROUTER = Web3.to_checksum_address("0x141fa059441E0ca23ce184B6A78bafD2A517DdE8")
HYPEREVM_ROUTER = Web3.to_checksum_address("0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec")
FACTORY = Web3.to_checksum_address("0x3a745EaC243ea7563CCbD5890dbCA1b05CEe1e0D")
# The pilot-v2 factory pair (contracts team, 2026-10-01): source `810e260` on
# `827ada0` plus the three 5-minute patches, CREATE3 anchor
# 0x6Fe4852dd57dC0Ec47928D55A3b8577f36704032, same address on both chains.
FACTORY_V2 = Web3.to_checksum_address("0x3BB74623A229Ff463bDe6B5b267B7c8d9086fd4b")
#: The canary's creator EOA, allowed on both v2 factories (creation is
#: restricted there); executors are predicted from it, not from the owner.
CREATOR = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
#: Pinned after the v2 pair was configured (assets, routes, creator).
V2_BLOCKS = (510_724_267, 47_394_896)
V2_EXECUTOR_CREATION_KECCAK = bytes.fromhex(
    "11e28748e1cda094e2b80620b85bfb01e008172a69f2592be72493caad285cd1"
)
V2_DISPATCHER_CREATION_KECCAK = bytes.fromhex(
    "cc2edfa5791f504e730114ed80fcd535a927e5e11e53045a2584bb1e161d5e03"
)
ARBITRUM_FUSION_FACTORY = Web3.to_checksum_address(
    "0x134fCAce7a2C7Ef3dF2479B62f03ddabAEa922d5"
)
HYPEREVM_FUSION_FACTORY = Web3.to_checksum_address(
    "0x6bBc827F34b4e862d15215ABd4AC0E3886181665"
)
# The executor asset equals the vault underlying, so this source cancels in the
# conversion ratio. Lifecycle assertions remain HTEST-denominated, never USD.
ARBITRUM_WBTC_USD_FEED = Web3.to_checksum_address(
    "0xd0C7101eACbB49F3deCcCc166d238410D6D46d57"
)
HYPEREVM_ONE_VALUE_FEED = Web3.to_checksum_address(
    "0xb00121888530a090a124913bBdBc39c68F804904"
)
OWNER = Web3.to_checksum_address("0x3F48E1FfC0Ea0b1f908770Ec3372eA0F50dA2af2")
RESCUE_GUARDIAN = Web3.to_checksum_address("0x0000000000000000000000000000000000000B22")
BALANCE_PROPOSER = Web3.to_checksum_address(
    "0x0000000000000000000000000000000000000A33"
)
BALANCE_APPROVER = Web3.to_checksum_address(
    "0x0000000000000000000000000000000000000B44"
)
ZERO_ADDRESS = Web3.to_checksum_address("0x0000000000000000000000000000000000000000")
SIMULATED_DEPLOYER = Web3.to_checksum_address(
    "0x000000000000000000000000000000000000D00D"
)

ASSETS = {
    "HTEST": (
        Web3.to_checksum_address("0x8c3f237ae7d31Bc141f321892B7f23c663243B43"),
        Web3.to_checksum_address("0x78B79a86F74374C59354f37EF7B92aF38AbB2438"),
    ),
    "qWHYPE": (
        Web3.to_checksum_address("0x30E12aFF2ce43d8C286d4702EB833A22d8405816"),
        Web3.to_checksum_address("0x30E12aFF2ce43d8C286d4702EB833A22d8405816"),
    ),
    "CCIP-BnM": (
        Web3.to_checksum_address("0x69D7121d9e444ECFfBC92fA9B9cf3B42264778cd"),
        Web3.to_checksum_address("0xECFF67559c0583027A5fbd85136E33bC4D66eeA0"),
    ),
    "TEST": (
        Web3.to_checksum_address("0x00d88A4c69B90b9dCb4AF59A90Ae4bCb2ba191EA"),
        Web3.to_checksum_address("0x6C46eFABF37a6705286Aa5D792664a98200a5Ca5"),
    ),
    "TESTTR": (
        Web3.to_checksum_address("0x83cB78b9009d48C57F29A453dd5bc774b1545682"),
        Web3.to_checksum_address("0x3DAc7a0294B6399468F908A7bB2B0c7f15ae71A6"),
    ),
}

FUSE_CONTRACTS = (
    FoundryContract(
        "contracts/fuses/crosschain/ccip/CcipCrosschainSupplyFuse.sol",
        "CcipCrosschainSupplyFuse",
    ),
    FoundryContract(
        "contracts/fuses/crosschain/ccip/CcipCrosschainCommandFuse.sol",
        "CcipCrosschainCommandFuse",
    ),
    FoundryContract(
        "contracts/fuses/crosschain/CrosschainClaimFuse.sol",
        "CrosschainClaimFuse",
    ),
    FoundryContract(
        "contracts/fuses/crosschain/CrosschainBalanceFuse.sol",
        "CrosschainBalanceFuse",
    ),
)
# The checkout every compiled contract comes from. On the `pilot` and `v2`
# generations only the four fuses are compiled (the executors and dispatchers
# come from the creation code the live factories store); on `source` the whole
# CCIP core is. Bump deliberately, with the fuse ABI re-checked against the
# encoders.
FUSE_SOURCE_REVISION = "827ada02eabdc01eae913de1114fa5bea45ee205"
CROSSCHAIN_MARKET = MarketId(54)

# The CCIP core of the source generation: libraries in link order
# (`CcipExecutorTransferLib` links `CcipExecutorAttestationLib`), the factory,
# and the two creation codes the factory stores. The simulated deployer creates
# them at the same nonces on both chains, so the factory lands at one address
# everywhere without the pilot's CREATE3 anchor.
_CCIP_LIB = "contracts/crosschain/ccip/lib"
CORE_LIBRARIES = (
    FoundryContract(f"{_CCIP_LIB}/CcipRouteLib.sol", "CcipRouteLib"),
    FoundryContract(f"{_CCIP_LIB}/CcipSendLib.sol", "CcipSendLib"),
    FoundryContract(f"{_CCIP_LIB}/CcipDecimalsLib.sol", "CcipDecimalsLib"),
    FoundryContract(
        f"{_CCIP_LIB}/CcipExecutorAttestationLib.sol", "CcipExecutorAttestationLib"
    ),
    FoundryContract(
        f"{_CCIP_LIB}/CcipExecutorCommandLib.sol", "CcipExecutorCommandLib"
    ),
    FoundryContract(
        f"{_CCIP_LIB}/CcipExecutorTransferLib.sol", "CcipExecutorTransferLib"
    ),
    FoundryContract("contracts/crosschain/lib/RescueLogicLib.sol", "RescueLogicLib"),
    FoundryContract(
        "contracts/crosschain/lib/CrosschainVaultActionLib.sol",
        "CrosschainVaultActionLib",
    ),
)
FACTORY_CONTRACT = FoundryContract(
    "contracts/crosschain/ccip/CcipCrosschainFactory.sol", "CcipCrosschainFactory"
)
EXECUTOR_CONTRACT = FoundryContract(
    "contracts/crosschain/ccip/CcipCrosschainExecutor.sol", "CcipCrosschainExecutor"
)
DISPATCHER_CONTRACT = FoundryContract(
    "contracts/crosschain/ccip/CcipCrosschainDispatcher.sol",
    "CcipCrosschainDispatcher",
)
#: `CrosschainConstants.ATTESTATION_ZERO_DUST_SD` of the new accounting.
ATTESTATION_ZERO_DUST_SD = 10_000
#: `pilot`: executors and dispatchers from the pilot factory's stored creation
#: code (old accounting). `v2`: the live pilot-v2 pair, pinned at V2_BLOCKS.
#: `source`: a factory pair built from FUSE_SOURCE_REVISION inside the
#: simulation. `v2` and `source` share the new accounting: the recall debits
#: the sent amount (recall-debit fix) and dust attests to zero (dust-to-zero fix).
GENERATIONS = ("pilot", "v2", "source")
NEW_ACCOUNTING = frozenset({"v2", "source"})
PILOT_EXECUTOR_CREATION_KECCAK = bytes.fromhex(
    PILOT_BUILD["creation_stores"]["executor"]["creation_keccak"][2:]
)


@dataclass(frozen=True)
class RehearsalAsset:
    symbol: str
    tokens: tuple[ChecksumAddress, ChecksumAddress]
    decimals: int
    blocks: tuple[int, int]
    feeds: tuple[ChecksumAddress, ChecksumAddress]
    configure_factory: bool = False

    @property
    def asset_id(self) -> bytes:
        return bytes(Web3.keccak(text=self.symbol))

    @property
    def deposit_amount(self) -> Amount:
        return Amount(10 * 10**self.decimals + 1)


HTEST = RehearsalAsset(
    "HTEST",
    ASSETS["HTEST"],
    18,
    (ARBITRUM_BLOCK, HYPEREVM_BLOCK),
    (ARBITRUM_WBTC_USD_FEED, HYPEREVM_ONE_VALUE_FEED),
)
# Native token identities: https://developers.circle.com/stablecoins/usdc-contract-addresses
# Feed proxies: data.chain.link/feeds/arbitrum/mainnet/usdc-usd and
# data.chain.link/feeds/hyperliquid/hyperliquid/usdc-usd (standard, not SVR).
USDC = RehearsalAsset(
    "USDC",
    (
        Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
        Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f"),
    ),
    6,
    # The readiness probe's re-pin candidates of 2026-10-01 (same timestamp on
    # both chains), after the owner enabled USDC on both pilot factories.
    (510_648_595, 47_375_011),
    (
        Web3.to_checksum_address("0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3"),
        Web3.to_checksum_address("0xA0Adc43ce7AfE3EE7d7eac3C994E178D0620223B"),
    ),
    configure_factory=True,
)
REHEARSAL_ASSETS = (HTEST, USDC)


class _CctpFeeTransport(CcipTransport):
    """Model a fixed destination-side CCTP deduction in each token leg."""

    def __init__(self, chains: Iterable[CcipChain], *, fee_raw_units: int) -> None:
        super().__init__(chains)
        self.fee_raw_units = fee_raw_units

    def outbound_messages(
        self, src_chain_id: ChainId, logs: Iterable[Mapping]
    ) -> list[OutboundMessage]:
        messages = super().outbound_messages(src_chain_id, logs)
        adjusted = []
        for message in messages:
            if message.token_amount:
                if message.token_amount <= self.fee_raw_units:
                    raise ValueError("CCTP test fee must be below the token amount")
                message = replace(
                    message, token_amount=message.token_amount - self.fee_raw_units
                )
            adjusted.append(message)
        return adjusted


def test_simulate_contract_creation(web3_arb):
    # Creation code returning a runtime contract whose fallback returns uint256(42).
    runtime = bytes.fromhex("602a60005260206000f3")
    init_code = bytes.fromhex("600a600c600039600a6000f3") + runtime
    simulator = VaultSimulator(
        web3_arb,
        vault=ZERO_ADDRESS,
        alpha=SIMULATED_DEPLOYER,
        block=ARBITRUM_BLOCK,
    )
    simulator.with_state_override(SIMULATED_DEPLOYER, balance=hex(10**18), nonce=hex(0))
    deployed = simulator.deploy_contract(
        init_code,
        from_=SIMULATED_DEPLOYER,
        nonce=0,
        label="deploy_constant",
    )
    simulator.observe(
        "constant",
        Call(to=deployed, data=b"", output_types=["uint256"]),
    )

    result = simulator.run()

    result.raise_for_failure()
    assert result.calls[0].return_data == runtime
    assert result.calls[0].predicted_address == deployed
    assert result.get("constant") == 42


def test_compile_and_deploy_crosschain_fuses(web3_arb):
    artifacts = _compile_crosschain_contracts()
    market_id = int.from_bytes(
        Web3.keccak(text="IPOR_FUSION_CROSSCHAIN_HTEST_V1"), "big"
    )
    simulator = VaultSimulator(
        web3_arb,
        vault=ZERO_ADDRESS,
        alpha=SIMULATED_DEPLOYER,
        block=ARBITRUM_BLOCK,
    )
    simulator.with_state_override(SIMULATED_DEPLOYER, balance=hex(10**18), nonce=hex(0))
    deployed = {
        contract: simulator.deploy_contract(
            artifacts[contract].init_code(("uint256",), (market_id,)),
            from_=SIMULATED_DEPLOYER,
            nonce=nonce,
            label=f"deploy_{contract.name}",
        )
        for nonce, contract in enumerate(FUSE_CONTRACTS)
    }
    market_id_call = bytes(Web3.keccak(text="MARKET_ID()")[:4])
    version_call = bytes(Web3.keccak(text="VERSION()")[:4])
    for contract, address in deployed.items():
        simulator.observe(
            f"market_id_{contract.name}",
            Call(to=address, data=market_id_call, output_types=["uint256"]),
        )
        if contract.name != "CrosschainBalanceFuse":
            simulator.observe(
                f"version_{contract.name}",
                Call(
                    to=address,
                    data=version_call,
                    output_types=["address"],
                    decoder=Web3.to_checksum_address,
                ),
            )

    result = simulator.run()

    result.raise_for_failure()
    for index, contract in enumerate(FUSE_CONTRACTS):
        deployment = result.calls[index]
        assert deployment.predicted_address == deployed[contract]
        assert artifacts[contract].matches_runtime(deployment.return_data)
        assert result.get(f"market_id_{contract.name}") == market_id
        if contract.name != "CrosschainBalanceFuse":
            assert result.get(f"version_{contract.name}") == deployed[contract]


def _ctx(web3, chain_id: ChainId, block: int) -> Web3Context:
    ctx = Web3Context(web3, chain_id=chain_id)
    ctx.default_block = block
    return ctx


def _compile_crosschain_contracts() -> dict[FoundryContract, SolidityArtifact]:
    contracts_dir = os.environ.get("IPOR_FUSION_CONTRACTS_DIR")
    if not contracts_dir:
        pytest.skip("IPOR_FUSION_CONTRACTS_DIR not set")
    revision = git_revision(contracts_dir)
    if revision != FUSE_SOURCE_REVISION:
        pytest.skip(
            f"IPOR_FUSION_CONTRACTS_DIR is at {revision}; "
            f"these tests pin {FUSE_SOURCE_REVISION}"
        )
    remappings = tuple(
        item
        for item in os.environ.get("IPOR_FUSION_FOUNDRY_REMAPPINGS", "").split(";")
        if item
    )
    return compile_foundry_contracts(
        Path(contracts_dir),
        (
            *FUSE_CONTRACTS,
            *CORE_LIBRARIES,
            FACTORY_CONTRACT,
            EXECUTOR_CONTRACT,
            DISPATCHER_CONTRACT,
        ),
        remappings=remappings,
    )


def _deploy_source_core(
    chain: VaultSimulator,
    artifacts: dict[FoundryContract, SolidityArtifact],
    *,
    router: ChecksumAddress,
) -> tuple[ChecksumAddress, dict[str, ChecksumAddress]]:
    """The libraries and the factory from the simulated deployer at nonces
    ``0..len(CORE_LIBRARIES)``; returns the factory and the library addresses."""
    chain.with_state_override(SIMULATED_DEPLOYER, balance=hex(10**18), nonce=hex(0))
    libraries: dict[str, ChecksumAddress] = {}
    # Explicit gas: the node's default call gas is below what a contract-sized
    # code deposit costs (200 gas per byte), and eth_simulateV1 charges the
    # caps against the block limit, so they stay close to the real cost and
    # the whole deployment fits one 30 M block, as it must live.
    for nonce, contract in enumerate(CORE_LIBRARIES):
        libraries[contract.name] = chain.deploy_contract(
            artifacts[contract].link(libraries).init_code(),
            from_=SIMULATED_DEPLOYER,
            nonce=nonce,
            gas=2_500_000,
            label=f"deploy_{contract.name}",
        )
    factory = chain.deploy_contract(
        artifacts[FACTORY_CONTRACT]
        .link(libraries)
        .init_code(("address", "address"), (router, OWNER)),
        from_=SIMULATED_DEPLOYER,
        nonce=len(CORE_LIBRARIES),
        gas=6_000_000,
        label="deploy_CcipCrosschainFactory",
    )
    return factory, libraries


def _configure_source_factory(
    chain: VaultSimulator,
    factory: CcipCrosschainFactory,
    artifacts: dict[FoundryContract, SolidityArtifact],
    libraries: dict[str, ChecksumAddress],
    *,
    asset: RehearsalAsset,
    index: int,
    peer_chain_id: ChainId,
    route_template,
    config_delay: int,
) -> bytes:
    """The owner's governance on a fresh factory: the creation codes
    (write-once), then the asset and the factory-to-factory route through the
    factory's own timelock. Returns the keccak of the executor creation code."""
    executor_code = artifacts[EXECUTOR_CONTRACT].link(libraries).creation_code
    dispatcher_code = artifacts[DISPATCHER_CONTRACT].link(libraries).creation_code
    route = replace(route_template, peer=factory.address)
    chain.next_block(time_shift_seconds=1).with_block_override(gasLimit=30_000_000)
    # Two code stores of ~26 KB each: ~12 M gas, mostly code deposit.
    chain.add_call(
        factory.configure_creation_codes(executor_code, dispatcher_code),
        from_=OWNER,
        label="configure_creation_codes",
        gas=14_000_000,
    )
    chain.add_call(
        factory.schedule_asset(asset.asset_id, asset.tokens[index], asset.decimals),
        from_=OWNER,
        label="schedule_factory_asset",
    )
    chain.add_call(
        factory.schedule_factory_route(peer_chain_id, route),
        from_=OWNER,
        label="schedule_factory_route",
    )
    chain.next_block(time_shift_seconds=config_delay + 1).with_block_override(
        gasLimit=30_000_000
    )
    chain.add_call(
        factory.execute_asset(asset.asset_id, asset.tokens[index], asset.decimals),
        from_=OWNER,
        label="enable_factory_asset",
    )
    chain.add_call(
        factory.execute_factory_route(peer_chain_id, route),
        from_=OWNER,
        label="execute_factory_route",
    )
    return bytes(Web3.keccak(executor_code))


def _deploy_source_generation(
    simulator: CrosschainSimulator,
    hub: VaultSimulator,
    spoke: VaultSimulator,
    arb_ctx: Web3Context,
    hyper_ctx: Web3Context,
    artifacts: dict[FoundryContract, SolidityArtifact],
    asset: RehearsalAsset,
) -> ChecksumAddress:
    """A pilot-v2 factory pair from the source generation, configured for
    ``asset`` and for each other, verified after one relay; returns the factory
    address (the same on both chains)."""
    factory_address, libraries = _deploy_source_core(
        hub, artifacts, router=ARBITRUM_ROUTER
    )
    spoke_factory_address, spoke_libraries = _deploy_source_core(
        spoke, artifacts, router=HYPEREVM_ROUTER
    )
    assert (factory_address, libraries) == (spoke_factory_address, spoke_libraries)
    hub_factory = CcipCrosschainFactory(arb_ctx, factory_address)
    spoke_factory = CcipCrosschainFactory(hyper_ctx, factory_address)
    # The factory's own timelock (24 h in source, 300 s on a patched pilot-style
    # build), so a patched checkout rehearses unchanged.
    simulator.observe(ARBITRUM, "source_config_delay", hub_factory.config_delay())
    simulator.observe(HYPEREVM, "source_config_delay", spoke_factory.config_delay())
    results = simulator.relay()
    delays = {
        chain_id: results[chain_id].get("source_config_delay")
        for chain_id in (ARBITRUM, HYPEREVM)
    }
    assert delays[ARBITRUM] == delays[HYPEREVM] > 0
    config_delay = delays[ARBITRUM]
    executor_code_hash = _configure_source_factory(
        hub,
        hub_factory,
        artifacts,
        libraries,
        asset=asset,
        index=0,
        peer_chain_id=HYPEREVM,
        route_template=CcipCrosschainFactory(arb_ctx, FACTORY)
        .ccip_route(HYPEREVM)
        .call(),
        config_delay=config_delay,
    )
    _configure_source_factory(
        spoke,
        spoke_factory,
        artifacts,
        libraries,
        asset=asset,
        index=1,
        peer_chain_id=ARBITRUM,
        route_template=CcipCrosschainFactory(hyper_ctx, FACTORY)
        .ccip_route(ARBITRUM)
        .call(),
        config_delay=config_delay,
    )
    for chain_id, factory, peer in (
        (ARBITRUM, hub_factory, HYPEREVM),
        (HYPEREVM, spoke_factory, ARBITRUM),
    ):
        simulator.observe(
            chain_id, "source_creation_codes", factory.creation_codes_configured()
        )
        simulator.observe(
            chain_id, "source_executor_code_hash", factory.executor_creation_code_hash()
        )
        simulator.observe(
            chain_id, "source_factory_asset", factory.asset_config(asset.asset_id)
        )
        simulator.observe(chain_id, "source_factory_route", factory.ccip_route(peer))
    results = simulator.relay()
    for chain_id, index, peer_selector in (
        (ARBITRUM, 0, HYPEREVM_SELECTOR),
        (HYPEREVM, 1, ARBITRUM_SELECTOR),
    ):
        result = results[chain_id]
        result.raise_for_failure()
        assert result.get("source_creation_codes") is True
        # The source generation must never silently run the pilot's code.
        assert result.get("source_executor_code_hash") == executor_code_hash
        assert executor_code_hash != PILOT_EXECUTOR_CREATION_KECCAK
        assert result.get("source_factory_asset") == (
            asset.tokens[index],
            asset.decimals,
            True,
        )
        route = result.get("source_factory_route")
        assert route.enabled
        assert route.peer == factory_address
        assert route.chain_selector == peer_selector
    return factory_address


def _asset_transport(asset: RehearsalAsset) -> CcipTransport:
    return CcipTransport(
        (
            CcipChain(
                ARBITRUM,
                ARBITRUM_SELECTOR,
                ARBITRUM_ROUTER,
                asset.tokens[0],
                asset.decimals,
            ),
            CcipChain(
                HYPEREVM,
                HYPEREVM_SELECTOR,
                HYPEREVM_ROUTER,
                asset.tokens[1],
                asset.decimals,
            ),
        )
    )


def _require_token_lanes(web3_arb, web3_hyperevm, asset: RehearsalAsset) -> None:
    unavailable = []
    for index, (web3, chain, router, peer) in enumerate(
        (
            (web3_arb, ARBITRUM, ARBITRUM_ROUTER, HYPEREVM_SELECTOR),
            (web3_hyperevm, HYPEREVM, HYPEREVM_ROUTER, ARBITRUM_SELECTOR),
        )
    ):
        lane = ccip_token_lane(
            _ctx(web3, chain, asset.blocks[index]), router, asset.tokens[index], peer
        )
        assert lane.message_lane, f"CCIP message lane missing on chain {chain}"
        assert lane.pool is not None, (
            f"{asset.symbol} has no CCIP pool on chain {chain}"
        )
        if not lane.token_lane:
            unavailable.append(
                f"chain {chain} pool {lane.pool} does not support selector {peer} "
                f"at block {asset.blocks[index]}"
            )
    if unavailable and asset == USDC:
        pytest.skip("USDC acceptance blocked: " + "; ".join(unavailable))
    assert not unavailable, unavailable


def _factory_view(ctx, signature, output_types):
    return Call(
        to=FACTORY,
        data=bytes(Web3.keccak(text=signature)[:4]),
        output_types=output_types,
        ctx=ctx,
    )


def _asset_governance_call(asset: RehearsalAsset, index: int, method: str) -> Call:
    signature = f"{method}(bytes32,address,uint8)"
    return Call(
        to=FACTORY,
        data=bytes(Web3.keccak(text=signature)[:4])
        + encode(
            ["bytes32", "address", "uint8"],
            [asset.asset_id, asset.tokens[index], asset.decimals],
        ),
    )


def _enable_factory_asset(simulator, ctx, asset: RehearsalAsset, *, index: int) -> None:
    factory = CcipCrosschainFactory(ctx, FACTORY)
    assert (
        Web3.to_checksum_address(_factory_view(ctx, "OWNER()", ["address"]).call())
        == OWNER
    )
    delay = _factory_view(ctx, "CONFIG_DELAY()", ["uint256"]).call()
    config = factory.asset_config(asset.asset_id).call()
    if config == (asset.tokens[index], asset.decimals, True):
        return
    assert config == (ZERO_ADDRESS, 0, False), config
    simulator.add_call(
        _asset_governance_call(asset, index, "scheduleAsset"),
        from_=OWNER,
        label="schedule_factory_asset",
    )
    simulator.next_block(time_shift_seconds=delay + 1).with_block_override(
        gasLimit=30_000_000
    )
    simulator.add_call(
        _asset_governance_call(asset, index, "executeAsset"),
        from_=OWNER,
        label="enable_factory_asset",
    )


def _feed_read(ctx, address, signature, output_types):
    return Call(
        to=address,
        data=bytes(Web3.keccak(text=signature)[:4]),
        output_types=output_types,
        ctx=ctx,
    ).call()


def _assert_configured_prices(
    simulator, contexts, instances, asset: RehearsalAsset, factory: ChecksumAddress
):
    for index, ctx in enumerate(contexts):
        manager = PriceOracleMiddlewareManager(ctx, instances[index].price_manager)
        simulator.observe(
            ctx.chain_id, "asset_price", manager.get_asset_price(asset.tokens[index])
        )
        simulator.observe(
            ctx.chain_id,
            "asset_source",
            manager.get_source_of_asset_price(asset.tokens[index]),
        )
        simulator.observe(
            ctx.chain_id,
            "factory_asset",
            CcipCrosschainFactory(ctx, factory).asset_config(asset.asset_id),
        )
    results = simulator.relay()
    for index, ctx in enumerate(contexts):
        result = results[ctx.chain_id]
        result.raise_for_failure()
        assert result.get("asset_source") == asset.feeds[index]
        assert result.get("factory_asset") == (
            asset.tokens[index],
            asset.decimals,
            True,
        )
        answer = _feed_read(
            ctx,
            asset.feeds[index],
            "latestRoundData()",
            ["uint80", "int256", "uint256", "uint256", "uint80"],
        )[1]
        decimals = _feed_read(ctx, asset.feeds[index], "decimals()", ["uint8"])
        price = result.get("asset_price")
        assert price.decimals == 18
        assert price.amount == answer * 10 ** (18 - decimals)


def _safety_config() -> SafetyConfig:
    return SafetyConfig(
        rescue_admin=OWNER,
        rescue_guardian=RESCUE_GUARDIAN,
        balance_proposer=BALANCE_PROPOSER,
        balance_approver=BALANCE_APPROVER,
        rescue_delay=3 * 24 * 60 * 60,
        balance_staleness_max=7 * 24 * 60 * 60,
        big_change_bps=2_000,
        min_update_interval=60 * 60,
        transfer_staleness_max=24 * 60 * 60,
    )


@pytest.mark.parametrize(
    ("web3_fixture", "chain_id", "block", "factory_address", "token"),
    (
        (
            "web3_arb",
            ARBITRUM,
            ARBITRUM_BLOCK,
            ARBITRUM_FUSION_FACTORY,
            ASSETS["HTEST"][0],
        ),
        (
            "web3_hyperevm",
            HYPEREVM,
            HYPEREVM_BLOCK,
            HYPEREVM_FUSION_FACTORY,
            ASSETS["HTEST"][1],
        ),
    ),
)
def test_simulate_clone_htest_vault(
    request, web3_fixture, chain_id, block, factory_address, token
):
    web3 = request.getfixturevalue(web3_fixture)
    ctx = _ctx(web3, chain_id, block)
    factory = FusionFactory(ctx, factory_address)
    clone = factory.clone(
        "Crosschain HTEST Rehearsal",
        "xcHTEST",
        token,
        1,
        OWNER,
    )
    preview = clone.call()
    simulator = VaultSimulator(web3, vault=ZERO_ADDRESS, alpha=OWNER, block=block)
    simulator.with_block_override(gasLimit=30_000_000)
    simulator.add_call(clone, from_=OWNER, label="clone_vault")
    vault = PlasmaVault(ctx, preview.plasma_vault)
    simulator.observe("vault_asset", vault.underlying_asset_address())
    simulator.observe("vault_access_manager", vault.get_access_manager_address())

    result = simulator.run()

    result.raise_for_failure()
    instance = result.get("clone_vault")
    assert instance == preview
    assert result.get("vault_asset") == token
    assert result.get("vault_access_manager") == preview.access_manager


def _asset_for(asset: RehearsalAsset, generation: str) -> RehearsalAsset:
    """The asset at the pins its generation needs: the v2 pair exists only
    from V2_BLOCKS on."""
    return replace(asset, blocks=V2_BLOCKS) if generation == "v2" else asset


@pytest.mark.parametrize("generation", GENERATIONS)
@pytest.mark.parametrize("asset", REHEARSAL_ASSETS, ids=lambda asset: asset.symbol)
def test_simulate_configured_asset_lane(web3_arb, web3_hyperevm, asset, generation):
    asset = _asset_for(asset, generation)
    _require_token_lanes(web3_arb, web3_hyperevm, asset)
    run = _prepare_asset_run(web3_arb, web3_hyperevm, asset, generation=generation)
    run_lifecycle(run)
    _assert_token_deliveries_within_gas_limit(run)


@pytest.mark.parametrize("index", (0, 1), ids=("arbitrum", "hyperevm"))
def test_v2_factory_pair_identity(web3_arb, web3_hyperevm, index):
    """The live pilot-v2 pair as the contracts team reported it, at the v2
    pins: same address and creation codes on both chains, the canary creator
    allowed, routes to each other, USDC and HTEST enabled."""
    web3 = (web3_arb, web3_hyperevm)[index]
    chain = (ARBITRUM, HYPEREVM)[index]
    ctx = _ctx(web3, chain, V2_BLOCKS[index])
    factory = CcipCrosschainFactory(ctx, FACTORY_V2)

    assert factory.owner().call() == OWNER
    assert factory.config_delay().call() == 300
    assert factory.creation_codes_configured().call()
    assert factory.creation_restricted().call()
    assert factory.is_allowed_creator(CREATOR).call()
    assert factory.executor_creation_code_hash().call() == V2_EXECUTOR_CREATION_KECCAK
    assert (
        factory.dispatcher_creation_code_hash().call() == V2_DISPATCHER_CREATION_KECCAK
    )
    assert V2_EXECUTOR_CREATION_KECCAK != PILOT_EXECUTOR_CREATION_KECCAK
    route = factory.ccip_route((HYPEREVM, ARBITRUM)[index]).call()
    assert route.enabled
    assert route.peer == FACTORY_V2
    assert route.chain_selector == (HYPEREVM_SELECTOR, ARBITRUM_SELECTOR)[index]
    assert (route.message_gas_limit, route.token_gas_limit) == (6_000_000, 1_000_000)
    for asset in REHEARSAL_ASSETS:
        assert factory.asset_config(asset.asset_id).call() == (
            asset.tokens[index],
            asset.decimals,
            True,
        )


def test_simulate_source_factory_pair(web3_arb, web3_hyperevm):
    """The pilot-v2 deployment the operator would send, as labeled calls:
    libraries, factory, creation codes, asset and route on both chains."""
    artifacts = _compile_crosschain_contracts()
    arb_ctx = _ctx(web3_arb, ARBITRUM, USDC.blocks[0])
    hyper_ctx = _ctx(web3_hyperevm, HYPEREVM, USDC.blocks[1])
    simulator = CrosschainSimulator(_asset_transport(USDC))
    hub = simulator.add_chain(ARBITRUM, web3_arb, block=USDC.blocks[0])
    spoke = simulator.add_chain(HYPEREVM, web3_hyperevm, block=USDC.blocks[1])
    hub.with_block_override(gasLimit=30_000_000)
    spoke.with_block_override(gasLimit=30_000_000)

    factory = _deploy_source_generation(
        simulator, hub, spoke, arb_ctx, hyper_ctx, artifacts, USDC
    )

    assert factory != FACTORY
    governance = [
        "configure_creation_codes",
        "schedule_factory_asset",
        "schedule_factory_route",
        "enable_factory_asset",
        "execute_factory_route",
    ]
    deployment = [f"deploy_{contract.name}" for contract in CORE_LIBRARIES] + [
        "deploy_CcipCrosschainFactory"
    ]
    for chain_id in (ARBITRUM, HYPEREVM):
        labels = [
            call.label
            for call in simulator.results[chain_id].calls
            if call.label and not call.label.startswith("source_")
        ]
        assert labels == deployment + governance


def _assert_token_deliveries_within_gas_limit(run: Run) -> None:
    """Every token-carrying delivery ran within the token gas limit of the
    route its sender holds: the executor's for the supply leg, the
    dispatcher's for the return leg.

    The relay calls ``ccipReceive`` directly, so ``gas_used`` is the receiver's
    own handling (deposit on the spoke, settlement on the hub). The token
    pool's release or mint is outside it, as it is for the live OffRamp, which
    meters the receiver call separately."""
    assert isinstance(run.lane, CcipLane)
    run.csim.observe(
        ARBITRUM, "gas_limit_route_hub", run.lane.executor.ccip_route(HYPEREVM)
    )
    run.csim.observe(
        HYPEREVM, "gas_limit_route_spoke", run.lane.dispatcher.ccip_route(ARBITRUM)
    )
    results = run.relay()
    limits = {
        HYPEREVM: results[ARBITRUM].get("gas_limit_route_hub").token_gas_limit,
        ARBITRUM: results[HYPEREVM].get("gas_limit_route_spoke").token_gas_limit,
    }
    token_legs = [message for message in run.csim.delivered if message.token_amount]
    assert [message.dst_chain_id for message in token_legs] == [HYPEREVM, ARBITRUM]
    for message in token_legs:
        label = f"ccip_receive:{message.message_id.hex()[:8]}"
        delivery = next(
            call for call in results[message.dst_chain_id].calls if call.label == label
        )
        run.log(
            "token delivery gas",
            dst_chain_id=message.dst_chain_id,
            gas_used=delivery.gas_used,
            token_gas_limit=limits[message.dst_chain_id],
        )
        assert 0 < delivery.gas_used <= limits[message.dst_chain_id]


@pytest.mark.parametrize("cancel", (False, True), ids=("retry", "cancel"))
@pytest.mark.parametrize("asset", REHEARSAL_ASSETS, ids=lambda asset: asset.symbol)
def test_simulate_failed_asset_deposit_recovery(web3_arb, web3_hyperevm, asset, cancel):
    _require_token_lanes(web3_arb, web3_hyperevm, asset)
    run = _prepare_asset_run(
        web3_arb, web3_hyperevm, asset, whitelist_dispatcher=cancel
    )
    run_failed_deposit_recovery(run, cancel=cancel)


@pytest.mark.parametrize("asset", REHEARSAL_ASSETS, ids=lambda asset: asset.symbol)
def test_simulate_unfunded_asset_return_recovery(web3_arb, web3_hyperevm, asset):
    _require_token_lanes(web3_arb, web3_hyperevm, asset)
    run_unfunded_return_recovery(_prepare_asset_run(web3_arb, web3_hyperevm, asset))


@pytest.mark.parametrize("index", (0, 1), ids=("arbitrum", "hyperevm"))
def test_usdc_pinned_prerequisites(web3_arb, web3_hyperevm, index):
    web3 = (web3_arb, web3_hyperevm)[index]
    chain = (ARBITRUM, HYPEREVM)[index]
    ctx = _ctx(web3, chain, USDC.blocks[index])
    token = ERC20(ctx, USDC.tokens[index])
    assert token.symbol().call() == "USDC"
    assert token.decimals().call() == 6
    assert CcipCrosschainFactory(ctx, FACTORY).asset_config(USDC.asset_id).call() == (
        USDC.tokens[index],
        USDC.decimals,
        True,
    )
    assert (
        Web3.to_checksum_address(_factory_view(ctx, "OWNER()", ["address"]).call())
        == OWNER
    )
    assert _factory_view(ctx, "CONFIG_DELAY()", ["uint256"]).call() == 300
    feed = USDC.feeds[index]
    assert _feed_read(ctx, feed, "description()", ["string"]) == "USDC / USD"
    assert _feed_read(ctx, feed, "decimals()", ["uint8"]) == 8
    round_id, answer, _, updated_at, answered_in_round = _feed_read(
        ctx,
        feed,
        "latestRoundData()",
        ["uint80", "int256", "uint256", "uint256", "uint80"],
    )
    assert 99_000_000 <= answer <= 101_000_000
    assert round_id > 0 and answered_in_round >= round_id
    assert (
        0 <= web3.eth.get_block(USDC.blocks[index])["timestamp"] - updated_at <= 86400
    )
    lane = ccip_token_lane(
        ctx,
        (ARBITRUM_ROUTER, HYPEREVM_ROUTER)[index],
        USDC.tokens[index],
        (HYPEREVM_SELECTOR, ARBITRUM_SELECTOR)[index],
    )
    assert lane.message_lane
    assert lane.pool_version == "USDCTokenPoolProxy 2.0.0"
    assert lane.token_lane


def test_simulate_usdc_configuration_and_rounding(web3_arb, web3_hyperevm):
    """USDC governance/deployment/pricing and local rounding, not CCIP acceptance."""
    run = _prepare_asset_run(web3_arb, web3_hyperevm, USDC)
    _check_hub_usdc_roundtrip(run)
    _check_spoke_usdc_rounding(run, web3_hyperevm)


@pytest.mark.parametrize("generation", GENERATIONS)
@pytest.mark.parametrize("fee_raw_units", (0, 24), ids=("no-fee-control", "cctp-fee"))
def test_simulate_usdc_cctp_fee_in_both_directions(
    web3_arb, web3_hyperevm, fee_raw_units, generation
):
    """Full recalls after the management fee has been re-attested leave only
    the CCTP fee, and only on the pilot generation."""
    # The pilot pins observed both USDC token lanes open while the pilot
    # factories still had USDC disabled, so that run also exercises the asset
    # governance path. Pilot: the recall debits the received amount, the fee
    # stays in `settled` and the relative gate refuses to re-mark it to zero.
    # New accounting (recall-debit fix): the fee is a realized loss, nothing remains.
    asset = _asset_for(replace(USDC, blocks=(510_124_921, 47_228_089)), generation)
    base = _asset_transport(asset)
    transport = _CctpFeeTransport(
        (base.chain(chain) for chain in (ARBITRUM, HYPEREVM)),
        fee_raw_units=fee_raw_units,
    )
    run = _prepare_asset_run(
        web3_arb, web3_hyperevm, asset, transport=transport, generation=generation
    )
    run.recall_min_return_bps = 10_000

    run_lifecycle(run)

    transfers = [message for message in run.csim.delivered if message.token_amount]
    assert [message.src_chain_id for message in transfers] == [ARBITRUM, HYPEREVM]
    for message in transfers:
        assert (
            message.raw.token_transfer[0].amount - message.token_amount == fee_raw_units
        )
    hub = run.csim.results[ARBITRUM]
    assert hub.get("outbound_after_send") == transfers[0].raw.token_transfer[0].amount
    assert hub.get("settled_after_settle") == transfers[0].token_amount
    assert hub.get("idle_ledger") == transfers[1].token_amount
    # What left the spoke is the credit less the management fee accrued over
    # the 7-day gap (5 bps a year), re-attested before the recall.
    accrued_fee = transfers[0].token_amount - transfers[1].raw.token_transfer[0].amount
    assert 0 < accrued_fee <= transfers[0].token_amount // 10_000
    residue = hub.get("settled_after_return")
    assert residue == (0 if run.ccip_debits_sent_amount else fee_raw_units)
    _assert_return_finalized(
        hub,
        run,
        sent=transfers[1].raw.token_transfer[0].amount,
        received=transfers[1].token_amount,
    )
    below_minimum_topic = Web3.keccak(
        text="ReturnBelowMinimumCredited(uint256,bytes32,uint256,uint256)"
    )
    below_minimum = [
        log
        for call in hub.calls
        for log in call.logs
        if log_topic0(log) == below_minimum_topic
    ]
    assert len(below_minimum) == (1 if fee_raw_units else 0)
    if below_minimum:
        chain_id, _operation_id, received, minimum = decode(
            ["uint256", "bytes32", "uint256", "uint256"],
            log_bytes(below_minimum[0]["data"]),
        )
        assert chain_id == HYPEREVM
        assert received == transfers[1].token_amount
        assert minimum == transfers[1].raw.token_transfer[0].amount

    run.advance(run.staleness_max + 1)
    stale = residue > run.attestation_zero_dust_sd
    if stale:
        run.expected_failures.add("nav_after_residue")
    run.hub.observe("nav_after_residue", run.lane.get_balance())
    results = run.relay()
    if stale:
        stale_nav = next(
            call
            for call in results[ARBITRUM].calls
            if call.label == "nav_after_residue"
        )
        assert_reverted(stale_nav, "ObservationStale(uint256)")
    else:
        assert results[ARBITRUM].get("nav_after_residue") == 0


def _assert_return_finalized(hub, run: Run, *, sent: int, received: int) -> None:
    """The one ``ReturnFinalized`` of the run, in the generation's shape:
    ``(chainId, operationId, received)`` on the pilot,
    ``(chainId, operationId, sentSD, received)`` on the new accounting."""
    types = ["uint256", "bytes32", "uint256"]
    if run.ccip_debits_sent_amount:
        types.append("uint256")
    topic = Web3.keccak(text=f"ReturnFinalized({','.join(types)})")
    (log,) = [
        log for call in hub.calls for log in call.logs if log_topic0(log) == topic
    ]
    values = decode(types, log_bytes(log["data"]))
    assert values[0] == HYPEREVM
    assert values[-1] == received
    if run.ccip_debits_sent_amount:
        assert values[2] == sent


@pytest.mark.parametrize("generation", GENERATIONS)
def test_simulate_usdc_cctp_fee_alone(web3_arb, web3_hyperevm, generation):
    """Without a spoke vault deposit only the return token-pool fee remains:
    phantom settled value on the pilot, a realized loss on the new accounting."""
    asset = _asset_for(replace(USDC, blocks=(510_124_921, 47_228_089)), generation)
    base = _asset_transport(asset)
    transport = _CctpFeeTransport(
        (base.chain(chain) for chain in (ARBITRUM, HYPEREVM)), fee_raw_units=24
    )
    run = _prepare_asset_run(
        web3_arb, web3_hyperevm, asset, transport=transport, generation=generation
    )
    credited = supply(run)
    attest(run, tag="initial", observation_label="observation_after_settle")

    run.hub.execute([run.lane.recall(amount=credited, min_return=credited)])
    run.relay()
    run.csim.observe(ARBITRUM, "idle_after_recall", run.lane.idle_ledger())
    run.csim.observe(
        ARBITRUM, "settled_after_recall", run.lane.settled_remote_balance()
    )
    run.csim.observe(HYPEREVM, "observation_after_recall", run.lane.observation())
    results = run.relay()
    received = results[ARBITRUM].get("idle_after_recall")
    assert received == credited - 24
    residue = 0 if run.ccip_debits_sent_amount else 24
    assert results[ARBITRUM].get("settled_after_recall") == residue
    assert results[HYPEREVM].get("observation_after_recall").tracked_idle == 0

    # Same wait as `_attest_residue`: the zero-to-zero approval is otherwise
    # refused with `MinUpdateIntervalNotMet`.
    run.advance(run.min_update_interval)
    attest(
        run,
        tag="zero_after_fee",
        observation_label="observation_after_recall",
        hub_idle=received,
        approve_error=BIG_CHANGE_EXCEEDED if residue else None,
    )
    run.advance(run.staleness_max + 1)
    if residue:
        run.expected_failures.add("nav_after_fee_only")
    run.hub.observe("nav_after_fee_only", run.lane.get_balance())
    results = run.relay()
    if residue:
        stale_nav = next(
            call
            for call in results[ARBITRUM].calls
            if call.label == "nav_after_fee_only"
        )
        assert_reverted(stale_nav, "ObservationStale(uint256)")
    else:
        assert results[ARBITRUM].get("nav_after_fee_only") == received


def _check_hub_usdc_roundtrip(run: Run):
    run.hub.observe("hub_share_decimals", run.vault.decimals())
    results = run.relay()
    scale = 10 ** (results[ARBITRUM].get("hub_share_decimals") - USDC.decimals)
    assert scale > 1
    run.advance(60)
    run.hub.add_call(run.vault.withdraw(Amount(1), OWNER, OWNER), from_=OWNER)
    run.hub.observe("after_one_raw_unit", run.hub_asset.balance_of(OWNER))
    run.hub.observe("shares_after_one_raw_unit", run.vault.balance_of(OWNER))
    remaining_shares = Shares((USDC.deposit_amount - 1) * scale)
    run.hub.add_call(run.vault.redeem(remaining_shares, OWNER, OWNER), from_=OWNER)
    run.hub.observe("usdc_recovered", run.hub_asset.balance_of(OWNER))
    run.hub.observe("hub_usdc_remaining", run.hub_asset.balance_of(run.vault.address))
    results = run.relay()
    assert results[ARBITRUM].get("after_one_raw_unit") == 1
    assert results[ARBITRUM].get("shares_after_one_raw_unit") == remaining_shares
    assert results[ARBITRUM].get("usdc_recovered") == USDC.deposit_amount
    assert results[ARBITRUM].get("hub_usdc_remaining") == 0


def _check_spoke_usdc_rounding(run: Run, web3):
    ctx = _ctx(web3, HYPEREVM, USDC.blocks[1])
    token = ERC20(ctx, USDC.tokens[1])
    vault = run.remote_vault
    holder = run.lane.executor_address
    run.spoke_sim.with_erc20_balance(
        token.address,
        holder,
        USDC.deposit_amount,
        slot=erc20_balance_slot(web3, token.address, block=USDC.blocks[1]),
    )
    run.spoke_sim.add_call(
        token.approve(vault.address, USDC.deposit_amount), from_=holder
    )
    run.spoke_sim.add_call(vault.deposit(USDC.deposit_amount, holder), from_=holder)
    run.spoke_sim.observe("spoke_share_decimals", vault.decimals())
    run.spoke_sim.observe("spoke_deposited_shares", vault.balance_of(holder))
    results = run.relay()
    scale = 10 ** (results[HYPEREVM].get("spoke_share_decimals") - USDC.decimals)
    assert scale > 1
    assert (
        results[HYPEREVM].get("spoke_deposited_shares") == USDC.deposit_amount * scale
    )
    run.advance(60)
    run.spoke_sim.add_call(
        vault.redeem(Shares(scale + 1), holder, holder), from_=holder
    )
    run.spoke_sim.observe("spoke_fractional_redemption", token.balance_of(holder))
    run.spoke_sim.add_call(
        vault.redeem(Shares((USDC.deposit_amount - 1) * scale - 1), holder, holder),
        from_=holder,
    )
    run.spoke_sim.observe("spoke_recovered", token.balance_of(holder))
    run.spoke_sim.observe("spoke_rounding_dust", token.balance_of(vault.address))
    run.spoke_sim.observe("spoke_final_shares", vault.balance_of(holder))
    results = run.relay()
    assert results[HYPEREVM].get("spoke_fractional_redemption") == 1
    assert results[HYPEREVM].get("spoke_final_shares") == 0
    assert results[HYPEREVM].get("spoke_recovered") == USDC.deposit_amount - 1
    assert results[HYPEREVM].get("spoke_rounding_dust") == 1


def _prepare_asset_run(
    web3_arb,
    web3_hyperevm,
    asset: RehearsalAsset,
    *,
    whitelist_dispatcher=True,
    transport: CcipTransport | None = None,
    generation: str = "pilot",
) -> Run:
    if generation not in GENERATIONS:
        raise ValueError(f"unknown generation {generation!r}")
    artifacts = _compile_crosschain_contracts()
    arb_ctx = _ctx(web3_arb, ARBITRUM, asset.blocks[0])
    hyper_ctx = _ctx(web3_hyperevm, HYPEREVM, asset.blocks[1])
    arb_fusion_factory = FusionFactory(arb_ctx, ARBITRUM_FUSION_FACTORY)
    hyper_fusion_factory = FusionFactory(hyper_ctx, HYPEREVM_FUSION_FACTORY)
    hub_clone = arb_fusion_factory.clone(
        f"Crosschain {asset.symbol} Hub Rehearsal",
        f"xc{asset.symbol}-ARB",
        asset.tokens[0],
        1,
        OWNER,
    )
    spoke_clone = hyper_fusion_factory.clone(
        f"Crosschain {asset.symbol} Spoke Rehearsal",
        f"xc{asset.symbol}-HYPE",
        asset.tokens[1],
        1,
        OWNER,
    )
    hub_instance = hub_clone.call()
    spoke_instance = spoke_clone.call()

    user_salt = Web3.keccak(text="ipor-fusion.py Arbitrum HyperEVM full lifecycle")
    simulator = CrosschainSimulator(transport or _asset_transport(asset))
    hub = simulator.add_chain(
        ARBITRUM,
        web3_arb,
        block=asset.blocks[0],
        vault=hub_instance.plasma_vault,
        alpha=OWNER,
    )
    spoke = simulator.add_chain(HYPEREVM, web3_hyperevm, block=asset.blocks[1])
    hub.with_block_time_shift(60).with_block_override(gasLimit=30_000_000)
    spoke.with_block_override(gasLimit=30_000_000)
    creator = CREATOR if generation == "v2" else OWNER
    if generation == "source":
        # A fresh factory pair exists only inside the simulation, so its reads
        # are observations, not live calls.
        factory_address = _deploy_source_generation(
            simulator, hub, spoke, arb_ctx, hyper_ctx, artifacts, asset
        )
        # The lifecycle starts in a fresh block, as on the deployed path: the
        # vault clone needs ~9 M gas and, queued uncapped behind the governance
        # calls on HyperEVM, it was starved.
        hub.next_block(time_shift_seconds=1).with_block_override(gasLimit=30_000_000)
        spoke.next_block(time_shift_seconds=1).with_block_override(gasLimit=30_000_000)
        ccip_factory = CcipCrosschainFactory(arb_ctx, factory_address)
        simulator.observe(
            ARBITRUM,
            "source_executor_address",
            ccip_factory.compute_executor_address(OWNER, user_salt),
        )
        simulator.observe(
            ARBITRUM, "source_executor_route", ccip_factory.ccip_route(HYPEREVM)
        )
        results = simulator.relay()
        results[ARBITRUM].raise_for_failure()
        executor = results[ARBITRUM].get("source_executor_address")
        route = replace(results[ARBITRUM].get("source_executor_route"), peer=executor)
        first_fuse_nonce = len(CORE_LIBRARIES) + 1
    else:
        factory_address = FACTORY_V2 if generation == "v2" else FACTORY
        ccip_factory = CcipCrosschainFactory(arb_ctx, factory_address)
        executor = ccip_factory.compute_executor_address(creator, user_salt).call()
        route = replace(ccip_factory.ccip_route(HYPEREVM).call(), peer=executor)
        if asset.configure_factory and generation == "pilot":
            _enable_factory_asset(hub, arb_ctx, asset, index=0)
            _enable_factory_asset(spoke, hyper_ctx, asset, index=1)
        hub.with_state_override(SIMULATED_DEPLOYER, balance=hex(10**18), nonce=hex(0))
        first_fuse_nonce = 0

    fuse_addresses = {
        contract.name: hub.deploy_contract(
            artifacts[contract].init_code(
                ("uint256",),
                (CROSSCHAIN_MARKET,),
            ),
            from_=SIMULATED_DEPLOYER,
            nonce=first_fuse_nonce + offset,
            label=f"deploy_{contract.name}",
        )
        for offset, contract in enumerate(FUSE_CONTRACTS)
    }
    hub.add_call(hub_clone, from_=OWNER, label="clone_hub_vault")
    spoke.add_call(spoke_clone, from_=OWNER, label="clone_spoke_vault")
    spoke.next_block(time_shift_seconds=1).with_block_override(gasLimit=30_000_000)

    hub_access = AccessManager(arb_ctx, hub_instance.access_manager)
    for role in (
        Roles.ATOMIST_ROLE,
        Roles.ALPHA_ROLE,
        Roles.FUSE_MANAGER_ROLE,
        Roles.UPDATE_MARKETS_BALANCES_ROLE,
        Roles.PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE,
        Roles.WHITELIST_ROLE,
    ):
        hub.add_call(
            hub_access.grant_role(role, OWNER, 0),
            from_=OWNER,
            label=f"grant_hub_{role.name.lower()}",
        )
    spoke_access = AccessManager(hyper_ctx, spoke_instance.access_manager)
    spoke.add_call(
        spoke_access.grant_role(Roles.ATOMIST_ROLE, OWNER, 0),
        from_=OWNER,
        label="grant_spoke_atomist_role",
    )
    if whitelist_dispatcher:
        spoke.add_call(
            spoke_access.grant_role(Roles.WHITELIST_ROLE, executor, 0),
            from_=OWNER,
            label="grant_dispatcher_spoke_whitelist",
        )
    spoke.add_call(
        spoke_access.grant_role(Roles.PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE, OWNER, 0),
        from_=OWNER,
        label="grant_spoke_price_manager_role",
    )
    spoke.add_call(
        PriceOracleMiddlewareManager(
            hyper_ctx, spoke_instance.price_manager
        ).set_assets_price_sources([asset.tokens[1]], [asset.feeds[1]]),
        from_=OWNER,
        label="configure_spoke_asset_price",
    )

    hub.add_call(
        PriceOracleMiddlewareManager(
            arb_ctx, hub_instance.price_manager
        ).set_assets_price_sources([asset.tokens[0]], [asset.feeds[0]]),
        from_=OWNER,
        label="configure_hub_asset_price",
    )
    hub.add_call(
        ccip_factory.create_executor(
            user_salt,
            asset.asset_id,
            hub_instance.plasma_vault,
            _safety_config(),
        ),
        from_=creator,
        label="create_executor",
    )

    hub_vault = PlasmaVault(arb_ctx, hub_instance.plasma_vault)
    hub.add_call(
        hub_vault.add_fuses(
            [
                fuse_addresses["CcipCrosschainSupplyFuse"],
                fuse_addresses["CcipCrosschainCommandFuse"],
                fuse_addresses["CrosschainClaimFuse"],
            ]
        ),
        from_=OWNER,
        label="add_crosschain_fuses",
    )
    hub.add_call(
        hub_vault.add_balance_fuse(
            CROSSCHAIN_MARKET, fuse_addresses["CrosschainBalanceFuse"]
        ),
        from_=OWNER,
        label="add_crosschain_balance_fuse",
    )
    hub.add_call(
        hub_vault.grant_market_substrates(
            CROSSCHAIN_MARKET,
            [
                CrosschainSubstrateLib.executor_substrate(executor),
                CrosschainSubstrateLib.remote_vault_substrate(
                    HYPEREVM, spoke_instance.plasma_vault
                ),
            ],
        ),
        from_=OWNER,
        label="grant_crosschain_substrates",
    )

    token = ERC20(arb_ctx, asset.tokens[0])
    hub.with_erc20_balance(
        asset.tokens[0],
        OWNER,
        asset.deposit_amount,
        slot=erc20_balance_slot(web3_arb, asset.tokens[0], block=asset.blocks[0]),
    )
    hub.add_call(
        token.approve(hub_instance.plasma_vault, asset.deposit_amount),
        from_=OWNER,
        label="approve_hub_deposit",
    )
    hub.add_call(
        hub_vault.deposit(asset.deposit_amount, OWNER),
        from_=OWNER,
        label="deposit_hub_vault",
    )

    lane = CcipLane(
        executor=CcipCrosschainExecutor(arb_ctx, executor),
        dispatcher=CcipCrosschainDispatcher(hyper_ctx, executor),
        spoke_chain_id=HYPEREVM,
        fuses=LaneFuses(
            supply=fuse_addresses["CcipCrosschainSupplyFuse"],
            command=fuse_addresses["CcipCrosschainCommandFuse"],
            claim=fuse_addresses["CrosschainClaimFuse"],
        ),
        route=route,
        decimal_conversion_rate=1,
    )
    hub.add_call(
        hub_vault.execute(
            [
                lane.command_fuse.enter(
                    CcipCommandType.REGISTER_ROUTE,
                    executor=executor,
                    chain_id=HYPEREVM,
                    route=route,
                ),
                lane.command_fuse.create_dispatcher(
                    executor=executor,
                    chain_id=HYPEREVM,
                    plasma_vaults=[spoke_instance.plasma_vault],
                    send=lane.default_command_send(),
                ),
            ]
        ),
        from_=OWNER,
        label="configure_lane_and_request_dispatcher",
    )
    # The hub factory pays the deployment ticket's CCIP fee, the spoke factory
    # the acknowledgement's; the pilot factory holds HYPE live, a fresh one none.
    simulator.fund_native(ARBITRUM, factory_address, route.max_fee)
    simulator.fund_native(HYPEREVM, factory_address, 10**18)
    simulator.fund_native(ARBITRUM, OWNER, 10**20)
    simulator.fund_native(ARBITRUM, creator, 10**20)
    simulator.fund_native(ARBITRUM, executor, 10**18)
    simulator.fund_native(HYPEREVM, OWNER, 10**20)
    simulator.fund_native(HYPEREVM, executor, 10**18)
    hub.next_block(time_shift_seconds=1).with_block_override(gasLimit=30_000_000)
    spoke.next_block(time_shift_seconds=1).with_block_override(gasLimit=30_000_000)

    results = simulator.relay()
    for result in results.values():
        result.raise_for_failure()

    simulator.observe(
        ARBITRUM,
        "dispatcher_ready",
        CcipCrosschainExecutor(arb_ctx, executor).has_dispatcher(HYPEREVM),
    )
    simulator.observe(
        HYPEREVM,
        "dispatcher_registered",
        CcipCrosschainFactory(hyper_ctx, factory_address).is_dispatcher(executor),
    )
    simulator.observe(ARBITRUM, "hub_shares", hub_vault.balance_of(OWNER))
    simulator.observe(
        ARBITRUM,
        "hub_assets",
        token.balance_of(hub_instance.plasma_vault),
    )
    results = simulator.relay()
    for result in results.values():
        result.raise_for_failure()
    assert results[ARBITRUM].get("dispatcher_ready") is True
    assert results[HYPEREVM].get("dispatcher_registered") is True
    share_scale = 10 ** (
        hub_instance.asset_decimals - hub_instance.underlying_token_decimals
    )
    assert hub_instance.underlying_token_decimals == asset.decimals
    assert spoke_instance.underlying_token_decimals == asset.decimals
    assert results[ARBITRUM].get("hub_shares") == asset.deposit_amount * share_scale
    assert results[ARBITRUM].get("hub_assets") == asset.deposit_amount
    _assert_configured_prices(
        simulator,
        (arb_ctx, hyper_ctx),
        (hub_instance, spoke_instance),
        asset,
        factory_address,
    )

    hub_start = hub.current_time
    return Run(
        hub_chain_id=ARBITRUM,
        spoke_chain_id=HYPEREVM,
        spoke_name="hyperevm",
        hub_vault_address=hub_instance.plasma_vault,
        hub_asset_address=asset.tokens[0],
        market_id=CROSSCHAIN_MARKET,
        owner=OWNER,
        balance_proposer=BALANCE_PROPOSER,
        balance_approver=BALANCE_APPROVER,
        transport_kind=CrosschainTransportKind.CHAINLINK_CCIP,
        lane=lane,
        csim=simulator,
        hub=hub,
        spoke_sim=spoke,
        vault=hub_vault,
        remote_vault=PlasmaVault(hyper_ctx, spoke_instance.plasma_vault),
        hub_asset=token,
        amount=10**asset.decimals + 1,
        hub_start=hub_start,
        staleness_max=_safety_config().balance_staleness_max,
        ccip_debits_sent_amount=generation in NEW_ACCOUNTING,
        attestation_zero_dust_sd=(
            ATTESTATION_ZERO_DUST_SD if generation in NEW_ACCOUNTING else 0
        ),
        min_update_interval=_safety_config().min_update_interval,
    )


def _simulate_factory_deployment(web3_arb, web3_hyperevm, *, big_block: bool):
    arb_ctx = _ctx(web3_arb, ARBITRUM, ARBITRUM_BLOCK)
    hyper_ctx = _ctx(web3_hyperevm, HYPEREVM, HYPEREVM_BLOCK)
    arb_factory = CcipCrosschainFactory(arb_ctx, FACTORY)
    asset_id = Web3.keccak(text="HTEST")
    user_salt = Web3.keccak(text="ipor-fusion.py Arbitrum HyperEVM rehearsal")
    executor = arb_factory.compute_executor_address(OWNER, user_salt).call()
    route = replace(arb_factory.ccip_route(HYPEREVM).call(), peer=executor)
    transport = CcipTransport(
        (
            CcipChain(
                ARBITRUM,
                ARBITRUM_SELECTOR,
                ARBITRUM_ROUTER,
                ASSETS["HTEST"][0],
                18,
                OWNER,
            ),
            CcipChain(
                HYPEREVM,
                HYPEREVM_SELECTOR,
                HYPEREVM_ROUTER,
                ASSETS["HTEST"][1],
                18,
                OWNER,
            ),
        )
    )
    simulator = CrosschainSimulator(transport)
    arb = simulator.add_chain(ARBITRUM, web3_arb, block=ARBITRUM_BLOCK)
    hyper = simulator.add_chain(HYPEREVM, web3_hyperevm, block=HYPEREVM_BLOCK)
    if big_block:
        # CCIP OffRamp transmitters execute in HyperEVM's 30M-gas big blocks.
        hyper.with_block_override(gasLimit=30_000_000)
    simulator.fund_native(ARBITRUM, FACTORY, route.max_fee)
    arb.add_call(
        arb_factory.create_executor(user_salt, asset_id, OWNER, _safety_config()),
        from_=OWNER,
        label="create_executor",
    )
    arb.add_call(
        arb_factory.register_executor_route(executor, HYPEREVM, route),
        from_=OWNER,
        label="register_executor_route",
    )
    arb.add_call(
        arb_factory.request_dispatcher(executor, HYPEREVM, [], route.max_fee),
        from_=OWNER,
        label="request_dispatcher",
    )
    return simulator, simulator.relay(), route, executor, arb_ctx, hyper_ctx


@pytest.mark.parametrize(
    ("web3_fixture", "chain_id", "block", "router", "peer_id", "peer_selector"),
    (
        (
            "web3_arb",
            ARBITRUM,
            ARBITRUM_BLOCK,
            ARBITRUM_ROUTER,
            HYPEREVM,
            HYPEREVM_SELECTOR,
        ),
        (
            "web3_hyperevm",
            HYPEREVM,
            HYPEREVM_BLOCK,
            HYPEREVM_ROUTER,
            ARBITRUM,
            ARBITRUM_SELECTOR,
        ),
    ),
)
def test_ccip_factory_is_ready(
    request,
    web3_fixture,
    chain_id,
    block,
    router,
    peer_id,
    peer_selector,
):
    web3 = request.getfixturevalue(web3_fixture)
    factory = CcipCrosschainFactory(_ctx(web3, chain_id, block), FACTORY)

    assert_pilot_code(web3, chain_id, block)
    assert_pilot_deployment(web3, chain_id)
    assert factory.factory_interface_version().call() == 1
    assert factory.ccip_router().call() == router
    assert factory.creation_restricted().call()
    assert factory.creation_codes_configured().call()
    assert factory.is_allowed_creator(OWNER).call()
    route = factory.ccip_route(peer_id).call()
    assert route.enabled
    assert route.peer == FACTORY
    assert route.chain_selector == peer_selector
    assert factory.chain_id_of_selector(peer_selector).call() == peer_id


def test_hyperevm_one_value_feed_is_ready(web3_hyperevm):
    ctx = _ctx(web3_hyperevm, HYPEREVM, HYPEREVM_BLOCK)
    decimals = Call(
        to=HYPEREVM_ONE_VALUE_FEED,
        data=bytes(Web3.keccak(text="decimals()")[:4]),
        output_types=["uint8"],
        ctx=ctx,
    )
    latest_round_data = Call(
        to=HYPEREVM_ONE_VALUE_FEED,
        data=bytes(Web3.keccak(text="latestRoundData()")[:4]),
        output_types=["uint80", "int256", "uint256", "uint256", "uint80"],
        ctx=ctx,
    )

    assert (
        len(
            web3_hyperevm.eth.get_code(
                HYPEREVM_ONE_VALUE_FEED, block_identifier=HYPEREVM_BLOCK
            )
        )
        > 0
    )
    assert decimals.call() == 8
    assert latest_round_data.call() == (0, 1, 0, 0, 0)


@pytest.mark.parametrize(("symbol", "tokens"), ASSETS.items())
def test_ccip_assets_travel_both_ways(web3_arb, web3_hyperevm, symbol, tokens):
    asset_id = Web3.keccak(text=symbol)
    specs = (
        (
            _ctx(web3_arb, ARBITRUM, ARBITRUM_BLOCK),
            ARBITRUM_ROUTER,
            tokens[0],
            HYPEREVM_SELECTOR,
        ),
        (
            _ctx(web3_hyperevm, HYPEREVM, HYPEREVM_BLOCK),
            HYPEREVM_ROUTER,
            tokens[1],
            ARBITRUM_SELECTOR,
        ),
    )
    for ctx, router, token, peer_selector in specs:
        assert CcipCrosschainFactory(ctx, FACTORY).asset_config(asset_id).call() == (
            token,
            18,
            True,
        )
        lane = ccip_token_lane(ctx, router, token, peer_selector)
        assert lane.message_lane
        assert lane.on_ramp_version is not None
        assert lane.on_ramp_version.startswith("OnRamp 2.")
        assert lane.token_lane


def test_usdc_was_disabled_at_acceptance_pins(web3_arb, web3_hyperevm):
    """The HTEST acceptance pins predate the owner's USDC enablement of
    2026-10-01; `_enable_factory_asset` simulates the governance from here."""
    asset_id = Web3.keccak(text="USDC")
    for ctx in (
        _ctx(web3_arb, ARBITRUM, ARBITRUM_BLOCK),
        _ctx(web3_hyperevm, HYPEREVM, HYPEREVM_BLOCK),
    ):
        assert CcipCrosschainFactory(ctx, FACTORY).asset_config(asset_id).call() == (
            ZERO_ADDRESS,
            0,
            False,
        )


def test_dispatcher_deployment_needs_a_hyperevm_big_block(web3_arb, web3_hyperevm):
    _, results, _, _, _, _ = _simulate_factory_deployment(
        web3_arb, web3_hyperevm, big_block=False
    )
    results[ARBITRUM].raise_for_failure()
    (failure,) = results[HYPEREVM].failed_calls
    assert failure.label is not None and failure.label.startswith("ccip_receive:")
    assert_reverted(failure, "InitializationFailed()")
    assert web3_hyperevm.eth.get_block(HYPEREVM_BLOCK)["gasLimit"] == 3_000_000


def test_simulate_executor_and_dispatcher_deployment(web3_arb, web3_hyperevm):
    simulator, results, route, executor, arb_ctx, hyper_ctx = (
        _simulate_factory_deployment(web3_arb, web3_hyperevm, big_block=True)
    )
    hyper_factory = CcipCrosschainFactory(hyper_ctx, FACTORY)
    for result in results.values():
        result.raise_for_failure()
    assert len(simulator.delivered) == 2
    deployment = next(
        call
        for call in results[HYPEREVM].calls
        if call.label and call.label.startswith("ccip_receive:")
    )
    assert 3_000_000 < deployment.gas_used <= route.message_gas_limit

    simulator.observe(
        ARBITRUM,
        "dispatcher_ready",
        CcipCrosschainExecutor(arb_ctx, executor).has_dispatcher(HYPEREVM),
    )
    simulator.observe(
        HYPEREVM,
        "dispatcher_registered",
        hyper_factory.is_dispatcher(executor),
    )
    results = simulator.relay()
    for result in results.values():
        result.raise_for_failure()
    assert results[ARBITRUM].get("dispatcher_ready") is True
    assert results[HYPEREVM].get("dispatcher_registered") is True
