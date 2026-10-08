"""Read-only checks for EVM/Core transfers and vault exits.

Transfer outcomes cover EVM-to-Core and Core-to-EVM only. Dex-to-dex sends and
orders need their own evidence, such as fills or the position precompile.

Status: preview, not production. The contracts behind it are under active development, the mainnet deployments are a proof of concept and IPOR Labs canaries, and interfaces may change between minor versions; see ``ipor_fusion.about.PREVIEW_FEATURES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast

from eth_typing import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.readers.hypercore import (
    HYPERCORE_PENDING_READERS,
    HyperCorePendingReader,
    HyperCoreReader,
)
from ipor_fusion.types import Amount, Shares

TransferDirection = Literal["evm_to_core", "core_to_evm"]


class HyperCoreTransferStatus(StrEnum):
    NOT_ENQUEUED = "not_enqueued"
    PENDING = "pending"
    APPLIED = "applied"
    DROPPED = "dropped"
    IN_TRANSIT = "in_transit"
    INDETERMINATE = "indeterminate"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class HyperCoreTransferSnapshot:
    """One block's Core spot, vault ERC-20 and pending-action observations."""

    block_number: int
    vault: ChecksumAddress
    token_index: int
    evm_token: ChecksumAddress
    action_nonce: int
    pending: bool
    settled: bool
    core_user_exists: bool
    core_total_wei: int
    core_hold_wei: int
    evm_balance: Amount


@dataclass(frozen=True, slots=True)
class HyperCoreTransferOutcome:
    """Observed transfer result after one pending window.

    ``DROPPED`` means no Core credit or account activation for an EVM deposit,
    or neither a Core debit nor EVM credit for a Core withdrawal.
    ``IN_TRANSIT`` means Core was debited but EVM was not credited yet. These
    are inferences, valid only without concurrent Core or ERC-20 movements.
    """

    status: HyperCoreTransferStatus
    direction: TransferDirection
    before: HyperCoreTransferSnapshot
    after: HyperCoreTransferSnapshot

    @property
    def core_delta_wei(self) -> int:
        return self.after.core_total_wei - self.before.core_total_wei

    @property
    def evm_delta_units(self) -> int:
        return self.after.evm_balance - self.before.evm_balance

    @property
    def account_activated(self) -> bool:
        return not self.before.core_user_exists and self.after.core_user_exists


@dataclass(frozen=True, slots=True)
class HyperCoreEvmExitCeiling:
    """Upper bound from idle underlying and this holder's shares.

    Roles, redemption locks, request reservations, fees and share rounding can
    lower the amount an actual ``withdraw`` will pay. Simulate the call before
    sending. Core holdings count in ``totalAssets`` but are not idle ERC-20.
    """

    shares: Shares
    share_assets: Amount
    idle_underlying: Amount
    upper_bound_assets: Amount


def _pinned_context(ctx: Web3Context) -> tuple[Web3Context, int]:
    block = ctx.default_block
    if not isinstance(block, int):
        block = ctx.web3.eth.block_number
    pinned = Web3Context(ctx.web3, ctx.chain_id)
    pinned.default_block = block
    return pinned, block


def read_hypercore_transfer_snapshot(
    ctx: Web3Context,
    vault_address: ChecksumAddress,
    evm_token: ChecksumAddress,
    *,
    token_index: int = 0,
    pending_reader: ChecksumAddress | None = None,
) -> HyperCoreTransferSnapshot:
    """Pin one block and batch the observations needed before/after a transfer.

    Capture ``before`` before the EVM transaction. Once the pending window has
    settled, capture ``after`` and pass both to
    :func:`compare_hypercore_transfer`. This does not send a transaction.
    """
    pinned, block = _pinned_context(ctx)
    pending_reader = pending_reader or HYPERCORE_PENDING_READERS.get(ctx.chain_id)
    if pending_reader is None:
        raise ValueError(f"no HyperCorePendingReader known for chain {ctx.chain_id}")
    vault = PlasmaVault(pinned, vault_address)
    reader = HyperCoreReader(pinned)
    calls = cast(
        list[Call[Any]],
        [
            reader.core_user_exists(vault_address),
            reader.spot_balance(vault_address, token_index),
            ERC20(pinned, evm_token).balance_of(vault_address),
            HyperCorePendingReader(vault, pending_reader).pending_state(),
        ],
    )
    exists, spot, evm_balance, pending = Multicall3(pinned).aggregate(calls)
    return HyperCoreTransferSnapshot(
        block_number=block,
        vault=vault_address,
        token_index=token_index,
        evm_token=evm_token,
        action_nonce=pending.action_nonce,
        pending=pending.pending,
        settled=pending.settled,
        core_user_exists=exists,
        core_total_wei=spot.total,
        core_hold_wei=spot.hold,
        evm_balance=evm_balance,
    )


def compare_hypercore_transfer(
    before: HyperCoreTransferSnapshot,
    after: HyperCoreTransferSnapshot,
    direction: TransferDirection,
) -> HyperCoreTransferOutcome:
    """Classify a single transfer from its pending nonce and observed balances.

    The destination change is the proof of application. If the pending action
    settled but the destination did not change, report a probable drop; for a
    first deposit, creation of the Core account also proves application even
    when the activation fee consumes the entire amount.
    """
    if (before.vault, before.token_index, before.evm_token) != (
        after.vault,
        after.token_index,
        after.evm_token,
    ):
        raise ValueError("transfer snapshots must observe the same vault and token")
    if after.block_number <= before.block_number:
        raise ValueError("after snapshot must be from a later block")
    if before.pending:
        raise ValueError("before snapshot must precede an action")
    if direction not in ("evm_to_core", "core_to_evm"):
        raise ValueError(f"unsupported transfer direction: {direction}")

    status = _pending_status(before, after)
    if status is None:
        status = (
            _evm_to_core_status(before, after)
            if direction == "evm_to_core"
            else _core_to_evm_status(before, after)
        )
    return HyperCoreTransferOutcome(status, direction, before, after)


def _pending_status(
    before: HyperCoreTransferSnapshot, after: HyperCoreTransferSnapshot
) -> HyperCoreTransferStatus | None:
    if after.action_nonce < before.action_nonce:
        return HyperCoreTransferStatus.INDETERMINATE
    if after.action_nonce == before.action_nonce:
        return HyperCoreTransferStatus.NOT_ENQUEUED
    if after.action_nonce > before.action_nonce + 1:
        return HyperCoreTransferStatus.SUPERSEDED
    if after.pending:
        return HyperCoreTransferStatus.PENDING
    if not after.settled:
        return HyperCoreTransferStatus.INDETERMINATE
    return None


def _evm_to_core_status(
    before: HyperCoreTransferSnapshot, after: HyperCoreTransferSnapshot
) -> HyperCoreTransferStatus:
    if after.core_total_wei > before.core_total_wei or (
        not before.core_user_exists and after.core_user_exists
    ):
        return HyperCoreTransferStatus.APPLIED
    if (
        after.core_total_wei == before.core_total_wei
        and after.core_user_exists == before.core_user_exists
    ):
        return HyperCoreTransferStatus.DROPPED
    return HyperCoreTransferStatus.INDETERMINATE


def _core_to_evm_status(
    before: HyperCoreTransferSnapshot, after: HyperCoreTransferSnapshot
) -> HyperCoreTransferStatus:
    if after.evm_balance > before.evm_balance:
        return HyperCoreTransferStatus.APPLIED
    if (
        after.evm_balance == before.evm_balance
        and after.core_total_wei < before.core_total_wei
    ):
        return HyperCoreTransferStatus.IN_TRANSIT
    if (
        after.evm_balance == before.evm_balance
        and after.core_total_wei == before.core_total_wei
    ):
        return HyperCoreTransferStatus.DROPPED
    return HyperCoreTransferStatus.INDETERMINATE


def read_hypercore_transfer_outcome(
    ctx: Web3Context,
    before: HyperCoreTransferSnapshot,
    direction: TransferDirection,
    *,
    pending_reader: ChecksumAddress | None = None,
) -> HyperCoreTransferOutcome:
    """Take an after snapshot and classify one Core/EVM transfer."""
    after = read_hypercore_transfer_snapshot(
        ctx,
        before.vault,
        before.evm_token,
        token_index=before.token_index,
        pending_reader=pending_reader,
    )
    return compare_hypercore_transfer(before, after, direction)


def read_hypercore_evm_exit_ceiling(
    ctx: Web3Context, vault_address: ChecksumAddress, holder: ChecksumAddress
) -> HyperCoreEvmExitCeiling:
    """Read the holder's upper bound for an immediate EVM underlying exit.

    This uses idle ERC-20 only and is a ceiling, not an executable quote.
    """
    pinned, _ = _pinned_context(ctx)
    vault = PlasmaVault(pinned, vault_address)
    shares, asset = Multicall3(pinned).aggregate(
        cast(
            list[Call[Any]],
            [vault.balance_of(holder), vault.underlying_asset_address()],
        )
    )
    share_assets, idle = Multicall3(pinned).aggregate(
        cast(
            list[Call[Any]],
            [
                vault.convert_to_assets(shares),
                ERC20(pinned, asset).balance_of(vault_address),
            ],
        )
    )
    return HyperCoreEvmExitCeiling(
        shares=shares,
        share_assets=share_assets,
        idle_underlying=idle,
        upper_bound_assets=Amount(min(share_assets, idle)),
    )
