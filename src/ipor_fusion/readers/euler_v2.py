from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Amount

# Euler's ISO 4217 code for USD (840) used as a unit-of-account address.
EULER_USD_UNIT_OF_ACCOUNT = Web3.to_checksum_address(
    "0x0000000000000000000000000000000000000348"
)


@dataclass(slots=True)
class EulerV2AccountLiquidity:
    """`accountLiquidityFull(account, liquidation=true)` of the account's controller.

    Values are in the controller's unit of account. Each collateral value is
    already scaled by its liquidation LTV, so it is the most debt that
    collateral backs, not its market value.
    """

    collaterals: list[ChecksumAddress]
    risk_adjusted_collateral_values: list[int]
    liability_value: int


def _account_liquidity_decoder(
    value: tuple[list[str], list[int], int],
) -> EulerV2AccountLiquidity:
    collaterals, values, liability = value
    return EulerV2AccountLiquidity(
        collaterals=[Web3.to_checksum_address(c) for c in collaterals],
        risk_adjusted_collateral_values=list(values),
        liability_value=liability,
    )


class EulerV2VaultReader(ContractWrapper):
    """Reader for an Euler V2 (EVK) vault."""

    def debt_of(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "debtOf(address)", account, output_types=["uint256"], decoder=Amount
        )

    def account_liquidity_full(
        self, account: ChecksumAddress
    ) -> Call[EulerV2AccountLiquidity]:
        """Reverts unless this vault is the account's controller."""
        return self._view(
            "accountLiquidityFull(address,bool)",
            account,
            True,
            output_types=["address[]", "uint256[]", "uint256"],
            decoder=_account_liquidity_decoder,
        )

    def ltv_liquidation(self, collateral: ChecksumAddress) -> Call[int]:
        """Liquidation LTV of ``collateral`` in basis points; 0 when not accepted."""
        return self._view(
            "LTVLiquidation(address)", collateral, output_types=["uint16"]
        )

    def unit_of_account(self) -> Call[ChecksumAddress]:
        return self._view(
            "unitOfAccount()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )
