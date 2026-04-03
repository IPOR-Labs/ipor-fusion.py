"""Dependency balance graph analysis for PlasmaVault markets."""

from __future__ import annotations


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
