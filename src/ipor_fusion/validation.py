"""Plasma Vault validation at one block: health findings, the totalAssets
reconciliation, ERC20 holdings and lending health, with an overall verdict.

`validate_vault` fetches the snapshot and validates it. A caller that already
holds one (the ``fusion vault info`` command) passes it to
`validate_vault_state`, which reads nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from eth_typing import ChecksumAddress
from web3.types import Timestamp

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.dependency_graph import (
    erc20_balance_tracks_non_underlying,
    find_markets_missing_erc20_dependency,
    find_orphan_fuse_markets,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.lending_health import VaultLendingHealth
from ipor_fusion.substrates import decode_substrate, format_market_label
from ipor_fusion.types import (
    Amount,
    ChainId,
    Decimals,
    FuseAddress,
    MarketId,
    PlasmaVaultAddress,
    Shares,
)
from ipor_fusion.types import Check as Check
from ipor_fusion.types import Severity as Severity
from ipor_fusion.types import Verdict as Verdict
from ipor_fusion.vault_data import (
    GET_MARKET_SUBSTRATES,
    GET_PRICE_ORACLE_MIDDLEWARE,
    GET_TOTAL_SUPPLY_CAP,
    fetch_vault_data,
)
from ipor_fusion.vault_erc20 import Erc20Holding as Erc20Holding
from ipor_fusion.vault_erc20 import Erc20Holdings as Erc20Holdings

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
    def shares_to_release(self) -> Shares: ...
    @property
    def shares_to_release_assets(self) -> Amount | None: ...


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
    def vault(self) -> PlasmaVaultAddress: ...
    @property
    def chain_id(self) -> ChainId: ...
    @property
    def block_number(self) -> int: ...
    @property
    def block_timestamp(self) -> Timestamp: ...
    @property
    def asset(self) -> ChecksumAddress: ...
    @property
    def asset_symbol(self) -> str: ...
    @property
    def asset_decimals(self) -> Decimals: ...
    @property
    def asset_price_usd(self) -> float | None: ...
    @property
    def total_assets(self) -> Amount: ...
    @property
    def underlying_balance_on_vault(self) -> Amount: ...
    @property
    def stored_markets_total(self) -> Amount | None:
        """The global market total the vault stores; None when not read."""
        ...

    @property
    def unrealized_management_fee(self) -> Amount | None:
        """``getUnrealizedManagementFee()``, None when not read."""
        ...

    @property
    def rewards_manager_balance(self) -> Amount | None:
        """The RewardsClaimManager's balanceOf(), part of totalAssets; 0
        without a manager, None when it could not be read."""
        ...

    @property
    def price_oracle(self) -> ChecksumAddress: ...
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
    @property
    def failed_reads(self) -> Sequence[str]:
        """Validation inputs whose read failed (``lending_health``,
        ``unpriceable_priced_tokens``): their checks did not run."""
        ...

    @property
    def market_balances(self) -> Mapping[MarketId, Amount]: ...
    @property
    def erc20(self) -> Erc20Holdings: ...


@dataclass(frozen=True)
class Finding:
    """One check result. ``message`` is the human-readable line; the uncovered
    ERC20 finding spans several lines (a heading plus one line per token)."""

    check: Check
    severity: Severity
    message: str


@dataclass(frozen=True)
class Reconciliation:
    """``totalAssets`` against what the vault's markets, idle underlying and
    rewards claim manager add up to. Raw amounts are in the underlying asset's decimals; ``*_usd`` values
    are 0 when the asset has no USD price (``on_chain_total_assets_usd``:
    ``None``).

    Balance fuses already value non-underlying ERC20s (via
    ERC20_VAULT_BALANCE), so ``sum`` is balance fuses plus idle underlying
    plus the rewards claim manager's balance, minus the unrealized management
    fee where ``totalAssets`` nets it (``management_fee_netted``);
    ``erc20_direct_total`` is informational and never added to it.
    """

    balance_fuses_total: Amount
    balance_fuses_total_usd: float
    underlying_on_vault: Amount
    underlying_on_vault_usd: float
    # RewardsClaimManager.balanceOf(): vested rewards totalAssets counts
    # (0 without a manager or when it could not be read).
    rewards_manager_balance: Amount
    rewards_manager_balance_usd: float
    # The unrealized management fee totalAssets subtracts, on vaults whose
    # contracts net it (before ipor-fusion #475); 0 on a vault returning gross.
    management_fee_netted: Amount
    management_fee_netted_usd: float
    erc20_direct_total: Amount
    erc20_direct_total_usd: float
    sum: Amount
    sum_usd: float
    on_chain_total_assets: Amount
    on_chain_total_assets_usd: float | None
    delta: Amount
    delta_usd: float
    delta_percent: float
    # sharesToRelease of the WithdrawManager converted to assets.
    pending_withdrawals: Amount
    pending_withdrawals_usd: float
    # totalAssets minus idle underlying and the rewards claim manager's
    # balance: the global market total the vault
    # stores, which per-market caches drift from after partial refreshes.
    implied_market_total: Amount

    @property
    def market_storage_divergence(self) -> Amount:
        return Amount(self.balance_fuses_total - self.implied_market_total)


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
    block_timestamp: Timestamp
    asset: ChecksumAddress
    asset_symbol: str
    asset_decimals: Decimals
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
    vault: PlasmaVaultAddress,
    *,
    block: int | None = None,
    metadata: bool = False,
) -> VaultValidation:
    """Fetch the vault's snapshot at one block (`fetch_vault_data`: ``block``,
    else the context's numeric default block, else the latest) and validate
    it; ``ctx`` itself is not modified.

    The checks need no display metadata, so by default the snapshot has no
    `token_symbols` / `token_decimals` and the ERC20 holdings are named
    ``"?"``; ``metadata=True`` adds one batched, cached read that names them
    in the holdings and findings."""
    return validate_vault_state(
        fetch_vault_data(ctx, vault, block=block, metadata=metadata)
    )


def validate_vault_state(state: VaultState) -> VaultValidation:
    """Validate an already-fetched snapshot; reads nothing."""
    reconciliation = _reconcile(state)
    findings = [
        *_unimplemented_getter_findings(state),
        *_rewards_manager_findings(state),
        *_orphan_fuse_findings(state),
        *_missing_erc20_dependency_findings(state),
        *_unpriceable_token_findings(state),
        *_lending_health_findings(state),
        *_failed_read_findings(state),
        *_reconciliation_findings(state, reconciliation),
        *_erc20_findings(state, state.erc20),
    ]
    return VaultValidation(
        vault=state.vault,
        chain_id=state.chain_id,
        block_number=state.block_number,
        block_timestamp=state.block_timestamp,
        asset=state.asset,
        asset_symbol=state.asset_symbol,
        asset_decimals=state.asset_decimals,
        asset_price_usd=state.asset_price_usd,
        findings=tuple(findings),
        reconciliation=reconciliation,
        erc20=state.erc20,
        lending_health=state.lending_health,
    )


def _reconcile(state: VaultState) -> Reconciliation:
    market_balances = state.market_balances
    erc20 = state.erc20
    decimals = state.asset_decimals
    price = state.asset_price_usd

    balance_fuses_total = Amount(0)
    balance_fuses_usd = 0.0
    for balance in market_balances.values():
        balance_fuses_total = Amount(balance_fuses_total + balance)
        if price is not None:
            balance_fuses_usd += (balance / 10**decimals) * price

    underlying = state.underlying_balance_on_vault
    underlying_usd = 0.0
    if underlying > 0 and price:
        underlying_usd = (underlying / 10**decimals) * price

    rewards = state.rewards_manager_balance or Amount(0)
    rewards_usd = _usd(rewards, decimals, price)

    on_chain = state.total_assets
    gross = Amount(balance_fuses_total + underlying + rewards)
    fee = _netted_management_fee(state, underlying + rewards)
    fee_usd = _usd(fee, decimals, price)
    total = Amount(gross - fee)
    total_usd = balance_fuses_usd + underlying_usd + rewards_usd - fee_usd
    delta = Amount(total - on_chain)
    delta_usd = abs(total_usd - (on_chain / 10**decimals * price)) if price else 0
    delta_percent = abs(delta / on_chain * 100) if on_chain else 0.0

    # totalAssets reads the global market total while balance fuses report
    # per-market caches; they diverge after withdrawals with sharesToRelease,
    # which refresh only the markets the instant-withdrawal fuses touch.
    pending = Amount(0)
    pending_usd = 0.0
    queue = state.withdraw_manager_data
    if queue and queue.shares_to_release > 0:
        assets = queue.shares_to_release_assets
        if assets is not None:
            pending = assets
            if price:
                pending_usd = (assets / 10**decimals) * price

    return Reconciliation(
        balance_fuses_total=balance_fuses_total,
        balance_fuses_total_usd=balance_fuses_usd,
        underlying_on_vault=underlying,
        underlying_on_vault_usd=underlying_usd,
        rewards_manager_balance=rewards,
        rewards_manager_balance_usd=rewards_usd,
        management_fee_netted=fee,
        management_fee_netted_usd=fee_usd,
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
        implied_market_total=Amount(max(0, on_chain + fee - underlying - rewards)),
    )


def _usd(amount: int, decimals: int, price: float | None) -> float:
    return (amount / 10**decimals) * price if amount and price else 0.0


def _netted_management_fee(state: VaultState, outside_markets: int) -> Amount:
    """The unrealized management fee ``totalAssets`` subtracts, 0 when it
    returns gross.

    PlasmaVault contracts before ipor-fusion #475 (Feb 2026) return
    ``max(0, gross - fee)``, later ones ``gross``, where gross is idle
    underlying plus the stored global market total plus the rewards manager's
    balance. The deployed version is not readable, so the formula is decided by
    which one ``totalAssets`` matches, from the stored global total rather than
    the per-market caches the reconciliation compares (a drift between those
    must not pass for the fee). Without the stored total, the fee or a match,
    nothing is netted.
    """
    fee, stored = state.unrealized_management_fee, state.stored_markets_total
    if not fee or stored is None:
        return Amount(0)
    gross = outside_markets + stored
    if state.total_assets == gross or state.total_assets != max(0, gross - fee):
        return Amount(0)
    return Amount(min(fee, gross))


def _unimplemented_getter_findings(state: VaultState) -> list[Finding]:
    return [
        Finding(
            "unimplemented_getter",
            "warning",
            f"WARNING — vault predates {getter}: {_UNIMPLEMENTED_GETTER_IMPACT[getter]}",
        )
        for getter in state.unimplemented_getters
    ]


def _rewards_manager_findings(state: VaultState) -> list[Finding]:
    if state.rewards_manager_balance is not None:
        return []
    return [
        Finding(
            "rewards_manager_balance",
            "warning",
            "WARNING — the rewards claim manager or its balanceOf() could not be "
            "read: totalAssets includes that balance, so the reconciliation "
            "leaves it out and its delta may be that balance",
        )
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


_FAILED_READ_IMPACT = {
    "lending_health": "the liquidation risk of the lending positions is unknown",
    "unpriceable_priced_tokens": "tokens the price oracle cannot price were not "
    "looked for",
}


def _failed_read_findings(state: VaultState) -> list[Finding]:
    findings = []
    for read in state.failed_reads:
        name, _, detail = read.partition(": ")
        what = f"{name} ({detail})" if detail else name
        impact = _FAILED_READ_IMPACT.get(name, "its check did not run")
        findings.append(
            Finding(
                "incomplete_read",
                "warning",
                f"WARNING — {what} could not be read: {impact}",
            )
        )
    return findings


def _lending_health_findings(state: VaultState) -> list[Finding]:
    health = state.lending_health
    if not health or not health.has_lending_positions:
        return []
    findings = [
        Finding(
            "lending_health",
            "warning",
            f"WARNING — {u.protocol} {u.market_name}"
            f"{f' ({u.substrate_id})' if u.substrate_id else ''}: health unknown, "
            f"{u.reason}",
        )
        for u in health.unreadable
    ]
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
    terms = "Balance fuses + underlying"
    if reconciliation.rewards_manager_balance > 0:
        terms += " + rewards manager"
    if reconciliation.management_fee_netted > 0:
        terms += " - unrealized management fee"
    line = (
        f"{terms} = {_format_amount(expected, decimals)} "
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
    # With the rewards manager's balance unknown the divergence may be that
    # balance; the rewards_manager_balance warning says so instead.
    if abs(divergence) > 10**decimals and state.rewards_manager_balance is not None:
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


def _format_amount(raw: int, decimals: int) -> str:
    """A raw amount with thousands separators and at most six decimals."""
    if decimals == 0:
        return f"{raw:,}"
    integer_part = raw // (10**decimals)
    fractional_part = raw % (10**decimals)
    frac_str = str(fractional_part).zfill(decimals)[:6].rstrip("0") or "0"
    return f"{integer_part:,}.{frac_str}"
