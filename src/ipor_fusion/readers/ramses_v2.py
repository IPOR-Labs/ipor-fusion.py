from dataclasses import dataclass

from ipor_fusion.core.contract import Call
from ipor_fusion.readers.position_manager import PositionData, PositionManagerReader
from ipor_fusion.types import TokenId


@dataclass(slots=True)
class RamsesV2Position(PositionData):
    """Liquidity position data from the Ramses V2 NonfungiblePositionManager."""


class RamsesV2Reader(PositionManagerReader):
    """Reader for Ramses V2 NonfungiblePositionManager on-chain state."""

    def positions(self, token_id: TokenId) -> Call[RamsesV2Position]:
        return self._positions(token_id, RamsesV2Position)
