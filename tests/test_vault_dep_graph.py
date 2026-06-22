"""Tests for dependency balance graph analysis."""

from ipor_fusion.cli.vault_dep_graph import (
    NO_BALANCE_FUSE_MARKETS,
    compute_update_groups,
    compute_update_reach,
    erc20_balance_tracks_non_underlying,
    find_markets_missing_erc20_dependency,
    find_orphan_fuse_markets,
)
from ipor_fusion.market_ids import IporFusionMarkets


class TestErc20BalanceTracksNonUnderlying:
    underlying = "0x" + "11" * 20
    other = "0x" + "22" * 20

    def test_empty_set_is_false(self):
        assert not erc20_balance_tracks_non_underlying(set(), self.underlying)

    def test_only_underlying_is_false(self):
        assert not erc20_balance_tracks_non_underlying(
            {self.underlying}, self.underlying
        )

    def test_non_underlying_present_is_true(self):
        assert erc20_balance_tracks_non_underlying(
            {self.underlying, self.other}, self.underlying
        )

    def test_case_insensitive(self):
        assert not erc20_balance_tracks_non_underlying(
            {self.underlying.upper()}, self.underlying.lower()
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


class TestFindOrphanFuseMarkets:
    def test_empty_inputs(self):
        assert not find_orphan_fuse_markets({}, set())

    def test_all_markets_covered(self):
        fuse_markets = {"0xfuse1": IporFusionMarkets.MORPHO}
        balance_markets = {IporFusionMarkets.MORPHO}
        assert not find_orphan_fuse_markets(fuse_markets, balance_markets)

    def test_morpho_action_fuse_without_balance_fuse(self):
        """The misconfiguration the CLI must catch: Morpho action fuse but no
        Morpho balance fuse, so positions opened via the fuse silently drop
        out of totalAssets."""
        fuse_markets = {
            "0xMorphoSupply": IporFusionMarkets.MORPHO,
            "0xMorphoBorrow": IporFusionMarkets.MORPHO,
        }
        balance_markets = {IporFusionMarkets.ERC20_VAULT_BALANCE}
        orphans = find_orphan_fuse_markets(fuse_markets, balance_markets)
        assert orphans == {
            IporFusionMarkets.MORPHO: ["0xMorphoSupply", "0xMorphoBorrow"]
        }

    def test_flash_loan_market_excluded(self):
        """Flash loans intentionally have no balance fuse — must not flag."""
        fuse_markets = {"0xFlashLoan": IporFusionMarkets.MORPHO_FLASH_LOAN}
        assert not find_orphan_fuse_markets(fuse_markets, set())

    def test_swap_markets_excluded(self):
        fuse_markets = {
            "0xUniV2": IporFusionMarkets.UNISWAP_SWAP_V2,
            "0xUniV3": IporFusionMarkets.UNISWAP_SWAP_V3,
            "0xUTS": IporFusionMarkets.UNIVERSAL_TOKEN_SWAPPER,
        }
        assert not find_orphan_fuse_markets(fuse_markets, set())

    def test_mixed_orphan_and_valid(self):
        fuse_markets = {
            "0xAaveSupply": IporFusionMarkets.AAVE_V3,
            "0xMorphoSupply": IporFusionMarkets.MORPHO,
            "0xFlashLoan": IporFusionMarkets.MORPHO_FLASH_LOAN,
        }
        balance_markets = {IporFusionMarkets.AAVE_V3}
        assert find_orphan_fuse_markets(fuse_markets, balance_markets) == {
            IporFusionMarkets.MORPHO: ["0xMorphoSupply"]
        }

    def test_unknown_market_id_flagged(self):
        """An unknown (newly added) market without balance fuse is still an
        orphan — fail loudly so the allowlist gets updated explicitly."""
        fuse_markets = {"0xWeird": 9999}
        assert find_orphan_fuse_markets(fuse_markets, set()) == {9999: ["0xWeird"]}

    def test_no_balance_fuse_markets_constant(self):
        """Sanity-check the allowlist contains the well-known flow-through
        markets. Adding new entries here is allowed; removing is a regression."""
        assert IporFusionMarkets.MORPHO_FLASH_LOAN in NO_BALANCE_FUSE_MARKETS
        assert IporFusionMarkets.UNIVERSAL_TOKEN_SWAPPER in NO_BALANCE_FUSE_MARKETS


class TestFindMarketsMissingErc20Dependency:
    """The misconfiguration mirrors what we found on a real Base vault:
    MORPHO_LIQUIDITY_IN_MARKETS had a balance fuse but no edge to
    ERC20_VAULT_BALANCE in the dep graph, so updateMarketsBalances(41)
    didn't refresh ERC20 cache."""

    erc20 = IporFusionMarkets.ERC20_VAULT_BALANCE
    morpho_lim = 41
    euler = IporFusionMarkets.EULER_V2

    def test_empty_inputs(self):
        assert not find_markets_missing_erc20_dependency(set(), {})

    def test_market_with_correct_dependency(self):
        result = find_markets_missing_erc20_dependency(
            {self.euler}, {self.euler: [self.erc20]}
        )
        assert not result

    def test_market_missing_erc20_dependency(self):
        """The actual user-reported case."""
        result = find_markets_missing_erc20_dependency(
            {self.morpho_lim}, dependency_graph={}
        )
        assert result == [self.morpho_lim]

    def test_market_with_other_deps_but_not_erc20(self):
        result = find_markets_missing_erc20_dependency(
            {self.morpho_lim}, {self.morpho_lim: [self.euler]}
        )
        assert result == [self.morpho_lim]

    def test_erc20_market_itself_excluded(self):
        result = find_markets_missing_erc20_dependency({self.erc20}, {})
        assert not result

    def test_zero_balance_sentinel_excluded(self):
        zero_balance = 2**256 - 1
        result = find_markets_missing_erc20_dependency({zero_balance}, {})
        assert not result

    def test_mixed_correct_and_missing(self):
        result = find_markets_missing_erc20_dependency(
            {self.euler, self.morpho_lim, self.erc20},
            {self.euler: [self.erc20]},
        )
        assert result == [self.morpho_lim]
