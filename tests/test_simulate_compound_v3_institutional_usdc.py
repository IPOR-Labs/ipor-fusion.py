"""Compound V3 Institutional USDC (market 57) on Ethereum via `eth_simulateV1`.

Mirrors `test/integrationTest/compoundV3Ethereum/CompoundV3InstitutionalUsdc*.t.sol`
in ipor-fusion, on the deployed fuses instead of freshly built ones: every test
clones a new vault from the factory, wires market 57 and drives the real ciUSDCv3
Comet. No vault holds the market at the pinned block, so the clone is what makes
the runs independent of anyone's position.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from _simulate import address_substrate, assert_all_success
from addresses import ETHEREUM_USDC, ETHEREUM_WETH
from constants import (
    ANVIL_WALLET,
    ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_BALANCE_FUSE,
    ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_BORROW_FUSE,
    ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_CLAIM_FUSE,
    ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_SUPPLY_FUSE,
    ETHEREUM_ERC20_BALANCE_FUSE,
)
from eth_abi.abi import encode
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    CompoundV3BorrowFuse,
    CompoundV3ClaimFuse,
    CompoundV3Reader,
    CompoundV3SupplyFuse,
    FusionFactory,
    IporFusionMarkets,
    PlasmaVault,
    RewardsManager,
    Roles,
    VaultSimulator,
    Web3Context,
    erc20_balance_slot,
)
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import MAX_UINT256, Amount, ChainId, MarketId

PINNED_BLOCK = 26_348_000  # after the last of the four fuses was deployed

FACTORY = Web3.to_checksum_address("0xcd05909C4A1F8E501e4ED554cEF4Ed5E48D9b852")
COMET = Web3.to_checksum_address("0x207158a267CBD2598BB3d611D8CBdEE2709F2F8C")
COMET_REWARDS = Web3.to_checksum_address("0x561e8E1E7EB56f558922C198A3C228545093f32d")
# Governor of both the Comet and its CometRewards.
GOVERNOR = Web3.to_checksum_address("0x4f05f11CA5De8946958d58aD765f16755f23b113")
COMP = Web3.to_checksum_address("0xc00e94Cb662C3520282E6f5717214004A7f26888")
OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")

MARKET = IporFusionMarkets.COMPOUND_V3_INSTITUTIONAL_USDC
ERC20_MARKET = IporFusionMarkets.ERC20_VAULT_BALANCE

# Comet rounds base balances through principal and index, by a few base units.
USDC_TOLERANCE = 10
# The same rounding and the WAD flooring of the balance fuse, valued in WETH.
WETH_TOLERANCE = 10**13

USDC_DEPOSIT = Amount(50_000 * 10**6)
WETH_COLLATERAL = Amount(40 * 10**18)
USDC_BORROW = Amount(20_000 * 10**6)
# Far beyond 40 WETH at any price; Comet checks the collateral before liquidity.
USDC_OVER_BORROW = Amount(10_000_000 * 10**6)
# Covers the interest of the hour the borrow test waits before repaying in full.
USDC_REPAY_TOP_UP = Amount(10 * 10**6)
ONE_HOUR_SECONDS = 3600
# The node's default cap is too low for the contracts a clone deploys.
CLONE_GAS = 30_000_000

# `CometStorage.userBasic` is the sixth slot of the storage layout, and its packed
# value holds `baseTrackingAccrued` above `principal` (104) and the index (64).
COMET_USER_BASIC_SLOT = 5
BASE_TRACKING_ACCRUED_BIT = 168
# In `baseAccrualScale` (1e6) units; CometRewards rescales them to COMP's 18.
TRACKING_ACCRUED = 5 * 10**6
EXPECTED_COMP_REWARD = 5 * 10**18

SUPPLY = CompoundV3SupplyFuse(ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_SUPPLY_FUSE)
BORROW = CompoundV3BorrowFuse(ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_BORROW_FUSE)
CLAIM = CompoundV3ClaimFuse(ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_CLAIM_FUSE)
READER = CompoundV3Reader(COMET)
USDC = ERC20(ETHEREUM_USDC)
WETH = ERC20(ETHEREUM_WETH)


class _Fuse(ContractWrapper):
    """The immutables a Compound V3 fuse is deployed with."""

    def market_id(self) -> Call[int]:
        return self._view("MARKET_ID()", output_types=["uint256"])

    def comet(self) -> Call[str]:
        return self._view("COMET()", output_types=["address"])

    def comet_rewards(self) -> Call[str]:
        return self._view("COMET_REWARDS()", output_types=["address"])


class _CometRewards(ContractWrapper):
    def set_reward_config(
        self, comet: ChecksumAddress, token: ChecksumAddress
    ) -> Call[None]:
        return self._write("setRewardConfig(address,address)", comet, token)


class _Comet(ContractWrapper):
    def base_tracking_accrued(self, account: ChecksumAddress) -> Call[int]:
        return self._view(
            "baseTrackingAccrued(address)", account, output_types=["uint64"]
        )


@dataclass(frozen=True)
class _Vault:
    sim: VaultSimulator
    vault: PlasmaVault
    rewards: RewardsManager


def _user_basic_key(account: ChecksumAddress) -> str:
    return Web3.keccak(
        encode(["address", "uint256"], [account, COMET_USER_BASIC_SLOT])
    ).to_0x_hex()


def _granted(*tokens: ChecksumAddress) -> list[bytes]:
    return [address_substrate(token) for token in tokens]


def _clone_vault(
    web3: Web3,
    underlying: ChecksumAddress,
    deposit: Amount,
    substrates: list[bytes],
) -> _Vault:
    """A new vault in the batch: market 57 wired, ``deposit`` of the underlying
    deposited by the alpha, who holds every role the scenarios need."""
    ctx = Web3Context(web3=web3, chain_id=ChainId(1), signer=OWNER).at_block(
        PINNED_BLOCK
    )
    factory = FusionFactory(FACTORY)
    args = {
        "asset_name": "Compound V3 Institutional (simulation)",
        "asset_symbol": "ciV3SIM",
        "underlying_token": underlying,
        "redemption_delay_seconds": 0,
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }
    preview = factory.clone(**args).call(ctx)
    vault = PlasmaVault(preview.plasma_vault)
    access_manager = AccessManager(preview.access_manager)
    rewards = RewardsManager(preview.rewards_manager)

    sim = VaultSimulator(
        web3=web3, vault=vault.address, alpha=ANVIL_WALLET, block=hex(PINNED_BLOCK)
    )
    sim.add_call(factory.clone(**args), from_=OWNER, label="clone", gas=CLONE_GAS)

    for role in (Roles.ATOMIST_ROLE, Roles.FUSE_MANAGER_ROLE):
        sim.add_call(access_manager.grant_role(role, OWNER, 0), from_=OWNER)
    for role in (Roles.ALPHA_ROLE, Roles.WHITELIST_ROLE, Roles.CLAIM_REWARDS_ROLE):
        sim.add_call(access_manager.grant_role(role, ANVIL_WALLET, 0), from_=OWNER)

    sim.add_call(vault.add_fuses([SUPPLY.address, BORROW.address]), from_=OWNER)
    sim.add_call(
        vault.add_balance_fuse(
            MARKET, ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_BALANCE_FUSE
        ),
        from_=OWNER,
    )
    sim.add_call(vault.grant_market_substrates(MARKET, substrates), from_=OWNER)
    sim.add_call(rewards.add_reward_fuses([CLAIM.address]), from_=OWNER)

    token = ERC20(underlying)
    slot = erc20_balance_slot(web3, underlying, block=PINNED_BLOCK)
    sim.with_erc20_balance(underlying, ANVIL_WALLET, deposit, slot=slot)
    sim.add_call(token.approve(vault.address, deposit), from_=ANVIL_WALLET)
    sim.add_call(vault.deposit(deposit, ANVIL_WALLET), from_=ANVIL_WALLET)
    return _Vault(sim, vault, rewards)


def _clone_borrowing_vault(web3: Web3) -> _Vault:
    """A WETH vault that values its borrowed USDC through the ERC20 market."""
    setup = _clone_vault(
        web3,
        ETHEREUM_WETH,
        WETH_COLLATERAL,
        _granted(ETHEREUM_WETH, ETHEREUM_USDC),
    )
    sim, vault = setup.sim, setup.vault
    sim.add_call(
        vault.add_balance_fuse(ERC20_MARKET, ETHEREUM_ERC20_BALANCE_FUSE), from_=OWNER
    )
    sim.add_call(
        vault.grant_market_substrates(ERC20_MARKET, _granted(ETHEREUM_USDC)),
        from_=OWNER,
    )
    sim.add_call(
        vault.update_dependency_balance_graphs([MARKET], [[ERC20_MARKET]]),
        from_=OWNER,
    )
    return setup


def _revert_selector(signature: str) -> str:
    return bytes(Web3.keccak(text=signature)[:4]).hex()


@pytest.mark.parametrize(
    ("fuse", "market"),
    [
        (ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_SUPPLY_FUSE, MARKET),
        (ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_BORROW_FUSE, MARKET),
        (ETHEREUM_COMPOUND_V3_INSTITUTIONAL_USDC_BALANCE_FUSE, MARKET),
    ],
)
def test_deployed_fuse_is_bound_to_market_57_and_its_comet(
    web3_eth, fuse: ChecksumAddress, market: MarketId
):
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(1)).at_block(PINNED_BLOCK)
    assert _Fuse(fuse).market_id().call(ctx) == market
    assert Web3.to_checksum_address(_Fuse(fuse).comet().call(ctx)) == COMET


def test_deployed_claim_fuse_is_bound_to_the_market_rewards(web3_eth):
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(1)).at_block(PINNED_BLOCK)
    bound = _Fuse(CLAIM.address).comet_rewards().call(ctx)
    assert Web3.to_checksum_address(bound) == COMET_REWARDS


def test_simulate_supply_and_withdraw_usdc_with_nav_at_each_step(web3_eth):
    setup = _clone_vault(web3_eth, ETHEREUM_USDC, USDC_DEPOSIT, _granted(ETHEREUM_USDC))
    sim, vault = setup.sim, setup.vault
    part = Amount(USDC_DEPOSIT * 40 // 100)

    sim.execute([SUPPLY.supply(asset=ETHEREUM_USDC, amount=USDC_DEPOSIT)])
    idle_after_supply = sim.observe(USDC.balance_of(vault.address))
    comet_after_supply = sim.observe(READER.balance_of(vault.address))
    market_after_supply = sim.observe(vault.total_assets_in_market(MARKET))
    total_after_supply = sim.observe(vault.total_assets())

    sim.execute([SUPPLY.withdraw(asset=ETHEREUM_USDC, amount=part)])
    idle_after_part = sim.observe(USDC.balance_of(vault.address))
    comet_after_part = sim.observe(READER.balance_of(vault.address))
    total_after_part = sim.observe(vault.total_assets())

    sim.execute([SUPPLY.withdraw(asset=ETHEREUM_USDC, amount=MAX_UINT256)])
    idle_after_exit = sim.observe(USDC.balance_of(vault.address))
    comet_after_exit = sim.observe(READER.balance_of(vault.address))
    market_after_exit = sim.observe(vault.total_assets_in_market(MARKET))
    total_after_exit = sim.observe(vault.total_assets())

    assert_all_success(sim.run())

    assert idle_after_supply.value == 0
    assert abs(comet_after_supply.value - USDC_DEPOSIT) <= USDC_TOLERANCE
    assert abs(market_after_supply.value - USDC_DEPOSIT) <= USDC_TOLERANCE
    assert abs(total_after_supply.value - USDC_DEPOSIT) <= USDC_TOLERANCE

    assert idle_after_part.value == part
    assert abs(comet_after_part.value - (USDC_DEPOSIT - part)) <= USDC_TOLERANCE
    assert abs(total_after_part.value - USDC_DEPOSIT) <= USDC_TOLERANCE

    assert comet_after_exit.value == 0
    assert market_after_exit.value == 0
    assert abs(idle_after_exit.value - USDC_DEPOSIT) <= USDC_TOLERANCE
    assert abs(total_after_exit.value - USDC_DEPOSIT) <= USDC_TOLERANCE


def test_simulate_borrow_repay_and_withdraw_collateral_with_nav_at_each_step(web3_eth):
    setup = _clone_borrowing_vault(web3_eth)
    sim, vault = setup.sim, setup.vault
    partial_repay = Amount(USDC_BORROW // 4)

    sim.execute([SUPPLY.supply(asset=ETHEREUM_WETH, amount=WETH_COLLATERAL)])
    collateral = sim.observe(READER.collateral_balance_of(vault.address, WETH.address))
    idle_weth = sim.observe(WETH.balance_of(vault.address))
    total_after_supply = sim.observe(vault.total_assets())

    sim.execute([BORROW.borrow(amount=USDC_BORROW)])
    idle_after_borrow = sim.observe(USDC.balance_of(vault.address))
    debt_after_borrow = sim.observe(READER.borrow_balance_of(vault.address))
    base_supply_after_borrow = sim.observe(READER.balance_of(vault.address))
    market_after_borrow = sim.observe(vault.total_assets_in_market(MARKET))
    erc20_after_borrow = sim.observe(vault.total_assets_in_market(ERC20_MARKET))
    total_after_borrow = sim.observe(vault.total_assets())

    sim.execute([BORROW.repay(amount=partial_repay)])
    idle_after_partial = sim.observe(USDC.balance_of(vault.address))
    debt_after_partial = sim.observe(READER.borrow_balance_of(vault.address))
    allowance_after_partial = sim.observe(USDC.allowance(vault.address, COMET))
    total_after_partial = sim.observe(vault.total_assets())

    # Interest grows the debt past what the vault borrowed, so a full repay needs
    # more than the idle USDC; the fuse resolves MAX_UINT256 to the accrued debt.
    sim.with_erc20_balance(
        USDC.address,
        ANVIL_WALLET,
        USDC_REPAY_TOP_UP,
        slot=erc20_balance_slot(web3_eth, USDC.address, block=PINNED_BLOCK),
    )
    sim.next_block(time_shift_seconds=ONE_HOUR_SECONDS)
    debt_after_interest = sim.observe(READER.borrow_balance_of(vault.address))
    sim.add_call(USDC.transfer(vault.address, USDC_REPAY_TOP_UP), from_=ANVIL_WALLET)
    sim.execute([BORROW.repay(amount=MAX_UINT256)])
    debt_after_full = sim.observe(READER.borrow_balance_of(vault.address))
    base_supply_after_full = sim.observe(READER.balance_of(vault.address))
    idle_after_full = sim.observe(USDC.balance_of(vault.address))

    sim.execute([SUPPLY.withdraw(asset=ETHEREUM_WETH, amount=WETH_COLLATERAL)])
    collateral_after_exit = sim.observe(
        READER.collateral_balance_of(vault.address, WETH.address)
    )
    idle_weth_after_exit = sim.observe(WETH.balance_of(vault.address))
    market_after_exit = sim.observe(vault.total_assets_in_market(MARKET))

    assert_all_success(sim.run())

    assert collateral.value == WETH_COLLATERAL
    assert idle_weth.value == 0
    assert abs(total_after_supply.value - WETH_COLLATERAL) <= WETH_TOLERANCE

    assert idle_after_borrow.value == USDC_BORROW
    assert debt_after_borrow.value >= USDC_BORROW
    assert debt_after_borrow.value - USDC_BORROW <= USDC_TOLERANCE
    assert base_supply_after_borrow.value == 0
    # Net of the debt, with the borrowed USDC valued by the ERC20 market through
    # the dependency graph; a balance fuse that ignored the debt inflates NAV.
    assert market_after_borrow.value < WETH_COLLATERAL
    assert erc20_after_borrow.value > 0
    assert abs(total_after_borrow.value - WETH_COLLATERAL) <= WETH_TOLERANCE

    assert idle_after_partial.value == USDC_BORROW - partial_repay
    remaining_debt = USDC_BORROW - partial_repay
    assert abs(debt_after_partial.value - remaining_debt) <= USDC_TOLERANCE
    assert allowance_after_partial.value == 0
    assert abs(total_after_partial.value - WETH_COLLATERAL) <= WETH_TOLERANCE

    assert debt_after_interest.value > debt_after_partial.value
    assert debt_after_full.value == 0
    # The repay stopped at the debt: no base supply opened with the rest.
    assert base_supply_after_full.value == 0
    expected_idle = (
        idle_after_partial.value + USDC_REPAY_TOP_UP - debt_after_interest.value
    )
    assert abs(idle_after_full.value - expected_idle) <= USDC_TOLERANCE

    assert collateral_after_exit.value == 0
    assert idle_weth_after_exit.value == WETH_COLLATERAL
    assert market_after_exit.value == 0


def test_simulate_borrow_above_the_collateral_factor_reverts(web3_eth):
    setup = _clone_borrowing_vault(web3_eth)
    sim = setup.sim
    sim.execute([SUPPLY.supply(asset=ETHEREUM_WETH, amount=WETH_COLLATERAL)])
    sim.execute([BORROW.borrow(amount=USDC_OVER_BORROW)])

    result = sim.run()

    assert result.success is False
    payload = bytes(result.failed_calls[0].return_data)
    assert payload[:4].hex() == _revert_selector("NotCollateralized()")


def test_simulate_supply_of_an_ungranted_asset_reverts(web3_eth):
    setup = _clone_vault(web3_eth, ETHEREUM_USDC, USDC_DEPOSIT, _granted(ETHEREUM_USDC))
    setup.sim.execute([SUPPLY.supply(asset=ETHEREUM_WETH, amount=Amount(1))])

    result = setup.sim.run()

    assert result.success is False
    payload = bytes(result.failed_calls[0].return_data)
    assert payload[:4].hex() == _revert_selector(
        "CompoundV3SupplyFuseUnsupportedAsset(string,address)"
    )


def test_simulate_borrow_without_the_base_token_substrate_reverts(web3_eth):
    setup = _clone_vault(
        web3_eth, ETHEREUM_WETH, WETH_COLLATERAL, _granted(ETHEREUM_WETH)
    )
    sim = setup.sim
    sim.execute([SUPPLY.supply(asset=ETHEREUM_WETH, amount=WETH_COLLATERAL)])
    sim.execute([BORROW.borrow(amount=USDC_BORROW)])

    result = sim.run()

    assert result.success is False
    payload = bytes(result.failed_calls[0].return_data)
    assert payload[:4].hex() == _revert_selector(
        "CompoundV3BorrowFuseUnsupportedAsset(string,address)"
    )


def test_simulate_claim_reverts_while_the_market_has_no_reward_token(web3_eth):
    setup = _clone_vault(web3_eth, ETHEREUM_USDC, USDC_DEPOSIT, _granted(ETHEREUM_USDC))
    setup.sim.execute_call(setup.rewards.claim_rewards([CLAIM.claim(comet=COMET)]))

    result = setup.sim.run()

    assert result.success is False
    payload = bytes(result.failed_calls[0].return_data)
    assert payload[:4].hex() == _revert_selector("NotSupported(address)")


def test_simulate_claim_pays_the_rewards_manager_once_rewards_are_configured(web3_eth):
    """The Comet accrues nothing at the pinned block (both tracking speeds are 0)
    and has no reward token, so the simulation stands in for both: the governor
    configures COMP and the vault's accrued tracking comes from a storage override.
    What it proves is the fuse's path, from the manager through the vault's context
    to `claimTo`, and that the reward lands on the manager."""
    setup = _clone_vault(web3_eth, ETHEREUM_USDC, USDC_DEPOSIT, _granted(ETHEREUM_USDC))
    sim, vault, rewards = setup.sim, setup.vault, setup.rewards
    comp = ERC20(COMP)

    sim.with_state_override(
        COMET,
        stateDiff={
            _user_basic_key(vault.address): "0x"
            + (TRACKING_ACCRUED << BASE_TRACKING_ACCRUED_BIT).to_bytes(32, "big").hex()
        },
    )
    sim.with_erc20_balance(
        COMP,
        COMET_REWARDS,
        EXPECTED_COMP_REWARD,
        slot=erc20_balance_slot(web3_eth, COMP, block=PINNED_BLOCK),
    )
    accrued = sim.observe(_Comet(COMET).base_tracking_accrued(vault.address))
    sim.add_call(
        _CometRewards(COMET_REWARDS).set_reward_config(COMET, COMP), from_=GOVERNOR
    )

    manager_before = sim.observe(comp.balance_of(rewards.address))
    sim.execute_call(rewards.claim_rewards([CLAIM.claim(comet=COMET)]))
    manager_after = sim.observe(comp.balance_of(rewards.address))
    vault_after = sim.observe(comp.balance_of(vault.address))

    assert_all_success(sim.run())

    assert accrued.value == TRACKING_ACCRUED
    assert manager_before.value == 0
    assert manager_after.value == EXPECTED_COMP_REWARD
    assert vault_after.value == 0
