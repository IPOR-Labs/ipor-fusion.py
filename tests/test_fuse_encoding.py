"""Unit tests for fuse encoding — pure functions, no Docker or blockchain needed."""

import pytest
from eth_abi import decode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.fuses.aave_v3 import AaveV3BorrowFuse, AaveV3SupplyFuse
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.compound_v3 import CompoundV3SupplyFuse
from ipor_fusion.fuses.erc4626 import Erc4626SupplyFuse
from ipor_fusion.fuses.fluid_instadapp import (
    FluidInstadappStakingFuse,
    FluidInstadappSupplyFuse,
)
from ipor_fusion.fuses.gearbox_v3 import GearboxStakeFuse, GearboxSupplyFuse
from ipor_fusion.fuses.morpho import (
    MorphoBorrowFuse,
    MorphoClaimFuse,
    MorphoCollateralFuse,
    MorphoFlashLoanFuse,
    MorphoSupplyFuse,
)
from ipor_fusion.fuses.ramses_v2 import (
    RamsesClaimFuse,
    RamsesV2CollectFuse,
    RamsesV2ModifyPositionFuse,
    RamsesV2NewPositionFuse,
)
from ipor_fusion.fuses.uniswap_v3 import (
    UniswapV3CollectFuse,
    UniswapV3ModifyPositionFuse,
    UniswapV3NewPositionFuse,
    UniswapV3SwapFuse,
)
from ipor_fusion.fuses.universal import UniversalTokenSwapperFuse
from ipor_fusion.types import MAX_UINT256

# Deterministic test addresses
FUSE_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
FUSE_ADDR_2 = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
TOKEN_A = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
TOKEN_B = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
VAULT_ADDR = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")

# Lowercase versions for comparing against eth_abi.decode output
TOKEN_A_LOW = str(TOKEN_A).lower()
TOKEN_B_LOW = str(TOKEN_B).lower()
VAULT_ADDR_LOW = str(VAULT_ADDR).lower()
FUSE_ADDR_LOW = str(FUSE_ADDR).lower()
FUSE_ADDR_2_LOW = str(FUSE_ADDR_2).lower()

MARKET_ID = "a" * 64  # 32-byte hex string without 0x prefix


def _selector(sig: str) -> bytes:
    return function_signature_to_4byte_selector(sig)


# ── FuseAction ──────────────────────────────────────────────────────────


class TestFuseAction:
    def test_encode_roundtrip(self):
        action = FuseAction(fuse=FUSE_ADDR, data=b"\x01\x02\x03")
        encoded = action.encode()
        (addr, data) = decode(["address", "bytes"], encoded)
        assert addr.lower() == FUSE_ADDR_LOW
        assert data == b"\x01\x02\x03"

    def test_frozen(self):
        action = FuseAction(fuse=FUSE_ADDR, data=b"\x00")
        with pytest.raises(AttributeError):
            action.fuse = TOKEN_A  # type: ignore[misc]

    def test_str_repr(self):
        action = FuseAction(fuse=FUSE_ADDR, data=b"\xab" * 20)
        s = str(action)
        assert FUSE_ADDR in s
        assert "0x" in s


# ── Fuse base class ────────────────────────────────────────────────────


class TestFuseBase:
    def test_address_required(self):
        with pytest.raises(ValueError, match="required"):
            AaveV3SupplyFuse("")  # type: ignore[arg-type]

    def test_address_property(self):
        fuse = AaveV3SupplyFuse(FUSE_ADDR)
        assert fuse.address == FUSE_ADDR


# ── AaveV3 ──────────────────────────────────────────────────────────────


class TestAaveV3SupplyFuse:
    def test_supply_selector_and_payload(self):
        fuse = AaveV3SupplyFuse(FUSE_ADDR)
        action = fuse.supply(TOKEN_A, 1000, e_mode=1)

        assert action.fuse == FUSE_ADDR
        assert action.data[:4] == _selector("enter((address,uint256,uint256))")
        (addr, amount, e_mode) = decode(
            ["address", "uint256", "uint256"], action.data[4:]
        )
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 1000
        assert e_mode == 1

    def test_supply_default_emode(self):
        action = AaveV3SupplyFuse(FUSE_ADDR).supply(TOKEN_A, 500)
        (_, _, e_mode) = decode(["address", "uint256", "uint256"], action.data[4:])
        assert e_mode == 0

    def test_withdraw(self):
        action = AaveV3SupplyFuse(FUSE_ADDR).withdraw(TOKEN_A, 999)
        assert action.data[:4] == _selector("exit((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 999


class TestAaveV3BorrowFuse:
    def test_borrow(self):
        action = AaveV3BorrowFuse(FUSE_ADDR).borrow(TOKEN_A, 5000)
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 5000

    def test_repay(self):
        action = AaveV3BorrowFuse(FUSE_ADDR).repay(TOKEN_B, 3000)
        assert action.data[:4] == _selector("exit((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_B_LOW
        assert amount == 3000


# ── Morpho ──────────────────────────────────────────────────────────────


class TestMorphoSupplyFuse:
    def test_supply(self):
        action = MorphoSupplyFuse(FUSE_ADDR).supply(MARKET_ID, 10_000)
        assert action.data[:4] == _selector("enter((bytes32,uint256))")
        (mid, amount) = decode(["bytes32", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 10_000

    def test_withdraw(self):
        action = MorphoSupplyFuse(FUSE_ADDR).withdraw(MARKET_ID, 7_000)
        assert action.data[:4] == _selector("exit((bytes32,uint256))")
        (mid, amount) = decode(["bytes32", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 7_000


class TestMorphoCollateralFuse:
    def test_supply_collateral(self):
        action = MorphoCollateralFuse(FUSE_ADDR).supply_collateral(MARKET_ID, 500)
        assert action.data[:4] == _selector("enter((bytes32,uint256))")
        (mid, amount) = decode(["bytes32", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 500

    def test_withdraw_collateral(self):
        action = MorphoCollateralFuse(FUSE_ADDR).withdraw_collateral(MARKET_ID, 300)
        assert action.data[:4] == _selector("exit((bytes32,uint256))")


class TestMorphoBorrowFuse:
    def test_borrow_appends_zero(self):
        action = MorphoBorrowFuse(FUSE_ADDR).borrow(MARKET_ID, 2000)
        assert action.data[:4] == _selector("enter((bytes32,uint256,uint256))")
        (mid, amount, zero) = decode(["bytes32", "uint256", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 2000
        assert zero == 0

    def test_repay_appends_zero(self):
        action = MorphoBorrowFuse(FUSE_ADDR).repay(MARKET_ID, 1500)
        assert action.data[:4] == _selector("exit((bytes32,uint256,uint256))")
        (_, _, zero) = decode(["bytes32", "uint256", "uint256"], action.data[4:])
        assert zero == 0


class TestMorphoFlashLoanFuse:
    def test_flash_loan_encodes_nested_actions(self):
        inner1 = FuseAction(fuse=FUSE_ADDR, data=b"\x01\x02")
        inner2 = FuseAction(fuse=FUSE_ADDR_2, data=b"\x03\x04")
        action = MorphoFlashLoanFuse(FUSE_ADDR).flash_loan(
            TOKEN_A, 50_000, [inner1, inner2]
        )

        assert action.fuse == FUSE_ADDR
        assert action.data[:4] == _selector("enter((address,uint256,bytes))")

        (decoded_tuple,) = decode(["(address,uint256,bytes)"], action.data[4:])
        asset, amount, inner_bytes = decoded_tuple
        assert asset.lower() == TOKEN_A_LOW
        assert amount == 50_000

        # inner_bytes should be ABI-encoded (address,bytes)[]
        (decoded_actions,) = decode(["(address,bytes)[]"], inner_bytes)
        assert len(decoded_actions) == 2
        assert decoded_actions[0][0].lower() == FUSE_ADDR_LOW
        assert decoded_actions[0][1] == b"\x01\x02"
        assert decoded_actions[1][0].lower() == FUSE_ADDR_2_LOW
        assert decoded_actions[1][1] == b"\x03\x04"

    def test_flash_loan_empty_actions(self):
        action = MorphoFlashLoanFuse(FUSE_ADDR).flash_loan(TOKEN_A, 100, [])
        (decoded_tuple,) = decode(["(address,uint256,bytes)"], action.data[4:])
        (decoded_actions,) = decode(["(address,bytes)[]"], decoded_tuple[2])
        assert len(decoded_actions) == 0


class TestMorphoClaimFuse:
    def test_claim_with_proofs(self):
        proof = [
            "0x" + "ab" * 32,
            "0x" + "cd" * 32,
        ]
        action = MorphoClaimFuse(FUSE_ADDR).claim(TOKEN_A, TOKEN_B, 9999, proof)
        assert action.data[:4] == _selector("claim(address,address,uint256,bytes32[])")
        (dist, token, claimable, proofs) = decode(
            ["address", "address", "uint256", "bytes32[]"], action.data[4:]
        )
        assert dist.lower() == TOKEN_A_LOW
        assert token.lower() == TOKEN_B_LOW
        assert claimable == 9999
        assert len(proofs) == 2
        assert proofs[0] == bytes.fromhex("ab" * 32)
        assert proofs[1] == bytes.fromhex("cd" * 32)

    def test_claim_proof_without_0x_prefix(self):
        proof = ["ab" * 32]
        action = MorphoClaimFuse(FUSE_ADDR).claim(TOKEN_A, TOKEN_B, 1, proof)
        (_, _, _, proofs) = decode(
            ["address", "address", "uint256", "bytes32[]"], action.data[4:]
        )
        assert proofs[0] == bytes.fromhex("ab" * 32)


# ── CompoundV3 ──────────────────────────────────────────────────────────


class TestCompoundV3SupplyFuse:
    def test_supply(self):
        action = CompoundV3SupplyFuse(FUSE_ADDR).supply(TOKEN_A, 4000)
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 4000

    def test_withdraw(self):
        action = CompoundV3SupplyFuse(FUSE_ADDR).withdraw(TOKEN_B, 2000)
        assert action.data[:4] == _selector("exit((address,uint256))")


# ── ERC4626 ─────────────────────────────────────────────────────────────


class TestErc4626SupplyFuse:
    def test_supply(self):
        action = Erc4626SupplyFuse(FUSE_ADDR).supply(VAULT_ADDR, 8000)
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 8000

    def test_withdraw(self):
        action = Erc4626SupplyFuse(FUSE_ADDR).withdraw(VAULT_ADDR, 6000)
        assert action.data[:4] == _selector("exit((address,uint256))")


# ── Uniswap V3 ─────────────────────────────────────────────────────────


class TestUniswapV3SwapFuse:
    def test_swap_encodes_packed_path(self):
        action = UniswapV3SwapFuse(FUSE_ADDR).swap(
            TOKEN_A, TOKEN_B, fee=3000, amount_in=10_000, min_amount_out=9_000
        )
        assert action.data[:4] == _selector("enter((uint256,uint256,bytes))")
        (decoded_tuple,) = decode(["(uint256,uint256,bytes)"], action.data[4:])
        amount_in, min_out, path = decoded_tuple
        assert amount_in == 10_000
        assert min_out == 9_000

        expected_path = encode_packed(
            ["address", "uint24", "address"], [TOKEN_A, 3000, TOKEN_B]
        )
        assert path == expected_path


class TestUniswapV3NewPositionFuse:
    def test_new_position(self):
        fuse = UniswapV3NewPositionFuse(FUSE_ADDR)
        action = fuse.new_position(
            TOKEN_A, TOKEN_B, 500, -100, 100, 1000, 2000, 900, 1800, 99999
        )
        assert action.data[:4] == _selector(
            "enter((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256))"
        )
        (decoded_tuple,) = decode(
            [
                "(address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256)"
            ],
            action.data[4:],
        )
        assert decoded_tuple[0].lower() == TOKEN_A_LOW
        assert decoded_tuple[1].lower() == TOKEN_B_LOW
        assert decoded_tuple[2] == 500
        assert decoded_tuple[3] == -100
        assert decoded_tuple[4] == 100
        assert decoded_tuple[5] == 1000

    def test_close_position(self):
        action = UniswapV3NewPositionFuse(FUSE_ADDR).close_position([42, 99])
        assert action.data[:4] == _selector("exit((uint256[]))")
        (decoded_tuple,) = decode(["(uint256[])"], action.data[4:])
        assert list(decoded_tuple[0]) == [42, 99]


class TestUniswapV3ModifyPositionFuse:
    def test_increase_liquidity(self):
        action = UniswapV3ModifyPositionFuse(FUSE_ADDR).increase_liquidity(
            TOKEN_A, TOKEN_B, 7, 1000, 2000, 900, 1800, 99999
        )
        assert action.data[:4] == _selector(
            "enter((address,address,uint256,uint256,uint256,uint256,uint256,uint256))"
        )

    def test_decrease_liquidity(self):
        action = UniswapV3ModifyPositionFuse(FUSE_ADDR).decrease_liquidity(
            token_id=5, liquidity=500, amount0_min=100, amount1_min=200, deadline=88888
        )
        assert action.data[:4] == _selector(
            "exit((uint256,uint128,uint256,uint256,uint256))"
        )
        (decoded_tuple,) = decode(
            ["(uint256,uint128,uint256,uint256,uint256)"], action.data[4:]
        )
        assert decoded_tuple[0] == 5
        assert decoded_tuple[1] == 500
        assert decoded_tuple[2] == 100


class TestUniswapV3CollectFuse:
    def test_collect(self):
        action = UniswapV3CollectFuse(FUSE_ADDR).collect([1, 2, 3])
        assert action.data[:4] == _selector("enter((uint256[]))")
        (decoded_tuple,) = decode(["(uint256[])"], action.data[4:])
        assert list(decoded_tuple[0]) == [1, 2, 3]


# ── Ramses V2 ──────────────────────────────────────────────────────────


class TestRamsesV2NewPositionFuse:
    def test_new_position_has_ve_ram_token_id(self):
        action = RamsesV2NewPositionFuse(FUSE_ADDR).new_position(
            TOKEN_A, TOKEN_B, 500, -100, 100, 1000, 2000, 900, 1800, 99999, 42
        )
        assert action.data[:4] == _selector(
            "enter((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256,uint256))"
        )
        (decoded_tuple,) = decode(
            [
                "(address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256,uint256)"
            ],
            action.data[4:],
        )
        assert decoded_tuple[10] == 42  # ve_ram_token_id

    def test_close_position(self):
        action = RamsesV2NewPositionFuse(FUSE_ADDR).close_position([10])
        assert action.data[:4] == _selector("exit((uint256[]))")


class TestRamsesV2ModifyPositionFuse:
    def test_increase_liquidity(self):
        action = RamsesV2ModifyPositionFuse(FUSE_ADDR).increase_liquidity(
            TOKEN_A, TOKEN_B, 7, 1000, 2000, 900, 1800, 99999
        )
        assert action.data[:4] == _selector(
            "enter((address,address,uint256,uint256,uint256,uint256,uint256,uint256))"
        )

    def test_decrease_liquidity(self):
        action = RamsesV2ModifyPositionFuse(FUSE_ADDR).decrease_liquidity(
            token_id=3, liquidity=400, amount0_min=50, amount1_min=60, deadline=77777
        )
        assert action.data[:4] == _selector(
            "exit((uint256,uint128,uint256,uint256,uint256))"
        )


class TestRamsesV2CollectFuse:
    def test_collect(self):
        action = RamsesV2CollectFuse(FUSE_ADDR).collect([5, 6])
        assert action.data[:4] == _selector("enter((uint256[]))")


class TestRamsesClaimFuse:
    def test_claim(self):
        rewards = [[str(TOKEN_A), str(TOKEN_B)], [str(TOKEN_A)]]
        action = RamsesClaimFuse(FUSE_ADDR).claim([1, 2], rewards)
        assert action.data[:4] == _selector("claim(uint256[],address[][])")
        (ids, token_rewards) = decode(["uint256[]", "address[][]"], action.data[4:])
        assert list(ids) == [1, 2]
        assert len(token_rewards) == 2


# ── Gearbox V3 ─────────────────────────────────────────────────────────

STAKING_FUSE = Web3.to_checksum_address("0x3333333333333333333333333333333333333333")
STAKING_CONTRACT = Web3.to_checksum_address(
    "0x4444444444444444444444444444444444444444"
)
STAKING_FUSE_LOW = str(STAKING_FUSE).lower()
STAKING_CONTRACT_LOW = str(STAKING_CONTRACT).lower()


class TestGearboxSupplyFuse:
    def setup_method(self):
        self.fuse = GearboxSupplyFuse(FUSE_ADDR)

    def test_supply_returns_single_action(self):
        action = self.fuse.supply(VAULT_ADDR, 5000)
        assert action.fuse == FUSE_ADDR

    def test_supply_encoding(self):
        action = self.fuse.supply(VAULT_ADDR, 5000)
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 5000

    def test_withdraw_returns_single_action(self):
        action = self.fuse.withdraw(VAULT_ADDR, 3000)
        assert action.fuse == FUSE_ADDR

    def test_withdraw_encoding(self):
        action = self.fuse.withdraw(VAULT_ADDR, 3000)
        assert action.data[:4] == _selector("exit((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 3000


class TestGearboxStakeFuse:
    def test_stake(self):
        fuse = GearboxStakeFuse(FUSE_ADDR, TOKEN_A)
        action = fuse.stake()
        assert action.data[:4] == _selector("enter((uint256,address))")
        (amount, token) = decode(["uint256", "address"], action.data[4:])
        assert amount == MAX_UINT256
        assert token.lower() == TOKEN_A_LOW

    def test_unstake(self):
        fuse = GearboxStakeFuse(FUSE_ADDR, TOKEN_A)
        action = fuse.unstake(7777)
        assert action.data[:4] == _selector("exit((uint256,address))")
        (amount, token) = decode(["uint256", "address"], action.data[4:])
        assert amount == 7777
        assert token.lower() == TOKEN_A_LOW


# ── FluidInstadapp ─────────────────────────────────────────────────────


class TestFluidInstadappSupplyFuse:
    def setup_method(self):
        self.fuse = FluidInstadappSupplyFuse(FUSE_ADDR)

    def test_supply_encoding(self):
        action = self.fuse.supply(VAULT_ADDR, 2000)
        assert action.fuse == FUSE_ADDR
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 2000

    def test_withdraw_encoding(self):
        action = self.fuse.withdraw(VAULT_ADDR, 1000)
        assert action.fuse == FUSE_ADDR
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 1000


class TestFluidInstadappStakingFuse:
    def setup_method(self):
        self.fuse = FluidInstadappStakingFuse(
            staking_fuse_address=STAKING_FUSE,
            staking_contract_address=STAKING_CONTRACT,
        )

    def test_stake_encoding(self):
        action = self.fuse.stake()
        assert action.fuse == STAKING_FUSE
        (amount, contract) = decode(["uint256", "address"], action.data[4:])
        assert amount == MAX_UINT256
        assert contract.lower() == STAKING_CONTRACT_LOW

    def test_unstake_encoding(self):
        action = self.fuse.unstake(1000)
        assert action.fuse == STAKING_FUSE
        (amount, contract) = decode(["uint256", "address"], action.data[4:])
        assert amount == 1000
        assert contract.lower() == STAKING_CONTRACT_LOW


# ── UniversalTokenSwapper ──────────────────────────────────────────────


class TestUniversalTokenSwapperFuse:
    def test_swap(self):
        targets = [TOKEN_A, TOKEN_B]
        data = [b"\xaa\xbb", b"\xcc\xdd"]
        action = UniversalTokenSwapperFuse(FUSE_ADDR).swap(
            TOKEN_A, TOKEN_B, 5000, targets, data
        )
        assert action.data[:4] == _selector(
            "enter((address,address,uint256,(address[],bytes[])))"
        )
        (decoded_tuple,) = decode(
            ["(address,address,uint256,(address[],bytes[]))"], action.data[4:]
        )
        assert decoded_tuple[0].lower() == TOKEN_A_LOW
        assert decoded_tuple[1].lower() == TOKEN_B_LOW
        assert decoded_tuple[2] == 5000
        assert decoded_tuple[3][0][0].lower() == TOKEN_A_LOW
        assert decoded_tuple[3][0][1].lower() == TOKEN_B_LOW
        assert decoded_tuple[3][1][0] == b"\xaa\xbb"

    def test_swap_empty_targets(self):
        action = UniversalTokenSwapperFuse(FUSE_ADDR).swap(
            TOKEN_A, TOKEN_B, 100, [], []
        )
        (decoded_tuple,) = decode(
            ["(address,address,uint256,(address[],bytes[]))"], action.data[4:]
        )
        assert len(decoded_tuple[3][0]) == 0
        assert len(decoded_tuple[3][1]) == 0
