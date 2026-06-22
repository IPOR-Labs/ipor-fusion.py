"""Dependency balance graph analysis for PlasmaVault markets."""

from __future__ import annotations

from ipor_fusion.market_ids import IporFusionMarkets


# Markets where action fuses are intentionally registered without a matching
# balance fuse. These represent flow-through actions (flash loans, swaps,
# maintenance) that hold no persistent on-chain position — funds end up tracked
# either by ERC20_VAULT_BALANCE or by another protocol's balance fuse.
NO_BALANCE_FUSE_MARKETS: frozenset[int] = frozenset(
    {
        IporFusionMarkets.UNISWAP_SWAP_V2,
        IporFusionMarkets.UNISWAP_SWAP_V3,
        IporFusionMarkets.UNIVERSAL_TOKEN_SWAPPER,
        IporFusionMarkets.MORPHO_FLASH_LOAN,
        IporFusionMarkets.HARVEST_HARD_WORK,
    }
)

# uint256 max — sentinel used by burn-request-fee fuses (ZERO_BALANCE_MARKET).
# Position never touches ERC20 balances, so dependency on ERC20_VAULT_BALANCE
# is not expected.
_ZERO_BALANCE_MARKET: int = 2**256 - 1

# Markets that hold non-ERC20 positions (Uniswap LP NFTs, etc.) and would not
# meaningfully refresh ERC20 cache. Kept narrow so new market types fail loudly
# until explicitly listed.
_ERC20_DEP_NOT_REQUIRED: frozenset[int] = frozenset(
    {
        IporFusionMarkets.ERC20_VAULT_BALANCE,  # cannot depend on itself
        _ZERO_BALANCE_MARKET,
    }
)


def find_orphan_fuse_markets(
    fuse_markets: dict[str, int],
    balance_fuse_market_ids: set[int],
) -> dict[int, list[str]]:
    """Return markets claimed by action fuses but lacking a balance fuse.

    Such a configuration means positions opened via the action fuse will not
    contribute to ``totalAssets`` — a silent accounting bug. Markets in
    ``NO_BALANCE_FUSE_MARKETS`` (flash loans, swaps, maintenance) are excluded
    because they intentionally have no balance fuse.

    Args:
        fuse_markets: map of fuse address to market_id (from ``MARKET_ID()``).
            Entries with ``market_id`` ``None`` are filtered upstream.
        balance_fuse_market_ids: set of market_ids that currently have a
            registered balance fuse.

    Returns:
        Mapping ``market_id -> [fuse_address, ...]`` for orphans, sorted by
        market_id. Empty dict when configuration is healthy.
    """
    orphans: dict[int, list[str]] = {}
    for fuse_addr, market_id in fuse_markets.items():
        if market_id in balance_fuse_market_ids:
            continue
        if market_id in NO_BALANCE_FUSE_MARKETS:
            continue
        orphans.setdefault(market_id, []).append(fuse_addr)
    return dict(sorted(orphans.items()))


def find_markets_missing_erc20_dependency(
    balance_fuse_market_ids: set[int],
    dependency_graph: dict[int, list[int]],
) -> list[int]:
    """Return balance-fuse markets that should depend on ERC20_VAULT_BALANCE
    but don't.

    Whenever a market's balance is realised by holding an underlying ERC20
    (lending pools, vault wrappers, Morpho liquidity, etc.), opening or
    closing a position changes the vault's ERC20 balance. If that market
    omits ``ERC20_VAULT_BALANCE`` from its dependency edges, then calling
    ``updateMarketsBalances(market_id)`` won't refresh the ERC20 cache —
    ``totalAssets`` then drifts from on-chain reality.

    Markets in ``_ERC20_DEP_NOT_REQUIRED`` (the ERC20 market itself, sentinel
    burn-fee market) are excluded.
    """
    erc20 = IporFusionMarkets.ERC20_VAULT_BALANCE
    missing: list[int] = []
    for market_id in sorted(balance_fuse_market_ids):
        if market_id in _ERC20_DEP_NOT_REQUIRED:
            continue
        deps = dependency_graph.get(market_id, [])
        if erc20 not in deps:
            missing.append(market_id)
    return missing


def erc20_balance_tracks_non_underlying(
    erc20_substrate_addrs: set[str], underlying_addr: str
) -> bool:
    """True if the ERC20_VAULT_BALANCE market tracks any token besides the
    underlying.

    Idle underlying held directly on the vault is already counted in
    ``totalAssets`` via ERC4626 base accounting, so it needs no balance-graph
    wiring. Only *non-underlying* idle tokens — swap intermediates, borrowed
    assets, reward tokens in multi-asset vaults — are tracked by
    ERC20_VAULT_BALANCE and require markets to depend on it.

    When this set is empty (a single-asset optimizer, or no
    ERC20_VAULT_BALANCE market at all) no balance market needs that dependency,
    so the missing-dependency check must not fire.
    """
    underlying = underlying_addr.lower()
    return any(addr.lower() != underlying for addr in erc20_substrate_addrs)


def compute_update_reach(graph: dict[int, list[int]]) -> dict[int, set[int]]:
    """For each market with dependencies, compute the transitive closure.

    When ``updateMarketsBalances(market)`` is called on-chain, the contract
    refreshes *market* itself **and** every market reachable by following
    dependency edges.  This function returns that full reachable set
    (excluding the root market itself).
    """
    cache: dict[int, set[int]] = {}

    def _reach(node: int, visiting: set[int]) -> set[int]:
        if node in cache:
            return cache[node]
        visiting.add(node)
        result: set[int] = set()
        for dep in graph.get(node, []):
            result.add(dep)
            if dep not in visiting:
                result |= _reach(dep, visiting)
        visiting.discard(node)
        cache[node] = result
        return result

    for node in graph:
        _reach(node, set())

    return {
        node: reachable - {node}
        for node, reachable in cache.items()
        if reachable - {node}
    }


def compute_update_groups(graph: dict[int, list[int]]) -> list[set[int]]:
    """Compute connected components of the dependency graph.

    Two markets belong to the same update group if they are connected by
    dependency edges in *either* direction.  Markets with no dependencies
    and not depended-upon are singletons (omitted from output for brevity).
    """
    adj: dict[int, set[int]] = {}
    for node, deps in graph.items():
        adj.setdefault(node, set()).update(deps)
        for dep in deps:
            adj.setdefault(dep, set()).add(node)

    visited: set[int] = set()
    groups: list[set[int]] = []

    for start in sorted(adj):
        if start in visited:
            continue
        component: set[int] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            stack.extend(adj.get(node, set()) - visited)
        if len(component) > 1:
            groups.append(component)

    return groups
