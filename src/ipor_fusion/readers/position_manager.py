from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Amount, Fee, Tick, TokenId

_POSITION_ABI_TYPES = [
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
]


@dataclass(slots=True)
class PositionData:
    """Decoded position data from a NonfungiblePositionManager contract."""

    nonce: int
    operator: ChecksumAddress
    token0: ChecksumAddress
    token1: ChecksumAddress
    fee: Fee
    tick_lower: Tick
    tick_upper: Tick
    liquidity: Amount
    fee_growth_inside0_last_x128: int
    fee_growth_inside1_last_x128: int
    tokens_owed0: Amount
    tokens_owed1: Amount


_T = TypeVar("_T", bound=PositionData)


def _decode_position_fields(value: tuple) -> dict:
    """Decode raw `positions(uint256)` tuple into a kwargs dict for `PositionData`
    (and subclasses). Used by reader-specific decoders.
    """
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
    ) = value
    return {
        "nonce": nonce,
        "operator": Web3.to_checksum_address(operator),
        "token0": Web3.to_checksum_address(token0),
        "token1": Web3.to_checksum_address(token1),
        "fee": fee,
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
        "liquidity": liquidity,
        "fee_growth_inside0_last_x128": fg0,
        "fee_growth_inside1_last_x128": fg1,
        "tokens_owed0": owed0,
        "tokens_owed1": owed1,
    }


class PositionManagerReader(ContractWrapper):
    """Base reader for NonfungiblePositionManager-style contracts."""

    def _positions(self, token_id: TokenId, into: Callable[..., _T]) -> Call[_T]:
        """Build a `positions(uint256)` view that decodes into the subclass's
        dataclass via `into(**fields)`.
        """
        return self._view(
            "positions(uint256)",
            token_id,
            output_types=_POSITION_ABI_TYPES,
            decoder=lambda v: into(**_decode_position_fields(v)),
        )
