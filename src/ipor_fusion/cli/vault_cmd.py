from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar

import click
from web3 import Web3
from web3.types import HexStr

from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    load_contract_cache,
    save_config,
    update_contract_cache,
)
from ipor_fusion.cli.config_store import (
    load_deployment_cache,
    update_deployment_cache,
)
from ipor_fusion.cli.explorer import get_contract_name, get_deployment_tx
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.market_ids import IporFusionMarkets


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


def _build_market_lookup() -> dict[int, str]:
    lookup: dict[int, str] = {}
    for name in dir(IporFusionMarkets):
        if not name.startswith("_"):
            val = getattr(IporFusionMarkets, name)
            if isinstance(val, int):
                lookup[val] = name
    return lookup


_MARKET_LOOKUP: dict[int, str] = _build_market_lookup()


def _market_name(market_id: int) -> str:
    return _MARKET_LOOKUP.get(market_id, "UNKNOWN")


def _resolve_vault(cfg: FusionConfig, vault_address: str | None) -> str:
    if vault_address is not None:
        return vault_address
    if cfg.default_vault is not None:
        return cfg.default_vault
    raise click.UsageError(
        "No vault specified. Use --vault or set default with "
        "'fusion config set-default-vault'"
    )


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
        "No chain ID. Use --chain-id or save vault with 'fusion vault add'"
    )


def _resolve_provider(cfg: FusionConfig, chain_id: int) -> str:
    if provider_url := cfg.providers.get(str(chain_id)):
        return provider_url
    raise click.UsageError(
        f"No provider for chain {chain_id}. "
        f"Use 'fusion config set-provider {chain_id} <url>'"
    )


UINT256_MAX = 2**256 - 1


def _format_amount(raw: int, decimals: int) -> str:
    if decimals == 0:
        return f"{raw:,}"
    integer_part = raw // (10**decimals)
    fractional_part = raw % (10**decimals)
    frac_str = str(fractional_part).zfill(decimals)[:6].rstrip("0") or "0"
    return f"{integer_part:,}.{frac_str}"


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
    type=int,
    default=None,
    help="Chain ID (auto-detected when only one provider is configured).",
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
    """List saved vaults."""
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
                    "default": bool(
                        cfg.default_vault
                        and v.address.lower() == cfg.default_vault.lower()
                    ),
                }
            )
        click.echo(json.dumps(entries, indent=2))
        return
    rows: list[tuple[str, ...]] = []
    for v in cfg.vaults:
        chain = CHAIN_NAMES.get(v.chain_id, str(v.chain_id))
        label = v.label
        if cfg.default_vault and v.address.lower() == cfg.default_vault.lower():
            label += " *"
        rows.append((chain, label, v.address))
    _print_table(("Chain", "Label", "Address"), rows)


vault.add_command(list_vaults, "ls")


@vault.command("info")
@click.option(
    "--vault", "vault_address", default=None, type=ADDRESS, help="Vault address."
)
@click.option("--chain-id", type=int, default=None, help="Chain ID.")
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
    vault_address: str | None,
    chain_id: int | None,
    block_number: int | None,
    json_output: bool,
) -> None:
    """Display full on-chain vault state."""
    cfg = load_config()
    vault_address = _resolve_vault(cfg, vault_address)
    chain_id = _resolve_chain_id(cfg, vault_address, chain_id)
    provider_url = _resolve_provider(cfg, chain_id)

    ctx = Web3Context.from_url(provider_url)
    if block_number is not None:
        ctx.default_block = block_number
    checksum_address = Web3.to_checksum_address(vault_address)
    plasma_vault = PlasmaVault(ctx, checksum_address)

    data = _fetch_vault_data(ctx, plasma_vault, block_number)
    _print_vault_info(
        ctx, plasma_vault, cfg, data, vault_address, chain_id, json_output
    )


@dataclass
class _VaultData:  # pylint: disable=too-many-instance-attributes
    block_label: str
    block_timestamp: int
    share_decimals: int
    asset_decimals: int
    total_assets: int
    total_supply: int
    supply_cap: int
    asset: str
    asset_symbol: str
    access_manager: str
    price_oracle_addr: str
    rewards_manager: str | None
    withdraw_manager: str | None
    asset_price_usd: float | None
    fuses: list
    balance_fuses: list
    instant_fuses: list
    deployment_block: int | None = None
    deployment_timestamp: int | None = None


def _fetch_vault_data(
    ctx: Web3Context, plasma_vault: PlasmaVault, block_number: int | None
) -> _VaultData:
    with ThreadPoolExecutor() as pool:
        # Phase 1: all independent vault reads in parallel
        f_block = pool.submit(lambda: ctx.web3.eth.block_number)
        f_decimals = pool.submit(plasma_vault.decimals)
        f_total_assets = pool.submit(plasma_vault.total_assets)
        f_total_supply = pool.submit(plasma_vault.total_supply)
        f_supply_cap = pool.submit(plasma_vault.get_total_supply_cap)
        f_asset = pool.submit(plasma_vault.underlying_asset_address)
        f_access = pool.submit(plasma_vault.get_access_manager_address)
        f_oracle = pool.submit(plasma_vault.get_price_oracle_middleware_address)
        f_fuses = pool.submit(plasma_vault.get_fuses)
        f_balance_fuses = pool.submit(plasma_vault.get_balance_fuses)
        f_rewards: Future = pool.submit(
            _safe_call, plasma_vault.get_rewards_claim_manager_address
        )
        f_withdraw = pool.submit(plasma_vault.withdraw_manager_address)
        f_instant = pool.submit(plasma_vault.get_instant_withdrawal_fuses)

        # Phase 2: asset-dependent (wait for asset + oracle addresses)
        asset = f_asset.result()
        price_oracle_addr = f_oracle.result()
        asset_erc20 = ERC20(ctx, asset)
        oracle = PriceOracleMiddleware(ctx, price_oracle_addr)

        f_symbol: Future = pool.submit(_safe_call, asset_erc20.symbol)
        f_adec = pool.submit(asset_erc20.decimals)
        f_price: Future = pool.submit(_safe_call, lambda: oracle.get_asset_price(asset))

        # Collect all results
        latest_block = f_block.result()
        effective_block = block_number if block_number is not None else latest_block
        block_label = (
            str(block_number)
            if block_number is not None
            else f"{latest_block} (latest)"
        )
        block_info = ctx.web3.eth.get_block(effective_block)
        block_timestamp: int = block_info["timestamp"]
        asset_price = f_price.result()

        return _VaultData(
            block_label=block_label,
            block_timestamp=block_timestamp,
            share_decimals=f_decimals.result(),
            asset_decimals=f_adec.result(),
            total_assets=f_total_assets.result(),
            total_supply=f_total_supply.result(),
            supply_cap=f_supply_cap.result(),
            asset=asset,
            asset_symbol=f_symbol.result() or "?",
            access_manager=f_access.result(),
            price_oracle_addr=price_oracle_addr,
            rewards_manager=f_rewards.result(),
            withdraw_manager=f_withdraw.result(),
            asset_price_usd=asset_price.readable() if asset_price else None,
            fuses=f_fuses.result(),
            balance_fuses=f_balance_fuses.result(),
            instant_fuses=f_instant.result(),
        )


def _fetch_deployment_info(
    ctx: Web3Context,
    chain_id: int,
    vault_address: str,
    api_key: str | None,
) -> tuple[int | None, int | None]:
    """Return (block, timestamp) for the vault deployment, using cache."""
    cache_key = f"{chain_id}:{vault_address}"
    cache = load_deployment_cache()
    if entry := cache.get(cache_key):
        return entry["block"], entry["timestamp"]

    if not (tx_hash := get_deployment_tx(chain_id, vault_address, api_key)):
        return None, None

    try:
        tx = ctx.web3.eth.get_transaction(HexStr(tx_hash))
        block_number: int = tx["blockNumber"]
        block_info = ctx.web3.eth.get_block(block_number)
        timestamp: int = block_info["timestamp"]
        update_deployment_cache(cache_key, block_number, timestamp)
        return block_number, timestamp
    except Exception:  # pylint: disable=broad-except
        return None, None


def _format_age(timestamp: int) -> str:
    """Format deployment age as human-readable string."""
    delta = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(
        timestamp, tz=timezone.utc
    )
    days = delta.days
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


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

    click.echo(f"Vault:            {vault_address}")
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
            balance_fuses_json.append(
                {
                    "market": market_str,
                    "balance": {
                        "raw": assets_in_market,
                        "formatted": _format_amount(
                            assets_in_market, data.asset_decimals
                        ),
                    },
                    "fuse": bf.fuse,
                    "contract": bf_contract_futs[i].result() or "?",
                }
            )

        # Substrates - also resolve symbols/contracts for addresses
        all_sub_addresses: set[str] = set()
        market_subs_raw: list[tuple[str, list]] = []
        for i, bf in enumerate(data.balance_fuses):
            market_label = _market_name(bf.market_id)
            market_str = (
                market_label if market_label != "UNKNOWN" else str(bf.market_id)
            )
            if subs := substrate_futs[i].result():
                market_subs_raw.append((market_str, subs))
                for sub in subs:
                    sub_info = _format_substrate(sub)
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
        for market_str, subs in market_subs_raw:
            entries = []
            for sub in subs:
                sub_info = _format_substrate(sub)
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
                    entries.append(entry)
                else:
                    entries.append({"raw": sub_info.raw_hex})
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
        "fuses": fuses_json,
        "balance_fuses": balance_fuses_json,
        "instant_withdrawal_fuses": instant_json,
        "substrates": substrates_json,
        "erc20_balances": erc20_json,
        "reconciliation": reconciliation_json,
        "health_check": health_json,
    }


@dataclass
class _TokenInfo:
    symbol: str
    balance_str: str
    usd_value: float | None = None


@dataclass
class _TokenDetail:
    address: str
    symbol: str
    decimals: int | None
    balance: int | None
    price_usd: float | None
    usd_value: float | None
    note: str


@dataclass
class _Erc20Totals:
    raw_asset_total: int = 0
    usd_total: float = 0.0
    cached_bf_value: int = 0
    tokens_without_price: list[str] = field(default_factory=list)
    token_addrs_on_vault: set[str] = field(default_factory=set)
    token_info: dict[str, _TokenInfo] = field(default_factory=dict)
    token_details: list[_TokenDetail] = field(default_factory=list)


def _compute_erc20_balances(  # pylint: disable=too-complex,too-many-locals
    ctx: Web3Context, plasma_vault: PlasmaVault, data: _VaultData
) -> _Erc20Totals:
    totals = _Erc20Totals()
    erc20_market = None
    for balance_fuse in data.balance_fuses:
        if _market_name(balance_fuse.market_id) == "ERC20_VAULT_BALANCE":
            erc20_market = balance_fuse.market_id
            break
    if erc20_market is None:
        return totals

    totals.cached_bf_value = plasma_vault.total_assets_in_market(erc20_market)

    substrates = plasma_vault.get_market_substrates(erc20_market)
    vault_addr = Web3.to_checksum_address(plasma_vault.address)
    oracle = PriceOracleMiddleware(
        ctx, Web3.to_checksum_address(data.price_oracle_addr)
    )

    erc20_substrate_addrs: set[str] = set()
    token_addrs: list[str] = []
    for sub in substrates:
        sub_info = _format_substrate(sub)
        if sub_info.address:
            token_addrs.append(sub_info.address)
            erc20_substrate_addrs.add(sub_info.address.lower())

    if not token_addrs:
        return totals

    with ThreadPoolExecutor() as pool:
        token_futures: dict[str, dict[str, Future]] = {}
        for addr in token_addrs:
            checksum = Web3.to_checksum_address(addr)
            token = ERC20(ctx, checksum)
            token_futures[addr] = {
                "symbol": pool.submit(_resolve_token_symbol, ctx, addr),
                "decimals": pool.submit(_safe_call, token.decimals),
                "balance": pool.submit(
                    _safe_call, lambda t=token: t.balance_of(vault_addr)
                ),
                "price": pool.submit(
                    _safe_call, lambda a=checksum: oracle.get_asset_price(a)
                ),
            }

        resolved: list[tuple] = []
        for addr in token_addrs:
            futs = token_futures[addr]
            resolved.append(
                (
                    addr,
                    futs["symbol"].result() or "?",
                    futs["decimals"].result(),
                    futs["balance"].result(),
                    futs["price"].result(),
                )
            )

        for addr, symbol, token_decimals, balance, price in resolved:
            if token_decimals is None or balance is None:
                continue
            price_usd = price.readable() if price else None
            if balance > 0:
                totals.token_addrs_on_vault.add(addr.lower())
            if price_usd is not None and data.asset_price_usd:
                token_value_usd = (balance / 10**token_decimals) * price_usd
                totals.usd_total += token_value_usd
                totals.raw_asset_total += int(
                    token_value_usd / data.asset_price_usd * 10**data.asset_decimals
                )
            elif balance > 0:
                totals.tokens_without_price.append(f"{addr} ({symbol})")

        cached_usd = (
            (totals.cached_bf_value / 10**data.asset_decimals) * data.asset_price_usd
            if data.asset_price_usd
            else None
        )

        for addr, symbol, token_decimals, balance, price in resolved:
            if token_decimals is None or balance is None:
                totals.token_details.append(
                    _TokenDetail(addr, symbol, token_decimals, balance, None, None, "")
                )
                continue

            price_usd = price.readable() if price else None
            token_usd_val = (
                (balance / 10**token_decimals) * price_usd if price_usd else None
            )

            if balance > 0:
                totals.token_info[addr.lower()] = _TokenInfo(
                    symbol=symbol,
                    balance_str=_format_amount(balance, token_decimals),
                    usd_value=token_usd_val,
                )

            is_underlying = addr.lower() == data.asset.lower()
            in_bf = addr.lower() in erc20_substrate_addrs
            note = ""
            if is_underlying:
                note = "underlying asset"
            elif not in_bf:
                note = "not in ERC20_VAULT_BALANCE"
            elif balance == 0:
                note = "in ERC20_VAULT_BALANCE, balance=0"
            elif (
                price_usd
                and cached_usd is not None
                and (balance / 10**token_decimals * price_usd) > cached_usd
            ):
                usd_fmt = f"${balance / 10**token_decimals * price_usd:,.2f}"
                note = f"{usd_fmt} not reflected in totalAssets (stale cache)"
            elif in_bf:
                note = "in ERC20_VAULT_BALANCE"

            totals.token_details.append(
                _TokenDetail(
                    addr,
                    symbol,
                    token_decimals,
                    balance,
                    price_usd,
                    token_usd_val,
                    note,
                )
            )
    return totals


def _print_erc20_balances(
    ctx: Web3Context, plasma_vault: PlasmaVault, data: _VaultData
) -> _Erc20Totals:
    totals = _compute_erc20_balances(ctx, plasma_vault, data)
    if not totals.token_details:
        has_erc20_market = any(
            _market_name(bf.market_id) == "ERC20_VAULT_BALANCE"
            for bf in data.balance_fuses
        )
        click.echo(
            "  (none)" if has_erc20_market else "  (no ERC20_VAULT_BALANCE market)"
        )
        return totals

    rows: list[tuple[str, ...]] = []
    for td in totals.token_details:
        if td.decimals is None or td.balance is None:
            rows.append((td.address, td.symbol, "error", "", ""))
            continue
        balance_str = (
            f"{_format_amount(td.balance, td.decimals)}"
            f"{_format_usd(td.balance, td.decimals, td.price_usd)}"
        )
        rows.append(
            (
                td.address,
                td.symbol,
                balance_str,
                f"${td.price_usd:,.2f}" if td.price_usd else "N/A",
                td.note,
            )
        )
    _print_table(("Token", "Symbol", "Balance", "Price", "Note"), rows)
    return totals


@dataclass
class _ReconciliationData:
    bf_total_raw: int = 0
    bf_total_usd: float = 0.0
    erc20_total_raw: int = 0
    erc20_total_usd: float = 0.0
    sum_raw: int = 0
    sum_usd: float = 0.0
    on_chain_raw: int = 0
    on_chain_usd: float | None = None
    delta_raw: int = 0
    delta_usd: float = 0.0
    delta_percent: float = 0.0


def _compute_reconciliation(
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
) -> _ReconciliationData:
    decimals = data.asset_decimals
    price = data.asset_price_usd

    sum_raw = bf_totals.raw_total + erc20_totals.raw_asset_total
    sum_usd = bf_totals.usd_total + erc20_totals.usd_total
    on_chain = data.total_assets
    delta_raw = sum_raw - on_chain
    delta_usd = abs(sum_usd - (on_chain / 10**decimals * price)) if price else 0
    pct = abs(delta_raw / on_chain * 100) if on_chain else 0.0

    return _ReconciliationData(
        bf_total_raw=bf_totals.raw_total,
        bf_total_usd=bf_totals.usd_total,
        erc20_total_raw=erc20_totals.raw_asset_total,
        erc20_total_usd=erc20_totals.usd_total,
        sum_raw=sum_raw,
        sum_usd=sum_usd,
        on_chain_raw=on_chain,
        on_chain_usd=(on_chain / 10**decimals * price) if price else None,
        delta_raw=delta_raw,
        delta_usd=delta_usd,
        delta_percent=pct,
    )


def _print_reconciliation(
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
) -> None:
    recon = _compute_reconciliation(data, bf_totals, erc20_totals)
    decimals = data.asset_decimals
    sym = data.asset_symbol
    price = data.asset_price_usd

    fmt_bf = _format_amount(recon.bf_total_raw, decimals)
    fmt_erc20 = _format_amount(recon.erc20_total_raw, decimals)
    fmt_sum = _format_amount(recon.sum_raw, decimals)
    fmt_onchain = _format_amount(recon.on_chain_raw, decimals)
    fmt_delta = _format_amount(abs(recon.delta_raw), decimals)

    usd_bf = f" (${recon.bf_total_usd:,.2f})" if price else ""
    usd_erc20 = f" (${recon.erc20_total_usd:,.2f})" if price else ""
    usd_sum = f" (${recon.sum_usd:,.2f})" if price else ""
    usd_onchain = _format_usd(recon.on_chain_raw, decimals, price)
    usd_delta = f" (${recon.delta_usd:,.2f})" if price else ""

    click.echo("Balance Reconciliation:")
    click.echo(
        f"  Balance fuses total:  {fmt_bf} {sym}{usd_bf}   [sum balance fuses, cached]"
    )
    click.echo(
        f"  ERC20 direct total:   {fmt_erc20} {sym}{usd_erc20}   [tokens on vault]"
    )
    click.echo(f"  Sum:                  {fmt_sum} {sym}{usd_sum}")
    click.echo(f"  On-chain totalAssets: {fmt_onchain} {sym}{usd_onchain}")
    sign = "+" if recon.delta_raw >= 0 else "-"
    delta_line = (
        f"  Delta:                {sign}{fmt_delta} {sym}"
        f"{usd_delta}   ({recon.delta_percent:.2f}%)"
    )
    if recon.delta_percent > 1.0:
        click.secho(f"{delta_line} !!! MISMATCH", fg="red")
    else:
        click.echo(delta_line)

    _ui = erc20_totals.token_info.get(data.asset.lower())
    erc20_non_underlying = erc20_totals.raw_asset_total - (
        int(_ui.usd_value / price * 10**decimals)
        if _ui and _ui.usd_value and price
        else 0
    )

    if erc20_totals.cached_bf_value > 0 or erc20_non_underlying > 0:
        cached_fmt = _format_amount(erc20_totals.cached_bf_value, decimals)
        direct_fmt = _format_amount(erc20_non_underlying, decimals)
        if erc20_non_underlying > 0 and erc20_totals.cached_bf_value == 0:
            click.secho(
                f"  ERC20_VAULT_BALANCE cached={cached_fmt}, "
                f"ERC20 direct={direct_fmt} — likely stale cache",
                fg="yellow",
            )
        elif erc20_totals.cached_bf_value > 0 and erc20_non_underlying > 0:
            cached_vs_direct = abs(erc20_totals.cached_bf_value - erc20_non_underlying)
            if cached_vs_direct / erc20_non_underlying > 0.1:
                click.secho(
                    f"  ERC20_VAULT_BALANCE cached={cached_fmt}, "
                    f"ERC20 direct={direct_fmt} — significant divergence",
                    fg="yellow",
                )


@dataclass
class _HealthCheckData:
    ok: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _compute_health_check(  # pylint: disable=too-complex
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
    all_substrate_addrs: set[str],
) -> _HealthCheckData:
    decimals = data.asset_decimals
    sym = data.asset_symbol
    underlying = data.asset.lower()
    result = _HealthCheckData()

    underlying_info = erc20_totals.token_info.get(underlying)
    underlying_raw = 0
    if underlying_info and underlying_info.usd_value and data.asset_price_usd:
        underlying_raw = int(
            underlying_info.usd_value / data.asset_price_usd * 10**decimals
        )
    expected = bf_totals.raw_total + underlying_raw
    if data.total_assets > 0:
        pct = abs(expected - data.total_assets) / data.total_assets * 100
        fmt_exp = _format_amount(expected, decimals)
        fmt_ta = _format_amount(data.total_assets, decimals)
        line = (
            f"Balance fuses + underlying = {fmt_exp} {sym} "
            f"vs totalAssets {fmt_ta} {sym} ({pct:.2f}%)"
        )
        if pct < 1.0:
            result.ok.append(line)
        else:
            result.warnings.append(line)

    uncovered: list[str] = []
    cached_usd = (
        erc20_totals.cached_bf_value / 10**decimals * data.asset_price_usd
        if data.asset_price_usd and erc20_totals.cached_bf_value > 0
        else 0
    )
    for addr in sorted(erc20_totals.token_addrs_on_vault):
        if addr == underlying:
            continue
        if not (info := erc20_totals.token_info.get(addr)):
            continue
        usd_str = f" (${info.usd_value:,.2f})" if info.usd_value else ""
        if addr not in all_substrate_addrs:
            uncovered.append(
                f"    {info.symbol:<16} {info.balance_str}{usd_str}"
                f" — not in any balance fuse substrate"
            )
        elif 0 < cached_usd < (info.usd_value or 0):
            uncovered.append(
                f"    {info.symbol:<16} {info.balance_str}{usd_str}"
                f" — {usd_str.strip()} not reflected in totalAssets (stale cache)"
            )
    if uncovered:
        result.warnings.append("ERC20 holdings not covered by balance fuses:")
        result.warnings.extend(uncovered)

    erc20_non_underlying = erc20_totals.raw_asset_total - underlying_raw
    if erc20_non_underlying > 0 and (
        erc20_totals.cached_bf_value == 0
        or abs(erc20_non_underlying - erc20_totals.cached_bf_value)
        / erc20_non_underlying
        > 0.1
    ):
        result.warnings.append(
            "updateMarketsBalances needed — ERC20_VAULT_BALANCE cache stale"
        )

    for token_ref in erc20_totals.tokens_without_price:
        result.warnings.append(f"No price feed for {token_ref}")

    return result


def _print_health_check(
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
    all_substrate_addrs: set[str],
) -> None:
    health = _compute_health_check(data, bf_totals, erc20_totals, all_substrate_addrs)
    click.echo("Health Check:")
    for line in health.ok:
        click.secho(f"  {line}", fg="green")
    if not health.ok and not health.warnings:
        click.secho("  All checks passed", fg="green")
    for line in health.warnings:
        click.secho(f"  {line}", fg="yellow")


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    if not rows:
        click.echo("  (none)")
        return

    widths = [len(hdr) for hdr in headers]
    for row in rows:
        for col_idx, val in enumerate(row):
            widths[col_idx] = max(widths[col_idx], len(val))

    fmt = "  ".join(f"{{:<{wid}}}" for wid in widths)
    click.echo(f"  {fmt.format(*headers)}")
    click.echo(f"  {fmt.format(*('-' * wid for wid in widths))}")
    for row in rows:
        click.echo(f"  {fmt.format(*row)}")


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


@dataclass
class _BalanceFuseTotals:
    raw_total: int = 0
    usd_total: float = 0.0
    per_market: dict[str, int] = field(default_factory=dict)


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
                market_label
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


def _substrate_details(symbol: str, contract: str, type_label: str) -> str:
    parts: list[str] = []
    if symbol:
        parts.append(f"symbol={symbol}")
    if contract:
        parts.append(f"contract={contract}")
    if type_label:
        parts.append(f"substrate-type={type_label}")
    return f" ({', '.join(parts)})" if parts else ""


def _print_substrates(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    balance_fuses: list,
    chain_id: int,
    api_key: str | None,
) -> set[str]:
    # Phase 1: fetch all substrates in parallel
    with ThreadPoolExecutor() as pool:
        substrate_futures: list[tuple[str, Future]] = []
        for balance_fuse in balance_fuses:
            market_label = _market_name(balance_fuse.market_id)
            market_id_str = (
                market_label
                if market_label != "UNKNOWN"
                else str(balance_fuse.market_id)
            )
            fut = pool.submit(
                plasma_vault.get_market_substrates, balance_fuse.market_id
            )
            substrate_futures.append((market_id_str, fut))

    # Collect substrates and identify addresses to resolve
    market_substrates: list[tuple[str, list]] = []
    all_addresses: set[str] = set()
    for market_id_str, fut in substrate_futures:
        if not (substrates := fut.result()):
            continue
        market_substrates.append((market_id_str, substrates))
        for sub in substrates:
            sub_info = _format_substrate(sub)
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
    for market_id_str, substrates in market_substrates:
        click.echo(f"  {market_id_str}:")
        for sub in substrates:
            sub_info = _format_substrate(sub)
            if sub_info.address:
                symbol = resolved_symbols.get(sub_info.address, "")
                contract = resolved_contracts.get(sub_info.address, "")
                details = _substrate_details(symbol, contract, sub_info.type_label)
                click.echo(f"    {sub_info.address}{details}")
            else:
                click.secho(f"    {sub_info.raw_hex} [encoding error]", fg="red")

    return {a.lower() for a in all_addresses}


EBISU_SUBSTRATE_TYPES: dict[int, str] = {
    0: "UNDEFINED",
    1: "ZAPPER",
    2: "REGISTRY",
}


@dataclass
class _SubstrateInfo:
    address: str = ""
    raw_hex: str = ""
    type_label: str = ""


def _format_substrate(raw: bytes) -> _SubstrateInfo:
    hex_str = raw.hex()
    if len(hex_str) != 64:
        return _SubstrateInfo(raw_hex=f"0x{hex_str}")
    # plain address: 12 zero bytes (24 hex chars) + 20 byte address
    if hex_str[:24] == "0" * 24:
        return _SubstrateInfo(address=f"0x{hex_str[24:]}")
    # typed substrate: 11 zero bytes (22 hex chars) + 1 byte type + 20 byte address
    if hex_str[:22] == "0" * 22:
        type_byte = int(hex_str[22:24], 16)
        addr = f"0x{hex_str[24:]}"
        label = EBISU_SUBSTRATE_TYPES.get(type_byte, f"type={type_byte}")
        return _SubstrateInfo(address=addr, type_label=label)
    return _SubstrateInfo(raw_hex=f"0x{hex_str}")


_NO_CONTRACT = "no contract"


def _resolve_token_symbol(ctx: Web3Context, address: str) -> str:
    cache = load_contract_cache()
    cache_key = f"symbol:{address}"
    if cached := cache.get(cache_key):
        return cached

    checksum = Web3.to_checksum_address(address)
    code = ctx.web3.eth.get_code(checksum)
    if not code or code == b"":
        update_contract_cache(cache_key, _NO_CONTRACT)
        return _NO_CONTRACT

    try:
        symbol = ERC20(ctx, checksum).symbol()
    except Exception:  # pylint: disable=broad-except
        symbol = ""
    if symbol:
        update_contract_cache(cache_key, symbol)
    return symbol


def _format_usd(raw: int, decimals: int, price_usd: float | None) -> str:
    if price_usd is None:
        return ""
    value = (raw / 10**decimals) * price_usd
    return f" (${value:,.2f})"


T = TypeVar("T")


def _safe_call(func: Callable[[], T]) -> T | None:
    try:
        return func()
    except Exception:  # pylint: disable=broad-except
        return None
