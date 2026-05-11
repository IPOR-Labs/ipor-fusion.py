"""Reader integration tests against archive RPC — no anvil needed.

Mirrors `test_readers_integration.py`. Readers are pure read paths (`eth_call`
under the hood), so they don't need VaultSimulator at all — `Web3Context` with
`default_block = PINNED_BLOCK` against an archive node gives identical results
to anvil.reset_fork(...) at a fraction of the time and zero infrastructure.
"""

from __future__ import annotations

from web3 import Web3

from addresses import ETHEREUM_USDC, ARBITRUM_COMPOUND_V3_C_USDC
from ipor_fusion import Web3Context
from ipor_fusion.readers import (
    MorphoReader,
    AaveV3Reader,
    CompoundV3Reader,
    UniswapV3Reader,
    RamsesV2Reader,
)
from ipor_fusion.types import ChainId, MorphoBlueMarketId

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
MORPHO_USDC_MARKET_ID = MorphoBlueMarketId(
    "3a85e619751152991742810df6ec69ce473daef99e28a64ab2340d7b7ccfee49"
)
ETHEREUM_MORPHO_VAULT = Web3.to_checksum_address(
    "0x43Ee0243eA8CF02f7087d8B16C8D2007CC9c7cA2"
)
ETHEREUM_AAVE_VAULT = Web3.to_checksum_address(
    "0x1fdf5dc3F915Cb40E0AD5690DE51E3cB464d1BAD"
)

ZERO = Web3.to_checksum_address("0x" + "00" * 20)


def _ctx(web3: Web3, block: int) -> Web3Context:
    ctx = Web3Context(web3=web3, chain_id=ChainId(web3.eth.chain_id))
    ctx.default_block = block
    return ctx


# ── Ethereum ───────────────────────────────────────────────────────────


class TestMorphoReaderIntegration:
    BLOCK = 22066578

    def test_market_returns_nonzero_supply(self, web3_eth):
        reader = MorphoReader(_ctx(web3_eth, self.BLOCK), ETHEREUM_MORPHO_BLUE)
        market = reader.market(MORPHO_USDC_MARKET_ID)
        assert market.total_supply_assets > 0
        assert market.total_supply_shares > 0

    def test_market_params_returns_valid_addresses(self, web3_eth):
        reader = MorphoReader(_ctx(web3_eth, self.BLOCK), ETHEREUM_MORPHO_BLUE)
        params = reader.market_params(MORPHO_USDC_MARKET_ID)
        assert params.loan_token == ETHEREUM_USDC
        assert params.lltv > 0
        assert params.collateral_token != ZERO
        assert params.oracle != ZERO
        assert params.irm != ZERO

    def test_position_returns_valid_dataclass(self, web3_eth):
        reader = MorphoReader(_ctx(web3_eth, self.BLOCK), ETHEREUM_MORPHO_BLUE)
        position = reader.position(MORPHO_USDC_MARKET_ID, ETHEREUM_MORPHO_VAULT)
        assert position.supply_shares >= 0
        assert position.borrow_shares >= 0
        assert position.collateral >= 0


class TestAaveV3ReaderIntegration:
    BLOCK = 22616438

    def test_get_user_account_data(self, web3_eth):
        reader = AaveV3Reader(_ctx(web3_eth, self.BLOCK), ETHEREUM_AAVE_V3_POOL)
        data = reader.get_user_account_data(ETHEREUM_AAVE_VAULT)
        assert data.health_factor > 0
        assert data.total_collateral_base >= 0
        assert data.ltv >= 0


# ── Arbitrum ───────────────────────────────────────────────────────────


class TestCompoundV3ReaderIntegration:
    BLOCK = 250690377

    def test_balance_of_comet(self, web3_arb):
        reader = CompoundV3Reader(
            _ctx(web3_arb, self.BLOCK), ARBITRUM_COMPOUND_V3_C_USDC
        )
        balance = reader.balance_of(ARBITRUM_COMPOUND_V3_C_USDC).call()
        assert balance >= 0

    def test_borrow_balance_of(self, web3_arb):
        reader = CompoundV3Reader(
            _ctx(web3_arb, self.BLOCK), ARBITRUM_COMPOUND_V3_C_USDC
        )
        borrow = reader.borrow_balance_of(ARBITRUM_COMPOUND_V3_C_USDC).call()
        assert borrow >= 0


class TestUniswapV3ReaderIntegration:
    BLOCK = 254084008

    def test_positions_reads_existing_position(self, web3_arb):
        reader = UniswapV3Reader(
            _ctx(web3_arb, self.BLOCK), ARBITRUM_UNISWAP_V3_POSITION_MANAGER
        )
        position = reader.positions(1)
        assert position.token0 != ZERO
        assert position.token1 != ZERO
        assert position.fee > 0


class TestRamsesV2ReaderIntegration:
    BLOCK = 261946538

    def test_positions_reads_existing_position(self, web3_arb):
        reader = RamsesV2Reader(
            _ctx(web3_arb, self.BLOCK), ARBITRUM_RAMSES_V2_POSITION_MANAGER
        )
        position = reader.positions(1)
        assert position.token0 != ZERO
        assert position.token1 != ZERO
        assert position.fee > 0
