from eth_abi import decode
from web3 import Web3

from ipor_fusion.core.contract import ContractWrapper

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


class PositionManagerReader(ContractWrapper):
    """Base reader for NonfungiblePositionManager-style contracts."""

    def _decode_position(self, token_id: int) -> dict:
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
        ) = decode(_POSITION_ABI_TYPES, raw)
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
