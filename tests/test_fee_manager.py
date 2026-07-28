"""Unit tests for FeeManager/FeeAccount — mock Web3Context, verify encoding and decoding."""

from unittest.mock import MagicMock

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.core.fee_manager import (
    FeeAccount,
    FeeManager,
    HighWaterMarkPerformanceFee,
    RecipientFee,
)
from ipor_fusion.types import Fee, Period

FEE_MANAGER_ADDR = Web3.to_checksum_address(
    "0x1111111111111111111111111111111111111111"
)
FEE_ACCOUNT_ADDR = Web3.to_checksum_address(
    "0x2222222222222222222222222222222222222222"
)
RECIPIENT_1 = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
RECIPIENT_2 = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
DAO_RECIPIENT = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")


def _make_fee_manager() -> tuple[FeeManager, MagicMock]:
    ctx = MagicMock()
    return FeeManager(ctx, FEE_MANAGER_ADDR), ctx


class TestFeeAccount:
    def test_fee_manager_address(self):
        ctx = MagicMock()
        account = FeeAccount(ctx, FEE_ACCOUNT_ADDR)
        ctx.call.return_value = encode(["address"], [FEE_MANAGER_ADDR])

        result = account.fee_manager().call()

        assert result == FEE_MANAGER_ADDR

    def test_fee_manager_selector(self):
        call = FeeAccount.encoder().fee_manager()

        assert call.calldata == function_signature_to_4byte_selector("FEE_MANAGER()")


class TestFeeManagerViews:
    def test_get_deposit_fee(self):
        manager, ctx = _make_fee_manager()
        ctx.call.return_value = encode(["uint256"], [10**16])  # 1% in WAD

        result = manager.get_deposit_fee().call()

        assert result == Fee(10**16)

    def test_get_total_performance_fee(self):
        manager, ctx = _make_fee_manager()
        ctx.call.return_value = encode(["uint256"], [1000])  # 10% with 2 decimals

        result = manager.get_total_performance_fee().call()

        assert result == Fee(1000)

    def test_get_total_management_fee(self):
        manager, ctx = _make_fee_manager()
        ctx.call.return_value = encode(["uint256"], [100])  # 1% with 2 decimals

        result = manager.get_total_management_fee().call()

        assert result == Fee(100)

    def test_get_performance_fee_recipients(self):
        manager, ctx = _make_fee_manager()
        ctx.call.return_value = encode(
            ["(address,uint256)[]"],
            [[(RECIPIENT_1, 700), (RECIPIENT_2, 300)]],
        )

        result = manager.get_performance_fee_recipients().call()

        assert result == [
            RecipientFee(recipient=RECIPIENT_1, fee_value=Fee(700)),
            RecipientFee(recipient=RECIPIENT_2, fee_value=Fee(300)),
        ]

    def test_get_management_fee_recipients_empty(self):
        manager, ctx = _make_fee_manager()
        ctx.call.return_value = encode(["(address,uint256)[]"], [[]])

        result = manager.get_management_fee_recipients().call()

        assert result == []

    def test_get_ipor_dao_fee_recipient_address(self):
        manager, ctx = _make_fee_manager()
        ctx.call.return_value = encode(["address"], [DAO_RECIPIENT])

        result = manager.get_ipor_dao_fee_recipient_address().call()

        assert result == DAO_RECIPIENT

    def test_get_plasma_vault_high_water_mark_performance_fee(self):
        manager, ctx = _make_fee_manager()
        ctx.call.return_value = encode(
            ["(uint128,uint32,uint32)"], [(10**18, 1_750_000_000, 86400)]
        )

        result = manager.get_plasma_vault_high_water_mark_performance_fee().call()

        assert result == HighWaterMarkPerformanceFee(
            high_water_mark=10**18,
            last_update=Timestamp(1_750_000_000),
            update_interval=Period(86400),
        )


class TestFeeManagerSelectors:
    """Selectors must match the Solidity signatures exactly — a mismatch in
    the signature string (e.g. wrong struct layout) would silently produce a
    call to a nonexistent function on-chain."""

    def test_selectors(self):
        encoder = FeeManager.encoder()
        for call, signature in [
            (encoder.get_deposit_fee(), "getDepositFee()"),
            (encoder.get_total_performance_fee(), "getTotalPerformanceFee()"),
            (encoder.get_total_management_fee(), "getTotalManagementFee()"),
            (
                encoder.get_performance_fee_recipients(),
                "getPerformanceFeeRecipients()",
            ),
            (encoder.get_management_fee_recipients(), "getManagementFeeRecipients()"),
            (
                encoder.get_ipor_dao_fee_recipient_address(),
                "getIporDaoFeeRecipientAddress()",
            ),
            (
                encoder.get_plasma_vault_high_water_mark_performance_fee(),
                "getPlasmaVaultHighWaterMarkPerformanceFee()",
            ),
        ]:
            assert call.calldata == function_signature_to_4byte_selector(signature), (
                signature
            )
