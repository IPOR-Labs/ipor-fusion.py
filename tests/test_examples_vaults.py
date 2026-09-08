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
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core import FusionFactory
from ipor_fusion.core.simulation import SimulatedCallResult, SimulationResult
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import MAX_UINT256, Amount

Loader = Callable[[str], ModuleType]


class _ExampleOfflineChecks:
    """Offline guards shared by every from-scratch vault example.

    A subclass sets ``module_filename`` (the example to load) and
    ``underlying_attr`` (the module constant naming the vault's accounting-asset
    address); the autouse fixture here loads the module into ``self.mod``, and
    example-specific encoding/config tests stay in the subclass. Not named
    ``Test*`` so pytest does not collect it on its own -- it has no module to
    load until a subclass supplies one.
    """

    mod: ModuleType
    module_filename: str
    underlying_attr: str

    @pytest.fixture(autouse=True)
    def _mod(self, load_example: Loader) -> None:
        self.mod = load_example(self.module_filename)

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
        assert args.underlying_token == getattr(self.mod, self.underlying_attr)
        assert args.redemption_delay_seconds == expected["redemption_delay_seconds"]
        assert args.owner == self.mod.OWNER
        assert args.dao_fee_package_index == expected["dao_fee_package_index"]

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


class TestSimpleAaveV3SupplyBase(_ExampleOfflineChecks):
    module_filename = "simple_aave_v3_supply_base.py"
    underlying_attr = "BASE_USDC"

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


class TestAdvancedEulerV2CreditMarketBase(_ExampleOfflineChecks):
    module_filename = "advanced_euler_v2_credit_market_base.py"
    underlying_attr = "BASE_WETH"

    def test_market_substrates_pack_minimal_capabilities(self) -> None:
        # The typed substrate layout is
        # eulerVault<<96 | isCollateral<<88 | canBorrow<<80 | subAccount<<72.
        # Decode both and assert the *minimal* flags: cbETH collateral-only,
        # WETH borrow-only -- a widened flag (e.g. cbETH borrowable) fails here.
        substrates = self.mod.market_substrates()
        assert len(substrates) == 2
        decoded = {}
        for s in substrates:
            assert len(s) == 32
            val = int.from_bytes(s, "big")
            vault = Web3.to_checksum_address(f"0x{(val >> 96) & ((1 << 160) - 1):040x}")
            decoded[vault] = {
                "is_collateral": (val >> 88) & 0xFF,
                "can_borrow": (val >> 80) & 0xFF,
                "sub_account": (val >> 72) & 0xFF,
            }
        assert decoded[self.mod.EVAULT_CBETH] == {
            "is_collateral": 1,
            "can_borrow": 0,
            "sub_account": self.mod.SUB_ACCOUNT,
        }
        assert decoded[self.mod.EVAULT_WETH] == {
            "is_collateral": 0,
            "can_borrow": 1,
            "sub_account": self.mod.SUB_ACCOUNT,
        }

    def test_credit_lifecycle_actions_encode_correctly(self) -> None:
        sub = bytes([self.mod.SUB_ACCOUNT])
        # (builder, expected fuse address, solidity signature, expected decoded tail)
        # The tail excludes the leading eulerVault, which every action targets.
        cases = [
            (
                self.mod.supply_collateral_action(Amount(10)),
                self.mod.EULER_SUPPLY_FUSE,
                "enter((address,uint256,bytes1))",
                self.mod.EVAULT_CBETH,
                (10, sub),
            ),
            (
                self.mod.enable_collateral_action(),
                self.mod.EULER_COLLATERAL_FUSE,
                "enter((address,bytes1))",
                self.mod.EVAULT_CBETH,
                (sub,),
            ),
            (
                self.mod.enable_controller_action(),
                self.mod.EULER_CONTROLLER_FUSE,
                "enter((address,bytes1))",
                self.mod.EVAULT_WETH,
                (sub,),
            ),
            (
                self.mod.borrow_action(Amount(1)),
                self.mod.EULER_BORROW_FUSE,
                "enter((address,uint256,bytes1))",
                self.mod.EVAULT_WETH,
                (1, sub),
            ),
            (
                self.mod.repay_action(MAX_UINT256),
                self.mod.EULER_BORROW_FUSE,
                "exit((address,uint256,bytes1))",
                self.mod.EVAULT_WETH,
                (MAX_UINT256, sub),
            ),
            (
                self.mod.disable_controller_action(),
                self.mod.EULER_CONTROLLER_FUSE,
                "exit((address,bytes1))",
                self.mod.EVAULT_WETH,
                (sub,),
            ),
            (
                self.mod.disable_collateral_action(),
                self.mod.EULER_COLLATERAL_FUSE,
                "exit((address,bytes1))",
                self.mod.EVAULT_CBETH,
                (sub,),
            ),
            (
                self.mod.withdraw_collateral_action(MAX_UINT256),
                self.mod.EULER_SUPPLY_FUSE,
                "exit((address,uint256,bytes1))",
                self.mod.EVAULT_CBETH,
                (MAX_UINT256, sub),
            ),
        ]
        for action, fuse, signature, vault, tail in cases:
            assert isinstance(action, FuseAction)
            assert action.fuse == fuse
            assert action.data[:4] == function_signature_to_4byte_selector(signature)
            tuple_type = signature[signature.index("(") + 1 : -1]
            (decoded_tuple,) = decode([tuple_type], action.data[4:])
            assert Web3.to_checksum_address(decoded_tuple[0]) == vault
            assert decoded_tuple[1:] == tail

    def test_euler_account_is_vault_xor_sub_account(self) -> None:
        vault = Web3.to_checksum_address("0x000000000000000000000000000000000000ff00")
        # XOR the low byte with SUB_ACCOUNT (0x01) -> ...ff01.
        assert self.mod._euler_account(vault) == Web3.to_checksum_address(
            "0x000000000000000000000000000000000000ff01"
        )

    def test_fuses_are_canonical(self) -> None:
        # Pin every address to its mainnet-base-fusion registry value, so a
        # silent drift away from the canonical fuses fails here, not only at
        # simulation time (where add_fuses accepts any address).
        expected = {
            "EULER_SUPPLY_FUSE": "0x598326fcEDE2C1B8E9023a20C18FFf6Dea5306A4",
            "EULER_COLLATERAL_FUSE": "0x12c479f8aB53D4884fc76F803dD24eb8B6D17a94",
            "EULER_CONTROLLER_FUSE": "0x108c8cFB9e00681FfA1fa3b654937E8b3BCd2E64",
            "EULER_BORROW_FUSE": "0x906496F0D4C733275F892b1a6fC92eD56639B379",
            "EULER_BALANCE_FUSE": "0xF8A6AA09bB55f2319113b0DA88883F392e66A5fa",
            "BASE_FUSION_FACTORY": "0x1455717668fA96534f675856347A973fA907e922",
        }
        for name, address in expected.items():
            assert getattr(self.mod, name) == Web3.to_checksum_address(address)

    def test_evault_debt_of_encodes_debtof_call(self) -> None:
        # _evault_debt_of builds calldata with no chain access (ctx may be None),
        # so guard its encoding here rather than only inside the RPC-gated sim: a
        # swapped account/vault or a signature typo would otherwise be invisible.
        account = self.mod.OWNER
        call = self.mod._evault_debt_of(None, self.mod.EVAULT_WETH, account)
        assert call.to == self.mod.EVAULT_WETH
        assert call.data[:4] == function_signature_to_4byte_selector("debtOf(address)")
        # debtOf returns underlying units; a wrong/dropped return type would
        # misdecode the debt read, so pin it too.
        assert call.output_types == ["uint256"]
        (decoded_account,) = decode(["address"], call.data[4:])
        assert Web3.to_checksum_address(decoded_account) == account
