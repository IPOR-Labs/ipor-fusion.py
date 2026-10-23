"""RPC-gated runs of the HyperCore example on the official HyperEVM deployment.

Both run at the example's pinned block. The wiring test needs only
``HYPEREVM_PROVIDER_URL``. The Core-leg test also needs a contracts checkout at
the release the official HyperCore set was built from (revision
``build.source_revision`` of ``fixtures/hypercore_official.json``;
``IPOR_FUSION_HYPERCORE_SOURCE_DIR``, plus ``IPOR_FUSION_FOUNDRY_REMAPPINGS``
for a checkout without ``node_modules``) and ``forge``: it compiles the fuses,
hooks and pending reader with the precompiles redirected, proves the rest is
the deployed bytecode, and answers the precompiles from the example's
``core_ledger()``.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest
from _hypercore_model import HyperCoreSimulationModel, market55_model
from _hypercore_shadow import compile_hypercore_shadow_runtimes
from _live import archive_context, unavailable
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion import StateOverrideProvider, is_simulate_v1_supported
from ipor_fusion.hypercore import (
    HyperCoreAccountMarginSummary,
    HyperCorePosition,
    HyperCoreSpotBalance,
    precompile_index_of,
)

EXAMPLE = "hypercore_vault_official_deployment_hyperevm.py"
OFFICIAL = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_official.json").read_text()
)
PROVIDER = "HYPEREVM_PROVIDER_URL"
SOURCE = "IPOR_FUSION_HYPERCORE_SOURCE_DIR"


@pytest.fixture
def hyper_web3(load_example) -> Web3:
    mod = load_example(EXAMPLE)
    web3 = archive_context(PROVIDER, mod.PINNED_BLOCK, mod.FUSION_FACTORY).web3
    if not is_simulate_v1_supported(web3):
        unavailable(PROVIDER, f"{PROVIDER} does not implement eth_simulateV1")
    return web3


class _CoreStandIn:
    """The example's ``CoreStandIn`` over the test-only HyperCore model."""

    def __init__(
        self, runtimes: Mapping[ChecksumAddress, bytes], read_index: int
    ) -> None:
        self._runtimes = runtimes
        self._read_index = read_index

    def runtimes(self) -> Mapping[ChecksumAddress, bytes]:
        return self._runtimes

    def answers(self, vault: ChecksumAddress, core) -> StateOverrideProvider:
        base = market55_model(vault)
        model = HyperCoreSimulationModel(
            l1_block_number=base.l1_block_number,
            core_users={vault: core.account_exists},
            spot_balances={(vault, 0): HyperCoreSpotBalance(core.spot_usdc_wei, 0, 0)},
            account_summaries={
                (0, vault): HyperCoreAccountMarginSummary(0, 0, 0, 0),
                (1, vault): HyperCoreAccountMarginSummary(
                    core.xyz_account_value_usd6, 0, 0, core.xyz_account_value_usd6
                ),
            },
            token_infos=base.token_infos,
            perp_asset_infos=base.perp_asset_infos,
            positions={
                (vault, self._read_index): HyperCorePosition(
                    core.position_szi, 0, 0, 1, False
                )
            },
            mark_prices={self._read_index: core.mark_px},
            oracle_prices={self._read_index: core.mark_px},
        )
        return model.provider()


def test_hypercore_example_wires_the_official_contracts(hyper_web3, load_example):
    mod = load_example(EXAMPLE)
    outcome = mod.run_simulation(hyper_web3)
    assert outcome.result.all_success
    assert [step.name for step in outcome.steps][0] == "bridge EVM -> Core"


def test_hypercore_example_core_leg_on_redirected_precompiles(hyper_web3, load_example):
    mod = load_example(EXAMPLE)
    source_dir = os.environ.get(SOURCE)
    if not source_dir:
        unavailable(SOURCE, f"{SOURCE} not set")
    if shutil.which("forge") is None:
        unavailable(SOURCE, "forge is not installed")
    remappings = tuple(
        item
        for item in os.environ.get("IPOR_FUSION_FOUNDRY_REMAPPINGS", "").split(";")
        if item
    )
    addresses = {
        name: Web3.to_checksum_address(address)
        for name, address in OFFICIAL["contracts"].items()
    }
    runtimes = compile_hypercore_shadow_runtimes(
        Path(source_dir),
        hyper_web3,
        addresses,
        mod.PINNED_BLOCK,
        remappings,
        pending_reader=True,
        optimizer_runs=OFFICIAL["build"]["optimizer_runs"],
    )
    stand_in = _CoreStandIn(runtimes, precompile_index_of(mod.XYZ_NVDA))

    outcome = mod.run_simulation(hyper_web3, core=stand_in)

    assert outcome.result.all_success
    assert len(outcome.steps) == 6
