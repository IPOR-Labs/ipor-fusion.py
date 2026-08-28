"""External-state operation fuse (market 50).

Moves off-vault capital through the per-vault ExternalStateExecutor. ``enter``
transfers ``amount`` of ``asset`` from the PlasmaVault to the executor, records
it against ``balance_account``, and runs ``actions`` from the executor context;
``exit`` inverts it (actions first, then the asset is pulled back to the vault).
The classic use is the margin leg of a delta-neutral strategy: an action calls
``USDC.transfer(<venue system address>, amount)`` to credit the vault's off-chain
trading account (e.g. Hyperliquid Core).

ABI (ExternalStateOperationFuse.sol), same shape for both, semantics invert::

    enter((address asset, uint256 amount, address balanceAccount,
           (address target, bytes data)[] actions))
    exit(( ... ))
"""

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount

#: One external call run from the executor context: (target, calldata).
ExternalStateAction = tuple[ChecksumAddress, bytes]


class ExternalStateOperationFuse(Fuse):
    """Fuse for moving off-vault capital via the per-vault ExternalStateExecutor."""

    _TUPLE = "(address,uint256,address,(address,bytes)[])"

    def enter(
        self,
        *,
        asset: ChecksumAddress,
        amount: Amount,
        balance_account: ChecksumAddress,
        actions: list[ExternalStateAction],
    ) -> FuseAction:
        """Transfer ``amount`` of ``asset`` vault->executor, record it against
        ``balance_account``, and run ``actions`` from the executor context.

        ``amount`` may be zero for an actions-only enter; ``asset`` may then be
        the zero address.
        """
        return self._build("enter", asset, amount, balance_account, actions)

    def exit(
        self,
        *,
        asset: ChecksumAddress,
        amount: Amount,
        balance_account: ChecksumAddress,
        actions: list[ExternalStateAction],
    ) -> FuseAction:
        """Run ``actions``, decrement the tracked ``balance_account``, and pull
        ``amount`` of ``asset`` back from the executor to the vault."""
        return self._build("exit", asset, amount, balance_account, actions)

    def _build(
        self,
        method: str,
        asset: ChecksumAddress,
        amount: Amount,
        balance_account: ChecksumAddress,
        actions: list[ExternalStateAction],
    ) -> FuseAction:
        if amount < 0:
            raise ValueError(f"amount must not be negative, got {amount}")
        self._validate_address(balance_account, "balance_account")
        # asset only moves when amount > 0; an actions-only call may omit it.
        if amount > 0:
            self._validate_address(asset, "asset")
        for index, (target, _) in enumerate(actions):
            self._validate_address(target, f"actions[{index}].target")
        encoded_actions = [[target, data] for target, data in actions]
        return self._action_raw(
            f"{method}({self._TUPLE})",
            [[asset, amount, balance_account, encoded_actions]],
        )
