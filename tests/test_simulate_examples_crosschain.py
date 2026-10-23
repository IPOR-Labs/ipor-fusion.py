"""RPC-gated proof for the crosschain CCIP example on the released contracts
(Arbitrum hub, HyperEVM spoke).

The example takes the executor and dispatcher creation codes as input; this
test builds them from a contracts checkout at ``FUSE_SOURCE_REVISION`` (the
release the example pins) with Foundry, linked against the example's
``RELEASE_LIBRARIES``. It also proves that every released contract the example
names runs the bytecode of that build on both chains at the pinned blocks.

Needs ``ARBITRUM_PROVIDER_URL``, ``HYPEREVM_PROVIDER_URL`` and
``IPOR_FUSION_CONTRACTS_DIR`` (plus ``IPOR_FUSION_FOUNDRY_REMAPPINGS`` for a
checkout without ``node_modules``) and ``forge``; each missing source is a skip
unless ``IPOR_FUSION_REQUIRE_LIVE`` names it. General PR CI supplies neither
the HyperEVM RPC nor the checkout, so it skips there.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from _foundry import (
    FoundryContract,
    SolidityArtifact,
    SolidityCompilationError,
    compile_foundry_contracts,
    git_revision,
)
from _live import unavailable
from test_simulate_crosschain_hyperevm import (
    CORE_LIBRARIES,
    DISPATCHER_CONTRACT,
    EXECUTOR_CONTRACT,
    FACTORY_CONTRACT,
    FUSE_CONTRACTS,
    FUSE_SOURCE_REVISION,
)
from web3 import Web3

MODULE = "crosschain_ccip_usdc_arbitrum_hyperevm.py"
CONTRACTS_DIR = "IPOR_FUSION_CONTRACTS_DIR"
PRE_HOOK_CONTRACT = FoundryContract(
    "contracts/handlers/pre_hooks/pre_hooks/CrosschainNavValidationPreHook.sol",
    "CrosschainNavValidationPreHook",
)
#: A library's runtime starts with ``PUSH20 <its own address>`` (the
#: delegatecall-only guard), zero in the compiled artifact.
LIBRARY_ADDRESS_GUARD = slice(1, 21)


@pytest.fixture(scope="module")
def release_artifacts() -> dict[FoundryContract, SolidityArtifact]:
    contracts_dir = os.environ.get(CONTRACTS_DIR)
    if not contracts_dir:
        unavailable(CONTRACTS_DIR, f"{CONTRACTS_DIR} not set")
    if shutil.which("forge") is None:
        unavailable(CONTRACTS_DIR, "forge is not on PATH")
    try:
        revision = git_revision(contracts_dir)
    except (SolidityCompilationError, ValueError) as exc:
        unavailable(CONTRACTS_DIR, f"{CONTRACTS_DIR} is unusable: {exc}")
    if revision != FUSE_SOURCE_REVISION:
        unavailable(
            CONTRACTS_DIR,
            f"{CONTRACTS_DIR} is at {revision}; the example pins {FUSE_SOURCE_REVISION}",
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
            PRE_HOOK_CONTRACT,
        ),
        remappings=remappings,
    )


def _libraries(mod) -> dict[str, str]:
    return {name: address for _, name, address in mod.RELEASE_LIBRARIES}


@pytest.mark.parametrize(
    ("web3_fixture", "block_attr"),
    (
        ("web3_arb", "ARBITRUM_PINNED_BLOCK"),
        ("web3_hyperevm", "HYPEREVM_PINNED_BLOCK"),
    ),
)
def test_deployed_contracts_are_the_release_build(
    request, load_example, release_artifacts, web3_fixture, block_attr
):
    mod = load_example(MODULE)
    web3 = request.getfixturevalue(web3_fixture)
    block = getattr(mod, block_attr)
    libraries = _libraries(mod)

    def deployed(address: str) -> bytes:
        return bytes(web3.eth.get_code(Web3.to_checksum_address(address), block))

    contracts = {
        FACTORY_CONTRACT: mod.CCIP_FACTORY,
        PRE_HOOK_CONTRACT: mod.CROSSCHAIN_NAV_PRE_HOOK,
        **dict(
            zip(
                FUSE_CONTRACTS,
                (
                    mod.CCIP_SUPPLY_FUSE,
                    mod.CCIP_COMMAND_FUSE,
                    mod.CROSSCHAIN_CLAIM_FUSE,
                    mod.CROSSCHAIN_BALANCE_FUSE,
                ),
                strict=True,
            )
        ),
    }
    for contract, address in contracts.items():
        artifact = release_artifacts[contract].link(libraries)
        assert artifact.matches_runtime(deployed(address)), contract.name
    for contract in CORE_LIBRARIES:
        address = libraries[contract.name]
        code = bytearray(deployed(address))
        assert bytes(code[LIBRARY_ADDRESS_GUARD]) == bytes.fromhex(address[2:])
        code[LIBRARY_ADDRESS_GUARD] = bytes(20)
        expected = release_artifacts[contract].link(libraries).runtime_code
        assert bytes(code) == expected, contract.name


def test_crosschain_ccip_usdc_arbitrum_hyperevm_simulation(
    web3_arb, web3_hyperevm, load_example, release_artifacts
):
    mod = load_example(MODULE)
    libraries = _libraries(mod)
    codes = mod.CreationCodes(
        executor=release_artifacts[EXECUTOR_CONTRACT].link(libraries).creation_code,
        dispatcher=release_artifacts[DISPATCHER_CONTRACT].link(libraries).creation_code,
    )

    numbers = mod.run_simulation(web3_arb, web3_hyperevm, codes)

    assert numbers["vault_usdc_after_deposit"] == mod.DEPOSIT_AMOUNT
    assert numbers["sent"] == mod.SUPPLY_AMOUNT
    assert 0 < numbers["credited"] <= numbers["sent"]
    assert numbers["spoke_shares"] > 0
    assert 0 < numbers["remote_idle"] <= numbers["credited"]
    assert numbers["attested_initial"] == numbers["credited"]
    assert numbers["attested_redeemed"] == numbers["remote_idle"]
    assert numbers["attested_residue"] == 0
    assert numbers["settled_after_return"] == 0
    assert 0 < numbers["idle"] <= numbers["remote_idle"]
    assert numbers["round_trip_cost"] == numbers["sent"] - numbers["idle"]
    assert (
        numbers["vault_usdc_after"]
        == numbers["vault_usdc_after_deposit"] - numbers["round_trip_cost"]
    )
