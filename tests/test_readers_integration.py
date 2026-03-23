"""Integration tests for protocol readers — forked mainnet via Anvil."""

import os

import pytest
from eth_typing import BlockNumber
from web3 import Web3

from addresses import ETHEREUM_USDC, ARBITRUM_COMPOUND_V3_C_USDC
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion.readers import (
    MorphoReader,
    AaveV3Reader,
    CompoundV3Reader,
    UniswapV3Reader,
    RamsesV2Reader,
)
from ipor_fusion.types import MorphoBlueMarketId

# Well-known protocol contract addresses (not fuse addresses)
ETHEREUM_MORPHO_BLUE = Web3.to_checksum_address(
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
)
ETHEREUM_AAVE_V3_POOL = Web3.to_checksum_address(
    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
)
ARBITRUM_UNISWAP_V3_POSITION_MANAGER = Web3.to_checksum_address(
    "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
)
ARBITRUM_RAMSES_V2_POSITION_MANAGER = Web3.to_checksum_address(
    "0xAA277CB7914b7e5514946Da92cb9De332Ce610EF"
)

# Known Morpho Blue market (USDC/wstETH) used in test_morpho_blue.py
MORPHO_USDC_MARKET_ID = MorphoBlueMarketId(
    "3a85e619751152991742810df6ec69ce473daef99e28a64ab2340d7b7ccfee49"
)

# Vault with existing Morpho position at block 22066578
ETHEREUM_MORPHO_VAULT = Web3.to_checksum_address(
    "0x43Ee0243eA8CF02f7087d8B16C8D2007CC9c7cA2"
)

# Vault with Aave V3 + borrow position at block 22616438
ETHEREUM_AAVE_VAULT = Web3.to_checksum_address(
    "0x1fdf5dc3F915Cb40E0AD5690DE51E3cB464d1BAD"
)


# ── Ethereum tests ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ethereum_anvil():
    if not (fork_url := os.environ.get("ETHEREUM_PROVIDER_URL")):
        pytest.skip("ETHEREUM_PROVIDER_URL not set")
    with AnvilTestContainerStarter(fork_url) as anvil:
        yield anvil


class TestMorphoReaderIntegration:
    def test_market_returns_nonzero_supply(self, ethereum_anvil):
        ethereum_anvil.reset_fork(BlockNumber(22066578))
        ctx = ForkedWeb3Context.from_url(ethereum_anvil.get_anvil_http_url())
        reader = MorphoReader(ctx, ETHEREUM_MORPHO_BLUE)

        market = reader.market(MORPHO_USDC_MARKET_ID)

        assert market.total_supply_assets > 0
        assert market.total_supply_shares > 0

    def test_market_params_returns_valid_addresses(self, ethereum_anvil):
        ethereum_anvil.reset_fork(BlockNumber(22066578))
        ctx = ForkedWeb3Context.from_url(ethereum_anvil.get_anvil_http_url())
        reader = MorphoReader(ctx, ETHEREUM_MORPHO_BLUE)

        params = reader.market_params(MORPHO_USDC_MARKET_ID)

        assert params.loan_token == ETHEREUM_USDC
        assert params.lltv > 0
        zero = Web3.to_checksum_address("0x" + "00" * 20)
        assert params.collateral_token != zero
        assert params.oracle != zero
        assert params.irm != zero

    def test_position_returns_valid_dataclass(self, ethereum_anvil):
        ethereum_anvil.reset_fork(BlockNumber(22066578))
        ctx = ForkedWeb3Context.from_url(ethereum_anvil.get_anvil_http_url())
        reader = MorphoReader(ctx, ETHEREUM_MORPHO_BLUE)

        position = reader.position(MORPHO_USDC_MARKET_ID, ETHEREUM_MORPHO_VAULT)

        assert position.supply_shares >= 0
        assert position.borrow_shares >= 0
        assert position.collateral >= 0


class TestAaveV3ReaderIntegration:
    def test_get_user_account_data(self, ethereum_anvil):
        ethereum_anvil.reset_fork(BlockNumber(22616438))
        ctx = ForkedWeb3Context.from_url(ethereum_anvil.get_anvil_http_url())
        reader = AaveV3Reader(ctx, ETHEREUM_AAVE_V3_POOL)

        data = reader.get_user_account_data(ETHEREUM_AAVE_VAULT)

        # Vault may not have collateral at this block, but call should succeed
        # and health_factor should be max uint256 (no debt) or > 0 (has debt)
        assert data.health_factor > 0
        assert data.total_collateral_base >= 0
        assert data.ltv >= 0


# ── Arbitrum tests ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def arbitrum_anvil():
    if not (fork_url := os.environ.get("ARBITRUM_PROVIDER_URL")):
        pytest.skip("ARBITRUM_PROVIDER_URL not set")
    with AnvilTestContainerStarter(fork_url) as anvil:
        yield anvil


class TestCompoundV3ReaderIntegration:
    def test_balance_of_comet(self, arbitrum_anvil):
        arbitrum_anvil.reset_fork(BlockNumber(250690377))
        ctx = ForkedWeb3Context.from_url(arbitrum_anvil.get_anvil_http_url())
        reader = CompoundV3Reader(ctx, ARBITRUM_COMPOUND_V3_C_USDC)

        # Comet contract itself should have 0 balance (it's the protocol, not a user)
        # but calling it shouldn't revert
        balance = reader.balance_of(ARBITRUM_COMPOUND_V3_C_USDC)
        assert balance >= 0

    def test_borrow_balance_of(self, arbitrum_anvil):
        arbitrum_anvil.reset_fork(BlockNumber(250690377))
        ctx = ForkedWeb3Context.from_url(arbitrum_anvil.get_anvil_http_url())
        reader = CompoundV3Reader(ctx, ARBITRUM_COMPOUND_V3_C_USDC)

        borrow = reader.borrow_balance_of(ARBITRUM_COMPOUND_V3_C_USDC)
        assert borrow >= 0


class TestUniswapV3ReaderIntegration:
    def test_positions_reads_existing_position(self, arbitrum_anvil):
        arbitrum_anvil.reset_fork(BlockNumber(254084008))
        ctx = ForkedWeb3Context.from_url(arbitrum_anvil.get_anvil_http_url())
        reader = UniswapV3Reader(ctx, ARBITRUM_UNISWAP_V3_POSITION_MANAGER)

        # Token ID 1 always exists on Uniswap V3 deployments
        position = reader.positions(1)

        zero = Web3.to_checksum_address("0x" + "00" * 20)
        assert position.token0 != zero
        assert position.token1 != zero
        assert position.fee > 0


class TestRamsesV2ReaderIntegration:
    def test_positions_reads_existing_position(self, arbitrum_anvil):
        arbitrum_anvil.reset_fork(BlockNumber(261946538))
        ctx = ForkedWeb3Context.from_url(arbitrum_anvil.get_anvil_http_url())
        reader = RamsesV2Reader(ctx, ARBITRUM_RAMSES_V2_POSITION_MANAGER)

        # Token ID 1 should exist on Ramses V2
        position = reader.positions(1)

        zero = Web3.to_checksum_address("0x" + "00" * 20)
        assert position.token0 != zero
        assert position.token1 != zero
        assert position.fee > 0
