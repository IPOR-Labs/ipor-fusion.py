"""Lido Lend readers against the Hoodi deployment (market 58 preview).

Opt-in: needs ``HOODI_PROVIDER_URL``; skipped otherwise. Reads are pinned to one
block. A provider that has pruned that block skips the test.
"""

from __future__ import annotations

import pytest
from _live import archive_context

from ipor_fusion.core.context import Web3Context
from ipor_fusion.readers.lido_lend import (
    LidoLendMarketController,
    LidoLendReader,
    lido_lend_core_address,
)
from ipor_fusion.types import ZERO_ADDRESS, ChainId, MorphoBlueMarketId

HOODI = ChainId(560048)
BLOCK = 3_955_700
CORE = lido_lend_core_address(HOODI)
# Hoodi WETH / wstETH market.
MARKET = MorphoBlueMarketId(
    "0x6a1c794db6232d046223fa809c14c59be90eedeb58ba10b9ee00d36e5b8f2f35"
)
CONTROLLER = "0x567032366c3Ccc84Af77647fffb3e7a00B8b3008"
WSTETH = "0x7E99eE3C66636DE415D2d7C880938F2f40f94De4"
# The settlement-queue root at BLOCK.
QUEUE_ROOT = "0xad79579eEceF31f4719426232D7b527B17b84f85"


@pytest.fixture(scope="module")
def ctx() -> Web3Context:
    return archive_context("HOODI_PROVIDER_URL", BLOCK, CORE)


def test_core_names_the_controller(ctx):
    reader = LidoLendReader(CORE)
    assert reader.market_controller(MARKET).call(ctx) == CONTROLLER
    assert reader.is_settlement_enabled(MARKET).call(ctx) is True
    assert reader.market_params(MARKET).call(ctx).collateral_token == WSTETH


def test_controller_views_decode(ctx):
    controller = LidoLendMarketController(CONTROLLER)
    assert controller.collateral_activation_config().call(ctx).min_deposit_amount > 0
    assert controller.settlement_config().call(ctx).slice_floor_bps > 0
    assert controller.supply_cap().call(ctx) > 0
    assert controller.is_paused().call(ctx) is False
    assert controller.settlement_borrowers_count().call(ctx) >= 1
    assert controller.settlement_ltv(QUEUE_ROOT).call(ctx).ltv >= 0
    quote = controller.settlement_for(QUEUE_ROOT, 0).call(ctx)
    assert quote.personal.borrow_shares >= 0
    assert quote.queue_root.borrow_shares >= 0
    assert controller.position_of(QUEUE_ROOT).call(ctx).debt_assets > 0


def test_next_settlement_borrower_is_the_queue_root(ctx):
    nxt = LidoLendMarketController(CONTROLLER).next_settlement_borrower(0).call(ctx)
    assert nxt.borrower == QUEUE_ROOT
    assert nxt.slice.borrow_shares > 0


@pytest.mark.parametrize("user", [CORE, QUEUE_ROOT])
def test_read_position_state_decodes(ctx, user):
    state = LidoLendReader(CORE).read_position_state(ctx, MARKET, user)
    assert state.controller == CONTROLLER
    assert state.controller != ZERO_ADDRESS
    assert state.settlement_enabled is True
    assert state.borrower is not None
    assert state.paused is False
    assert state.frozen is False
