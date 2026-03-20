from dataclasses import dataclass

from eth_typing import ChecksumAddress

from ipor_fusion.readers.position_manager import PositionManagerReader
from ipor_fusion.types import Amount, Fee, Tick, TokenId


@dataclass(slots=True)
class UniswapV3Position:
    """Liquidity position data from the Uniswap V3 NonfungiblePositionManager."""

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


class UniswapV3Reader(PositionManagerReader):
    """Reader for Uniswap V3 NonfungiblePositionManager on-chain state."""

    def positions(self, token_id: TokenId) -> UniswapV3Position:
        return UniswapV3Position(**self._decode_position(token_id))
