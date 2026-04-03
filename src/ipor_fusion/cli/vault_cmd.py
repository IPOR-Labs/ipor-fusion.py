from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from concurrent.futures import Future, ThreadPoolExecutor

import click
from web3 import Web3

from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    save_config,
)
from ipor_fusion.cli.explorer import get_contract_name
from ipor_fusion.cli.vault_fetcher import (
    _VaultData,
    _fetch_deployment_info,
    _fetch_vault_data,
    _resolve_token_symbol,
    _safe_call,
)
from ipor_fusion.cli.vault_health import (
    _BalanceFuseTotals,
    _compute_erc20_balances,
    _compute_health_check,
    _compute_reconciliation,
    _print_erc20_balances,
    _print_health_check,
    _print_reconciliation,
)
from ipor_fusion.cli.vault_rendering import (
    _format_age,
    _format_amount,
    _format_remaining,
    _format_usd,
    _print_table,
    _substrate_details,
)
from ipor_fusion.cli.vault_substrate import (
    _format_substrate,
    _market_name,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.types import MarketId


class AddressType(click.ParamType):
    name = "address"

    def convert(self, value, param, ctx):
        if not isinstance(value, str):
            self.fail(f"expected string, got {type(value).__name__}", param, ctx)

        value = value.strip()
        if not value.startswith("0x"):
            value = "0x" + value

        raw = value[2:]
        if len(raw) != 40 or not all(c in "0123456789abcdefABCDEF" for c in raw):
            self.fail(f"invalid Ethereum address: {value}", param, ctx)

        if not raw.islower() and not raw.isupper():
            try:
                checksum = Web3.to_checksum_address(value)
            except Exception:  # pylint: disable=broad-except
                self.fail(f"invalid address checksum: {value}", param, ctx)
            if checksum != value:
                self.fail(
                    f"bad checksum — expected {checksum}, got {value}",
                    param,
                    ctx,
                )

        return Web3.to_checksum_address(value)


ADDRESS = AddressType()


CHAIN_NAMES: dict[int, str] = {
    1: "ethereum",
    42161: "arbitrum",
    8453: "base",
    10: "optimism",
    137: "polygon",
    56: "bsc",
    43114: "avalanche",
    250: "fantom",
}

CHAIN_NAME_TO_ID: dict[str, int] = {name: cid for cid, name in CHAIN_NAMES.items()}


class ChainType(click.ParamType):
    """Accepts chain ID as int or chain name (ethereum, base, arbitrum, ...)."""

    name = "chain"

    def convert(self, value, param, ctx):  # type: ignore[override]
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except ValueError:
            pass
        lower = value.lower().strip()
        if lower in CHAIN_NAME_TO_ID:
            return CHAIN_NAME_TO_ID[lower]
        self.fail(
            f"Unknown chain: {value}. Use a numeric ID or one of: "
            f"{', '.join(sorted(CHAIN_NAME_TO_ID))}",
            param,
            ctx,
        )
        return None  # unreachable, keeps mypy happy


CHAIN = ChainType()

BLOCK_EXPLORER_URLS: dict[int, str] = {
    1: "https://etherscan.io",
    42161: "https://arbiscan.io",
    8453: "https://basescan.org",
    10: "https://optimistic.etherscan.io",
    137: "https://polygonscan.com",
    56: "https://bscscan.com",
    43114: "https://snowtrace.io",
    250: "https://ftmscan.com",
}

IPOR_APP_URL = "https://app.ipor.io/fusion"

UINT256_MAX = 2**256 - 1


def _resolve_chain_id(
    cfg: FusionConfig, vault_address: str, chain_id: int | None
) -> int:
    if chain_id is not None:
        return chain_id
    if entry := next(
        (v for v in cfg.vaults if v.address.lower() == vault_address.lower()),
        None,
    ):
        return entry.chain_id
    raise click.UsageError(
        "Unknown vault — use --chain-id (e.g. --chain-id ethereum, --chain-id 8453)"
    )


def _resolve_provider(cfg: FusionConfig, chain_id: int) -> str:
    if provider_url := cfg.providers.get(str(chain_id)):
        return provider_url
    raise click.UsageError(
        f"No provider for chain {chain_id}. "
        f"Use 'fusion config set-provider {chain_id} <url>'"
    )


def _auto_save_vault(
    cfg: FusionConfig, vault_address: str, chain_id: int, plasma_vault: PlasmaVault
) -> None:
    """Save vault to config if not already present and it looks like a Plasma Vault."""
    if any(v.address.lower() == vault_address.lower() for v in cfg.vaults):
        return
    try:
        label = plasma_vault.name()
    except Exception:  # pylint: disable=broad-except
        return
    cfg.vaults.append(VaultEntry(address=vault_address, label=label, chain_id=chain_id))
    save_config(cfg)
    click.echo(f"Vault saved: {label} ({vault_address})", err=True)


@click.group()
def vault() -> None:
    """Inspect Plasma Vaults."""


@vault.command("add")
@click.argument("address", type=ADDRESS)
@click.option(
    "--label", default=None, help="Label (default: fetched from on-chain name())."
)
@click.option(
    "--chain-id",
    type=CHAIN,
    default=None,
    help="Chain ID or name (auto-detected when only one provider is configured).",
)
def add(address: str, label: str | None, chain_id: int | None) -> None:
    """Save a vault to the config."""
    cfg = load_config()

    if chain_id is None:
        if len(cfg.providers) == 1:
            chain_id = int(next(iter(cfg.providers)))
        else:
            raise click.UsageError(
                "Cannot auto-detect chain ID — multiple providers configured. "
                "Use --chain-id to specify."
            )

    if label is None:
        provider_url = _resolve_provider(cfg, chain_id)
        ctx = Web3Context.from_url(provider_url)
        checksum = Web3.to_checksum_address(address)
        try:
            label = PlasmaVault(ctx, checksum).name()
        except Exception:  # pylint: disable=broad-except
            label = checksum

    for vault_entry in cfg.vaults:
        if vault_entry.address.lower() == address.lower():
            click.echo("Vault already exists, updating.")
            vault_entry.label = label
            vault_entry.chain_id = chain_id
            save_config(cfg)
            return
    cfg.vaults.append(VaultEntry(address=address, label=label, chain_id=chain_id))
    save_config(cfg)
    click.echo(f"Vault {label} ({address}) added.")


@vault.command("remove")
@click.argument("address", type=ADDRESS)
def remove(address: str) -> None:
    """Remove a saved vault."""
    cfg = load_config()
    before = len(cfg.vaults)
    cfg.vaults = [v for v in cfg.vaults if v.address.lower() != address.lower()]
    if len(cfg.vaults) == before:
        click.echo("Vault not found.")
        return
    save_config(cfg)
    click.echo("Vault removed.")


@vault.command("list")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def list_vaults(json_output: bool) -> None:
    """List saved vaults. (alias: ls)"""
    cfg = load_config()
    if not cfg.vaults:
        if json_output:
            click.echo("[]")
        else:
            click.echo("(no saved vaults)")
        return
    if json_output:
        entries = []
        for v in cfg.vaults:
            entries.append(
                {
                    "address": v.address,
                    "label": v.label,
                    "chain": CHAIN_NAMES.get(v.chain_id, str(v.chain_id)),
                    "chain_id": v.chain_id,
                }
            )
        click.echo(json.dumps(entries, indent=2))
        return
    rows: list[tuple[str, ...]] = []
    for v in cfg.vaults:
        chain = CHAIN_NAMES.get(v.chain_id, str(v.chain_id))
        rows.append((chain, v.label, v.address))
    _print_table(("Chain", "Label", "Address"), rows)


vault.add_command(list_vaults, "ls")


@vault.command("info")
@click.argument("vault_address", type=ADDRESS)
@click.option(
    "--chain-id", type=CHAIN, default=None, help="Chain ID or name (e.g. 1, ethereum)."
)
@click.option(
    "--block-number",
    type=int,
    default=None,
    help="Block number (default: latest).",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def info(
    vault_address: str,
    chain_id: int | None,
    block_number: int | None,
    json_output: bool,
) -> None:
    """Display full on-chain vault state."""
    cfg = load_config()
    chain_id = _resolve_chain_id(cfg, vault_address, chain_id)
    provider_url = _resolve_provider(cfg, chain_id)

    ctx = Web3Context.from_url(provider_url)
    if block_number is not None:
        ctx.default_block = block_number
    checksum_address = Web3.to_checksum_address(vault_address)
    plasma_vault = PlasmaVault(ctx, checksum_address)

    _auto_save_vault(cfg, vault_address, chain_id, plasma_vault)

    data = _fetch_vault_data(ctx, plasma_vault, block_number)
    _print_vault_info(
        ctx, plasma_vault, cfg, data, vault_address, chain_id, json_output
    )


@vault.command("market-detail")
@click.argument("vault_address", type=ADDRESS)
@click.option(
    "--chain-id", type=CHAIN, default=None, help="Chain ID or name (e.g. 1, ethereum)."
)
@click.option(
    "--block-number",
    type=int,
    default=None,
    help="Block number (default: latest).",
)
@click.option("--market-id", type=int, required=True, help="Market ID to inspect.")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def market_detail(
    vault_address: str,
    chain_id: int | None,
    block_number: int | None,
    market_id: int,
    json_output: bool,
) -> None:
    """Show detailed info for a single market in a vault."""
    cfg = load_config()
    chain_id = _resolve_chain_id(cfg, vault_address, chain_id)
    provider_url = _resolve_provider(cfg, chain_id)

    ctx = Web3Context.from_url(provider_url)
    if block_number is not None:
        ctx.default_block = block_number
    checksum_address = Web3.to_checksum_address(vault_address)
    plasma_vault = PlasmaVault(ctx, checksum_address)

    _auto_save_vault(cfg, vault_address, chain_id, plasma_vault)

    data = _fetch_market_detail(
        ctx, plasma_vault, cfg, market_id, chain_id, block_number
    )
    if json_output:
        click.echo(json.dumps(data, indent=2))
    else:
        _print_market_detail(data)


def _fetch_market_detail(  # pylint: disable=too-many-locals,too-complex
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    cfg: FusionConfig,
    market_id: int,
    chain_id: int,
    block_number: int | None,
) -> dict:
    api_key = cfg.etherscan_api_key

    with ThreadPoolExecutor() as pool:
        # Phase 1: discover balance fuses + asset info
        f_balance_fuses = pool.submit(plasma_vault.get_balance_fuses)
        f_asset = pool.submit(plasma_vault.underlying_asset_address)
        f_oracle_addr = pool.submit(plasma_vault.get_price_oracle_middleware_address)
        f_block = pool.submit(lambda: ctx.web3.eth.block_number)

        balance_fuses = f_balance_fuses.result()
        match = next((bf for bf in balance_fuses if bf.market_id == market_id), None)
        if match is None:
            raise click.UsageError(
                f"Market ID {market_id} not found in vault balance fuses. "
                f"Available: {', '.join(_market_name(bf.market_id) + '=' + str(bf.market_id) for bf in balance_fuses)}"
            )

        asset = f_asset.result()
        oracle_addr = f_oracle_addr.result()
        asset_erc20 = ERC20(ctx, asset)
        oracle = PriceOracleMiddleware(ctx, oracle_addr)

        # Phase 2: market-specific data in parallel
        mid = MarketId(market_id)
        f_balance = pool.submit(plasma_vault.total_assets_in_market, mid)
        f_substrates = pool.submit(plasma_vault.get_market_substrates, mid)
        f_fuse_name = pool.submit(get_contract_name, chain_id, match.fuse, api_key)
        f_symbol: Future = pool.submit(_safe_call, asset_erc20.symbol)
        f_decimals = pool.submit(asset_erc20.decimals)
        f_price: Future = pool.submit(_safe_call, lambda: oracle.get_asset_price(asset))

        latest_block = f_block.result()
        effective_block = block_number if block_number is not None else latest_block
        block_label = (
            str(block_number)
            if block_number is not None
            else f"{latest_block} (latest)"
        )

        decimals = f_decimals.result()
        symbol = f_symbol.result() or "?"
        price_result = f_price.result()
        price_usd = price_result.readable() if price_result else None
        balance_raw = f_balance.result()
        fuse_name = f_fuse_name.result() or "?"
        substrates = f_substrates.result()

        # Phase 3: resolve substrate addresses
        sub_addresses: set[str] = set()
        for sub in substrates:
            sub_info = _format_substrate(sub, market_id=market_id)
            if sub_info.address:
                sub_addresses.add(sub_info.address)

        sym_futs = {
            addr: pool.submit(_resolve_token_symbol, ctx, addr)
            for addr in sub_addresses
        }
        contract_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in sub_addresses
        }

        substrates_list = []
        for sub in substrates:
            sub_info = _format_substrate(sub, market_id=market_id)
            if sub_info.address:
                entry: dict = {"address": sub_info.address}
                sub_sym = sym_futs[sub_info.address].result()
                sub_contract = contract_futs[sub_info.address].result()
                if sub_sym:
                    entry["symbol"] = sub_sym
                if sub_contract:
                    entry["contract"] = sub_contract
                if sub_info.type_label:
                    entry["substrate_type"] = sub_info.type_label
                if sub_info.extra:
                    entry.update(sub_info.extra)
                substrates_list.append(entry)
            else:
                raw_entry: dict = {"raw": sub_info.raw_hex}
                if sub_info.is_error:
                    raw_entry["error"] = True
                if sub_info.type_label:
                    raw_entry["substrate_type"] = sub_info.type_label
                if sub_info.extra:
                    raw_entry.update(sub_info.extra)
                substrates_list.append(raw_entry)

    market_label = _market_name(market_id)
    balance_entry: dict = {
        "raw": balance_raw,
        "formatted": _format_amount(balance_raw, decimals),
    }
    if price_usd is not None:
        balance_entry["usd"] = round((balance_raw / 10**decimals) * price_usd, 2)

    return {
        "vault": Web3.to_checksum_address(plasma_vault.address),
        "chain_id": chain_id,
        "block": block_label,
        "block_number": effective_block,
        "market": market_label if market_label != "UNKNOWN" else str(market_id),
        "market_id": market_id,
        "asset": {"address": asset, "symbol": symbol, "decimals": decimals},
        "balance": balance_entry,
        "fuse": {"address": match.fuse, "contract": fuse_name},
        "substrates": substrates_list,
    }


def _print_market_detail(data: dict) -> None:
    market_str = data["market"]
    if market_str != str(data["market_id"]):
        market_str += f" (id={data['market_id']})"

    click.echo(f"Vault:   {data['vault']}")
    click.echo(f"Block:   {data['block']}")
    click.echo(f"Market:  {market_str}")
    click.echo(f"Fuse:    {data['fuse']['address']}  ({data['fuse']['contract']})")

    balance = data["balance"]
    usd_str = f" (${balance['usd']:,.2f})" if "usd" in balance else ""
    click.echo(
        f"Balance: {balance['formatted']} {data['asset']['symbol']}"
        f"{usd_str} (cached)"
    )

    if data["substrates"]:
        click.echo()
        click.echo("Substrates:")
        _SKIP_KEYS = {"address", "raw", "error", "symbol", "contract", "substrate_type"}
        for sub in data["substrates"]:
            extra_parts = [
                f"{k}={v}"
                for k, v in sub.items()
                if k not in _SKIP_KEYS and isinstance(v, str)
            ]
            if "address" in sub:
                parts = []
                if sub.get("symbol"):
                    parts.append(f"symbol={sub['symbol']}")
                if sub.get("contract"):
                    parts.append(f"contract={sub['contract']}")
                if sub.get("substrate_type"):
                    parts.append(f"substrate-type={sub['substrate_type']}")
                parts.extend(extra_parts)
                detail = f" ({', '.join(parts)})" if parts else ""
                click.echo(f"  {sub['address']}{detail}")
            elif sub.get("error"):
                click.secho(f"  {sub['raw']} [encoding error]", fg="red")
            else:
                parts = []
                sub_type = sub.get("substrate_type", "")
                if sub_type:
                    parts.append(sub_type)
                parts.extend(extra_parts)
                label = ", ".join(parts) if parts else "bytes32"
                is_no_decoder = sub_type.startswith("no_decoder(")
                fg = "red" if is_no_decoder else None
                click.secho(f"  {sub['raw']} ({label})", fg=fg)
    else:
        click.echo("Substrates: (none)")


def _print_vault_info(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    cfg: FusionConfig,
    data: _VaultData,
    vault_address: str,
    chain_id: int,
    json_output: bool = False,
) -> None:
    api_key = cfg.etherscan_api_key
    chain_label = CHAIN_NAMES.get(chain_id, str(chain_id))

    data.deployment_block, data.deployment_timestamp = _fetch_deployment_info(
        ctx, chain_id, vault_address, api_key
    )

    if json_output:
        result = _build_json_output(
            ctx, plasma_vault, data, vault_address, chain_id, chain_label, api_key
        )
        click.echo(json.dumps(result, indent=2))
        return

    total_assets_usd = _format_usd(
        data.total_assets, data.asset_decimals, data.asset_price_usd
    )

    name_suffix = f" ({data.vault_name})" if data.vault_name else ""
    click.echo(f"Vault:            {vault_address}{name_suffix}")
    if explorer_base := BLOCK_EXPLORER_URLS.get(chain_id):
        click.echo(f"Etherscan:        {explorer_base}/address/{vault_address}")
    click.echo(f"IPOR app:         {IPOR_APP_URL}/{chain_label}/{vault_address}")
    click.echo(f"Chain:            {chain_label} (chain-id={chain_id})")
    click.echo(f"Block:            {data.block_label}")
    block_dt = datetime.fromtimestamp(data.block_timestamp, tz=timezone.utc)
    block_iso = block_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    click.echo(f"Block time:       {data.block_timestamp} ({block_iso})")
    if data.deployment_block is not None and data.deployment_timestamp is not None:
        deploy_dt = datetime.fromtimestamp(data.deployment_timestamp, tz=timezone.utc)
        deploy_iso = deploy_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        age = _format_age(data.deployment_timestamp)
        click.echo(
            f"Deployed at:      block {data.deployment_block}" f" ({deploy_iso}, {age})"
        )
    else:
        click.echo("Deployed at:      N/A")
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
            f"{_format_amount(data.supply_cap, data.asset_decimals)} {data.asset_symbol}"
        )
    click.echo(f"Access Manager:   {data.access_manager}")
    click.echo(f"Price Oracle:     {data.price_oracle_addr}")
    click.echo(f"Rewards Manager:  {data.rewards_manager or 'N/A'}")
    click.echo(f"Withdraw Manager: {data.withdraw_manager or 'N/A'}")
    if data.withdraw_manager_data:
        wmd = data.withdraw_manager_data
        window_h = wmd.withdraw_window / 3600
        click.echo(f"  Window:         {wmd.withdraw_window}s ({window_h:.1f}h)")
        click.echo(
            f"  Request fee:    {wmd.request_fee / 1e18:.4%}"
            f"   Withdraw fee: {wmd.withdraw_fee / 1e18:.4%}"
        )
        release_fmt = _format_amount(wmd.shares_to_release, data.share_decimals)
        click.echo(f"  Shares to release: {release_fmt}")
        if wmd.last_release_funds_timestamp > 0:
            last_dt = datetime.fromtimestamp(
                wmd.last_release_funds_timestamp, tz=timezone.utc
            )
            click.echo(f"  Last release:   {last_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        _print_pending_requests(data, plasma_vault)
    click.echo()

    click.echo(f"Fuses ({len(data.fuses)}):")
    _print_fuses_table(data.fuses, chain_id, api_key)
    click.echo()

    click.echo(f"Balance Fuses ({len(data.balance_fuses)}):")
    bf_totals = _print_balance_fuses_table(
        plasma_vault,
        data.balance_fuses,
        data.asset_decimals,
        data.asset_symbol,
        data.asset_price_usd,
        chain_id,
        api_key,
    )
    click.echo()

    _print_dependency_graph(data)

    click.echo(f"Instant Withdrawal Fuses ({len(data.instant_fuses)}):")
    _print_fuses_table(data.instant_fuses, chain_id, api_key)
    click.echo()

    click.echo("ERC20 Balances (vault holdings):")
    erc20_totals = _print_erc20_balances(ctx, plasma_vault, data)
    click.echo()

    click.echo("Substrates per Market:")
    all_substrate_addrs = _print_substrates(
        ctx, plasma_vault, data.balance_fuses, chain_id, api_key
    )
    click.echo()

    _print_reconciliation(
        data,
        bf_totals,
        erc20_totals,
    )
    click.echo()

    _print_health_check(data, bf_totals, erc20_totals, all_substrate_addrs)


def _build_share_price_json(data: _VaultData) -> dict | None:
    if data.total_supply == 0:
        return None
    price = (data.total_assets / 10**data.asset_decimals) / (
        data.total_supply / 10**data.share_decimals
    )
    result: dict = {
        "asset": round(price, 10),
    }
    if data.asset_price_usd is not None:
        result["usd"] = round(price * data.asset_price_usd, 10)
    return result


def _build_deployment_json(data: _VaultData) -> dict | None:
    if data.deployment_block is None or data.deployment_timestamp is None:
        return None
    return {
        "block": data.deployment_block,
        "timestamp": data.deployment_timestamp,
        "timestamp_utc": datetime.fromtimestamp(
            data.deployment_timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_days": (
            datetime.now(tz=timezone.utc)
            - datetime.fromtimestamp(data.deployment_timestamp, tz=timezone.utc)
        ).days,
    }


def _build_withdraw_manager_json(
    data: _VaultData, plasma_vault: PlasmaVault
) -> dict | None:
    if data.withdraw_manager_data is None:
        return None
    wmd = data.withdraw_manager_data
    sdec = data.share_decimals
    adec = data.asset_decimals

    total_pending_shares = sum((r.shares for r in wmd.pending_requests), 0)

    requests_json = []
    for req in wmd.pending_requests:
        assets: int | None = _safe_call(
            lambda s=req.shares: plasma_vault.convert_to_assets(s)  # type: ignore[misc]
        )
        entry: dict = {
            "account": req.account,
            "shares": {
                "raw": req.shares,
                "formatted": _format_amount(req.shares, sdec),
            },
            "end_withdraw_window_timestamp": req.end_withdraw_window_timestamp,
            "end_withdraw_window_utc": datetime.fromtimestamp(
                req.end_withdraw_window_timestamp, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "remaining_seconds": max(
                0, req.end_withdraw_window_timestamp - data.block_timestamp
            ),
            "can_withdraw": req.can_withdraw,
        }
        if assets is not None:
            entry["assets"] = {
                "raw": assets,
                "formatted": _format_amount(assets, adec),
            }
            if data.asset_price_usd is not None:
                entry["assets"]["usd"] = round(
                    (assets / 10**adec) * data.asset_price_usd, 2
                )
        requests_json.append(entry)

    result: dict = {
        "withdraw_window_seconds": wmd.withdraw_window,
        "request_fee_wad": wmd.request_fee,
        "request_fee_percent": round(wmd.request_fee / 1e18 * 100, 4),
        "withdraw_fee_wad": wmd.withdraw_fee,
        "withdraw_fee_percent": round(wmd.withdraw_fee / 1e18 * 100, 4),
        "shares_to_release": {
            "raw": wmd.shares_to_release,
            "formatted": _format_amount(wmd.shares_to_release, sdec),
        },
        "last_release_funds_timestamp": wmd.last_release_funds_timestamp,
        "pending_requests": requests_json,
        "total_pending_shares": {
            "raw": total_pending_shares,
            "formatted": _format_amount(total_pending_shares, sdec),
        },
    }
    if wmd.last_release_funds_timestamp > 0:
        result["last_release_funds_utc"] = datetime.fromtimestamp(
            wmd.last_release_funds_timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _format_market_label(market_id: int) -> str:
    label = _market_name(market_id)
    return f"{label} ({market_id})" if label != "UNKNOWN" else str(market_id)


def _print_dependency_graph(data: _VaultData) -> None:
    if not data.dependency_graph:
        return
    click.echo("Dependency Balance Graph:")
    for market_id, deps in data.dependency_graph.items():
        label = _format_market_label(market_id)
        dep_labels = ", ".join(_format_market_label(d) for d in deps)
        click.echo(f"  {label} → {dep_labels}")
    click.echo()


def _build_dependency_graph_json(data: _VaultData) -> dict[str, list[str]] | None:
    if not data.dependency_graph:
        return None
    return {
        _format_market_label(mid): [_format_market_label(d) for d in deps]
        for mid, deps in data.dependency_graph.items()
    }


def _build_json_output(  # pylint: disable=too-many-locals,too-complex
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    data: _VaultData,
    vault_address: str,
    chain_id: int,
    chain_label: str,
    api_key: str | None,
) -> dict:
    """Build a dict with all vault info for JSON serialization."""
    total_assets_usd: float | None = None
    if data.asset_price_usd is not None:
        total_assets_usd = (
            data.total_assets / 10**data.asset_decimals
        ) * data.asset_price_usd

    # Resolve fuse contract names in parallel
    with ThreadPoolExecutor() as pool:
        fuse_name_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in data.fuses
        }
        instant_name_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in data.instant_fuses
        }
        bf_contract_futs = [
            pool.submit(get_contract_name, chain_id, bf.fuse, api_key)
            for bf in data.balance_fuses
        ]
        bf_balance_futs = [
            pool.submit(plasma_vault.total_assets_in_market, bf.market_id)
            for bf in data.balance_fuses
        ]
        substrate_futs = [
            pool.submit(plasma_vault.get_market_substrates, bf.market_id)
            for bf in data.balance_fuses
        ]

        fuses_json = [
            {"address": addr, "contract": fuse_name_futs[addr].result() or "?"}
            for addr in data.fuses
        ]
        instant_json = [
            {"address": addr, "contract": instant_name_futs[addr].result() or "?"}
            for addr in data.instant_fuses
        ]

        balance_fuses_json = []
        for i, bf in enumerate(data.balance_fuses):
            market_label = _market_name(bf.market_id)
            market_str = (
                market_label if market_label != "UNKNOWN" else str(bf.market_id)
            )
            assets_in_market = bf_balance_futs[i].result()
            bf_entry: dict = {
                "market": market_str,
                "market_id": bf.market_id,
                "balance": {
                    "raw": assets_in_market,
                    "formatted": _format_amount(assets_in_market, data.asset_decimals),
                },
                "fuse": bf.fuse,
                "contract": bf_contract_futs[i].result() or "?",
            }
            if data.dependency_graph and bf.market_id in data.dependency_graph:
                deps = data.dependency_graph[bf.market_id]
                bf_entry["depends_on"] = [_format_market_label(d) for d in deps]
            balance_fuses_json.append(bf_entry)

        # Substrates - also resolve symbols/contracts for addresses
        all_sub_addresses: set[str] = set()
        market_subs_raw: list[tuple[str, int, list]] = []
        for i, bf in enumerate(data.balance_fuses):
            market_label = _market_name(bf.market_id)
            market_str = (
                f"{market_label} ({bf.market_id})"
                if market_label != "UNKNOWN"
                else str(bf.market_id)
            )
            if subs := substrate_futs[i].result():
                market_subs_raw.append((market_str, bf.market_id, subs))
                for sub in subs:
                    sub_info = _format_substrate(sub, market_id=bf.market_id)
                    if sub_info.address:
                        all_sub_addresses.add(sub_info.address)

        sym_futs = {
            addr: pool.submit(_resolve_token_symbol, ctx, addr)
            for addr in all_sub_addresses
        }
        contract_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in all_sub_addresses
        }

        substrates_json: dict[str, list] = {}
        for market_str, mid, subs in market_subs_raw:
            entries = []
            for sub in subs:
                sub_info = _format_substrate(sub, market_id=mid)
                if sub_info.address:
                    entry: dict = {"address": sub_info.address}
                    symbol = sym_futs[sub_info.address].result()
                    contract = contract_futs[sub_info.address].result()
                    if symbol:
                        entry["symbol"] = symbol
                    if contract:
                        entry["contract"] = contract
                    if sub_info.type_label:
                        entry["substrate_type"] = sub_info.type_label
                    if sub_info.extra:
                        entry.update(sub_info.extra)
                    entries.append(entry)
                else:
                    raw_entry: dict = {"raw": sub_info.raw_hex}
                    if sub_info.is_error:
                        raw_entry["error"] = True
                    if sub_info.type_label:
                        raw_entry["substrate_type"] = sub_info.type_label
                    if sub_info.extra:
                        raw_entry.update(sub_info.extra)
                    entries.append(raw_entry)
            substrates_json[market_str] = entries

    # ERC20 balances
    erc20_totals = _compute_erc20_balances(ctx, plasma_vault, data)
    erc20_json = []
    for td in erc20_totals.token_details:
        erc20_entry: dict = {"address": td.address, "symbol": td.symbol}
        if td.decimals is not None and td.balance is not None:
            erc20_entry["decimals"] = td.decimals
            erc20_entry["balance"] = {
                "raw": td.balance,
                "formatted": _format_amount(td.balance, td.decimals),
            }
            erc20_entry["price_usd"] = td.price_usd
            erc20_entry["usd_value"] = td.usd_value
        erc20_entry["note"] = td.note
        erc20_json.append(erc20_entry)

    # Balance fuse totals for reconciliation
    bf_totals = _BalanceFuseTotals()
    for bfj in balance_fuses_json:
        bf_totals.raw_total += bfj["balance"]["raw"]
        if data.asset_price_usd is not None:
            bf_totals.usd_total += (
                bfj["balance"]["raw"] / 10**data.asset_decimals
            ) * data.asset_price_usd

    # Reconciliation
    recon = _compute_reconciliation(data, bf_totals, erc20_totals)
    decimals = data.asset_decimals
    reconciliation_json = {
        "balance_fuses_total": {
            "raw": recon.bf_total_raw,
            "formatted": _format_amount(recon.bf_total_raw, decimals),
            "usd": recon.bf_total_usd,
        },
        "underlying_on_vault": {
            "raw": recon.underlying_raw,
            "formatted": _format_amount(recon.underlying_raw, decimals),
            "usd": recon.underlying_usd,
        },
        "erc20_direct_total": {
            "raw": recon.erc20_total_raw,
            "formatted": _format_amount(recon.erc20_total_raw, decimals),
            "usd": recon.erc20_total_usd,
        },
        "sum": {
            "raw": recon.sum_raw,
            "formatted": _format_amount(recon.sum_raw, decimals),
            "usd": recon.sum_usd,
        },
        "on_chain_total_assets": {
            "raw": recon.on_chain_raw,
            "formatted": _format_amount(recon.on_chain_raw, decimals),
            "usd": recon.on_chain_usd,
        },
        "delta": {
            "raw": recon.delta_raw,
            "formatted": _format_amount(abs(recon.delta_raw), decimals),
            "usd": recon.delta_usd,
            "percent": recon.delta_percent,
        },
    }

    # Health check
    all_sub_lower = {a.lower() for a in all_sub_addresses}
    health = _compute_health_check(data, bf_totals, erc20_totals, all_sub_lower)
    health_json = {"ok": health.ok, "warnings": health.warnings}

    explorer_base = BLOCK_EXPLORER_URLS.get(chain_id)
    links: dict[str, str] = {
        "ipor_app": f"{IPOR_APP_URL}/{chain_label}/{vault_address}",
    }
    if explorer_base:
        links["etherscan"] = f"{explorer_base}/address/{vault_address}"

    return {
        "vault": vault_address,
        "name": data.vault_name or None,
        "links": links,
        "chain": chain_label,
        "chain_id": chain_id,
        "block": data.block_label,
        "block_timestamp": data.block_timestamp,
        "block_timestamp_utc": datetime.fromtimestamp(
            data.block_timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployment": _build_deployment_json(data),
        "asset": {
            "address": data.asset,
            "symbol": data.asset_symbol,
            "decimals": data.asset_decimals,
            "price_usd": data.asset_price_usd,
        },
        "share_decimals": data.share_decimals,
        "total_assets": {
            "raw": data.total_assets,
            "formatted": _format_amount(data.total_assets, data.asset_decimals),
            "usd": total_assets_usd,
        },
        "total_supply": {
            "raw": data.total_supply,
            "formatted": _format_amount(data.total_supply, data.share_decimals),
        },
        "share_price": _build_share_price_json(data),
        "supply_cap": {
            "raw": data.supply_cap,
            "formatted": (
                "unlimited"
                if data.supply_cap == UINT256_MAX
                else _format_amount(data.supply_cap, data.asset_decimals)
            ),
        },
        "managers": {
            "access": data.access_manager,
            "price_oracle": data.price_oracle_addr,
            "rewards": data.rewards_manager,
            "withdraw": data.withdraw_manager,
        },
        "withdraw_manager_details": _build_withdraw_manager_json(data, plasma_vault),
        "fuses": fuses_json,
        "balance_fuses": balance_fuses_json,
        "instant_withdrawal_fuses": instant_json,
        "substrates": substrates_json,
        "dependency_graph": _build_dependency_graph_json(data),
        "erc20_balances": erc20_json,
        "reconciliation": reconciliation_json,
        "health_check": health_json,
    }


def _print_pending_requests(data: _VaultData, plasma_vault: PlasmaVault) -> None:
    if (wmd := data.withdraw_manager_data) is None:
        return
    if not (requests := wmd.pending_requests):
        click.echo("  Pending requests: (none)")
        return

    total_shares = sum((r.shares for r in requests), 0)
    total_assets: int | None = _safe_call(
        lambda ts=total_shares: plasma_vault.convert_to_assets(ts)  # type: ignore[misc]
    )

    click.echo(f"  Pending requests ({len(requests)}):")
    rows: list[tuple[str, ...]] = []
    for req in requests:
        assets: int | None = _safe_call(
            lambda s=req.shares: plasma_vault.convert_to_assets(s)  # type: ignore[misc]
        )
        assets_str = (
            f"{_format_amount(assets, data.asset_decimals)} {data.asset_symbol}"
            if assets is not None
            else "?"
        )
        usd_str = ""
        if assets is not None and data.asset_price_usd is not None:
            usd_val = (assets / 10**data.asset_decimals) * data.asset_price_usd
            usd_str = f" (${usd_val:,.2f})"
        end_dt = datetime.fromtimestamp(
            req.end_withdraw_window_timestamp, tz=timezone.utc
        )
        remaining = req.end_withdraw_window_timestamp - data.block_timestamp
        remaining_str = _format_remaining(remaining)
        status = "can_withdraw" if req.can_withdraw else "waiting"
        rows.append(
            (
                req.account,
                f"{_format_amount(req.shares, data.share_decimals)} shares",
                f"{assets_str}{usd_str}",
                f"{end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} ({remaining_str})",
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


def _print_fuses_table(
    fuses: Sequence[str], chain_id: int, api_key: str | None
) -> None:
    with ThreadPoolExecutor() as pool:
        futures = [
            (idx, addr, pool.submit(get_contract_name, chain_id, addr, api_key))
            for idx, addr in enumerate(fuses, 1)
        ]
        rows: list[tuple[str, ...]] = []
        for idx, addr, fut in futures:
            rows.append((str(idx), addr, fut.result() or "?"))
    _print_table(("#", "Address", "Contract"), rows)


def _print_balance_fuses_table(
    plasma_vault: PlasmaVault,
    balance_fuses: list,
    decimals: int,
    asset_symbol: str,
    asset_price_usd: float | None,
    chain_id: int,
    api_key: str | None,
) -> _BalanceFuseTotals:
    totals = _BalanceFuseTotals()
    with ThreadPoolExecutor() as pool:
        futures: list[tuple[int, str, Future, Future]] = []
        for idx, balance_fuse in enumerate(balance_fuses, 1):
            market_label = _market_name(balance_fuse.market_id)
            market_id_str = (
                f"{market_label} ({balance_fuse.market_id})"
                if market_label != "UNKNOWN"
                else str(balance_fuse.market_id)
            )
            f_balance = pool.submit(
                plasma_vault.total_assets_in_market, balance_fuse.market_id
            )
            f_contract = pool.submit(
                get_contract_name, chain_id, balance_fuse.fuse, api_key
            )
            futures.append((idx, market_id_str, f_balance, f_contract))

        rows: list[tuple[str, ...]] = []
        for idx, market_id_str, f_balance, f_contract in futures:
            assets_in_market = f_balance.result()
            totals.raw_total += assets_in_market
            totals.per_market[market_id_str] = assets_in_market
            if asset_price_usd is not None:
                totals.usd_total += (assets_in_market / 10**decimals) * asset_price_usd
            contract_name = f_contract.result()
            balance_str = (
                f"{_format_amount(assets_in_market, decimals)} {asset_symbol}"
                f"{_format_usd(assets_in_market, decimals, asset_price_usd)}"
                f" (cached)"
            )
            rows.append(
                (
                    str(idx),
                    market_id_str,
                    balance_str,
                    balance_fuses[idx - 1].fuse,
                    contract_name or "?",
                )
            )
    _print_table(("#", "Market", "Balance", "Fuse", "Contract"), rows)
    return totals


def _print_substrates(  # pylint: disable=too-complex
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    balance_fuses: list,
    chain_id: int,
    api_key: str | None,
) -> set[str]:
    # Phase 1: fetch all substrates in parallel
    with ThreadPoolExecutor() as pool:
        substrate_futures: list[tuple[str, int, Future]] = []
        for balance_fuse in balance_fuses:
            market_label = _market_name(balance_fuse.market_id)
            market_id_str = (
                f"{market_label} ({balance_fuse.market_id})"
                if market_label != "UNKNOWN"
                else str(balance_fuse.market_id)
            )
            fut = pool.submit(
                plasma_vault.get_market_substrates, balance_fuse.market_id
            )
            substrate_futures.append((market_id_str, balance_fuse.market_id, fut))

    # Collect substrates and identify addresses to resolve
    market_substrates: list[tuple[str, int, list]] = []
    all_addresses: set[str] = set()
    for market_id_str, mid, fut in substrate_futures:
        if not (substrates := fut.result()):
            continue
        market_substrates.append((market_id_str, mid, substrates))
        for sub in substrates:
            sub_info = _format_substrate(sub, market_id=mid)
            if sub_info.address:
                all_addresses.add(sub_info.address)

    if not market_substrates:
        click.echo("  (none)")
        return {a.lower() for a in all_addresses}

    # Phase 2: resolve all symbols + contract names in parallel
    with ThreadPoolExecutor() as pool:
        symbol_futs = {
            addr: pool.submit(_resolve_token_symbol, ctx, addr)
            for addr in all_addresses
        }
        contract_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in all_addresses
        }

        resolved_symbols = {addr: fut.result() for addr, fut in symbol_futs.items()}
        resolved_contracts = {addr: fut.result() for addr, fut in contract_futs.items()}

    # Phase 3: print
    for market_id_str, mid, substrates in market_substrates:
        click.echo(f"  {market_id_str}:")
        for sub in substrates:
            sub_info = _format_substrate(sub, market_id=mid)
            if sub_info.address:
                symbol = resolved_symbols.get(sub_info.address, "")
                contract = resolved_contracts.get(sub_info.address, "")
                details = _substrate_details(
                    symbol, contract, sub_info.type_label, sub_info.extra
                )
                click.echo(f"    {sub_info.address}{details}")
            elif sub_info.is_error:
                click.secho(f"    {sub_info.raw_hex} [encoding error]", fg="red")
            else:
                parts = []
                if sub_info.type_label:
                    parts.append(sub_info.type_label)
                for k, v in sub_info.extra.items():
                    parts.append(f"{k}={v}")
                label = ", ".join(parts) if parts else "bytes32"
                is_no_decoder = sub_info.type_label.startswith("no_decoder(")
                fg = "red" if is_no_decoder else None
                click.secho(f"    {sub_info.raw_hex} ({label})", fg=fg)

    return {a.lower() for a in all_addresses}
