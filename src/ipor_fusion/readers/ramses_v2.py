from dataclasses import asdict, dataclass

from ipor_fusion.readers.position_manager import PositionData, PositionManagerReader
from ipor_fusion.types import TokenId


@dataclass(slots=True)
class RamsesV2Position(PositionData):
    """Liquidity position data from the Ramses V2 NonfungiblePositionManager."""


class RamsesV2Reader(PositionManagerReader):
    """Reader for Ramses V2 NonfungiblePositionManager on-chain state."""

    def positions(self, token_id: TokenId) -> RamsesV2Position:
        data = self._decode_position(token_id)
        return RamsesV2Position(**asdict(data))
