from eth_abi import encode
from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import MorphoBlueMarketId


class MorphoSupplyFuse(Fuse):
    def supply(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        return self._action_raw(
            "enter((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )

    def withdraw(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )


class MorphoFlashLoanFuse(Fuse):
    def flash_loan(
        self, asset: ChecksumAddress, amount: int, actions: list[FuseAction]
    ) -> FuseAction:
        bytes_data = [[action.fuse, action.data] for action in actions]
        encoded_actions = encode(["(address,bytes)[]"], [bytes_data])
        return self._action_raw(
            "enter((address,uint256,bytes))",
            ["(address,uint256,bytes)"],
            [[asset, amount, encoded_actions]],
        )


class MorphoCollateralFuse(Fuse):
    def supply_collateral(
        self, market_id: MorphoBlueMarketId, amount: int
    ) -> FuseAction:
        return self._action_raw(
            "enter((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )

    def withdraw_collateral(
        self, market_id: MorphoBlueMarketId, amount: int
    ) -> FuseAction:
        return self._action_raw(
            "exit((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )


class MorphoBorrowFuse(Fuse):
    def borrow(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        return self._action_raw(
            "enter((bytes32,uint256,uint256))",
            ["bytes32", "uint256", "uint256"],
            [bytes.fromhex(market_id), amount, 0],
        )

    def repay(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((bytes32,uint256,uint256))",
            ["bytes32", "uint256", "uint256"],
            [bytes.fromhex(market_id), amount, 0],
        )


class MorphoClaimFuse(Fuse):
    def claim(
        self,
        universal_rewards_distributor: ChecksumAddress,
        rewards_token: ChecksumAddress,
        claimable: int,
        proof: list[str],
    ) -> FuseAction:
        proofs = [bytes.fromhex(h.removeprefix("0x")) for h in proof]
        return self._action_raw(
            "claim(address,address,uint256,bytes32[])",
            ["address", "address", "uint256", "bytes32[]"],
            [universal_rewards_distributor, rewards_token, claimable, proofs],
        )
