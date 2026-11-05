"""Compound V3 lending health on real vaults, through `fetch_vault_data`.

The Base vault holds a small live borrow on market 2 at the pinned block, so the
whole path runs: balance fuse to Comet, the Comet's reads, the health row. The
Ethereum vault is supply-only on markets 2 and 57 and stays free of warnings.
"""

from __future__ import annotations

from web3 import Web3

from ipor_fusion import (
    CompoundV3FuseReader,
    IporFusionMarkets,
    PlasmaVault,
    Web3Context,
    fetch_vault_data,
    parse_plasma_vault_address,
)
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import ChainId

BASE_BORROWER = parse_plasma_vault_address("0x4Fe083a56aeb8EF36188Fd6eBe24Bac9Ad708266")
BASE_BLOCK = 53_544_534
BASE_COMET = Web3.to_checksum_address("0xb125E6687d4313864e53df431d5425969c15Eb2F")

ETHEREUM_SUPPLIER = parse_plasma_vault_address(
    "0xDf0D13ADa59eAaED36C1348247e3D9EcC06A9355"
)
ETHEREUM_BLOCK = 26_348_285
COMPOUND_MARKETS = {
    IporFusionMarkets.COMPOUND_V3_USDC,
    IporFusionMarkets.COMPOUND_V3_INSTITUTIONAL_USDC,
}


class _Comet(ContractWrapper):
    def is_liquidatable(self, account: str) -> Call[bool]:
        return self._view("isLiquidatable(address)", account, output_types=["bool"])


def test_borrowing_vault_reports_comet_health(web3_base):
    ctx = Web3Context(web3=web3_base, chain_id=ChainId(8453))

    data = fetch_vault_data(ctx, BASE_BORROWER, block=BASE_BLOCK)

    assert data.lending_health is not None
    [row] = [
        m
        for m in data.lending_health.markets
        if m.market_id == IporFusionMarkets.COMPOUND_V3_USDC
    ]
    assert row.protocol == "compound_v3"
    assert row.total_debt_usd == 0.01
    assert row.total_collateral_usd is not None
    assert row.total_collateral_usd > 0.01
    assert row.health_factor is not None
    assert row.health_factor > 1
    assert row.max_ltv == 0.85  # only cbBTC is held
    pinned = ctx.at_block(BASE_BLOCK)
    assert _Comet(BASE_COMET).is_liquidatable(BASE_BORROWER).call(pinned) is False


def test_balance_fuse_resolves_the_institutional_comet(web3_eth):
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(1)).at_block(ETHEREUM_BLOCK)
    fuses = {
        bf.market_id: bf.fuse
        for bf in PlasmaVault(ETHEREUM_SUPPLIER).read_balance_fuses(ctx)
    }

    comet = CompoundV3FuseReader(
        fuses[IporFusionMarkets.COMPOUND_V3_INSTITUTIONAL_USDC]
    ).comet()

    assert comet.call(ctx) == Web3.to_checksum_address(
        "0x207158a267CBD2598BB3d611D8CBdEE2709F2F8C"
    )


def test_supply_only_vault_gets_no_row_and_no_warning(web3_eth):
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(1))

    data = fetch_vault_data(ctx, ETHEREUM_SUPPLIER, block=ETHEREUM_BLOCK)

    assert data.lending_health is not None
    assert not COMPOUND_MARKETS & {m.market_id for m in data.lending_health.markets}
    assert not COMPOUND_MARKETS & {m.market_id for m in data.lending_health.unreadable}
