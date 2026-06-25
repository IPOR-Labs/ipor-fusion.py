"""Euler V2 fuses: collateral supply and EulerSwap v2 LP-pool deploy/decommission.

Mirrors the Solidity `EulerV2SupplyFuse` and `EulerV2SwapDeployFuse`
(`ipor-fusion/contracts/fuses/euler/`). The deploy fuse's `enter` is a CREATE2
pool deployment: the caller must pass a `predicted_pool` equal to
`factory.computePoolAddress(static_params, salt)` whose low 14 address bits
satisfy the Uniswap-v4 hook-flag constraint (salt mined off-chain).
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


def euler_substrate(
    *,
    euler_vault: ChecksumAddress,
    is_collateral: bool,
    can_borrow: bool,
    sub_account: int,
) -> bytes:
    """Pack an Euler `EulerSubstrate` into its bytes32 market-substrate form.

    Layout matches `EulerFuseLib.substrateToBytes32`:
    `eulerVault<<96 | isCollateral<<88 | canBorrow<<80 | subAccounts<<72`.
    """
    _validate_sub_account(sub_account)
    value = (
        (int(euler_vault, 16) << 96)
        | ((1 if is_collateral else 0) << 88)
        | ((1 if can_borrow else 0) << 80)
        | (sub_account << 72)
    )
    return value.to_bytes(32, "big")


def _validate_sub_account(sub_account: int) -> None:
    if not 0 <= sub_account <= 0xFF:
        raise ValueError(
            f"sub_account must be a single byte (0-255), got {sub_account}"
        )


def _sub_account_byte(sub_account: int) -> bytes:
    _validate_sub_account(sub_account)
    return bytes([sub_account])


@dataclass(frozen=True, slots=True)
class EulerSwapStaticParams:
    """Immutable EulerSwap pool configuration (vaults, owner account, fees)."""

    supply_vault0: ChecksumAddress
    supply_vault1: ChecksumAddress
    borrow_vault0: ChecksumAddress
    borrow_vault1: ChecksumAddress
    euler_account: ChecksumAddress
    fee_recipient: ChecksumAddress

    def to_tuple(self) -> list:
        return [
            self.supply_vault0,
            self.supply_vault1,
            self.borrow_vault0,
            self.borrow_vault1,
            self.euler_account,
            self.fee_recipient,
        ]


@dataclass(frozen=True, slots=True)
class EulerSwapDynamicParams:
    """Mutable curve / fee configuration applied on pool activation."""

    equilibrium_reserve0: int
    equilibrium_reserve1: int
    min_reserve0: int
    min_reserve1: int
    price_x: int
    price_y: int
    concentration_x: int
    concentration_y: int
    fee0: int
    fee1: int
    expiration: int
    swap_hooked_operations: int
    swap_hook: ChecksumAddress

    def to_tuple(self) -> list:
        return [
            self.equilibrium_reserve0,
            self.equilibrium_reserve1,
            self.min_reserve0,
            self.min_reserve1,
            self.price_x,
            self.price_y,
            self.concentration_x,
            self.concentration_y,
            self.fee0,
            self.fee1,
            self.expiration,
            self.swap_hooked_operations,
            self.swap_hook,
        ]


@dataclass(frozen=True, slots=True)
class EulerSwapInitialState:
    """Initial virtual reserves used when the pool curve is activated."""

    reserve0: int
    reserve1: int

    def to_tuple(self) -> list:
        return [self.reserve0, self.reserve1]


class EulerV2SupplyFuse(Fuse):
    """Fuse for supplying/withdrawing collateral on an Euler V2 vault sub-account."""

    def supply(
        self,
        *,
        euler_vault: ChecksumAddress,
        max_amount: Amount,
        sub_account: int,
    ) -> FuseAction:
        self._validate_address(euler_vault, "euler_vault")
        return self._action_raw(
            "enter((address,uint256,bytes1))",
            [[euler_vault, max_amount, _sub_account_byte(sub_account)]],
        )

    def withdraw(
        self,
        *,
        euler_vault: ChecksumAddress,
        max_amount: Amount,
        sub_account: int,
    ) -> FuseAction:
        self._validate_address(euler_vault, "euler_vault")
        return self._action_raw(
            "exit((address,uint256,bytes1))",
            [[euler_vault, max_amount, _sub_account_byte(sub_account)]],
        )


class EulerV2SwapDeployFuse(Fuse):
    """Fuse for deploying and decommissioning an EulerSwap v2 LP pool.

    `deploy` installs the (CREATE2-predicted) pool as the EVC account operator
    and deploys it via the factory; `decommission` removes that authorization.
    """

    _ENTER_SIGNATURE = (
        "enter("
        "((address,address,address,address,address,address),"
        "(uint112,uint112,uint112,uint112,uint80,uint80,uint64,uint64,"
        "uint64,uint64,uint40,uint8,address),"
        "(uint112,uint112),bytes32,address,bytes1))"
    )
    _EXIT_SIGNATURE = "exit((address,bytes1))"

    def deploy(
        self,
        *,
        static_params: EulerSwapStaticParams,
        dynamic_params: EulerSwapDynamicParams,
        initial_state: EulerSwapInitialState,
        salt: bytes,
        predicted_pool: ChecksumAddress,
        sub_account: int,
    ) -> FuseAction:
        self._validate_address(predicted_pool, "predicted_pool")
        self._validate_address(
            static_params.euler_account, "static_params.euler_account"
        )
        if len(salt) != 32:
            raise ValueError(f"salt must be 32 bytes, got {len(salt)}")
        return self._action_raw(
            self._ENTER_SIGNATURE,
            [
                [
                    static_params.to_tuple(),
                    dynamic_params.to_tuple(),
                    initial_state.to_tuple(),
                    salt,
                    predicted_pool,
                    _sub_account_byte(sub_account),
                ]
            ],
        )

    def decommission(
        self,
        *,
        pool: ChecksumAddress,
        sub_account: int,
    ) -> FuseAction:
        self._validate_address(pool, "pool")
        return self._action_raw(
            self._EXIT_SIGNATURE,
            [[pool, _sub_account_byte(sub_account)]],
        )


class EulerV2SwapReconfigureFuse(Fuse):
    """Fuse for updating the mutable curve / fee parameters of an EulerSwap pool.

    Reconfiguration is one-directional: there is no `exit` (decommissioning is
    handled by `EulerV2SwapDeployFuse.decommission`).
    """

    _ENTER_SIGNATURE = (
        "enter("
        "(address,bytes1,"
        "(uint112,uint112,uint112,uint112,uint80,uint80,uint64,uint64,"
        "uint64,uint64,uint40,uint8,address),"
        "(uint112,uint112)))"
    )

    def reconfigure(
        self,
        *,
        pool: ChecksumAddress,
        sub_account: int,
        dynamic_params: EulerSwapDynamicParams,
        initial_state: EulerSwapInitialState,
    ) -> FuseAction:
        self._validate_address(pool, "pool")
        return self._action_raw(
            self._ENTER_SIGNATURE,
            [
                [
                    pool,
                    _sub_account_byte(sub_account),
                    dynamic_params.to_tuple(),
                    initial_state.to_tuple(),
                ]
            ],
        )


@dataclass(frozen=True, slots=True)
class EulerV2BatchItem:
    """A single EVC batch operation: a raw call to `target_contract` executed on
    behalf of the vault sub-account `on_behalf_of_account`.

    `data` is the ABI-encoded calldata of the inner op (e.g. `EVC.enableController`,
    `eVault.borrow`, `eVault.repay`, `eVault.disableController`).
    """

    target_contract: ChecksumAddress
    on_behalf_of_account: int
    data: bytes

    def to_tuple(self) -> list:
        return [
            self.target_contract,
            _sub_account_byte(self.on_behalf_of_account),
            self.data,
        ]


class EulerV2BatchFuse(Fuse):
    """Fuse executing a batch of Euler V2 operations atomically via `EVC.batch`.

    Supported inner operations: deposit, withdraw, borrow, repay, repayWithShares,
    enableController, disableController. `assets_for_approvals` /
    `euler_vaults_for_approvals` are set to max-approval before the batch and reset
    to zero after (e.g. to let an eVault pull the asset on deposit/repay).
    """

    _ENTER_SIGNATURE = "enter(((address,bytes1,bytes)[],address[],address[]))"

    def batch(
        self,
        *,
        items: list[EulerV2BatchItem],
        assets_for_approvals: list[ChecksumAddress] | None = None,
        euler_vaults_for_approvals: list[ChecksumAddress] | None = None,
    ) -> FuseAction:
        self._validate_non_empty_list(items, "items")
        assets = list(assets_for_approvals or [])
        vaults = list(euler_vaults_for_approvals or [])
        if len(assets) != len(vaults):
            raise ValueError(
                "assets_for_approvals and euler_vaults_for_approvals must have the "
                f"same length, got {len(assets)} and {len(vaults)}"
            )
        return self._action_raw(
            self._ENTER_SIGNATURE,
            [[[item.to_tuple() for item in items], assets, vaults]],
        )


class EulerV2SwapRegistryFuse(Fuse):
    """Fuse for registering / unregistering an EulerSwap v2 pool in the public registry.

    Registration is always zero-bond (the PlasmaVault cannot source native ETH).
    `unregister` requires the pool's EVC account operator to have been removed
    first (via `EulerV2SwapDeployFuse.decommission`), otherwise the live registry
    reverts `OldOperatorStillInstalled`.
    """

    _ENTER_SIGNATURE = "enter((address,bytes1))"
    _EXIT_SIGNATURE = "exit((address,bytes1))"

    def register(
        self,
        *,
        pool: ChecksumAddress,
        sub_account: int,
    ) -> FuseAction:
        self._validate_address(pool, "pool")
        return self._action_raw(
            self._ENTER_SIGNATURE,
            [[pool, _sub_account_byte(sub_account)]],
        )

    def unregister(
        self,
        *,
        pool: ChecksumAddress,
        sub_account: int,
    ) -> FuseAction:
        self._validate_address(pool, "pool")
        return self._action_raw(
            self._EXIT_SIGNATURE,
            [[pool, _sub_account_byte(sub_account)]],
        )
