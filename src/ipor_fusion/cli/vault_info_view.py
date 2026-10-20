from __future__ import annotations

import json
from functools import partial
from typing import Any

import click

from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.cli.config_store import (
    FusionConfig,
)
from ipor_fusion.cli.vault_health import (
    _print_erc20_balances,
    _print_health_check,
    _print_reconciliation,
)
from ipor_fusion.cli.vault_markets_view import _print_lending_health, _print_markets
from ipor_fusion.cli.vault_rendering import (
    _format_age,
    _format_amount,
    _format_remaining,
    _format_usd,
    _print_table,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.fee_manager import RecipientFee
from ipor_fusion.explorer import get_contract_name
from ipor_fusion.readers.hypercore import HyperCorePendingState
from ipor_fusion.substrates import (
    format_market_label,
)
from ipor_fusion.types import ZERO_ADDRESS, ChainId, PlasmaVaultAddress, Shares
from ipor_fusion.validation import (
    validate_vault_state,
)
from ipor_fusion.vault_data import VaultData, fetch_deployment_info
from ipor_fusion.vault_info import (
    BLOCK_EXPLORER_URLS,
    IPOR_APP_URL,
    UINT256_MAX,
    _bps_to_percent,
    _role_accounts_json,
    _unix_to_iso,
    _wad_to_percent,
    _wad_to_usd,
    build_vault_info_json,
)


def _print_role_accounts_table(role_accounts: list[dict[str, Any]]) -> None:
    _print_table(
        ("Account", "Role", "Role ID", "Delay (s)"),
        [
            (
                entry["account"],
                entry["role_name"],
                str(entry["role_id"]),
                str(entry["execution_delay"]),
            )
            for entry in role_accounts
        ],
    )


def _print_role_accounts(role_accounts: list[dict[str, Any]] | None) -> None:
    click.echo("Role Accounts:")
    if role_accounts is None:
        click.echo("  (unavailable — the RoleGranted log scan did not complete)")
    else:
        _print_role_accounts_table(role_accounts)
    click.echo()


def _print_vault_info(
    ctx: Web3Context, cfg: FusionConfig, data: VaultData, json_output: bool = False
) -> None:
    api_key = cfg.etherscan_api_key
    chain_id = data.chain_id
    chain_label = CHAIN_NAMES.get(chain_id, str(chain_id))

    (
        data.deployment_block,
        data.deployment_timestamp,
        data.deployment_error,
    ) = fetch_deployment_info(ctx, data.vault, api_key=api_key)

    if json_output:
        result = build_vault_info_json(
            data,
            resolve_name=partial(
                get_contract_name, chain_id, api_key=api_key, cache=ctx.cache
            ),
        )
        click.echo(json.dumps(result, indent=2))
        return

    _print_identity(data, data.vault, chain_id, chain_label)
    _print_supply(data)
    _print_managers(data)
    click.echo()

    _print_fees(data)
    click.echo()

    _print_role_accounts(_role_accounts_json(data))

    _print_markets(data, api_key)

    validation = validate_vault_state(data)
    click.echo("ERC20 Balances (vault holdings):")
    _print_erc20_balances(data, validation.erc20)
    click.echo()

    _print_reconciliation(data, validation)
    click.echo()

    _print_lending_health(data)
    click.echo()
    _print_hypercore(data)

    _print_health_check(validation)


def _print_identity(
    data: VaultData,
    vault_address: PlasmaVaultAddress,
    chain_id: ChainId,
    chain_label: str,
) -> None:
    name_suffix = f" ({data.vault_name})" if data.vault_name else ""
    click.echo(f"Vault:            {vault_address}{name_suffix}")
    if explorer_base := BLOCK_EXPLORER_URLS.get(chain_id):
        click.echo(f"Etherscan:        {explorer_base}/address/{vault_address}")
    click.echo(f"IPOR app:         {IPOR_APP_URL}/{chain_label}/{vault_address}")
    click.echo(f"Chain:            {chain_label} (chain-id={chain_id})")
    block_suffix = " (latest)" if data.is_latest else ""
    click.echo(f"Block:            {data.block_number}{block_suffix}")
    block_iso = _unix_to_iso(data.block_timestamp)
    click.echo(f"Block time:       {data.block_timestamp} ({block_iso})")
    if data.deployment_block is not None and data.deployment_timestamp is not None:
        deploy_iso = _unix_to_iso(data.deployment_timestamp)
        age = _format_age(data.deployment_timestamp)
        click.echo(
            f"Deployed at:      block {data.deployment_block} ({deploy_iso}, {age})"
        )
    elif data.deployment_error:
        click.echo(f"Deployed at:      N/A ({data.deployment_error})")
    else:
        click.echo("Deployed at:      N/A")


def _print_supply(data: VaultData) -> None:
    total_assets_usd = _format_usd(
        data.total_assets, data.asset_decimals, data.asset_price_usd
    )
    click.echo(f"Asset:            {data.asset} ({data.asset_symbol})")
    click.echo(f"Asset decimals:   {data.asset_decimals}")
    click.echo(f"Share decimals:   {data.share_decimals}")
    if data.asset_price_usd is not None:
        click.echo(f"Asset price:      ${data.asset_price_usd:,.2f}")
    else:
        click.echo("Asset price:      N/A")
    click.echo(
        f"Total Assets:     "
        f"{_format_amount(data.total_assets, data.asset_decimals)} "
        f"{data.asset_symbol}{total_assets_usd}"
    )
    click.echo(
        f"Total Supply:     "
        f"{_format_amount(data.total_supply, data.share_decimals)} shares"
    )
    if data.total_supply > 0:
        share_price = (data.total_assets / 10**data.asset_decimals) / (
            data.total_supply / 10**data.share_decimals
        )
        share_price_usd = (
            f" (${share_price * data.asset_price_usd:,.6f})"
            if data.asset_price_usd is not None
            else ""
        )
        click.echo(
            f"Share Price:      {share_price:,.6f} {data.asset_symbol}{share_price_usd}"
        )
    if data.supply_cap == UINT256_MAX:
        click.echo("Supply Cap:       unlimited")
    else:
        click.echo(
            f"Supply Cap:       "
            f"{_format_amount(data.supply_cap, data.share_decimals)} shares"
        )


def _print_managers(data: VaultData) -> None:
    click.echo(f"Access Manager:   {data.access_manager}")
    click.echo(f"Price Oracle:     {data.price_oracle_addr}")
    click.echo(f"Rewards Manager:  {data.rewards_manager or 'N/A'}")
    click.echo(f"Withdraw Manager: {data.withdraw_manager or 'N/A'}")
    if data.withdraw_manager_data:
        wmd = data.withdraw_manager_data
        window_h = wmd.withdraw_window / 3600
        click.echo(f"  Window:         {wmd.withdraw_window}s ({window_h:.1f}h)")
        click.echo('  Fees:           see the "Fees" section below')
        release_fmt = _format_amount(wmd.shares_to_release, data.share_decimals)
        click.echo(f"  Shares to release: {release_fmt}")
        if wmd.last_release_funds_timestamp > 0:
            last_iso = _unix_to_iso(wmd.last_release_funds_timestamp)
            click.echo(f"  Last release:   {last_iso}")
        _print_pending_requests(data)


def _format_fee_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    # Four decimals would render a small-but-real fee as a flat 0.0000%, which
    # reads as "free". Flag it instead; --json carries the exact raw value.
    if 0 < value < 0.0001:
        return "<0.0001%"
    return f"{value:.4f}%"


def _print_fee_with_recipients(
    label: str,
    charged_bps: int | None,
    total_bps: int | None,
    recipients: list[RecipientFee] | None,
) -> None:
    charged = _bps_to_percent(charged_bps)
    total = _bps_to_percent(total_bps)
    line = f"  {label}: {_format_fee_percent(charged)}"
    # Vault and FeeManager should agree; only the disagreement is worth
    # printing, since it means the two configurations have drifted. A missing
    # vault-side value is a failed read, not drift, so it must not flag.
    if charged is not None and total is not None and total != charged:
        line += f"   (FeeManager total: {_format_fee_percent(total)} - MISMATCH)"
    click.echo(line)
    if recipients:
        click.echo("    recipients:")
    for recipient_fee in recipients or []:
        share = _format_fee_percent(_bps_to_percent(recipient_fee.fee_value))
        click.echo(f"      {recipient_fee.recipient}  {share}")


def _print_fees(data: VaultData) -> None:
    """Human rendering of the `fees` JSON object.

    Labels follow the IPOR app's Fees And Limits table so the CLI and the app
    name the same thing the same way; the on-chain getter is shown alongside
    because the JSON keys mirror the contracts, not the app.
    """
    if (fees := data.fee_data) is None:
        return
    wmd = data.withdraw_manager_data

    click.echo("Fees:")
    click.echo(f"  Fee Manager:      {fees.fee_manager or 'N/A'}")
    dao_payee = fees.ipor_dao_fee_recipient
    if not dao_payee or dao_payee == ZERO_ADDRESS:
        dao_payee = "N/A"
    click.echo(f"  IPOR DAO payee:   {dao_payee}")
    onboarding_fee = _format_fee_percent(_wad_to_percent(fees.deposit_fee_wad))
    instant_fee = _format_fee_percent(
        _wad_to_percent(wmd.withdraw_fee) if wmd else None
    )
    scheduled_fee = _format_fee_percent(
        _wad_to_percent(wmd.request_fee) if wmd else None
    )
    click.echo(f"  Onboarding Contribution (depositFee): {onboarding_fee}")
    click.echo("  Offboarding Contribution - charged on one path only, never both:")
    click.echo(f"    for Instant Withdrawals (withdrawFee):   {instant_fee}")
    click.echo(
        f"    for Scheduled Withdrawals (requestFee):  "
        f"{scheduled_fee}   charged at request time"
    )

    _print_fee_with_recipients(
        "Performance Fee",
        fees.performance_fee_vault_bps,
        fees.performance_fee_manager_bps,
        fees.performance_fee_recipients,
    )
    if hwm := fees.high_water_mark:
        hwm_line = f"    high-water mark: {hwm.high_water_mark}"
        if hwm.last_update > 0:
            hwm_line += f" (updated {_unix_to_iso(hwm.last_update)})"
        else:
            # 0 is the contract's "never" sentinel, common on a fresh vault.
            hwm_line += " (never updated)"
        click.echo(hwm_line)

    _print_fee_with_recipients(
        "Management Fee",
        fees.management_fee_vault_bps,
        fees.management_fee_manager_bps,
        fees.management_fee_recipients,
    )
    if fees.unrealized_management_fee is not None:
        accrued = _format_amount(fees.unrealized_management_fee, data.asset_decimals)
        click.echo(f"    accrued, uncollected: {accrued} {data.asset_symbol}")


def _print_hypercore(data: VaultData) -> None:
    state = data.hypercore
    if state is None:
        return
    nav = state.nav
    click.echo(f"HyperCore ({format_market_label(state.market_id)}):")
    if not nav.core_user_exists:
        click.echo(
            "  Core account: none (NAV is 0 until the first EVM -> Core deposit)"
        )
    if state.balance_fuse_value_wad is None:
        fuse_text = "unavailable"
    else:
        verdict = "match" if state.nav_matches_balance_fuse else "MISMATCH"
        fuse_text = f"{_wad_to_usd(state.balance_fuse_value_wad):.6f} USD ({verdict})"
    click.echo(
        f"  NAV: {_wad_to_usd(nav.value_wad):.6f} USD | balance fuse: {fuse_text}"
    )
    for leg in nav.spot:
        price = f"{leg.price.readable():.4f} USD" if leg.price else "unpriced"
        click.echo(
            f"  Spot token {leg.token_index} ({leg.evm_asset}): {leg.total} wei"
            f" ({leg.wei_decimals} dec), hold {leg.hold}, @ {price}"
            f" = {_wad_to_usd(leg.value_wad):.6f} USD"
        )
    if nav.native_perp is not None:
        click.echo(
            f"  Native perp (dex 0): {_wad_to_usd(nav.native_perp.value_wad):.6f} USD"
        )
    for leg in nav.hip3:
        click.echo(f"  HIP-3 dex {leg.dex}: {_wad_to_usd(leg.value_wad):.6f} USD")
    for pm in state.perp_markets:
        reduce_only = "required" if pm.reduce_only_required else "optional"
        click.echo(
            f"  Perp market {pm.coin or '?'} (asset {pm.asset}, dex {pm.dex},"
            f" read index {pm.read_index}): cap {pm.max_notional_usd6 / 10**6:,.2f} USD,"
            f" reduce-only {reduce_only}"
        )
    _print_hypercore_pending(state.pending)
    click.echo()


def _print_hypercore_pending(pending: HyperCorePendingState | None) -> None:
    if pending is None:
        click.echo(
            "  Pending action: unknown (no HyperCorePendingReader known on this chain)"
        )
        return
    last = f"{pending.action_class.name} #{pending.action_nonce}"
    if pending.pending:
        click.echo(
            f"  Pending action: {last} (id {pending.action_id}) until"
            f" {_unix_to_iso(pending.pending_until)}; L1 block {pending.current_l1_block}"
        )
    else:
        click.echo(
            f"  Pending action: none (last {last} settled); L1 block {pending.current_l1_block}"
        )


def _print_pending_requests(data: VaultData) -> None:
    if (wmd := data.withdraw_manager_data) is None:
        return
    if wmd.pending_requests is None:
        click.echo(
            "  Pending requests: (unavailable — the WithdrawRequestUpdated "
            "log scan did not complete)"
        )
        return
    if not (requests := wmd.pending_requests):
        click.echo("  Pending requests: (none)")
        return

    total_shares = Shares(sum(r.shares for r in requests))
    request_assets = [wmd.pending_request_assets.get(r.account) for r in requests]
    total_assets = (
        None if None in request_assets else sum(a for a in request_assets if a)
    )

    click.echo(f"  Pending requests ({len(requests)}):")
    rows: list[tuple[str, ...]] = []
    for req, assets in zip(requests, request_assets, strict=True):
        assets_str = (
            f"{_format_amount(assets, data.asset_decimals)} {data.asset_symbol}"
            if assets is not None
            else "?"
        )
        usd_str = ""
        if assets is not None and data.asset_price_usd is not None:
            usd_val = (assets / 10**data.asset_decimals) * data.asset_price_usd
            usd_str = f" (${usd_val:,.2f})"
        end_iso = _unix_to_iso(req.end_withdraw_window_timestamp)
        remaining = req.end_withdraw_window_timestamp - data.block_timestamp
        remaining_str = _format_remaining(remaining)
        status = "can_withdraw" if req.can_withdraw else "waiting"
        rows.append(
            (
                req.account,
                f"{_format_amount(req.shares, data.share_decimals)} shares",
                f"{assets_str}{usd_str}",
                f"{end_iso} ({remaining_str})",
                status,
            )
        )
    _print_table(("Account", "Shares", "Assets", "Window ends", "Status"), rows)

    total_fmt = _format_amount(total_shares, data.share_decimals)
    total_assets_fmt = (
        f" = {_format_amount(total_assets, data.asset_decimals)} {data.asset_symbol}"
        if total_assets is not None
        else ""
    )
    click.echo(f"  Total pending: {total_fmt} shares{total_assets_fmt}")
