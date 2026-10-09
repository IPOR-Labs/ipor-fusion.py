from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper


@dataclass(slots=True)
class AaveV4UserAccountData:
    """Account data of a user on one Aave V4 Spoke (`IAaveV4Spoke.UserAccountData`).

    `health_factor` and `avg_collateral_factor` are WAD. `total_collateral_value`
    is in the Spoke oracle's base currency scaled by `10**(oracle decimals + 18)`;
    `total_debt_value_ray` is the same unit scaled by a further RAY.
    `health_factor` is `type(uint256).max` while `borrow_count` is zero.
    """

    risk_premium: int
    avg_collateral_factor: int
    health_factor: int
    total_collateral_value: int
    total_debt_value_ray: int
    active_collateral_count: int
    borrow_count: int


def _user_account_data_decoder(value: tuple) -> AaveV4UserAccountData:
    return AaveV4UserAccountData(*value)


class AaveV4SpokeReader(ContractWrapper):
    """Reader for an Aave V4 Spoke. Health is per account per Spoke."""

    def get_user_account_data(
        self, user: ChecksumAddress
    ) -> Call[AaveV4UserAccountData]:
        return self._view(
            "getUserAccountData(address)",
            user,
            output_types=["uint256"] * 7,
            decoder=_user_account_data_decoder,
        )

    def oracle(self) -> Call[ChecksumAddress]:
        return self._view(
            "ORACLE()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )


class AaveV4OracleReader(ContractWrapper):
    """Reader for the price oracle of an Aave V4 Spoke."""

    def decimals(self) -> Call[int]:
        return self._view("decimals()", output_types=["uint8"])
