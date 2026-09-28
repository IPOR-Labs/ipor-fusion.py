"""Pinned Arbitrum-to-HyperEVM CCIP factory and deployment rehearsal.

The module requires both RPCs and skips in CI until ``HYPEREVM_PROVIDER_URL``
is configured there.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from _foundry import FoundryContract, compile_foundry_contracts
from web3 import Web3

from ipor_fusion import (
    CcipChain,
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
    CcipTransport,
    CrosschainSimulator,
    SafetyConfig,
    VaultSimulator,
    Web3Context,
    ccip_token_lane,
)
from ipor_fusion.core.contract import Call
from ipor_fusion.types import ChainId

ARBITRUM = ChainId(42161)
HYPEREVM = ChainId(999)
ARBITRUM_BLOCK = 509_772_283
HYPEREVM_BLOCK = 47_131_895
ARBITRUM_SELECTOR = 4949039107694359620
HYPEREVM_SELECTOR = 2442541497099098535
ARBITRUM_ROUTER = Web3.to_checksum_address("0x141fa059441E0ca23ce184B6A78bafD2A517DdE8")
HYPEREVM_ROUTER = Web3.to_checksum_address("0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec")
FACTORY = Web3.to_checksum_address("0x3a745EaC243ea7563CCbD5890dbCA1b05CEe1e0D")
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
    contracts_dir = os.environ.get("IPOR_FUSION_CONTRACTS_DIR")
    if not contracts_dir:
        pytest.skip("IPOR_FUSION_CONTRACTS_DIR not set")
    remappings = tuple(
        item
        for item in os.environ.get("IPOR_FUSION_FOUNDRY_REMAPPINGS", "").split(";")
        if item
    )
    artifacts = compile_foundry_contracts(
        Path(contracts_dir),
        FUSE_CONTRACTS,
        remappings=remappings,
    )
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

    assert len(web3.eth.get_code(FACTORY, block_identifier=block)) > 0
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


def test_usdc_is_explicitly_disabled(web3_arb, web3_hyperevm):
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
    assert bytes(failure.return_data[:4]) == bytes.fromhex("19b991a8")
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
