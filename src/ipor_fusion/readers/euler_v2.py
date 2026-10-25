from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Amount

# A value in an Euler vault's unit of account, in that unit's decimals.
UnitOfAccountValue = NewType("UnitOfAccountValue", int)
# An Euler LTV in basis points (10_000 = 100%).
LtvBps = NewType("LtvBps", int)
# The byte XORed into the Plasma Vault address to name an EVC sub-account.
EulerSubAccountId = NewType("EulerSubAccountId", int)

# Euler's ISO 4217 code for USD (840) used as a unit-of-account address.
EULER_USD_UNIT_OF_ACCOUNT = Web3.to_checksum_address(
    "0x0000000000000000000000000000000000000348"
)


@dataclass(frozen=True, slots=True)
class EulerV2Substrate:
    """A decoded Euler V2 market substrate (`EulerFuseLib.EulerSubstrate`)."""

    euler_vault: ChecksumAddress
    is_collateral: bool
    can_borrow: bool
    sub_account_id: EulerSubAccountId

    @classmethod
    def from_bytes(cls, substrate: bytes) -> EulerV2Substrate:
        """Inverse of `EulerFuseLib.substrateToBytes32`:
        `eulerVault<<96 | isCollateral<<88 | canBorrow<<80 | subAccounts<<72`."""
        word = int.from_bytes(substrate, "big")
        return cls(
            euler_vault=Web3.to_checksum_address(f"0x{word >> 96:040x}"),
            is_collateral=bool(word >> 88 & 0x01),
            can_borrow=bool(word >> 80 & 0x01),
            sub_account_id=EulerSubAccountId(word >> 72 & 0xFF),
        )

    def sub_account(self, plasma_vault: ChecksumAddress) -> ChecksumAddress:
        """`EulerFuseLib.generateSubAccountAddress`: the vault address XOR the id."""
        return Web3.to_checksum_address(
            f"0x{int(plasma_vault, 16) ^ self.sub_account_id:040x}"
        )


@dataclass(slots=True)
class EulerV2AccountLiquidity:
    """`accountLiquidityFull(account, liquidation=true)` of the account's controller.

    Each collateral value is already scaled by its liquidation LTV, so it is
    the most debt that collateral backs, not its market value.
    """

    collaterals: list[ChecksumAddress]
    risk_adjusted_collateral_values: list[UnitOfAccountValue]
    liability_value: UnitOfAccountValue


def _account_liquidity_decoder(
    value: tuple[list[str], list[int], int],
) -> EulerV2AccountLiquidity:
    collaterals, values, liability = value
    return EulerV2AccountLiquidity(
        collaterals=[Web3.to_checksum_address(c) for c in collaterals],
        risk_adjusted_collateral_values=[UnitOfAccountValue(v) for v in values],
        liability_value=UnitOfAccountValue(liability),
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

    def ltv_liquidation(self, collateral: ChecksumAddress) -> Call[LtvBps]:
        """Liquidation LTV of ``collateral``; 0 when not accepted."""
        return self._view(
            "LTVLiquidation(address)",
            collateral,
            output_types=["uint16"],
            decoder=LtvBps,
        )

    def unit_of_account(self) -> Call[ChecksumAddress]:
        return self._view(
            "unitOfAccount()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )
