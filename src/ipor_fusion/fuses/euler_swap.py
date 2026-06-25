from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.fuses.base import ZERO_ADDRESS, Fuse, FuseAction

# ABI type fragments for the shared IEulerV2Swap param structs. These are the single source of the
# struct shapes; field *order* lives only in each dataclass's to_tuple(). Keep the two in sync.
_STATIC = "(address,address,address,address,address,address)"
_DYNAMIC = "(uint112,uint112,uint112,uint112,uint80,uint80,uint64,uint64,uint64,uint64,uint40,uint8,address)"
_INITIAL = "(uint112,uint112)"

# Per-side swap fee is scaled to 1e18 == 100% (mirrors MAX_FEE in the fuse contracts).
_MAX_FEE = 10**18


def euler_account(vault: ChecksumAddress, sub_account: bytes) -> ChecksumAddress:
    """The EVC account that owns the LP position: vault XOR sub_account (low byte only).

    Mirrors EulerFuseLib.generateSubAccountAddress: uint160(vault) ^ uint160(uint8(subAccount)).
    StaticParams.euler_account must equal this or EulerV2SwapDeployFuse.enter reverts
    EulerV2SwapDeployFuseInvalidEulerAccount.
    """
    _validate_sub_account(sub_account)
    xored = int(vault, 16) ^ sub_account[0]
    return Web3.to_checksum_address(xored.to_bytes(20, "big"))


def _validate_sub_account(sub_account: bytes) -> None:
    if len(sub_account) != 1:
        raise ValueError(f"sub_account must be exactly 1 byte, got {len(sub_account)}")


def _validate_salt(salt: bytes) -> None:
    if len(salt) != 32:
        raise ValueError(f"salt must be exactly 32 bytes, got {len(salt)}")


def _validate_fee(fee: int, name: str) -> None:
    if fee < 0 or fee >= _MAX_FEE:
        raise ValueError(f"{name} must be in [0, 1e18), got {fee}")


@dataclass(frozen=True, slots=True)
class StaticParams:
    """Immutable pool configuration. `fee_recipient` is contract-fixed to address(0) (fees compound
    into the supply vault), so it is not a caller field — it is pinned in to_tuple().
    """

    supply_vault0: ChecksumAddress
    supply_vault1: ChecksumAddress
    borrow_vault0: ChecksumAddress  # may be ZERO_ADDRESS (supply-only / non-JIT pool)
    borrow_vault1: ChecksumAddress  # may be ZERO_ADDRESS (supply-only / non-JIT pool)
    euler_account: ChecksumAddress

    def to_tuple(self) -> tuple:
        return (
            self.supply_vault0,
            self.supply_vault1,
            self.borrow_vault0,
            self.borrow_vault1,
            self.euler_account,
            ZERO_ADDRESS,  # feeRecipient: contract-fixed 0 (fees compound into the supply vault)
        )


@dataclass(frozen=True, slots=True)
class DynamicParams:
    """Mutable curve / fee configuration. `swap_hook` / `swap_hooked_operations` are contract-fixed
    to 0 (no external swap hook over vault funds), so they are not caller fields — they are pinned in
    to_tuple()."""

    equilibrium_reserve0: int  # uint112
    equilibrium_reserve1: int  # uint112
    min_reserve0: int  # uint112
    min_reserve1: int  # uint112
    price_x: int  # uint80
    price_y: int  # uint80
    concentration_x: int  # uint64
    concentration_y: int  # uint64
    fee0: int  # uint64
    fee1: int  # uint64
    expiration: int  # uint40

    def to_tuple(self) -> tuple:
        return (
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
            0,  # swapHookedOperations: contract-fixed 0
            ZERO_ADDRESS,  # swapHook: contract-fixed 0 (no external hook)
        )


@dataclass(frozen=True, slots=True)
class InitialState:
    """Initial virtual reserves used when (re)activating the pool curve."""

    reserve0: int  # uint112
    reserve1: int  # uint112

    def to_tuple(self) -> tuple:
        return (self.reserve0, self.reserve1)


class EulerV2SwapDeployFuse(Fuse):
    """Fuse for deploying / decommissioning an EulerSwap pool owned by a vault sub-account."""

    def deploy(
        self,
        *,
        static: StaticParams,
        dynamic: DynamicParams,
        initial: InitialState,
        salt: bytes,
        predicted_pool: ChecksumAddress,
        sub_account: bytes,
    ) -> FuseAction:
        self._validate_address(static.supply_vault0, "static.supply_vault0")
        self._validate_address(static.supply_vault1, "static.supply_vault1")
        self._validate_address(static.euler_account, "static.euler_account")
        self._validate_address(predicted_pool, "predicted_pool")
        _validate_fee(dynamic.fee0, "dynamic.fee0")
        _validate_fee(dynamic.fee1, "dynamic.fee1")
        _validate_salt(salt)
        _validate_sub_account(sub_account)
        return self._action_raw(
            f"enter(({_STATIC},{_DYNAMIC},{_INITIAL},bytes32,address,bytes1))",
            [
                [
                    static.to_tuple(),
                    dynamic.to_tuple(),
                    initial.to_tuple(),
                    salt,
                    predicted_pool,
                    sub_account,
                ]
            ],
        )

    def decommission(self, *, pool: ChecksumAddress, sub_account: bytes) -> FuseAction:
        self._validate_address(pool, "pool")
        _validate_sub_account(sub_account)
        return self._action_raw("exit((address,bytes1))", [[pool, sub_account]])


class EulerV2SwapReconfigureFuse(Fuse):
    """Fuse for updating an EulerSwap pool's mutable curve / fee params (enter only).

    There is no exit: the on-chain exit() reverts (UnsupportedOperation); reconfiguration is
    one-directional and decommissioning is handled by EulerV2SwapDeployFuse.decommission().
    """

    def reconfigure(
        self,
        *,
        pool: ChecksumAddress,
        sub_account: bytes,
        dynamic: DynamicParams,
        initial: InitialState,
    ) -> FuseAction:
        self._validate_address(pool, "pool")
        _validate_fee(dynamic.fee0, "dynamic.fee0")
        _validate_fee(dynamic.fee1, "dynamic.fee1")
        _validate_sub_account(sub_account)
        return self._action_raw(
            f"enter((address,bytes1,{_DYNAMIC},{_INITIAL}))",
            [[pool, sub_account, dynamic.to_tuple(), initial.to_tuple()]],
        )


class EulerV2SwapRegistryFuse(Fuse):
    """Fuse for (un)registering an EulerSwap pool in the public registry."""

    def register(self, *, pool: ChecksumAddress, sub_account: bytes) -> FuseAction:
        self._validate_address(pool, "pool")
        _validate_sub_account(sub_account)
        return self._action_raw("enter((address,bytes1))", [[pool, sub_account]])

    def unregister(self, *, pool: ChecksumAddress, sub_account: bytes) -> FuseAction:
        self._validate_address(pool, "pool")
        _validate_sub_account(sub_account)
        return self._action_raw("exit((address,bytes1))", [[pool, sub_account]])
