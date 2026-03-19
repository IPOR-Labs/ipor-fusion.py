"""Unit tests for fuse encoding — pure functions, no Docker or blockchain needed."""

import pytest
from eth_abi import decode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.fuses.aave_v3 import AaveV3BorrowFuse, AaveV3SupplyFuse
from ipor_fusion.fuses.base import ZERO_ADDRESS, FuseAction
from ipor_fusion.fuses.compound_v3 import CompoundV3SupplyFuse
from ipor_fusion.fuses.erc4626 import ERC4626SupplyFuse
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
        action = fuse.supply(asset=TOKEN_A, amount=1000, e_mode=1)

        assert action.fuse == FUSE_ADDR
        assert action.data[:4] == _selector("enter((address,uint256,uint256))")
        (addr, amount, e_mode) = decode(
            ["address", "uint256", "uint256"], action.data[4:]
        )
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 1000
        assert e_mode == 1

    def test_supply_default_emode(self):
        action = AaveV3SupplyFuse(FUSE_ADDR).supply(asset=TOKEN_A, amount=500)
        (_, _, e_mode) = decode(["address", "uint256", "uint256"], action.data[4:])
        assert e_mode == 0

    def test_withdraw(self):
        action = AaveV3SupplyFuse(FUSE_ADDR).withdraw(asset=TOKEN_A, amount=999)
        assert action.data[:4] == _selector("exit((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 999


class TestAaveV3BorrowFuse:
    def test_borrow(self):
        action = AaveV3BorrowFuse(FUSE_ADDR).borrow(asset=TOKEN_A, amount=5000)
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 5000

    def test_repay(self):
        action = AaveV3BorrowFuse(FUSE_ADDR).repay(asset=TOKEN_B, amount=3000)
        assert action.data[:4] == _selector("exit((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_B_LOW
        assert amount == 3000


# ── Morpho ──────────────────────────────────────────────────────────────


class TestMorphoSupplyFuse:
    def test_supply(self):
        action = MorphoSupplyFuse(FUSE_ADDR).supply(market_id=MARKET_ID, amount=10_000)
        assert action.data[:4] == _selector("enter((bytes32,uint256))")
        (mid, amount) = decode(["bytes32", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 10_000

    def test_withdraw(self):
        action = MorphoSupplyFuse(FUSE_ADDR).withdraw(market_id=MARKET_ID, amount=7_000)
        assert action.data[:4] == _selector("exit((bytes32,uint256))")
        (mid, amount) = decode(["bytes32", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 7_000


class TestMorphoCollateralFuse:
    def test_supply_collateral(self):
        action = MorphoCollateralFuse(FUSE_ADDR).supply_collateral(
            market_id=MARKET_ID, amount=500
        )
        assert action.data[:4] == _selector("enter((bytes32,uint256))")
        (mid, amount) = decode(["bytes32", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 500

    def test_withdraw_collateral(self):
        action = MorphoCollateralFuse(FUSE_ADDR).withdraw_collateral(
            market_id=MARKET_ID, amount=300
        )
        assert action.data[:4] == _selector("exit((bytes32,uint256))")


class TestMorphoBorrowFuse:
    def test_borrow_appends_zero(self):
        action = MorphoBorrowFuse(FUSE_ADDR).borrow(market_id=MARKET_ID, amount=2000)
        assert action.data[:4] == _selector("enter((bytes32,uint256,uint256))")
        (mid, amount, zero) = decode(["bytes32", "uint256", "uint256"], action.data[4:])
        assert mid == bytes.fromhex(MARKET_ID)
        assert amount == 2000
        assert zero == 0

    def test_repay_appends_zero(self):
        action = MorphoBorrowFuse(FUSE_ADDR).repay(market_id=MARKET_ID, amount=1500)
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
        action = MorphoClaimFuse(FUSE_ADDR).claim(
            universal_rewards_distributor=TOKEN_A,
            rewards_token=TOKEN_B,
            claimable=9999,
            proof=proof,
        )
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
        action = MorphoClaimFuse(FUSE_ADDR).claim(
            universal_rewards_distributor=TOKEN_A,
            rewards_token=TOKEN_B,
            claimable=1,
            proof=proof,
        )
        (_, _, _, proofs) = decode(
            ["address", "address", "uint256", "bytes32[]"], action.data[4:]
        )
        assert proofs[0] == bytes.fromhex("ab" * 32)


# ── CompoundV3 ──────────────────────────────────────────────────────────


class TestCompoundV3SupplyFuse:
    def test_supply(self):
        action = CompoundV3SupplyFuse(FUSE_ADDR).supply(asset=TOKEN_A, amount=4000)
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == TOKEN_A_LOW
        assert amount == 4000

    def test_withdraw(self):
        action = CompoundV3SupplyFuse(FUSE_ADDR).withdraw(asset=TOKEN_B, amount=2000)
        assert action.data[:4] == _selector("exit((address,uint256))")


# ── ERC4626 ─────────────────────────────────────────────────────────────


class TestERC4626SupplyFuse:
    def test_supply(self):
        action = ERC4626SupplyFuse(FUSE_ADDR).supply(
            vault_address=VAULT_ADDR, amount=8000
        )
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 8000

    def test_withdraw(self):
        action = ERC4626SupplyFuse(FUSE_ADDR).withdraw(
            vault_address=VAULT_ADDR, amount=6000
        )
        assert action.data[:4] == _selector("exit((address,uint256))")


# ── Uniswap V3 ─────────────────────────────────────────────────────────


class TestUniswapV3SwapFuse:
    def test_swap_encodes_packed_path(self):
        action = UniswapV3SwapFuse(FUSE_ADDR).swap(
            token_in=TOKEN_A,
            token_out=TOKEN_B,
            fee=3000,
            amount_in=10_000,
            min_amount_out=9_000,
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
            token0=TOKEN_A,
            token1=TOKEN_B,
            fee=500,
            tick_lower=-100,
            tick_upper=100,
            amount0_desired=1000,
            amount1_desired=2000,
            amount0_min=900,
            amount1_min=1800,
            deadline=99999,
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
            token0=TOKEN_A,
            token1=TOKEN_B,
            token_id=7,
            amount0_desired=1000,
            amount1_desired=2000,
            amount0_min=900,
            amount1_min=1800,
            deadline=99999,
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
            token0=TOKEN_A,
            token1=TOKEN_B,
            fee=500,
            tick_lower=-100,
            tick_upper=100,
            amount0_desired=1000,
            amount1_desired=2000,
            amount0_min=900,
            amount1_min=1800,
            deadline=99999,
            ve_ram_token_id=42,
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
            token0=TOKEN_A,
            token1=TOKEN_B,
            token_id=7,
            amount0_desired=1000,
            amount1_desired=2000,
            amount0_min=900,
            amount1_min=1800,
            deadline=99999,
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
        action = RamsesClaimFuse(FUSE_ADDR).claim(
            token_ids=[1, 2], token_rewards=rewards
        )
        assert action.data[:4] == _selector("claim(uint256[],address[][])")
        (ids, token_rewards) = decode(["uint256[]", "address[][]"], action.data[4:])
        assert list(ids) == [1, 2]
        assert len(token_rewards) == 2

    def test_claim_rejects_empty_token_ids(self):
        with pytest.raises(ValueError, match="token_ids"):
            RamsesClaimFuse(FUSE_ADDR).claim(
                token_ids=[], token_rewards=[[str(TOKEN_A)]]
            )

    def test_claim_rejects_empty_token_rewards(self):
        with pytest.raises(ValueError, match="token_rewards"):
            RamsesClaimFuse(FUSE_ADDR).claim(token_ids=[1], token_rewards=[])

    def test_claim_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            RamsesClaimFuse(FUSE_ADDR).claim(
                token_ids=[1, 2], token_rewards=[[str(TOKEN_A)]]
            )


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
        action = self.fuse.supply(vault_address=VAULT_ADDR, amount=5000)
        assert action.fuse == FUSE_ADDR

    def test_supply_encoding(self):
        action = self.fuse.supply(vault_address=VAULT_ADDR, amount=5000)
        assert action.data[:4] == _selector("enter((address,uint256))")
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 5000

    def test_withdraw_returns_single_action(self):
        action = self.fuse.withdraw(vault_address=VAULT_ADDR, amount=3000)
        assert action.fuse == FUSE_ADDR

    def test_withdraw_encoding(self):
        action = self.fuse.withdraw(vault_address=VAULT_ADDR, amount=3000)
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
        action = self.fuse.supply(vault_address=VAULT_ADDR, amount=2000)
        assert action.fuse == FUSE_ADDR
        (addr, amount) = decode(["address", "uint256"], action.data[4:])
        assert addr.lower() == VAULT_ADDR_LOW
        assert amount == 2000

    def test_withdraw_encoding(self):
        action = self.fuse.withdraw(vault_address=VAULT_ADDR, amount=1000)
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
            token_in=TOKEN_A,
            token_out=TOKEN_B,
            amount_in=5000,
            targets=targets,
            data=data,
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
            token_in=TOKEN_A, token_out=TOKEN_B, amount_in=100, targets=[], data=[]
        )
        (decoded_tuple,) = decode(
            ["(address,address,uint256,(address[],bytes[]))"], action.data[4:]
        )
        assert len(decoded_tuple[3][0]) == 0
        assert len(decoded_tuple[3][1]) == 0


# ── Input Validation ──────────────────────────────────────────────────

ZERO_ADDR = Web3.to_checksum_address(ZERO_ADDRESS)


class TestAmountValidation:
    """Amount=0 and amount<0 must raise ValueError across fuse types."""

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_aave_supply_rejects_bad_amount(self, bad_amount):
        with pytest.raises(ValueError, match="amount"):
            AaveV3SupplyFuse(FUSE_ADDR).supply(asset=TOKEN_A, amount=bad_amount)

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_aave_borrow_rejects_bad_amount(self, bad_amount):
        with pytest.raises(ValueError, match="amount"):
            AaveV3BorrowFuse(FUSE_ADDR).borrow(asset=TOKEN_A, amount=bad_amount)

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_morpho_supply_rejects_bad_amount(self, bad_amount):
        with pytest.raises(ValueError, match="amount"):
            MorphoSupplyFuse(FUSE_ADDR).supply(market_id=MARKET_ID, amount=bad_amount)

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_compound_supply_rejects_bad_amount(self, bad_amount):
        with pytest.raises(ValueError, match="amount"):
            CompoundV3SupplyFuse(FUSE_ADDR).supply(asset=TOKEN_A, amount=bad_amount)

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_erc4626_supply_rejects_bad_amount(self, bad_amount):
        with pytest.raises(ValueError, match="amount"):
            ERC4626SupplyFuse(FUSE_ADDR).supply(
                vault_address=VAULT_ADDR, amount=bad_amount
            )

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_uniswap_swap_rejects_bad_amount(self, bad_amount):
        with pytest.raises(ValueError, match="amount_in"):
            UniswapV3SwapFuse(FUSE_ADDR).swap(
                token_in=TOKEN_A,
                token_out=TOKEN_B,
                fee=3000,
                amount_in=bad_amount,
                min_amount_out=0,
            )

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_unstake_rejects_bad_amount(self, bad_amount):
        with pytest.raises(ValueError, match="amount"):
            GearboxStakeFuse(FUSE_ADDR, TOKEN_A).unstake(bad_amount)

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_decrease_liquidity_rejects_bad_liquidity(self, bad_amount):
        with pytest.raises(ValueError, match="liquidity"):
            UniswapV3ModifyPositionFuse(FUSE_ADDR).decrease_liquidity(
                token_id=1,
                liquidity=bad_amount,
                amount0_min=0,
                amount1_min=0,
                deadline=99,
            )

    @pytest.mark.parametrize("bad_amount", [0, -1])
    def test_morpho_claim_rejects_bad_claimable(self, bad_amount):
        with pytest.raises(ValueError, match="claimable"):
            MorphoClaimFuse(FUSE_ADDR).claim(
                universal_rewards_distributor=TOKEN_A,
                rewards_token=TOKEN_B,
                claimable=bad_amount,
                proof=[],
            )


class TestAddressValidation:
    """Zero address must raise ValueError across fuse types."""

    def test_aave_supply_rejects_zero_address(self):
        with pytest.raises(ValueError, match="asset"):
            AaveV3SupplyFuse(FUSE_ADDR).supply(asset=ZERO_ADDR, amount=1000)

    def test_compound_supply_rejects_zero_address(self):
        with pytest.raises(ValueError, match="asset"):
            CompoundV3SupplyFuse(FUSE_ADDR).supply(asset=ZERO_ADDR, amount=1000)

    def test_erc4626_supply_rejects_zero_address(self):
        with pytest.raises(ValueError, match="vault_address"):
            ERC4626SupplyFuse(FUSE_ADDR).supply(vault_address=ZERO_ADDR, amount=1000)

    def test_uniswap_swap_rejects_zero_token_in(self):
        with pytest.raises(ValueError, match="token_in"):
            UniswapV3SwapFuse(FUSE_ADDR).swap(
                token_in=ZERO_ADDR,
                token_out=TOKEN_B,
                fee=3000,
                amount_in=100,
                min_amount_out=0,
            )

    def test_uniswap_swap_rejects_zero_token_out(self):
        with pytest.raises(ValueError, match="token_out"):
            UniswapV3SwapFuse(FUSE_ADDR).swap(
                token_in=TOKEN_A,
                token_out=ZERO_ADDR,
                fee=3000,
                amount_in=100,
                min_amount_out=0,
            )

    def test_universal_swap_rejects_zero_token_in(self):
        with pytest.raises(ValueError, match="token_in"):
            UniversalTokenSwapperFuse(FUSE_ADDR).swap(
                token_in=ZERO_ADDR,
                token_out=TOKEN_B,
                amount_in=100,
                targets=[],
                data=[],
            )

    def test_morpho_flash_loan_rejects_zero_asset(self):
        with pytest.raises(ValueError, match="asset"):
            MorphoFlashLoanFuse(FUSE_ADDR).flash_loan(ZERO_ADDR, 100, [])

    def test_morpho_claim_rejects_zero_distributor(self):
        with pytest.raises(ValueError, match="universal_rewards_distributor"):
            MorphoClaimFuse(FUSE_ADDR).claim(
                universal_rewards_distributor=ZERO_ADDR,
                rewards_token=TOKEN_B,
                claimable=100,
                proof=[],
            )

    def test_morpho_claim_rejects_zero_rewards_token(self):
        with pytest.raises(ValueError, match="rewards_token"):
            MorphoClaimFuse(FUSE_ADDR).claim(
                universal_rewards_distributor=TOKEN_A,
                rewards_token=ZERO_ADDR,
                claimable=100,
                proof=[],
            )

    def test_aave_borrow_rejects_zero_asset(self):
        with pytest.raises(ValueError, match="asset"):
            AaveV3BorrowFuse(FUSE_ADDR).borrow(asset=ZERO_ADDR, amount=1000)


class TestSlippageParamsAllowZero:
    """min_amount_out and amount_min params must accept zero (slippage tolerance)."""

    def test_uniswap_swap_allows_zero_min_amount_out(self):
        action = UniswapV3SwapFuse(FUSE_ADDR).swap(
            token_in=TOKEN_A,
            token_out=TOKEN_B,
            fee=3000,
            amount_in=100,
            min_amount_out=0,
        )
        assert action.fuse == FUSE_ADDR

    def test_new_position_allows_zero_amount_min(self):
        action = UniswapV3NewPositionFuse(FUSE_ADDR).new_position(
            token0=TOKEN_A,
            token1=TOKEN_B,
            fee=500,
            tick_lower=-100,
            tick_upper=100,
            amount0_desired=1000,
            amount1_desired=2000,
            amount0_min=0,
            amount1_min=0,
            deadline=99999,
        )
        assert action.fuse == FUSE_ADDR

    def test_decrease_liquidity_allows_zero_amount_min(self):
        action = UniswapV3ModifyPositionFuse(FUSE_ADDR).decrease_liquidity(
            token_id=1, liquidity=500, amount0_min=0, amount1_min=0, deadline=99
        )
        assert action.fuse == FUSE_ADDR


class TestFuseConstructorValidation:
    """Fuse constructor must reject zero and empty addresses."""

    def test_zero_address_rejected(self):
        with pytest.raises(ValueError, match="zero address"):
            AaveV3SupplyFuse(ZERO_ADDR)

    def test_none_address_rejected(self):
        with pytest.raises(ValueError):
            AaveV3SupplyFuse(None)  # type: ignore[arg-type]


class TestListParameterValidation:
    """Empty token_ids lists must raise ValueError."""

    def test_uniswap_close_position_rejects_empty_list(self):
        with pytest.raises(ValueError, match="token_ids"):
            UniswapV3NewPositionFuse(FUSE_ADDR).close_position([])

    def test_uniswap_collect_rejects_empty_list(self):
        with pytest.raises(ValueError, match="token_ids"):
            UniswapV3CollectFuse(FUSE_ADDR).collect([])

    def test_ramses_close_position_rejects_empty_list(self):
        with pytest.raises(ValueError, match="token_ids"):
            RamsesV2NewPositionFuse(FUSE_ADDR).close_position([])

    def test_ramses_collect_rejects_empty_list(self):
        with pytest.raises(ValueError, match="token_ids"):
            RamsesV2CollectFuse(FUSE_ADDR).collect([])


class TestTokenIdValidation:
    """Negative token_id must raise ValueError."""

    def test_uniswap_decrease_liquidity_rejects_negative_token_id(self):
        with pytest.raises(ValueError, match="token_id"):
            UniswapV3ModifyPositionFuse(FUSE_ADDR).decrease_liquidity(
                token_id=-1, liquidity=500, amount0_min=0, amount1_min=0, deadline=99
            )

    def test_uniswap_increase_liquidity_rejects_negative_token_id(self):
        with pytest.raises(ValueError, match="token_id"):
            UniswapV3ModifyPositionFuse(FUSE_ADDR).increase_liquidity(
                token0=TOKEN_A,
                token1=TOKEN_B,
                token_id=-1,
                amount0_desired=1000,
                amount1_desired=2000,
                amount0_min=0,
                amount1_min=0,
                deadline=99999,
            )

    def test_ramses_decrease_liquidity_rejects_negative_token_id(self):
        with pytest.raises(ValueError, match="token_id"):
            RamsesV2ModifyPositionFuse(FUSE_ADDR).decrease_liquidity(
                token_id=-1, liquidity=400, amount0_min=0, amount1_min=0, deadline=99
            )

    def test_ramses_increase_liquidity_rejects_negative_token_id(self):
        with pytest.raises(ValueError, match="token_id"):
            RamsesV2ModifyPositionFuse(FUSE_ADDR).increase_liquidity(
                token0=TOKEN_A,
                token1=TOKEN_B,
                token_id=-1,
                amount0_desired=1000,
                amount1_desired=2000,
                amount0_min=0,
                amount1_min=0,
                deadline=99999,
            )

    def test_uniswap_decrease_liquidity_allows_zero_token_id(self):
        action = UniswapV3ModifyPositionFuse(FUSE_ADDR).decrease_liquidity(
            token_id=0, liquidity=500, amount0_min=0, amount1_min=0, deadline=99
        )
        assert action.fuse == FUSE_ADDR


class TestUniversalSwapEdgeCases:
    """Mismatched targets/data lengths must raise ValueError."""

    def test_mismatched_targets_data_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            UniversalTokenSwapperFuse(FUSE_ADDR).swap(
                token_in=TOKEN_A,
                token_out=TOKEN_B,
                amount_in=100,
                targets=[TOKEN_A],
                data=[],
            )

    def test_mismatched_data_longer_than_targets(self):
        with pytest.raises(ValueError, match="same length"):
            UniversalTokenSwapperFuse(FUSE_ADDR).swap(
                token_in=TOKEN_A,
                token_out=TOKEN_B,
                amount_in=100,
                targets=[],
                data=[b"\xaa"],
            )


# ── _parse_param_types ────────────────────────────────────────────────


class TestParseParamTypes:
    def test_simple_types(self):
        assert _parse_param_types("transfer(address,uint256)") == [
            "address",
            "uint256",
        ]

    def test_empty_params(self):
        assert not _parse_param_types("doSomething()")

    def test_single_param(self):
        assert _parse_param_types("foo(uint256)") == ["uint256"]

    def test_nested_tuple(self):
        assert _parse_param_types("enter((address,uint256))") == ["(address,uint256)"]

    def test_tuple_array(self):
        assert _parse_param_types("execute((address,bytes)[])") == ["(address,bytes)[]"]

    def test_multiple_params_with_tuple(self):
        assert _parse_param_types("foo(uint256,(address,uint256),bool)") == [
            "uint256",
            "(address,uint256)",
            "bool",
        ]

    def test_deeply_nested_tuple(self):
        assert _parse_param_types("enter((address,uint256,(uint256,address)))") == [
            "(address,uint256,(uint256,address))"
        ]

    def test_multiple_tuples(self):
        assert _parse_param_types("foo((uint256,address),(bool,bytes))") == [
            "(uint256,address)",
            "(bool,bytes)",
        ]
