"""`ipor_fusion.addresses`: the shipped ipor-abi snapshot and its resolvers."""

from __future__ import annotations

import json
from importlib import resources
from unittest.mock import MagicMock

import pytest
from web3 import Web3

from ipor_fusion import AddressNotFoundError, UnsupportedChainError, addresses
from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.core import FusionFactory
from ipor_fusion.market_ids import IporFusionMarkets

BASE = 8453
ARBITRUM = 42161
ETHEREUM = 1


def _snapshot() -> dict:
    path = resources.files("ipor_fusion.data").joinpath("ipor_abi_addresses.json")
    return json.loads(path.read_text(encoding="utf-8"))


class TestSnapshot:
    def test_source_names_the_registry_commit(self):
        src = addresses.source()
        assert src.repository == "https://github.com/IPOR-Labs/ipor-abi"
        assert len(src.commit) == 40 and int(src.commit, 16)
        assert src.synced_at[:2] == "20"

    def test_every_chain_has_the_factory_pair_and_is_named(self):
        for chain_id in addresses.chain_ids():
            names = addresses.names(chain_id)
            assert addresses.FACTORY_PROXY_NAME in names, chain_id
            assert addresses.FACTORY_IMPL_NAME in names, chain_id
            assert CHAIN_NAMES[chain_id] == addresses.chain_name(chain_id)

    def test_addresses_are_checksummed(self):
        # Uniqueness is not asserted: the registry publishes aliases (one
        # contract under two names) on some chains.
        for chain in _snapshot()["chains"].values():
            assert all(Web3.is_checksum_address(v) for v in chain["contracts"].values())

    def test_report_factory_table(self):
        # The two factory proxies an agent is most likely to need (report FX-01).
        assert addresses.factory_proxy(BASE) == Web3.to_checksum_address(
            "0x1455717668fA96534f675856347A973fA907e922"
        )
        assert addresses.factory_proxy(ARBITRUM) == Web3.to_checksum_address(
            "0x134fCAce7a2C7Ef3dF2479B62f03ddabAEa922d5"
        )
        assert {
            ETHEREUM,
            BASE,
            ARBITRUM,
            130,
            239,
            9745,
            43114,
            143,
            999,
            747474,
        } <= set(addresses.chain_ids())


class TestResolve:
    def test_proxy_is_the_deploy_entry_point(self):
        hit = addresses.resolve(BASE, addresses.FACTORY_PROXY_NAME)
        assert hit.deploy_entry_point is True
        assert hit.role == "deploy-entry-point"
        assert hit.note is None
        assert hit.address == addresses.factory_proxy(BASE)

    def test_impl_is_flagged_with_a_note(self):
        hit = addresses.resolve(BASE, addresses.FACTORY_IMPL_NAME)
        assert hit.deploy_entry_point is False
        assert hit.role is None
        assert hit.note is not None
        assert "DaoFeePackagesArrayEmpty" in hit.note
        assert "IporFusionFactoryProxy" in hit.note

    def test_unknown_name_points_at_names(self):
        with pytest.raises(AddressNotFoundError, match="names\\(8453\\)"):
            addresses.resolve(BASE, "NoSuchContract")

    def test_unknown_chain(self):
        with pytest.raises(UnsupportedChainError, match="known chains"):
            addresses.resolve(10, addresses.FACTORY_PROXY_NAME)


class TestBalanceFuse:
    @pytest.mark.parametrize("chain_id", [BASE, ARBITRUM])
    def test_aave_v3_resolves_on_chains_with_different_registry_names(self, chain_id):
        # No literal: the answer must be one of the registry's own Aave V3
        # balance-fuse entries for that chain, whichever name the chain uses.
        contracts = _snapshot()["chains"][str(chain_id)]["contracts"]
        candidates = {
            contracts[name]
            for name in addresses.BALANCE_FUSE_NAMES[IporFusionMarkets.AAVE_V3]
            if name in contracts
        }
        assert candidates, "registry lost its Aave V3 balance fuse"
        assert addresses.balance_fuse(chain_id, "AAVE_V3") in candidates
        assert addresses.balance_fuse(chain_id, IporFusionMarkets.AAVE_V3) in candidates

    def test_prefers_the_current_naming_when_both_exist(self):
        contracts = _snapshot()["chains"][str(BASE)]["contracts"]
        assert "BalanceFuseAaveV3" in contracts
        assert addresses.balance_fuse(BASE, "AAVE_V3") == contracts["BalanceFuseAaveV3"]

    def test_erc4626_market_ids_map_to_numbered_entries(self):
        contracts = _snapshot()["chains"][str(ETHEREUM)]["contracts"]
        assert (
            addresses.balance_fuse(ETHEREUM, IporFusionMarkets.ERC4626_0001)
            == contracts["BalanceFuseErc4626Market1"]
        )

    def test_unknown_market_name(self):
        with pytest.raises(AddressNotFoundError, match="IporFusionMarkets"):
            addresses.balance_fuse(BASE, "NOT_A_MARKET")

    def test_market_without_a_registry_name(self):
        with pytest.raises(AddressNotFoundError, match="BALANCE_FUSE_NAMES"):
            addresses.balance_fuse(BASE, 999)


class TestLookup:
    def test_name_query_is_case_insensitive_and_scoped(self):
        hits = addresses.lookup("iporfusionfactory", chain_id=BASE)
        assert [h.name for h in hits] == [
            addresses.FACTORY_IMPL_NAME,
            addresses.FACTORY_PROXY_NAME,
        ]
        assert all(h.chain_id == BASE for h in hits)
        assert [h.role for h in hits] == [None, "deploy-entry-point"]

    def test_factory_query_tags_the_proxy_on_every_chain(self):
        hits = [h for h in addresses.lookup("factory") if h.deploy_entry_point]
        assert {h.chain_id for h in hits} == set(addresses.chain_ids())

    def test_address_query_ignores_chain_id(self):
        proxy = addresses.factory_proxy(BASE)
        hits = addresses.lookup(proxy.lower(), chain_id=ARBITRUM)
        assert [(h.chain_id, h.name) for h in hits] == [
            (BASE, addresses.FACTORY_PROXY_NAME)
        ]

    def test_no_match_is_empty(self):
        assert addresses.lookup("definitely-not-a-contract") == []


class TestFusionFactoryDefaults:
    def _ctx(self, chain_id: int) -> MagicMock:
        ctx = MagicMock()
        ctx.chain_id = chain_id
        return ctx

    def test_defaults_to_the_chain_proxy(self):
        assert FusionFactory(self._ctx(BASE)).address == addresses.factory_proxy(BASE)
        assert FusionFactory(self._ctx(ARBITRUM)).address == addresses.factory_proxy(
            ARBITRUM
        )

    def test_explicit_address_is_kept(self):
        custom = Web3.to_checksum_address("0x" + "ab" * 20)
        assert FusionFactory(self._ctx(BASE), custom).address == custom

    def test_impl_address_is_rejected_before_sending(self):
        impl = addresses.factory_impl(BASE)
        with pytest.raises(ValueError, match="DaoFeePackagesArrayEmpty"):
            FusionFactory(self._ctx(BASE), impl)

    def test_unknown_chain_without_address(self):
        with pytest.raises(UnsupportedChainError):
            FusionFactory(self._ctx(10))
