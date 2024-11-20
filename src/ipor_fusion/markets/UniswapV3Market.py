from typing import List

from web3 import Web3

from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.UniswapV3CollectFuse import UniswapV3CollectFuse
from ipor_fusion.fuse.UniswapV3ModifyPositionFuse import UniswapV3ModifyPositionFuse
from ipor_fusion.fuse.UniswapV3NewPositionFuse import UniswapV3NewPositionFuse
from ipor_fusion.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse


class UniswapV3Market:
    UNISWAP_V3_SWAP_FUSE = Web3.to_checksum_address(
        "0x84C5aB008C66d664681698A9E4536D942B916F89"
    )
    UNISWAP_V3_NEW_POSITION_FUSE = Web3.to_checksum_address(
        "0x0ce06c57173b7E4079B2AFB132cB9Ce846dDAC9b"
    )
    UNISWAP_V3_MODIFY_POSITION_FUSE = Web3.to_checksum_address(
        "0xba503b6f2b95A4A47ee9884bbBcd80cAce2D2EB3"
    )
    UNISWAP_V3_COLLECT_FUSE = Web3.to_checksum_address(
        "0x75781AB6CdcE9c505DbD0848f4Ad8A97c68F53c1"
    )
    UNISWAP_V3_SUPPLY_FUSE = Web3.to_checksum_address(
        "0x5C0d0e13B18B3Ea43E05Df0Bd1b89e0a3e4b2B0f"
    )

    def __init__(self, fuses: List[str]):
        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse == self.UNISWAP_V3_SWAP_FUSE:
                self._uniswap_v3_swap_fuse = UniswapV3SwapFuse(checksum_fuse)
                self._any_fuse_supported = True
            if checksum_fuse == self.UNISWAP_V3_NEW_POSITION_FUSE:
                self._uniswap_v3_new_position_fuse = UniswapV3NewPositionFuse(
                    checksum_fuse
                )
                self._any_fuse_supported = True
            if checksum_fuse == self.UNISWAP_V3_MODIFY_POSITION_FUSE:
                self._uniswap_v3_modify_position_fuse = UniswapV3ModifyPositionFuse(
                    checksum_fuse
                )
                self._any_fuse_supported = True
            if checksum_fuse == self.UNISWAP_V3_COLLECT_FUSE:
                self._uniswap_v3_collect_fuse = UniswapV3CollectFuse(checksum_fuse)
                self._any_fuse_supported = True

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def swap(
        self,
        token_in_address: str,
        token_out_address: str,
        fee: int,
        token_in_amount: int,
        min_out_amount: int,
    ) -> FuseAction:
        # Check if _uniswap_v3_swap_fuse is set
        if not hasattr(self, "_uniswap_v3_swap_fuse"):
            raise UnsupportedFuseError(
                "UniswapV3SwapFuse is not supported by PlasmaVault"
            )

        return self._uniswap_v3_swap_fuse.swap(
            token_in_address=token_in_address,
            token_out_address=token_out_address,
            fee=fee,
            token_in_amount=token_in_amount,
            min_out_amount=min_out_amount,
        )

    def new_position(
        self,
        token0: str,
        token1: str,
        fee: int,
        tick_lower: int,
        tick_upper: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        if not hasattr(self, "_uniswap_v3_new_position_fuse"):
            raise UnsupportedFuseError(
                "UniswapV3NewPositionFuse is not supported by PlasmaVault"
            )

        return self._uniswap_v3_new_position_fuse.new_position(
            token0=token0,
            token1=token1,
            fee=fee,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            amount0_desired=amount0_desired,
            amount1_desired=amount1_desired,
            amount0_min=amount0_min,
            amount1_min=amount1_min,
            deadline=deadline,
        )

    def increase_position(
        self,
        token0: str,
        token1: str,
        token_id: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        if not hasattr(self, "_uniswap_v3_modify_position_fuse"):
            raise UnsupportedFuseError(
                "UniswapV3ModifyPositionFuse is not supported by PlasmaVault"
            )

        return self._uniswap_v3_modify_position_fuse.increase_position(
            token0=token0,
            token1=token1,
            token_id=token_id,
            amount0_desired=amount0_desired,
            amount1_desired=amount1_desired,
            amount0_min=amount0_min,
            amount1_min=amount1_min,
            deadline=deadline,
        )

    def decrease_position(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        if not hasattr(self, "_uniswap_v3_modify_position_fuse"):
            raise UnsupportedFuseError(
                "UniswapV3ModifyPositionFuse is not supported by PlasmaVault"
            )

        return self._uniswap_v3_modify_position_fuse.decrease_position(
            token_id=token_id,
            liquidity=liquidity,
            amount0_min=amount0_min,
            amount1_min=amount1_min,
            deadline=deadline,
        )

    def collect(self, token_ids: List[int]) -> FuseAction:
        if not hasattr(self, "_uniswap_v3_collect_fuse"):
            raise UnsupportedFuseError(
                "UniswapV3CollectFuse is not supported by PlasmaVault"
            )

        return self._uniswap_v3_collect_fuse.collect(token_ids)

    def close_position(self, token_ids: List[int]) -> FuseAction:
        if not hasattr(self, "_uniswap_v3_new_position_fuse"):
            raise UnsupportedFuseError(
                "UniswapV3NewPositionFuse is not supported by PlasmaVault"
            )

        return self._uniswap_v3_new_position_fuse.close_position(token_ids)
