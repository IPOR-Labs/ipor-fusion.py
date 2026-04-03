"""Tests for dependency balance graph analysis."""

from ipor_fusion.cli.vault_dep_graph import (
    compute_update_groups,
    compute_update_reach,
)


class TestComputeUpdateReach:
    def test_empty_graph(self):
        assert compute_update_reach({}) == {}

    def test_single_dependency(self):
        graph = {1: [2]}
        reach = compute_update_reach(graph)
        assert reach[1] == {2}

    def test_linear_chain(self):
        graph = {1: [2], 2: [3]}
        reach = compute_update_reach(graph)
        assert reach[1] == {2, 3}
        assert reach[2] == {3}

    def test_diamond(self):
        graph = {1: [2, 3], 2: [4], 3: [4]}
        reach = compute_update_reach(graph)
        assert reach[1] == {2, 3, 4}
        assert reach[2] == {4}
        assert reach[3] == {4}

    def test_cycle(self):
        graph = {1: [2], 2: [1]}
        reach = compute_update_reach(graph)
        assert reach[1] == {2}
        assert reach[2] == {1}

    def test_cycle_with_tail(self):
        graph = {1: [2], 2: [1, 3]}
        reach = compute_update_reach(graph)
        assert reach[1] == {2, 3}
        assert reach[2] == {1, 3}

    def test_leaf_not_in_result(self):
        graph = {1: [2]}
        reach = compute_update_reach(graph)
        assert 2 not in reach


class TestComputeUpdateGroups:
    def test_empty_graph(self):
        assert not compute_update_groups({})

    def test_single_edge(self):
        graph = {1: [2]}
        groups = compute_update_groups(graph)
        assert len(groups) == 1
        assert groups[0] == {1, 2}

    def test_disjoint_subgraphs(self):
        graph = {1: [2], 3: [4]}
        groups = compute_update_groups(graph)
        assert len(groups) == 2
        group_sets = [frozenset(g) for g in groups]
        assert frozenset({1, 2}) in group_sets
        assert frozenset({3, 4}) in group_sets

    def test_connected_via_shared_dep(self):
        graph = {1: [3], 2: [3]}
        groups = compute_update_groups(graph)
        assert len(groups) == 1
        assert groups[0] == {1, 2, 3}

    def test_singletons_omitted(self):
        graph = {1: [2]}
        groups = compute_update_groups(graph)
        assert len(groups) == 1
        assert groups[0] == {1, 2}

    def test_real_world_vault(self):
        """Simulates the BTCD vault dependency graph."""
        morpho, erc4626, erc20, flash, uniswap = 14, 100002, 7, 19, 10
        graph = {
            flash: [erc20],
            morpho: [erc20, erc4626],
            12: [erc20],
            100001: [erc20],
            erc4626: [erc20, morpho],
            uniswap: [erc20],
        }
        groups = compute_update_groups(graph)
        assert len(groups) == 1
        assert erc20 in groups[0]
        assert morpho in groups[0]
        assert erc4626 in groups[0]

        reach = compute_update_reach(graph)
        assert reach[morpho] == {erc20, erc4626}
        assert reach[erc4626] == {erc20, morpho}
        assert reach[flash] == {erc20}
