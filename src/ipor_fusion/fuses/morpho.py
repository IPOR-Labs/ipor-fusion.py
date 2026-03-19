from eth_abi import encode
from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount, MorphoBlueMarketId


class MorphoSupplyFuse(Fuse):
    """Fuse for supplying and withdrawing assets on Morpho Blue markets."""

    def supply(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "enter((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )

    def withdraw(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "exit((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )


class MorphoFlashLoanFuse(Fuse):
    """Fuse for executing flash loans through Morpho Blue."""

    def flash_loan(
        self, asset: ChecksumAddress, amount: Amount, actions: list[FuseAction]
    ) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        bytes_data = [[action.fuse, action.data] for action in actions]
        encoded_actions = encode(["(address,bytes)[]"], [bytes_data])
        return self._action_raw(
            "enter((address,uint256,bytes))",
            ["(address,uint256,bytes)"],
            [[asset, amount, encoded_actions]],
        )


class MorphoCollateralFuse(Fuse):
    """Fuse for supplying and withdrawing collateral on Morpho Blue markets."""

    def supply_collateral(
        self, market_id: MorphoBlueMarketId, amount: Amount
    ) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "enter((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )

    def withdraw_collateral(
        self, market_id: MorphoBlueMarketId, amount: Amount
    ) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "exit((bytes32,uint256))",
            ["bytes32", "uint256"],
            [bytes.fromhex(market_id), amount],
        )


class MorphoBorrowFuse(Fuse):
    def borrow(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "enter((bytes32,uint256,uint256))",
            ["bytes32", "uint256", "uint256"],
            [bytes.fromhex(market_id), amount, 0],
        )

    def repay(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        self._validate_amount(amount, "amount")
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
        claimable: Amount,
        proof: list[str],
    ) -> FuseAction:
        self._validate_address(
            universal_rewards_distributor, "universal_rewards_distributor"
        )
        self._validate_address(rewards_token, "rewards_token")
        self._validate_amount(claimable, "claimable")
        proofs = [bytes.fromhex(h.removeprefix("0x")) for h in proof]
        return self._action_raw(
            "claim(address,address,uint256,bytes32[])",
            ["address", "address", "uint256", "bytes32[]"],
            [universal_rewards_distributor, rewards_token, claimable, proofs],
        )
