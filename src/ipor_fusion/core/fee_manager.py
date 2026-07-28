from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Fee, Period


@dataclass(slots=True)
class RecipientFee:
    """Per-recipient fee share (percentage with 2 decimals, 10000 = 100%)."""

    recipient: ChecksumAddress
    fee_value: Fee


@dataclass(slots=True)
class HighWaterMarkPerformanceFee:
    """High-water-mark state for performance fee calculation.

    `high_water_mark` is an exchange-rate reference: the assets one whole
    share converts to, i.e. `convertToAssets(10**share_decimals)`, so it is
    denominated in the underlying asset and scaled by 10**asset_decimals.
    """

    high_water_mark: int
    last_update: Timestamp
    update_interval: Period


def _recipient_fee_list_decoder(value: list) -> list[RecipientFee]:
    return [
        RecipientFee(
            recipient=Web3.to_checksum_address(recipient), fee_value=Fee(fee_value)
        )
        for recipient, fee_value in value
    ]


def _high_water_mark_decoder(value: tuple) -> HighWaterMarkPerformanceFee:
    high_water_mark, last_update, update_interval = value
    return HighWaterMarkPerformanceFee(
        high_water_mark=high_water_mark,
        last_update=Timestamp(last_update),
        update_interval=Period(update_interval),
    )


class FeeAccount(ContractWrapper):
    """Escrow account collecting one fee stream; knows its FeeManager.

    The addresses in the vault's performance/management fee data
    (`PlasmaVault.get_performance_fee_data().fee_account`) are FeeAccounts;
    `fee_manager()` is the discovery hop to the vault's FeeManager.
    """

    def fee_manager(self) -> Call[ChecksumAddress]:
        return self._view(
            "FEE_MANAGER()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )


class FeeManager(ContractWrapper):
    """Fee configuration hub of a Plasma Vault: deposit ("onboarding
    contribution"), performance, and management fees plus their recipients.

    Percent-value scales differ per getter: deposit fee is WAD
    (1e18 = 100%), the totals and recipient splits use 2 decimals
    (10000 = 100%).
    """

    def get_deposit_fee(self) -> Call[Fee]:
        """WAD (1e18 = 100%). Reverts on FeeManager deployments predating
        the deposit fee - callers should treat a revert as no-deposit-fee."""
        return self._view("getDepositFee()", output_types=["uint256"], decoder=Fee)

    def get_total_performance_fee(self) -> Call[Fee]:
        """Percentage with 2 decimals (10000 = 100%)."""
        return self._view(
            "getTotalPerformanceFee()", output_types=["uint256"], decoder=Fee
        )

    def get_total_management_fee(self) -> Call[Fee]:
        """Percentage with 2 decimals (10000 = 100%)."""
        return self._view(
            "getTotalManagementFee()", output_types=["uint256"], decoder=Fee
        )

    def get_performance_fee_recipients(self) -> Call[list[RecipientFee]]:
        return self._view(
            "getPerformanceFeeRecipients()",
            output_types=["(address,uint256)[]"],
            decoder=_recipient_fee_list_decoder,
        )

    def get_management_fee_recipients(self) -> Call[list[RecipientFee]]:
        return self._view(
            "getManagementFeeRecipients()",
            output_types=["(address,uint256)[]"],
            decoder=_recipient_fee_list_decoder,
        )

    def get_ipor_dao_fee_recipient_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "getIporDaoFeeRecipientAddress()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_plasma_vault_high_water_mark_performance_fee(
        self,
    ) -> Call[HighWaterMarkPerformanceFee]:
        """Reverts on FeeManager deployments predating the high-water mark -
        callers should treat a revert as HWM-not-supported."""
        return self._view(
            "getPlasmaVaultHighWaterMarkPerformanceFee()",
            output_types=["(uint128,uint32,uint32)"],
            decoder=_high_water_mark_decoder,
        )
