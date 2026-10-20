"""The vault's ERC20_VAULT_BALANCE tokens read directly: balance, decimals and
middleware price per token, valued in the underlying asset. Part of the
`VaultData` snapshot; validation compares it with the market's cached value."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial

from eth_typing import ChecksumAddress, HexAddress
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import price_oracle_middleware
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.substrates import decode_substrate
from ipor_fusion.types import MarketId, PlasmaVaultAddress
from ipor_fusion.vault_reads import _safe_call


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
    price_oracle: ChecksumAddress,
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
    reads = _read_tokens(ctx, vault, price_oracle, addresses, resolve_symbol)
    return _value_tokens(asset, reads, cached_value)


def _read_tokens(
    ctx: Web3Context,
    vault: PlasmaVaultAddress,
    price_oracle: ChecksumAddress,
    addresses: list[HexAddress],
    resolve_symbol: Callable[[HexAddress], str],
) -> list[_TokenRead]:
    oracle = price_oracle_middleware(ctx, price_oracle)
    with ThreadPoolExecutor() as pool:
        futures: list[tuple[HexAddress, Future, Future, Future, Future]] = []
        for address in addresses:
            checksum = Web3.to_checksum_address(address)
            token = ERC20(checksum)
            futures.append(
                (
                    address,
                    pool.submit(resolve_symbol, address),
                    pool.submit(_safe_call, partial(token.decimals().call, ctx)),
                    pool.submit(_safe_call, partial(token.balance_of(vault).call, ctx)),
                    pool.submit(
                        _safe_call,
                        partial(oracle.get_asset_price(checksum).call, ctx),
                    ),
                )
            )
        reads: list[_TokenRead] = []
        for address, symbol, decimals, balance, price in futures:
            price_value = price.result()
            reads.append(
                _TokenRead(
                    address=address,
                    symbol=symbol.result() or "?",
                    decimals=decimals.result(),
                    balance=balance.result(),
                    price_usd=price_value.readable() if price_value else None,
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
