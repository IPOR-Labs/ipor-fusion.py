from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.types import ZERO_ADDRESS, Amount

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


def _user_account_data_decoder(
    value: tuple[int, int, int, int, int, int],
) -> AaveV3UserAccountData:
    collateral, debt, available, liquidation_threshold, ltv, health_factor = value
    return AaveV3UserAccountData(
        total_collateral_base=Amount(collateral),
        total_debt_base=Amount(debt),
        available_borrows_base=Amount(available),
        current_liquidation_threshold=liquidation_threshold,
        ltv=ltv,
        health_factor=health_factor,
    )


# getReserveData: configuration, five uint128 indexes and rates,
# lastUpdateTimestamp, id, four addresses, three uint128 totals.
_ReserveData = tuple[
    int, int, int, int, int, int, int, int, str, str, str, str, int, int, int
]


def _reserve_tokens_decoder(value: _ReserveData) -> AaveV3ReserveTokens:
    a_token = value[8]
    stable_debt_token = value[9]
    variable_debt_token = value[10]
    return AaveV3ReserveTokens(
        a_token=Web3.to_checksum_address(a_token),
        stable_debt_token=Web3.to_checksum_address(stable_debt_token),
        variable_debt_token=Web3.to_checksum_address(variable_debt_token),
    )


class AaveV3PoolAddressesProvider(ContractWrapper):
    """Registry of one Aave V3 deployment's contracts; knows its Pool."""

    def get_pool(self) -> Call[ChecksumAddress]:
        return self._view(
            "getPool()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )


class AaveV3FuseReader(ContractWrapper):
    """Reader for the immutables of an IPOR Fusion Aave V3 fuse.

    The same fuse contracts serve every Aave V3 deployment a vault can use —
    Aave V3 Core, Aave V3 Prime (`AAVE_V3_LIDO`) and the SparkLend fork
    (`SPARK_LEND`) — each deployed with that deployment's PoolAddressesProvider.
    The Pool behind a market is therefore a property of its fuse, not of the
    chain: resolve it with `read_pool(ctx)`.
    """

    def pool_addresses_provider(self) -> Call[ChecksumAddress]:
        return self._view(
            "AAVE_V3_POOL_ADDRESSES_PROVIDER()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def read_pool(self, ctx: Web3Context) -> ChecksumAddress:
        """Return the Aave V3 Pool this fuse operates on (two reads on ``ctx``)."""
        provider = self.pool_addresses_provider().call(ctx)
        return AaveV3PoolAddressesProvider(provider).get_pool().call(ctx)


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

    def read_position_breakdown(
        self, ctx: Web3Context, asset: ChecksumAddress, user: ChecksumAddress
    ) -> AaveV3PositionBreakdown:
        """Return the user's position in `asset` as supply / variable / stable amounts.

        Combines `getReserveData()` with `balanceOf()` on each reserve token,
        the balances batched in one Multicall3 round trip. A zero token address
        counts as a zero balance, as in the IPOR Fusion Aave V3 balance fuse:
        stable debt is zeroed on reserves that dropped it, and every token is
        zero when `asset` is not listed on this Pool.
        """
        tokens = self.reserve_tokens(asset).call(ctx)
        reserve_tokens = (
            tokens.a_token,
            tokens.variable_debt_token,
            tokens.stable_debt_token,
        )
        listed = [token for token in reserve_tokens if token.lower() != ZERO_ADDRESS]
        balances = iter(
            Multicall3(ctx).aggregate(
                [ERC20(token).balance_of(user) for token in listed]
            )
        )
        supply, variable_debt, stable_debt = (
            next(balances) if token in listed else Amount(0) for token in reserve_tokens
        )
        return AaveV3PositionBreakdown(
            asset=asset,
            a_token=tokens.a_token,
            variable_debt_token=tokens.variable_debt_token,
            stable_debt_token=tokens.stable_debt_token,
            supply=supply,
            variable_debt=variable_debt,
            stable_debt=stable_debt,
        )
