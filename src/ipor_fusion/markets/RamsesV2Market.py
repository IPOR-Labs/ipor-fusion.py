from typing import List

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.FuseMapper import FuseMapper
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.RamsesClaimFuse import RamsesClaimFuse
from ipor_fusion.fuse.RamsesV2CollectFuse import RamsesV2CollectFuse
from ipor_fusion.fuse.RamsesV2ModifyPositionFuse import RamsesV2ModifyPositionFuse
from ipor_fusion.fuse.RamsesV2NewPositionFuse import RamsesV2NewPositionFuse


class RamsesV2NewPositionEvent:
    def __init__(
        self,
        version,
        token_id,
        liquidity,
        amount0,
        amount1,
        sender,
        recipient,
        fee,
        tick_lower,
        tick_upper,
    ):
        self.version = version
        self.token_id = token_id
        self.liquidity = liquidity
        self.amount0 = amount0
        self.amount1 = amount1
        self.sender = sender
        self.recipient = recipient
        self.fee = fee
        self.tick_lower = tick_lower
        self.tick_upper = tick_upper


class RamsesV2Market:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        fuses: List[str],
        rewards_fuses: List[str],
        rewards_claim_manager: RewardsClaimManager,
    ):
        self._chain_id = chain_id
        self._transaction_executor = transaction_executor
        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse in FuseMapper.map(chain_id, "RamsesV2NewPositionFuse"):
                self._ramses_v2_new_position_fuse = RamsesV2NewPositionFuse(
                    checksum_fuse
                )
                self._any_fuse_supported = True
            if checksum_fuse in FuseMapper.map(chain_id, "RamsesV2ModifyPositionFuse"):
                self._ramses_v2_modify_position_fuse = RamsesV2ModifyPositionFuse(
                    checksum_fuse
                )
                self._any_fuse_supported = True
            if checksum_fuse in FuseMapper.map(chain_id, "RamsesV2CollectFuse"):
                self._ramses_v2_collect_fuse = RamsesV2CollectFuse(checksum_fuse)
                self._any_fuse_supported = True

        for rewards_fuse in rewards_fuses:
            checksum_rewards_fuse = Web3.to_checksum_address(rewards_fuse)
            if checksum_rewards_fuse in FuseMapper.map(chain_id, "RamsesClaimFuse"):
                self._ramses_v2_claim_fuse = RamsesClaimFuse(checksum_rewards_fuse)
                self._any_fuse_supported = True

        if not rewards_fuses:
            for rewards_fuse in FuseMapper.map(chain_id, "RamsesClaimFuse"):
                if rewards_claim_manager.is_reward_fuse_supported(rewards_fuse):
                    self._ramses_v2_claim_fuse = RamsesClaimFuse(rewards_fuse)
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
                "RamsesClaimFuse is not supported by PlasmaVault"
            )

        return self._ramses_v2_claim_fuse.claim(token_ids, token_rewards)

    def ram(self):
        return ERC20(self._transaction_executor, self.get_RAM())

    def x_ram(self):
        return ERC20(self._transaction_executor, self.get_xRAM())

    def extract_new_position_enter_events(
        self, receipt: TxReceipt
    ) -> List[RamsesV2NewPositionEvent]:
        event_signature_hash = Web3.keccak(
            text="RamsesV2NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
        )

        result = []
        for evnet_log in receipt.logs:
            if evnet_log.topics[0] == event_signature_hash:
                decoded_data = decode(
                    [
                        "address",
                        "uint256",
                        "uint128",
                        "uint256",
                        "uint256",
                        "address",
                        "address",
                        "uint24",
                        "int24",
                        "int24",
                    ],
                    evnet_log["data"],
                )
                (
                    version,
                    token_id,
                    liquidity,
                    amount0,
                    amount1,
                    sender,
                    recipient,
                    fee,
                    tick_lower,
                    tick_upper,
                ) = decoded_data
                result.append(
                    RamsesV2NewPositionEvent(
                        version=version,
                        token_id=token_id,
                        liquidity=liquidity,
                        amount0=amount0,
                        amount1=amount1,
                        sender=sender,
                        recipient=recipient,
                        fee=fee,
                        tick_lower=tick_lower,
                        tick_upper=tick_upper,
                    )
                )
        return result

    def get_RAM(self) -> ChecksumAddress:
        if self._chain_id == 42161:
            return Web3.to_checksum_address(
                "0xAAA6C1E32C55A7Bfa8066A6FAE9b42650F262418"
            )

        raise BaseException("Chain ID not supported")

    def get_xRAM(self) -> ChecksumAddress:
        if self._chain_id == 42161:
            return Web3.to_checksum_address(
                "0xAAA1eE8DC1864AE49185C368e8c64Dd780a50Fb7"
            )

        raise BaseException("Chain ID not supported")
