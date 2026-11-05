from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


class CompoundV3SupplyFuse(Fuse):
    """Fuse for supplying and withdrawing assets on Compound V3."""

    SIGNATURES = (
        "enter((address,uint256))",
        "exit((address,uint256))",
    )

    def supply(self, *, asset: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((address,uint256))", [[asset, amount]])

    def withdraw(self, *, asset: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw("exit((address,uint256))", [[asset, amount]])


class CompoundV3BorrowFuse(Fuse):
    """Fuse for borrowing and repaying the base token of a Comet.

    The fuse is bound to one Comet, whose base token is the only asset it can
    borrow, so neither action names an asset; the base token must be granted as
    a substrate of the fuse's market. A repay is capped at the debt, so
    ``MAX_UINT256`` repays in full.
    """

    SIGNATURES = (
        "enter((uint256))",
        "exit((uint256))",
    )

    def borrow(self, *, amount: Amount) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((uint256))", [[amount]])

    def repay(self, *, amount: Amount) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw("exit((uint256))", [[amount]])


class CompoundV3ClaimFuse(Fuse):
    """Fuse for claiming a Comet's rewards to the vault's rewards claim manager.

    The fuse is bound to one ``CometRewards``. Route the action through
    ``RewardsManager.claim_rewards(...)``, not ``PlasmaVault.execute(...)``, and
    register the fuse with ``RewardsManager.add_reward_fuses(...)`` first. A
    Comet with no reward token configured reverts ``NotSupported(address)``.
    """

    SIGNATURES = ("claim(address)",)

    def claim(self, *, comet: ChecksumAddress) -> FuseAction:
        self._validate_address(comet, "comet")
        return self._action_raw("claim(address)", [comet])
