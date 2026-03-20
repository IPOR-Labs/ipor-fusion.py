from dataclasses import asdict, dataclass

from ipor_fusion.readers.position_manager import PositionData, PositionManagerReader
from ipor_fusion.types import TokenId


@dataclass(slots=True)
class UniswapV3Position(PositionData):
    """Liquidity position data from the Uniswap V3 NonfungiblePositionManager."""


class UniswapV3Reader(PositionManagerReader):
    """Reader for Uniswap V3 NonfungiblePositionManager on-chain state."""

    def positions(self, token_id: TokenId) -> UniswapV3Position:
        data = self._decode_position(token_id)
        return UniswapV3Position(**asdict(data))
