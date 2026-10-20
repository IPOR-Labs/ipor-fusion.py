"""A vault's fee configuration (vault fee data plus the FeeManager hop) and its
WithdrawManager snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

from eth_abi.exceptions import DecodingError
from web3.types import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.fee_manager import (
    FeeAccount,
    FeeManager,
    HighWaterMarkPerformanceFee,
    RecipientFee,
)
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.core.withdraw_manager import AccountRequest, WithdrawManager
from ipor_fusion.errors import EmptyCallResultError
from ipor_fusion.types import ZERO_ADDRESS, Shares
from ipor_fusion.vault_reads import _read_batch, _safe_call, _scan_or_none


@dataclass
class FeeData:
    """Vault fee configuration: vault-level fee data plus FeeManager state.

    Every field is optional because each degrades independently: a vault may
    have no fee account configured (no FeeManager at all), and FeeManagers
    predating the deposit fee / high-water mark revert on those getters.
    Scales differ per source, so every field names its own: `*_bps` is basis
    points (10000 = 100%), `*_wad` is WAD (1e18 = 100%). Pair each with the
    matching converter — `_bps_to_percent` / `_wad_to_percent` — and a
    mismatch reads wrong at the call site.

    `*_vault_bps` and `*_manager_bps` are the SAME fee read from two places:
    the vault charges the former, the FeeManager believes it configured the
    latter. They agree in a healthy deployment; a difference means the two
    configurations have drifted.
    """

    performance_fee_vault_bps: int | None = None
    management_fee_vault_bps: int | None = None
    management_fee_last_update: int | None = None
    unrealized_management_fee: int | None = None
    fee_manager: ChecksumAddress | None = None
    ipor_dao_fee_recipient: ChecksumAddress | None = None
    deposit_fee_wad: int | None = None
    performance_fee_manager_bps: int | None = None
    management_fee_manager_bps: int | None = None
    performance_fee_recipients: list[RecipientFee] | None = None
    management_fee_recipients: list[RecipientFee] | None = None
    high_water_mark: HighWaterMarkPerformanceFee | None = None


@dataclass
class WithdrawManagerData:
    """On-chain state snapshot from the WithdrawManager contract."""

    withdraw_window: int
    # None (not 0) when the getter failed: a zero fee and an unreadable fee
    # are different facts, and the fees section reports both.
    request_fee: int | None
    withdraw_fee: int | None
    shares_to_release: int
    last_release_funds_timestamp: int
    # None when the WithdrawRequestUpdated scan or its reads failed.
    pending_requests: list[AccountRequest] | None
    # convertToAssets of shares_to_release at the snapshot block; None when
    # the read failed (or there is nothing to release: 0).
    shares_to_release_assets: int | None = None
    # convertToAssets of each pending request's shares, by account; a failed
    # read is None.
    pending_request_assets: dict[ChecksumAddress, int | None] = field(
        default_factory=dict
    )


def _fetch_withdraw_manager(
    ctx: Web3Context, plasma_vault: PlasmaVault
) -> tuple[ChecksumAddress | None, WithdrawManagerData | None]:
    """Resolve the vault's WithdrawManager and snapshot its state."""
    address = plasma_vault.read_withdraw_manager_address(ctx)
    if not address:
        return None, None
    wm = WithdrawManager(address)
    _, (window, request_fee, withdraw_fee, shares, last_ts) = _read_batch(
        ctx,
        [],
        [
            wm.get_withdraw_window(),
            wm.get_request_fee(),
            wm.get_withdraw_fee(),
            wm.get_shares_to_release(),
            wm.get_last_release_funds_timestamp(),
        ],
    )
    pending = _scan_or_none(partial(wm.read_pending_requests, ctx))
    to_release = Shares(shares or 0)
    _, (release_assets, *request_assets) = _read_batch(
        ctx,
        [],
        [
            plasma_vault.convert_to_assets(to_release),
            *(plasma_vault.convert_to_assets(req.shares) for req in pending or []),
        ],
    )
    return address, WithdrawManagerData(
        withdraw_window=window or 0,
        request_fee=request_fee,
        withdraw_fee=withdraw_fee,
        shares_to_release=to_release,
        last_release_funds_timestamp=last_ts or 0,
        pending_requests=pending,
        shares_to_release_assets=release_assets,
        pending_request_assets={
            req.account: assets
            for req, assets in zip(pending or [], request_assets, strict=True)
        },
    )


def _fetch_fee_manager_address(
    ctx: Web3Context, fee_account: ChecksumAddress
) -> ChecksumAddress | None:
    """Resolve FeeAccount.FEE_MANAGER(); None when it is not a FeeAccount.

    The fee account reported by a vault is not guaranteed to be a FeeAccount
    contract — older deployments can name a plain recipient address. An EOA
    answers eth_call with empty data (`EmptyCallResultError`) instead of
    reverting, so `_safe_call` alone does not cover it; data too short to
    decode is treated the same.
    """
    try:
        return _safe_call(partial(FeeAccount(fee_account).fee_manager().call, ctx))
    except (EmptyCallResultError, DecodingError):
        return None


def _fetch_fee_data(ctx: Web3Context, plasma_vault: PlasmaVault) -> FeeData:
    """Fetch the vault's fee configuration, including the FeeManager hop.

    Three dependent round trips: vault-level fee data, then
    FeeAccount.FEE_MANAGER() to discover the FeeManager, then the FeeManager
    getters. Nothing raises on a revert — every read degrades to ``None``, so
    vaults without a fee account and FeeManagers predating the deposit fee or
    high-water mark still produce a usable snapshot.
    """
    _, (perf, mgmt, unrealized) = _read_batch(
        ctx,
        [],
        [
            plasma_vault.get_performance_fee_data(),
            plasma_vault.get_management_fee_data(),
            plasma_vault.get_unrealized_management_fee(),
        ],
    )
    result = FeeData(
        performance_fee_vault_bps=perf.fee_in_percentage if perf else None,
        management_fee_vault_bps=mgmt.fee_in_percentage if mgmt else None,
        management_fee_last_update=mgmt.last_update_timestamp if mgmt else None,
        unrealized_management_fee=unrealized,
    )

    # A FeeManager deploys two separate escrow accounts (performance and
    # management) but is itself the FEE_MANAGER of both, so either hop
    # resolves to the same address; take whichever is configured.
    fee_account = next(
        (
            data.fee_account
            for data in (perf, mgmt)
            if data is not None and data.fee_account != ZERO_ADDRESS
        ),
        None,
    )
    if fee_account is None:
        return result

    fee_manager_addr = _fetch_fee_manager_address(ctx, fee_account)
    if not fee_manager_addr or fee_manager_addr == ZERO_ADDRESS:
        return result
    result.fee_manager = fee_manager_addr

    fee_manager = FeeManager(fee_manager_addr)
    (
        _,
        (
            result.deposit_fee_wad,
            result.performance_fee_manager_bps,
            result.management_fee_manager_bps,
            result.performance_fee_recipients,
            result.management_fee_recipients,
            result.ipor_dao_fee_recipient,
            result.high_water_mark,
        ),
    ) = _read_batch(
        ctx,
        [],
        [
            fee_manager.get_deposit_fee(),
            fee_manager.get_total_performance_fee(),
            fee_manager.get_total_management_fee(),
            fee_manager.get_performance_fee_recipients(),
            fee_manager.get_management_fee_recipients(),
            fee_manager.get_ipor_dao_fee_recipient_address(),
            fee_manager.get_plasma_vault_high_water_mark_performance_fee(),
        ],
    )
    return result
