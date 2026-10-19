"""Plasma Vault validation at one block: health findings, the totalAssets
reconciliation, ERC20 holdings and lending health, with an overall verdict.

`validate_vault` reads everything it needs. A caller that already holds the
vault state (the ``fusion vault info`` command) validates it without a second
fetch: `read_erc20_holdings`, then `validate_vault_state`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from eth_typing import ChecksumAddress, HexAddress
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, Web3RPCError

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.oracle import price_oracle_middleware
from ipor_fusion.core.plasma_vault import BalanceFuse, PlasmaVault
from ipor_fusion.dependency_graph import (
    erc20_balance_tracks_non_underlying,
    find_markets_missing_erc20_dependency,
    find_orphan_fuse_markets,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.lending_health import VaultLendingHealth
from ipor_fusion.substrates import decode_substrate, format_market_label, market_name
from ipor_fusion.types import ChainId, FuseAddress, MarketId, PlasmaVaultAddress, Shares
from ipor_fusion.types import Check as Check
from ipor_fusion.types import Severity as Severity
from ipor_fusion.types import Verdict as Verdict
from ipor_fusion.vault_data import (
    GET_MARKET_SUBSTRATES,
    GET_PRICE_ORACLE_MIDDLEWARE,
    GET_TOTAL_SUPPLY_CAP,
    fetch_vault_data,
)

T = TypeVar("T")

_UNIMPLEMENTED_GETTER_IMPACT = {
    GET_MARKET_SUBSTRATES: (
        "substrates are unavailable, so substrate-based checks (ERC20 "
        "coverage, lending positions, unpriceable tokens) were skipped"
    ),
    GET_TOTAL_SUPPLY_CAP: "it has no supply cap, reported as unlimited",
    GET_PRICE_ORACLE_MIDDLEWARE: (
        "its price oracle was read through the older getPriceOracle()"
    ),
}

_UNPRICEABLE_BLOCKS = "updateMarketsBalances and every execute on this market"


class WithdrawQueue(Protocol):
    """The WithdrawManager state the reconciliation needs."""

    @property
    def shares_to_release(self) -> int: ...


class UnpriceableToken(Protocol):
    """A token a balance fuse prices via the PriceOracleMiddleware, which
    cannot price it (see `ipor_fusion.readers.unpriceable`)."""

    @property
    def market_id(self) -> MarketId: ...
    @property
    def token(self) -> str: ...
    @property
    def via(self) -> str: ...
    @property
    def zero_balance_reverts(self) -> bool: ...
    @property
    def position_open(self) -> bool | None: ...
    @property
    def morpho_collateral(self) -> bool: ...
    @property
    def price_source(self) -> str | None: ...


class VaultState(Protocol):
    """The fetched vault state validation runs on.

    Amounts are raw on-chain integers in the underlying asset's decimals.
    ``None`` collections mean "not read" (a chain without the reader, or a
    vault that predates the getter).
    """

    @property
    def block_number(self) -> int: ...
    @property
    def block_timestamp(self) -> int: ...
    @property
    def asset(self) -> ChecksumAddress: ...
    @property
    def asset_symbol(self) -> str: ...
    @property
    def asset_decimals(self) -> int: ...
    @property
    def asset_price_usd(self) -> float | None: ...
    @property
    def total_assets(self) -> int: ...
    @property
    def underlying_balance_on_vault(self) -> int: ...
    @property
    def price_oracle_addr(self) -> ChecksumAddress: ...
    @property
    def balance_fuses(self) -> Sequence[BalanceFuse]: ...
    @property
    def market_substrates(self) -> Mapping[MarketId, Sequence[bytes]] | None: ...
    @property
    def fuse_markets(self) -> Mapping[FuseAddress, MarketId] | None: ...
    @property
    def dependency_graph(self) -> Mapping[MarketId, Sequence[MarketId]] | None: ...
    @property
    def withdraw_manager_data(self) -> WithdrawQueue | None: ...
    @property
    def lending_health(self) -> VaultLendingHealth | None: ...
    @property
    def unpriceable_priced_tokens(self) -> Sequence[UnpriceableToken] | None: ...
    @property
    def unimplemented_getters(self) -> Sequence[str]: ...


@dataclass(frozen=True)
class Finding:
    """One check result. ``message`` is the human-readable line; the uncovered
    ERC20 finding spans several lines (a heading plus one line per token)."""

    check: Check
    severity: Severity
    message: str


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
class Reconciliation:
    """``totalAssets`` against what the vault's markets and idle underlying add
    up to. Raw amounts are in the underlying asset's decimals; ``*_usd`` values
    are 0 when the asset has no USD price (``on_chain_total_assets_usd``:
    ``None``).

    Balance fuses already value non-underlying ERC20s (via
    ERC20_VAULT_BALANCE), so ``sum`` is balance fuses plus idle underlying
    only; ``erc20_direct_total`` is informational and never added to it.
    """

    balance_fuses_total: int
    balance_fuses_total_usd: float
    underlying_on_vault: int
    underlying_on_vault_usd: float
    erc20_direct_total: int
    erc20_direct_total_usd: float
    sum: int
    sum_usd: float
    on_chain_total_assets: int
    on_chain_total_assets_usd: float | None
    delta: int
    delta_usd: float
    delta_percent: float
    # sharesToRelease of the WithdrawManager converted to assets.
    pending_withdrawals: int
    pending_withdrawals_usd: float
    # totalAssets minus idle underlying: the global market total the vault
    # stores, which per-market caches drift from after partial refreshes.
    implied_market_total: int

    @property
    def market_storage_divergence(self) -> int:
        return self.balance_fuses_total - self.implied_market_total


@dataclass(frozen=True)
class VaultValidation:
    """Validation of one vault at ``block_number``.

    ``findings`` keep the order checks run in; ``criticals``, ``warnings`` and
    ``ok`` are their messages by severity, and ``verdict`` the worst severity
    found. Raw amounts are in the underlying ``asset``'s decimals.
    """

    vault: PlasmaVaultAddress
    chain_id: ChainId
    block_number: int
    block_timestamp: int
    asset: ChecksumAddress
    asset_symbol: str
    asset_decimals: int
    asset_price_usd: float | None
    findings: tuple[Finding, ...]
    reconciliation: Reconciliation
    erc20: Erc20Holdings
    lending_health: VaultLendingHealth | None
    verdict: Verdict = field(init=False)
    criticals: tuple[str, ...] = field(init=False)
    warnings: tuple[str, ...] = field(init=False)
    ok: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        by_severity = {
            severity: tuple(f.message for f in self.findings if f.severity == severity)
            for severity in ("critical", "warning", "ok")
        }
        verdict: Verdict = (
            "critical"
            if by_severity["critical"]
            else "warning"
            if by_severity["warning"]
            else "ok"
        )
        object.__setattr__(self, "verdict", verdict)
        object.__setattr__(self, "criticals", by_severity["critical"])
        object.__setattr__(self, "warnings", by_severity["warning"])
        object.__setattr__(self, "ok", by_severity["ok"])


def validate_vault(
    ctx: Web3Context,
    vault_address: PlasmaVaultAddress,
    block_number: int | None = None,
    *,
    resolve_symbol: Callable[[HexAddress], str] | None = None,
) -> VaultValidation:
    """Read a Plasma Vault and validate it, every read pinned to one block.

    ``block_number`` defaults to ``ctx.default_block`` when that is a number,
    else the latest block; ``ctx`` itself is not modified. ``resolve_symbol``
    names ERC20 tokens in findings (default: their on-chain ``symbol()``).
    """
    if block_number is None:
        default = ctx.default_block
        block_number = default if isinstance(default, int) else None
    if block_number is None:
        block_number = ctx.web3.eth.block_number
    pinned = Web3Context(
        ctx.web3,
        ctx.chain_id,
        log_scan_timeout_s=ctx.log_scan_timeout_s,
        log_scan_max_requests=ctx.log_scan_max_requests,
    )
    pinned.default_block = block_number

    plasma_vault = PlasmaVault(pinned, vault_address)
    state = fetch_vault_data(
        pinned, plasma_vault, block_number, chain_id=pinned.chain_id
    )
    markets = list(dict.fromkeys(bf.market_id for bf in state.balance_fuses))
    balances = Multicall3(pinned).aggregate(
        [plasma_vault.total_assets_in_market(market) for market in markets]
    )
    return validate_vault_state(
        pinned,
        plasma_vault,
        state,
        dict(zip(markets, balances, strict=True)),
        read_erc20_holdings(pinned, plasma_vault, state, resolve_symbol),
    )


def validate_vault_state(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    state: VaultState,
    market_balances: Mapping[MarketId, int],
    erc20: Erc20Holdings,
) -> VaultValidation:
    """Validate an already-fetched vault.

    ``market_balances`` maps each balance-fuse market to its cached
    ``totalAssetsInMarket``, once per market; its order is the order the USD
    total is summed in. ``erc20`` comes from `read_erc20_holdings`. The only
    read is ``convertToAssets`` of the WithdrawManager's shares to release.
    """
    reconciliation = _reconcile(ctx, plasma_vault, state, market_balances, erc20)
    findings = [
        *_unimplemented_getter_findings(state),
        *_orphan_fuse_findings(state),
        *_missing_erc20_dependency_findings(state),
        *_unpriceable_token_findings(state),
        *_lending_health_findings(state),
        *_reconciliation_findings(state, reconciliation),
        *_erc20_findings(state, erc20),
    ]
    return VaultValidation(
        vault=plasma_vault.address,
        chain_id=ctx.chain_id,
        block_number=state.block_number,
        block_timestamp=state.block_timestamp,
        asset=state.asset,
        asset_symbol=state.asset_symbol,
        asset_decimals=state.asset_decimals,
        asset_price_usd=state.asset_price_usd,
        findings=tuple(findings),
        reconciliation=reconciliation,
        erc20=erc20,
        lending_health=state.lending_health,
    )


def read_erc20_holdings(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    state: VaultState,
    resolve_symbol: Callable[[HexAddress], str] | None = None,
) -> Erc20Holdings:
    """Read every ERC20_VAULT_BALANCE token the vault is granted: balance,
    decimals and middleware price, valued in the underlying asset.

    Empty when the vault has no ERC20_VAULT_BALANCE market.
    ``resolve_symbol`` maps a token address to its symbol, ``""`` when unknown
    (default: the on-chain ``symbol()``).
    """
    erc20_market = next(
        (
            bf.market_id
            for bf in state.balance_fuses
            if market_name(bf.market_id) == "ERC20_VAULT_BALANCE"
        ),
        None,
    )
    if erc20_market is None:
        return Erc20Holdings()
    cached_value = plasma_vault.total_assets_in_market(erc20_market).call()
    addresses = [
        sub_info.address
        for sub in (state.market_substrates or {}).get(erc20_market, [])
        if (sub_info := decode_substrate(sub, market_id=erc20_market)).address
    ]
    if not addresses:
        return Erc20Holdings(market_cached_value=cached_value)
    reads = _read_tokens(ctx, plasma_vault, state, addresses, resolve_symbol)
    return _value_tokens(state, reads, cached_value)


@dataclass(frozen=True)
class _TokenRead:
    address: HexAddress
    symbol: str
    decimals: int | None
    balance: int | None
    price_usd: float | None


def _read_tokens(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    state: VaultState,
    addresses: list[HexAddress],
    resolve_symbol: Callable[[HexAddress], str] | None,
) -> list[_TokenRead]:
    resolve = resolve_symbol or (lambda address: _onchain_symbol(ctx, address))
    vault = Web3.to_checksum_address(plasma_vault.address)
    oracle = price_oracle_middleware(
        ctx, Web3.to_checksum_address(state.price_oracle_addr)
    )
    with ThreadPoolExecutor() as pool:
        futures: list[tuple[HexAddress, Future, Future, Future, Future]] = []
        for address in addresses:
            checksum = Web3.to_checksum_address(address)
            token = ERC20(ctx, checksum)
            futures.append(
                (
                    address,
                    pool.submit(resolve, address),
                    pool.submit(_call_or_none, token.decimals()),
                    pool.submit(_call_or_none, token.balance_of(vault)),
                    pool.submit(_call_or_none, oracle.get_asset_price(checksum)),
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
    state: VaultState, reads: list[_TokenRead], cached_value: int
) -> Erc20Holdings:
    decimals = state.asset_decimals
    asset_price = state.asset_price_usd
    value_in_asset = 0
    value_usd = 0.0
    unpriced: list[str] = []
    for read in reads:
        if read.decimals is None or read.balance is None:
            continue
        if read.price_usd is not None and asset_price:
            token_usd = (read.balance / 10**read.decimals) * read.price_usd
            value_usd += token_usd
            value_in_asset += int(token_usd / asset_price * 10**decimals)
        elif read.balance > 0:
            unpriced.append(f"{read.address} ({read.symbol})")

    cached_usd = (cached_value / 10**decimals) * asset_price if asset_price else None
    return Erc20Holdings(
        tokens=tuple(_holding(state, read, cached_usd) for read in reads),
        value_in_asset=value_in_asset,
        value_usd=value_usd,
        market_cached_value=cached_value,
        unpriced_tokens=tuple(unpriced),
    )


def _holding(
    state: VaultState, read: _TokenRead, cached_usd: float | None
) -> Erc20Holding:
    if read.decimals is None or read.balance is None:
        return Erc20Holding(
            read.address, read.symbol, read.decimals, read.balance, None, None, ""
        )
    amount = read.balance / 10**read.decimals
    usd_value = amount * read.price_usd if read.price_usd else None
    if read.address.lower() == state.asset.lower():
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


def _reconcile(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    state: VaultState,
    market_balances: Mapping[MarketId, int],
    erc20: Erc20Holdings,
) -> Reconciliation:
    decimals = state.asset_decimals
    price = state.asset_price_usd

    balance_fuses_total = 0
    balance_fuses_usd = 0.0
    for balance in market_balances.values():
        balance_fuses_total += balance
        if price is not None:
            balance_fuses_usd += (balance / 10**decimals) * price

    underlying = state.underlying_balance_on_vault
    underlying_usd = 0.0
    if underlying > 0 and price:
        underlying_usd = (underlying / 10**decimals) * price

    total = balance_fuses_total + underlying
    total_usd = balance_fuses_usd + underlying_usd
    on_chain = state.total_assets
    delta = total - on_chain
    delta_usd = abs(total_usd - (on_chain / 10**decimals * price)) if price else 0
    delta_percent = abs(delta / on_chain * 100) if on_chain else 0.0

    # totalAssets reads the global market total while balance fuses report
    # per-market caches; they diverge after withdrawals with sharesToRelease,
    # which refresh only the markets the instant-withdrawal fuses touch.
    pending = 0
    pending_usd = 0.0
    queue = state.withdraw_manager_data
    if queue and queue.shares_to_release > 0:
        shares = Shares(queue.shares_to_release)
        assets = _call_or_none(plasma_vault.convert_to_assets(shares))
        if assets is not None:
            pending = assets
            if price:
                pending_usd = (assets / 10**decimals) * price

    return Reconciliation(
        balance_fuses_total=balance_fuses_total,
        balance_fuses_total_usd=balance_fuses_usd,
        underlying_on_vault=underlying,
        underlying_on_vault_usd=underlying_usd,
        erc20_direct_total=erc20.value_in_asset,
        erc20_direct_total_usd=erc20.value_usd,
        sum=total,
        sum_usd=total_usd,
        on_chain_total_assets=on_chain,
        on_chain_total_assets_usd=(on_chain / 10**decimals * price) if price else None,
        delta=delta,
        delta_usd=delta_usd,
        delta_percent=delta_percent,
        pending_withdrawals=pending,
        pending_withdrawals_usd=pending_usd,
        implied_market_total=max(0, on_chain - underlying),
    )


def _unimplemented_getter_findings(state: VaultState) -> list[Finding]:
    return [
        Finding(
            "unimplemented_getter",
            "warning",
            f"WARNING — vault predates {getter}: {_UNIMPLEMENTED_GETTER_IMPACT[getter]}",
        )
        for getter in state.unimplemented_getters
    ]


def _orphan_fuse_findings(state: VaultState) -> list[Finding]:
    """Action fuses whose market has no balance fuse: their positions silently
    bypass ``totalAssets``. Flow-through markets (flash loans, swaps) are
    excluded by ``find_orphan_fuse_markets``."""
    if not state.fuse_markets:
        return []
    balance_markets = {bf.market_id for bf in state.balance_fuses}
    orphans = find_orphan_fuse_markets(state.fuse_markets, balance_markets)
    return [
        Finding(
            "orphan_fuse",
            "critical",
            f"CRITICAL — action fuse(s) registered for market "
            f"{format_market_label(market_id)} but no balance fuse — positions "
            f"via these fuses will not contribute to totalAssets: "
            f"{', '.join(fuses)}",
        )
        for market_id, fuses in orphans.items()
    ]


def _erc20_market_addresses(state: VaultState) -> set[str]:
    """Lowercase token addresses tracked by ERC20_VAULT_BALANCE; a substrate
    holds the address in its low 20 bytes."""
    raws = (state.market_substrates or {}).get(
        IporFusionMarkets.ERC20_VAULT_BALANCE, []
    )
    return {f"0x{raw.hex()[24:]}".lower() for raw in raws if len(raw.hex()) == 64}


def _missing_erc20_dependency_findings(state: VaultState) -> list[Finding]:
    """Balance-fuse markets whose dependency graph omits ERC20_VAULT_BALANCE.

    Only relevant when the vault holds non-underlying tokens idle: those are
    tracked by ERC20_VAULT_BALANCE, and markets touching them must depend on it
    so ``updateMarketsBalances(market)`` refreshes that cache. Idle underlying
    is already in ``totalAssets`` via ERC4626 base accounting, so a
    single-asset vault needs no such edge.
    """
    if not erc20_balance_tracks_non_underlying(
        _erc20_market_addresses(state), state.asset
    ):
        return []
    balance_markets = {bf.market_id for bf in state.balance_fuses}
    if not balance_markets:
        return []
    missing = find_markets_missing_erc20_dependency(
        balance_markets, state.dependency_graph or {}
    )
    return [
        Finding(
            "missing_erc20_dependency",
            "critical",
            f"CRITICAL — market {format_market_label(market_id)} has a balance "
            f"fuse but its dependency graph omits ERC20_VAULT_BALANCE — "
            f"updateMarketsBalances({market_id}) will not refresh the ERC20 "
            f"cache, causing totalAssets drift after deposits/withdrawals "
            f"through this market",
        )
        for market_id in missing
    ]


def _unpriceable_token_findings(state: VaultState) -> list[Finding]:
    """Critical only when ``balanceOf()`` demonstrably reverts now; a latent or
    unread position is a warning."""
    return [
        _unpriceable_token_finding(t) for t in state.unpriceable_priced_tokens or []
    ]


def _unpriceable_token_finding(token: UnpriceableToken) -> Finding:
    source = (
        f"its price source {token.price_source} reverts"
        if token.price_source
        else "it has no price source"
    )
    head = (
        f"market {format_market_label(token.market_id)}: {token.via} — "
        f"PriceOracleMiddleware.getAssetPrice({token.token}) reverts ({source})"
    )
    if token.zero_balance_reverts:
        return Finding(
            "unpriceable_token",
            "critical",
            f"CRITICAL — {head} — balanceOf() reverts even with no position, "
            f"which blocks {_UNPRICEABLE_BLOCKS}",
        )
    if token.position_open:
        return Finding(
            "unpriceable_token",
            "critical",
            f"CRITICAL — {head} — the vault holds it, so balanceOf() reverts "
            f"now and blocks {_UNPRICEABLE_BLOCKS}",
        )
    if token.position_open is None:
        position = (
            "position not read, so current impact is unknown; balanceOf() "
            "reverts while the vault holds any"
        )
    else:
        position = (
            "the vault holds none in this position, so it does not revert yet, "
            "but anyone can open one for the vault (deposits on its behalf "
            "need no consent); balanceOf() then reverts"
        )
    fix = "add a price source"
    if token.morpho_collateral:
        fix += (
            ", or, if every Morpho market of this market is and stays "
            "supply-only (no borrow, no collateral counted in NAV), register "
            "a deployed MorphoOnlyLiquidityBalanceFuse as its balance fuse; "
            "it values supply only"
        )
    return Finding(
        "unpriceable_token",
        "warning",
        f"WARNING — {head} — {position} and blocks {_UNPRICEABLE_BLOCKS} — fix: {fix}",
    )


def _lending_health_findings(state: VaultState) -> list[Finding]:
    health = state.lending_health
    if not health or not health.has_lending_positions:
        return []
    findings: list[Finding] = []
    for market in health.markets:
        if market.ltv_usage_percent is None:
            continue
        ltv = f"{market.current_ltv:.4f}" if market.current_ltv is not None else "?"
        health_factor = (
            f", health_factor={market.health_factor:.4f}"
            if market.health_factor is not None
            else ""
        )
        line = (
            f"{market.protocol} {market.market_name}: "
            f"LTV {ltv}/{market.max_ltv:.4f} "
            f"({market.ltv_usage_percent:.1f}% usage){health_factor}"
        )
        if market.is_critical:
            findings.append(
                Finding(
                    "lending_health",
                    "critical",
                    f"CRITICAL — {line} — NEAR LIQUIDATION",
                )
            )
        elif market.is_warning:
            findings.append(
                Finding(
                    "lending_health",
                    "warning",
                    f"WARNING — {line} — approaching liquidation",
                )
            )
        else:
            findings.append(Finding("lending_health", "ok", line))
    return findings


def _reconciliation_findings(
    state: VaultState, reconciliation: Reconciliation
) -> list[Finding]:
    total_assets = state.total_assets
    if total_assets <= 0:
        return []
    decimals = state.asset_decimals
    symbol = state.asset_symbol
    expected = reconciliation.sum
    percent = abs(expected - total_assets) / total_assets * 100
    line = (
        f"Balance fuses + underlying = {_format_amount(expected, decimals)} "
        f"{symbol} vs totalAssets {_format_amount(total_assets, decimals)} "
        f"{symbol} ({percent:.2f}%)"
    )
    if percent < 1.0:
        return [Finding("total_assets_reconciliation", "ok", line)]
    findings = [Finding("total_assets_reconciliation", "warning", line)]
    if reconciliation.pending_withdrawals > 0 and reconciliation.delta > 0:
        pending = _format_amount(reconciliation.pending_withdrawals, decimals)
        findings.append(
            Finding(
                "pending_withdrawals",
                "warning",
                f"Pending withdrawals: {pending} {symbol} "
                f"(sharesToRelease) — market storage may be stale",
            )
        )
    divergence = reconciliation.market_storage_divergence
    if abs(divergence) > 10**decimals:
        findings.append(
            Finding(
                "market_storage_drift",
                "warning",
                f"Market storage drift: "
                f"{_format_amount(abs(divergence), decimals)} {symbol} between "
                f"sum(per-market) and global total — "
                f"updateMarketsBalances() needed for all markets",
            )
        )
    return findings


def _substrate_addresses(state: VaultState) -> set[str]:
    """Lowercase addresses among the substrates of every balance-fuse market."""
    substrates = state.market_substrates or {}
    return {
        sub_info.address.lower()
        for bf in state.balance_fuses
        for sub in substrates.get(bf.market_id, [])
        if (sub_info := decode_substrate(sub, market_id=bf.market_id)).address
    }


def _erc20_findings(state: VaultState, erc20: Erc20Holdings) -> list[Finding]:
    findings: list[Finding] = []
    if uncovered := _uncovered_erc20_lines(state, erc20):
        findings.append(
            Finding(
                "uncovered_erc20",
                "warning",
                "\n".join(["ERC20 holdings not covered by balance fuses:", *uncovered]),
            )
        )
    underlying = state.underlying_balance_on_vault
    non_underlying = erc20.value_in_asset - underlying
    cached = erc20.market_cached_value
    if non_underlying > 0 and (
        cached == 0 or abs(non_underlying - cached) / non_underlying > 0.1
    ):
        findings.append(
            Finding(
                "erc20_cache_stale",
                "warning",
                "updateMarketsBalances needed — ERC20_VAULT_BALANCE cache stale",
            )
        )
    findings.extend(
        Finding("missing_price_feed", "warning", f"No price feed for {token}")
        for token in erc20.unpriced_tokens
    )
    return findings


def _uncovered_erc20_lines(state: VaultState, erc20: Erc20Holdings) -> list[str]:
    """Held non-underlying tokens no balance fuse values, or values only from a
    stale ERC20_VAULT_BALANCE cache."""
    price = state.asset_price_usd
    cached = erc20.market_cached_value
    cached_usd = (
        cached / 10**state.asset_decimals * price if price and cached > 0 else 0
    )
    covered = _substrate_addresses(state)
    # Last read wins for a token granted twice.
    held = {token.address.lower(): token for token in erc20.tokens if token.balance}
    lines: list[str] = []
    for address in sorted(held):
        token = held[address]
        if (
            address == state.asset.lower()
            or token.decimals is None
            or token.balance is None
        ):
            continue
        usd = f" (${token.usd_value:,.2f})" if token.usd_value else ""
        row = (
            f"    {token.symbol:<16} "
            f"{_format_amount(token.balance, token.decimals)}{usd}"
        )
        if address not in covered:
            lines.append(f"{row} — not in any balance fuse substrate")
        elif 0 < cached_usd < (token.usd_value or 0):
            lines.append(
                f"{row} — {usd.strip()} not reflected in totalAssets (stale cache)"
            )
    return lines


def _onchain_symbol(ctx: Web3Context, address: str) -> str:
    symbol = _call_or_none(ERC20(ctx, Web3.to_checksum_address(address)).symbol())
    return symbol or ""


def _call_or_none(call: Call[T]) -> T | None:
    """A read that reverts or fails at the RPC is ``None``; anything else
    (an empty result, a decoding error) still raises."""
    try:
        return call.call()
    except (ContractLogicError, Web3RPCError, TimeExhausted):
        return None


def _format_amount(raw: int, decimals: int) -> str:
    """A raw amount with thousands separators and at most six decimals."""
    if decimals == 0:
        return f"{raw:,}"
    integer_part = raw // (10**decimals)
    fractional_part = raw % (10**decimals)
    frac_str = str(fractional_part).zfill(decimals)[:6].rstrip("0") or "0"
    return f"{integer_part:,}.{frac_str}"
