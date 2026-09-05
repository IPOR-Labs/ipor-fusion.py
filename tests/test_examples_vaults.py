"""Offline guards for the examples/ vault scripts.

These run without a provider: they import each example module (proving import
has no chain side effects) and exercise its pure calldata builders and static
config. This is what keeps the examples from silently going stale against the
SDK even when no RPC is available. The on-chain simulation path is covered
separately by the RPC-gated test_simulate_* suite.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

import pytest
from eth_abi.abi import decode
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core import FusionFactory
from ipor_fusion.core.simulation import SimulatedCallResult, SimulationResult
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount

Loader = Callable[[str], ModuleType]


class TestSimpleAaveV3SupplyBase:
    @pytest.fixture(autouse=True)
    def _mod(self, load_example: Loader) -> None:
        self.mod = load_example("simple_aave_v3_supply_base.py")

    def test_unsigned_clone_calldata_roundtrips(self) -> None:
        calldata = self.mod.unsigned_clone_calldata()
        assert isinstance(calldata, bytes)
        # Selector matches the deployed 6-arg clone(...).
        assert calldata[:4] == FusionFactory.CLONE_SELECTOR
        # All six args decode back to exactly what the example configured.
        args = FusionFactory.decode_clone_calldata(calldata)
        expected = self.mod.clone_args()
        assert args.asset_name == expected["asset_name"]
        assert args.asset_symbol == expected["asset_symbol"]
        assert args.underlying_token == self.mod.BASE_USDC
        assert args.redemption_delay_seconds == expected["redemption_delay_seconds"]
        assert args.owner == self.mod.OWNER
        assert args.dao_fee_package_index == expected["dao_fee_package_index"]

    def test_build_supply_action(self) -> None:
        action = self.mod.build_supply_action(Amount(999_000_000))
        assert isinstance(action, FuseAction)
        assert action.fuse == self.mod.BASE_AAVE_V3_SUPPLY_FUSE
        # Decode enter((address,uint256,uint256)) and check every field, so a
        # swapped asset, amount, or e_mode can't slip past this guard.
        asset, amount, e_mode = decode(
            ["address", "uint256", "uint256"], action.data[4:]
        )
        assert Web3.to_checksum_address(asset) == self.mod.BASE_USDC
        assert amount == 999_000_000
        assert e_mode == 0

    def test_usdc_substrate_is_padded_address(self) -> None:
        substrate = self.mod._address_substrate(self.mod.BASE_USDC)
        assert len(substrate) == 32
        # 12 leading zero bytes, then the 20-byte address in the low bytes.
        assert substrate[:12] == bytes(12)
        assert substrate.endswith(bytes.fromhex(self.mod.BASE_USDC[2:]))

    def test_supply_and_balance_fuses_are_canonical(self) -> None:
        # Pin the addresses to the mainnet-base-fusion registry values, so a
        # silent edit away from the canonical fuses fails here rather than only
        # at simulation time.
        assert self.mod.BASE_AAVE_V3_SUPPLY_FUSE == Web3.to_checksum_address(
            "0x26fD6EF391E98C78CfCA27e00c3d15be4D941625"
        )
        assert self.mod.BASE_AAVE_V3_BALANCE_FUSE == Web3.to_checksum_address(
            "0x952573Ec1B6895a88a95CA523097083d4da4D8e5"
        )
        assert self.mod.BASE_FUSION_FACTORY == Web3.to_checksum_address(
            "0x1455717668fA96534f675856347A973fA907e922"
        )
        assert self.mod.BASE_USDC == Web3.to_checksum_address(
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        )

    def test_connected_web3_exits_without_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BASE_PROVIDER_URL", raising=False)
        with pytest.raises(SystemExit):
            self.mod._connected_web3()

    def test_assert_all_success_raises_on_failure(self) -> None:
        failed = SimulatedCallResult(
            label="clone",
            success=False,
            return_data=HexBytes(b""),
            gas_used=0,
            error="execution reverted",
            logs=[],
            decoded=None,
        )
        result = SimulationResult(
            success=False,
            all_success=False,
            revert_reason="execution reverted",
            gas_used=0,
            execute_logs=[],
            observations={},
            calls=[failed],
            failed_calls=[failed],
        )
        with pytest.raises(AssertionError, match="simulation calls failed"):
            self.mod._assert_all_success(result)
