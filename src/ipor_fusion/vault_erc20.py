"""The vault's ERC20_VAULT_BALANCE tokens read directly: balance, decimals and
middleware price per token, valued in the underlying asset. Part of the
`VaultData` snapshot; validation compares it with the market's cached value."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from eth_typing import ChecksumAddress, HexAddress
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.substrates import decode_substrate
from ipor_fusion.types import MarketId, PlasmaVaultAddress


@dataclass(frozen=True)
class Erc20Holding:
    """A token of the ERC20_VAULT_BALANCE market as held by the vault.

    ``decimals`` and ``balance`` are ``None`` when the token could not be read;
    ``note`` says how the holding relates to the market's cached value.
    """

    address: HexAddress
    symbol: str
    decimals: int | None
    balance: int | None
    price_usd: float | None
    usd_value: float | None
    note: str


@dataclass(frozen=True)
class Erc20Holdings:
    """The vault's ERC20_VAULT_BALANCE tokens read directly.

    ``value_in_asset`` and ``value_usd`` sum the priced tokens, underlying
    included; ``market_cached_value`` is the market's cached
    ``totalAssetsInMarket``. ``unpriced_tokens`` lists ``"<address> (<symbol>)"``
    for held tokens that could not be valued.
    """

    tokens: tuple[Erc20Holding, ...] = ()
    value_in_asset: int = 0
    value_usd: float = 0.0
    market_cached_value: int = 0
    unpriced_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Asset:
    address: ChecksumAddress
    decimals: int
    price_usd: float | None


@dataclass(frozen=True)
class _TokenRead:
    address: HexAddress
    symbol: str
    decimals: int | None
    balance: int | None
    price_usd: float | None


def _read_erc20_holdings(
    ctx: Web3Context,
    vault: PlasmaVaultAddress,
    *,
    balance_fuses: Sequence[BalanceFuse],
    market_substrates: Mapping[MarketId, Sequence[bytes]],
    market_balances: Mapping[MarketId, int],
    oracle: PriceOracleMiddleware,
    asset: _Asset,
    resolve_symbol: Callable[[HexAddress], str],
) -> Erc20Holdings:
    """Empty when the vault has no ERC20_VAULT_BALANCE market."""
    erc20_market = next(
        (
            bf.market_id
            for bf in balance_fuses
            if bf.market_id == IporFusionMarkets.ERC20_VAULT_BALANCE
        ),
        None,
    )
    if erc20_market is None:
        return Erc20Holdings()
    cached_value = market_balances[erc20_market]
    addresses = [
        sub_info.address
        for sub in market_substrates.get(erc20_market, [])
        if (sub_info := decode_substrate(sub, market_id=erc20_market)).address
    ]
    if not addresses:
        return Erc20Holdings(market_cached_value=cached_value)
    reads = _read_tokens(ctx, vault, oracle, addresses, resolve_symbol)
    return _value_tokens(asset, reads, cached_value)


def _read_tokens(
    ctx: Web3Context,
    vault: PlasmaVaultAddress,
    oracle: PriceOracleMiddleware,
    addresses: list[HexAddress],
    resolve_symbol: Callable[[HexAddress], str],
) -> list[_TokenRead]:
    """Every token's decimals, the vault's balance and the middleware price in
    one Multicall3 batch (a read that fails is ``None``); symbols through
    ``resolve_symbol``."""
    calls: list[Call[Any]] = []
    for address in addresses:
        checksum = Web3.to_checksum_address(address)
        token = ERC20(checksum)
        calls += [
            token.decimals(),
            token.balance_of(vault),
            oracle.get_asset_price(checksum),
        ]
    results = Multicall3(ctx).try_aggregate(calls)
    reads: list[_TokenRead] = []
    for index, address in enumerate(addresses):
        decimals, balance, price = results[3 * index : 3 * index + 3]
        reads.append(
            _TokenRead(
                address=address,
                symbol=resolve_symbol(address) or "?",
                decimals=decimals,
                balance=balance,
                price_usd=price.readable() if price else None,
            )
        )
    return reads


def _value_tokens(
    asset: _Asset, reads: list[_TokenRead], cached_value: int
) -> Erc20Holdings:
    value_in_asset = 0
    value_usd = 0.0
    unpriced: list[str] = []
    for read in reads:
        if read.decimals is None or read.balance is None:
            continue
        if read.price_usd is not None and asset.price_usd:
            token_usd = (read.balance / 10**read.decimals) * read.price_usd
            value_usd += token_usd
            value_in_asset += int(token_usd / asset.price_usd * 10**asset.decimals)
        elif read.balance > 0:
            unpriced.append(f"{read.address} ({read.symbol})")

    cached_usd = (
        (cached_value / 10**asset.decimals) * asset.price_usd
        if asset.price_usd
        else None
    )
    return Erc20Holdings(
        tokens=tuple(_holding(asset, read, cached_usd) for read in reads),
        value_in_asset=value_in_asset,
        value_usd=value_usd,
        market_cached_value=cached_value,
        unpriced_tokens=tuple(unpriced),
    )


def _holding(asset: _Asset, read: _TokenRead, cached_usd: float | None) -> Erc20Holding:
    if read.decimals is None or read.balance is None:
        return Erc20Holding(
            read.address, read.symbol, read.decimals, read.balance, None, None, ""
        )
    amount = read.balance / 10**read.decimals
    usd_value = amount * read.price_usd if read.price_usd else None
    if read.address.lower() == asset.address.lower():
        note = "underlying asset"
    elif read.balance == 0:
        note = "in ERC20_VAULT_BALANCE, balance=0"
    elif (
        read.price_usd
        and cached_usd is not None
        and amount * read.price_usd > cached_usd
    ):
        note = (
            f"${amount * read.price_usd:,.2f} not reflected in totalAssets "
            f"(stale cache)"
        )
    else:
        note = "in ERC20_VAULT_BALANCE"
    return Erc20Holding(
        read.address,
        read.symbol,
        read.decimals,
        read.balance,
        read.price_usd,
        usd_value,
        note,
    )
