from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import MorphoBlueMarketId


class MorphoSupplyFuse(Fuse):
    def supply(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        data = encode(["bytes32", "uint256"], [bytes.fromhex(market_id), amount])
        selector = function_signature_to_4byte_selector("enter((bytes32,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)

    def withdraw(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        data = encode(["bytes32", "uint256"], [bytes.fromhex(market_id), amount])
        selector = function_signature_to_4byte_selector("exit((bytes32,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)


class MorphoFlashLoanFuse(Fuse):
    def flash_loan(
        self, asset: ChecksumAddress, amount: int, actions: list[FuseAction]
    ) -> FuseAction:
        bytes_data = [[action.fuse, action.data] for action in actions]
        encoded_actions = encode(["(address,bytes)[]"], [bytes_data])
        data = encode(["(address,uint256,bytes)"], [[asset, amount, encoded_actions]])
        selector = function_signature_to_4byte_selector(
            "enter((address,uint256,bytes))"
        )
        return FuseAction(fuse=self._address, data=selector + data)


class MorphoCollateralFuse(Fuse):
    def supply_collateral(
        self, market_id: MorphoBlueMarketId, amount: int
    ) -> FuseAction:
        data = encode(["bytes32", "uint256"], [bytes.fromhex(market_id), amount])
        selector = function_signature_to_4byte_selector("enter((bytes32,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)

    def withdraw_collateral(
        self, market_id: MorphoBlueMarketId, amount: int
    ) -> FuseAction:
        data = encode(["bytes32", "uint256"], [bytes.fromhex(market_id), amount])
        selector = function_signature_to_4byte_selector("exit((bytes32,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)


class MorphoBorrowFuse(Fuse):
    def borrow(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        data = encode(
            ["bytes32", "uint256", "uint256"], [bytes.fromhex(market_id), amount, 0]
        )
        selector = function_signature_to_4byte_selector(
            "enter((bytes32,uint256,uint256))"
        )
        return FuseAction(fuse=self._address, data=selector + data)

    def repay(self, market_id: MorphoBlueMarketId, amount: int) -> FuseAction:
        data = encode(
            ["bytes32", "uint256", "uint256"], [bytes.fromhex(market_id), amount, 0]
        )
        selector = function_signature_to_4byte_selector(
            "exit((bytes32,uint256,uint256))"
        )
        return FuseAction(fuse=self._address, data=selector + data)


class MorphoClaimFuse(Fuse):
    def claim(
        self,
        universal_rewards_distributor: ChecksumAddress,
        rewards_token: ChecksumAddress,
        claimable: int,
        proof: list[str],
    ) -> FuseAction:
        proofs = [bytes.fromhex(h.removeprefix("0x")) for h in proof]
        data = encode(
            ["address", "address", "uint256", "bytes32[]"],
            [universal_rewards_distributor, rewards_token, claimable, proofs],
        )
        selector = function_signature_to_4byte_selector(
            "claim(address,address,uint256,bytes32[])"
        )
        return FuseAction(fuse=self._address, data=selector + data)
