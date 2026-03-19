from dataclasses import dataclass

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import ContractWrapper


@dataclass
class RamsesV2Position:
    """Liquidity position data from the Ramses V2 NonfungiblePositionManager."""

    nonce: int
    operator: ChecksumAddress
    token0: ChecksumAddress
    token1: ChecksumAddress
    fee: int
    tick_lower: int
    tick_upper: int
    liquidity: int
    fee_growth_inside0_last_x128: int
    fee_growth_inside1_last_x128: int
    tokens_owed0: int
    tokens_owed1: int


class RamsesV2Reader(ContractWrapper):
    """Reader for Ramses V2 NonfungiblePositionManager on-chain state."""

    def positions(self, token_id: int) -> RamsesV2Position:
        raw = self._call("positions(uint256)", token_id)
        (
            nonce,
            operator,
            token0,
            token1,
            fee,
            tick_lower,
            tick_upper,
            liquidity,
            fg0,
            fg1,
            owed0,
            owed1,
        ) = decode(
            [
                "uint96",
                "address",
                "address",
                "address",
                "uint24",
                "int24",
                "int24",
                "uint128",
                "uint256",
                "uint256",
                "uint128",
                "uint128",
            ],
            raw,
        )
        return RamsesV2Position(
            nonce=nonce,
            operator=Web3.to_checksum_address(operator),
            token0=Web3.to_checksum_address(token0),
            token1=Web3.to_checksum_address(token1),
            fee=fee,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            liquidity=liquidity,
            fee_growth_inside0_last_x128=fg0,
            fee_growth_inside1_last_x128=fg1,
            tokens_owed0=owed0,
            tokens_owed1=owed1,
        )
