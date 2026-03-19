from dataclasses import dataclass

from eth_abi import decode
from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import ContractWrapper


@dataclass
class AaveV3UserAccountData:
    """Aggregated account data for an Aave V3 user."""

    total_collateral_base: int
    total_debt_base: int
    available_borrows_base: int
    current_liquidation_threshold: int
    ltv: int
    health_factor: int


class AaveV3Reader(ContractWrapper):
    """Reader for Aave V3 lending pool on-chain state."""

    def get_user_account_data(self, user: ChecksumAddress) -> AaveV3UserAccountData:
        raw = self._call("getUserAccountData(address)", user)
        values = decode(
            ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            raw,
        )
        return AaveV3UserAccountData(*values)
