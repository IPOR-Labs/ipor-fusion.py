from typing import List

from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.RamsesV2ClaimFuse import RamsesV2ClaimFuse
from ipor_fusion.fuse.RamsesV2CollectFuse import RamsesV2CollectFuse
from ipor_fusion.fuse.RamsesV2ModifyPositionFuse import RamsesV2ModifyPositionFuse
from ipor_fusion.fuse.RamsesV2NewPositionFuse import RamsesV2NewPositionFuse


class RamsesV2Market:
    RAMSES_V2_NEW_POSITION_FUSE = Web3.to_checksum_address(
        "0xb025CC5e73e2966e12e4d859360B51c1D0F45EA3"
    )
    RAMSES_V2_MODIFY_POSITION_FUSE = Web3.to_checksum_address(
        "0xD41501B46a68DeA06a460fD79a7bCda9e3b92674"
    )
    RAMSES_V2_COLLECT_FUSE = Web3.to_checksum_address(
        "0x859F5c9D5CB2800A9Ff72C56d79323EA01cB30b9"
    )
    RAMSES_V2_CLAIM_FUSE = Web3.to_checksum_address(
        "0x6F292d12a2966c9B796642cAFD67549bbbE3D066"
    )

    RAMSES_V2_RAM_TOKEN = Web3.to_checksum_address(
        "0xAAA6C1E32C55A7Bfa8066A6FAE9b42650F262418"
    )
    RAMSES_V2_X_RAM_TOKEN = Web3.to_checksum_address(
        "0xAAA1eE8DC1864AE49185C368e8c64Dd780a50Fb7"
    )

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        fuses: List[str],
        rewards_fuses: List[str],
        rewards_claim_manager: RewardsClaimManager,
    ):
        self._transaction_executor = transaction_executor
        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse == self.RAMSES_V2_NEW_POSITION_FUSE:
                self._ramses_v2_new_position_fuse = RamsesV2NewPositionFuse(
                    checksum_fuse
                )
                self._any_fuse_supported = True
            if checksum_fuse == self.RAMSES_V2_MODIFY_POSITION_FUSE:
                self._ramses_v2_modify_position_fuse = RamsesV2ModifyPositionFuse(
                    checksum_fuse
                )
                self._any_fuse_supported = True
            if checksum_fuse == self.RAMSES_V2_COLLECT_FUSE:
                self._ramses_v2_collect_fuse = RamsesV2CollectFuse(checksum_fuse)
                self._any_fuse_supported = True

        for rewards_fuse in rewards_fuses:
            checksum_rewards_fuse = Web3.to_checksum_address(rewards_fuse)
            if checksum_rewards_fuse == self.RAMSES_V2_CLAIM_FUSE:
                self._ramses_v2_claim_fuse = RamsesV2ClaimFuse(checksum_rewards_fuse)
                self._any_fuse_supported = True

        if not rewards_fuses:
            if rewards_claim_manager.is_reward_fuse_supported(
                self.RAMSES_V2_CLAIM_FUSE
            ):
                self._ramses_v2_claim_fuse = RamsesV2ClaimFuse(
                    self.RAMSES_V2_CLAIM_FUSE
                )
                self._any_fuse_supported = True

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

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
        ve_ram_token_id: int,
    ) -> FuseAction:
        if not hasattr(self, "_ramses_v2_new_position_fuse"):
            raise UnsupportedFuseError(
                "RamsesV2NewPositionFuse is not supported by PlasmaVault"
            )

        return self._ramses_v2_new_position_fuse.new_position(
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
            ve_ram_token_id=ve_ram_token_id,
        )

    def decrease_position(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        if not hasattr(self, "_ramses_v2_modify_position_fuse"):
            raise UnsupportedFuseError(
                "RamsesV2ModifyPositionFuse is not supported by PlasmaVault"
            )

        return self._ramses_v2_modify_position_fuse.decrease_position(
            token_id=token_id,
            liquidity=liquidity,
            amount0_min=amount0_min,
            amount1_min=amount1_min,
            deadline=deadline,
        )

    def collect(self, token_ids: List[int]) -> FuseAction:
        if not hasattr(self, "_ramses_v2_collect_fuse"):
            raise UnsupportedFuseError(
                "RamsesV2CollectFuse is not supported by PlasmaVault"
            )

        return self._ramses_v2_collect_fuse.collect(token_ids)

    def close_position(self, token_ids: List[int]) -> FuseAction:
        if not hasattr(self, "_ramses_v2_new_position_fuse"):
            raise UnsupportedFuseError(
                "RamsesV2NewPositionFuse is not supported by PlasmaVault"
            )

        return self._ramses_v2_new_position_fuse.close_position(token_ids)

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
        if not hasattr(self, "_ramses_v2_modify_position_fuse"):
            raise UnsupportedFuseError(
                "RamsesV2ModifyPositionFuse is not supported by PlasmaVault"
            )

        return self._ramses_v2_modify_position_fuse.increase_position(
            token0=token0,
            token1=token1,
            token_id=token_id,
            amount0_desired=amount0_desired,
            amount1_desired=amount1_desired,
            amount0_min=amount0_min,
            amount1_min=amount1_min,
            deadline=deadline,
        )

    def claim(self, token_ids: List[int], token_rewards: List[List[str]]) -> FuseAction:
        if not hasattr(self, "_ramses_v2_claim_fuse"):
            raise UnsupportedFuseError(
                "RamsesV2ClaimFuse is not supported by PlasmaVault"
            )

        return self._ramses_v2_claim_fuse.claim(token_ids, token_rewards)

    def ram(self):
        return ERC20(self._transaction_executor, self.RAMSES_V2_RAM_TOKEN)

    def x_ram(self):
        return ERC20(self._transaction_executor, self.RAMSES_V2_X_RAM_TOKEN)
