from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.types import Amount

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# DataTypes.ReserveDataLegacy returned by Pool.getReserveData(address).
# All fields are static, so the struct is encoded inline as a flat sequence.
_RESERVE_DATA_TYPES = [
    "uint256",  # configuration
    "uint128",  # liquidityIndex
    "uint128",  # currentLiquidityRate
    "uint128",  # variableBorrowIndex
    "uint128",  # currentVariableBorrowRate
    "uint128",  # currentStableBorrowRate
    "uint40",  # lastUpdateTimestamp
    "uint16",  # id
    "address",  # aTokenAddress
    "address",  # stableDebtTokenAddress
    "address",  # variableDebtTokenAddress
    "address",  # interestRateStrategyAddress
    "uint128",  # accruedToTreasury
    "uint128",  # unbacked
    "uint128",  # isolationModeTotalDebt
]


@dataclass(slots=True)
class AaveV3UserAccountData:
    """Aggregated account data for an Aave V3 user."""

    total_collateral_base: Amount
    total_debt_base: Amount
    available_borrows_base: Amount
    current_liquidation_threshold: int
    ltv: int
    health_factor: int


@dataclass(slots=True)
class AaveV3ReserveTokens:
    """aToken / stable / variable debt token addresses for a single reserve."""

    a_token: ChecksumAddress
    stable_debt_token: ChecksumAddress
    variable_debt_token: ChecksumAddress


@dataclass(slots=True)
class AaveV3PositionBreakdown:
    """Per-reserve position of a user on Aave V3, expressed in asset amounts.

    `supply` is the aToken balance (principal + accrued interest, also serves
    as collateral when `useAsCollateral` is on); `variable_debt` and
    `stable_debt` are the corresponding debt-token balances. All three are
    denominated in the reserve's underlying asset.
    """

    asset: ChecksumAddress
    a_token: ChecksumAddress
    variable_debt_token: ChecksumAddress
    stable_debt_token: ChecksumAddress
    supply: Amount
    variable_debt: Amount
    stable_debt: Amount

    @property
    def is_empty(self) -> bool:
        return self.supply == 0 and self.variable_debt == 0 and self.stable_debt == 0


def _user_account_data_decoder(value: tuple) -> AaveV3UserAccountData:
    return AaveV3UserAccountData(*value)


def _reserve_tokens_decoder(value: tuple) -> AaveV3ReserveTokens:
    a_token = value[8]
    stable_debt_token = value[9]
    variable_debt_token = value[10]
    return AaveV3ReserveTokens(
        a_token=Web3.to_checksum_address(a_token),
        stable_debt_token=Web3.to_checksum_address(stable_debt_token),
        variable_debt_token=Web3.to_checksum_address(variable_debt_token),
    )


class AaveV3Reader(ContractWrapper):
    """Reader for Aave V3 lending pool on-chain state."""

    def get_user_account_data(
        self, user: ChecksumAddress
    ) -> Call[AaveV3UserAccountData]:
        return self._view(
            "getUserAccountData(address)",
            user,
            output_types=["uint256"] * 6,
            decoder=_user_account_data_decoder,
        )

    def reserve_tokens(self, asset: ChecksumAddress) -> Call[AaveV3ReserveTokens]:
        """Return aToken / stable / variable debt token addresses for `asset`."""
        return self._view(
            "getReserveData(address)",
            asset,
            output_types=_RESERVE_DATA_TYPES,
            decoder=_reserve_tokens_decoder,
        )

    def position_breakdown(
        self, asset: ChecksumAddress, user: ChecksumAddress
    ) -> AaveV3PositionBreakdown:
        """Return the user's position in `asset` as supply / variable / stable amounts.

        Combines `getReserveData()` with `balanceOf()` on each reserve token.
        A zero token address counts as a zero balance, as in the IPOR Fusion
        Aave V3 balance fuse: stable debt is zeroed on reserves that dropped
        it, and every token is zero when `asset` is not listed on this Pool.
        """
        tokens = self.reserve_tokens(asset).call()
        return AaveV3PositionBreakdown(
            asset=asset,
            a_token=tokens.a_token,
            variable_debt_token=tokens.variable_debt_token,
            stable_debt_token=tokens.stable_debt_token,
            supply=self._balance_of(tokens.a_token, user),
            variable_debt=self._balance_of(tokens.variable_debt_token, user),
            stable_debt=self._balance_of(tokens.stable_debt_token, user),
        )

    def _balance_of(self, token: ChecksumAddress, user: ChecksumAddress) -> Amount:
        if token.lower() == _ZERO_ADDRESS:
            return Amount(0)
        return ERC20(self._ctx, token).balance_of(user).call()
