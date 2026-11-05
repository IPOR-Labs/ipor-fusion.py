"""Borrow USDC against WETH on Compound V3 Institutional USDC from a new Ethereum vault.

Continues from ``simple_aave_v3_supply_base.py``, which explains the vault
bootstrap at more length. This example shows the credit lifecycle on one Comet
(ciUSDCv3, market 57), end to end:

  1. preview a vault deployment and clone it, underlying WETH;
  2. bootstrap the vault's roles in the correct order;
  3. pair the supply and borrow fuses with the market's balance fuse;
  4. grant WETH as collateral and USDC as the borrow gate;
  5. value the borrowed USDC through the ERC20 market and a dependency edge;
  6. deposit WETH and supply it as collateral;
  7. borrow USDC, confirming NAV holds because the balance fuse nets the debt;
  8. repay part of the debt, let an hour of interest accrue, repay it in full;
  9. withdraw the collateral and confirm the position closed.

Everything runs inside one ``eth_simulateV1`` batch against a pinned Ethereum
block -- nothing is signed or broadcast. To put a flow on-chain, hand the
``.calldata`` each builder produces to your own signer, each step signed by the
role authorized for it.

Reading the health of a real position does not fit in a batch, since the vault
here exists only inside the simulation. On a deployed vault use
``fetch_vault_data(ctx, vault).lending_health`` or ``fusion vault validate``.

Rewards are out of scope: the market's claim fuse (``CompoundV3ClaimFuse``) goes
through ``RewardsManager.claim_rewards``, and this Comet has no reward token at
the pinned block, so a claim reverts ``NotSupported(address)``.

Run it (the shell snippets assume a POSIX shell -- bash or zsh):

    export ETHEREUM_PROVIDER_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"
    uv run python examples/advanced_compound_v3_institutional_usdc_ethereum.py

The provider must be an archive node that implements ``eth_simulateV1`` (Alchemy
and other geth/reth-based providers do).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TypedDict

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    CompoundV3Reader,
    FuseAction,
    FusionFactory,
    IporFusionMarkets,
    PlasmaVault,
    PriceOracleMiddlewareManager,
    Roles,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    erc20_balance_slot,
    is_simulate_v1_supported,
    parse_checksum_address,
    parse_fuse_address,
)
from ipor_fusion.fuses import CompoundV3BorrowFuse, CompoundV3SupplyFuse
from ipor_fusion.types import MAX_UINT256, Amount, ChainId, Period

log = logging.getLogger("advanced_compound_v3_institutional_usdc_ethereum")

# ── Live IPOR / Ethereum infrastructure (do NOT change) ──────────────────────
# Every address below is a live, canonical deployment. Provenance for all IPOR
# Fusion addresses: https://github.com/IPOR-Labs/ipor-abi
#   mainnet/mainnet-ethereum-fusion/addresses.json

ETHEREUM_CHAIN_ID = ChainId(1)

# IporFusionFactoryProxy on Ethereum.
ETHEREUM_FUSION_FACTORY = parse_checksum_address(
    "0xcd05909C4A1F8E501e4ED554cEF4Ed5E48D9b852"
)

# Canonical WETH and Circle-issued USDC on Ethereum (18 and 6 decimals).
ETHEREUM_WETH = parse_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
ETHEREUM_USDC = parse_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")

# The ciUSDCv3 Comet (registry name CompoundV3CometInstitutionalUsdc), base token
# USDC. It is a separate Comet from cUSDCv3, so the fuses below are their own
# deployments, bound to this Comet and market 57 by their immutables.
COMET = parse_checksum_address("0x207158a267CBD2598BB3d611D8CBdEE2709F2F8C")

COMPOUND_MARKET = IporFusionMarkets.COMPOUND_V3_INSTITUTIONAL_USDC
ERC20_MARKET = IporFusionMarkets.ERC20_VAULT_BALANCE

# Registry name SupplyFuseCompoundV3InstitutionalUsdc: supplies and withdraws
# collateral and base token.
SUPPLY_FUSE = parse_fuse_address("0xe7CaDa548534973b9d6A4124bBC476b586d1b3e4")
# Registry name BorrowFuseCompoundV3InstitutionalUsdc: borrows and repays the base
# token. A Comet lends only its base token, so neither action names an asset.
BORROW_FUSE = parse_fuse_address("0xff1762A6579787c7eab2E4f55A502F7c36714eF6")
# Registry name BalanceFuseCompoundV3WithPriceOracleMiddlewareInstitutionalUsdc:
# values the position as collateral minus debt, at the vault's oracle prices.
BALANCE_FUSE = parse_fuse_address("0x5dF0CB1cE5159De1efb8038B5185E4612fAd9168")
# Registry name BalanceFuseErc20: values the borrowed USDC sitting idle on the vault.
ERC20_BALANCE_FUSE = parse_fuse_address("0x6cEBf3e3392D0860Ed174402884b941DCBB30654")

# ── Simulation parameters (safe to adjust) ───────────────────────────────────
#
# OWNER, ALPHA, DEPOSITOR and USDC_SOURCE are impersonated simulation actors: the
# simulator runs with validation disabled, so it can act as any address without
# its key. They are NOT accounts you control -- there is nothing to replace and
# nothing to broadcast here.
#
# OWNER, ALPHA and DEPOSITOR are kept distinct on purpose, because they are three
# distinct roles that must NOT share an address in production:
#   OWNER    -- governance/atomist; can reconfigure the whole vault. A cold key
#               or multisig.
#   ALPHA    -- the online operator; drives execute(), least-privilege, it cannot
#               reconfigure the vault. Collapsing it into OWNER means a compromised
#               alpha key becomes full governance control.
#   DEPOSITOR-- an end user; only ever holds and deposits funds.

OWNER = parse_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
# Anvil account #0, a well-known test address.
ALPHA = parse_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
# Anvil account #1, the natural sibling to the alpha's Anvil account #0.
DEPOSITOR = parse_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
# Anvil account #2. Holds the USDC that covers the interest; see run_simulation.
USDC_SOURCE = parse_checksum_address("0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")

# Pinned Ethereum block, after the fuses above were deployed. Pinning makes the
# simulation deterministic and immune to mainnet state drift. It requires an
# archive node that can serve state at this height -- if your provider cannot (an
# error mentioning missing trie/state, or a pruned block), BUMP THIS to a recent
# Ethereum block your node can reach.
PINNED_BLOCK = 26_348_000

WETH_COLLATERAL = Amount(40 * 10**18)  # 40 WETH
USDC_BORROW = Amount(20_000 * 10**6)  # 20,000 USDC
USDC_PARTIAL_REPAY = Amount(5_000 * 10**6)  # 5,000 USDC
USDC_INTEREST_COVER = Amount(10 * 10**6)  # 10 USDC, far above an hour of interest
ONE_HOUR_SECONDS = 3600
# A clone deploys the whole vault stack, more than a node's default gas cap for a
# simulated call allows.
CLONE_GAS = 30_000_000
# Comet rounds a base balance through its principal and index, by a few base units.
USDC_DUST_TOLERANCE = Amount(10)
# The same rounding and the WAD flooring of the balance fuse, valued in WETH.
NAV_DUST_TOLERANCE = Amount(10**13)  # 0.00001 WETH


# ── Inlined helpers (kept local so this file is self-contained) ──────────────


def _address_substrate(addr: str) -> bytes:
    """Pad an EVM address into a 32-byte substrate value (left-zero-padded)."""
    return bytes.fromhex(addr.removeprefix("0x").lower().rjust(64, "0"))


def _check(condition: bool, message: str) -> None:
    """Fail loud unconditionally -- unlike ``assert``, survives ``python -O``."""
    if not condition:
        raise AssertionError(message)


def _assert_all_success(result: SimulationResult) -> None:
    """Raise with a readable summary if any simulated call reverted."""
    if result.all_success:
        return
    failed = [(c.label, c.error) for c in result.failed_calls]
    raise AssertionError(f"simulation calls failed: {failed}")


def _connected_web3() -> Web3:
    """Build a Web3 client from ETHEREUM_PROVIDER_URL, or exit with a clear message."""
    url = os.environ.get("ETHEREUM_PROVIDER_URL")
    if not url:
        sys.exit("ETHEREUM_PROVIDER_URL is not set -- export your RPC URL first.")
    web3 = Web3(Web3.HTTPProvider(url))
    if not web3.is_connected():
        sys.exit("cannot reach the RPC at ETHEREUM_PROVIDER_URL -- check the endpoint.")
    if not is_simulate_v1_supported(web3):
        sys.exit(
            "the provider does not implement eth_simulateV1 -- use an archive node."
        )
    return web3


# ── Pure builders (no chain access -- safe to import and unit-test offline) ───


class CloneKwargs(TypedDict):
    """The keyword arguments of ``FusionFactory.clone``."""

    asset_name: str
    asset_symbol: str
    underlying_token: ChecksumAddress
    redemption_delay_seconds: Period
    owner: ChecksumAddress
    dao_fee_package_index: int


def clone_args() -> CloneKwargs:
    """Arguments for FusionFactory.clone(...).

    The preview and the in-batch create MUST use identical args: same args plus
    the same factory state yields the same CREATE addresses in one simulation.
    """
    return {
        "asset_name": "IPOR ciUSDCv3 Borrow Vault (example)",
        "asset_symbol": "ipCIBex",
        "underlying_token": ETHEREUM_WETH,
        "redemption_delay_seconds": Period(0),
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def compound_market_substrates() -> list[bytes]:
    """WETH is the collateral the supply and balance fuses accept. USDC is the gate
    the borrow fuse checks on both borrow and repay: without it, a borrow reverts
    ``CompoundV3BorrowFuseUnsupportedAsset`` even though the fuse's target is fixed."""
    return [_address_substrate(ETHEREUM_WETH), _address_substrate(ETHEREUM_USDC)]


def erc20_market_substrates() -> list[bytes]:
    """The idle token the ERC20 market values: the borrowed USDC."""
    return [_address_substrate(ETHEREUM_USDC)]


def build_supply_collateral_action(amount: Amount) -> FuseAction:
    return CompoundV3SupplyFuse(SUPPLY_FUSE).supply(asset=ETHEREUM_WETH, amount=amount)


def build_withdraw_collateral_action(amount: Amount) -> FuseAction:
    return CompoundV3SupplyFuse(SUPPLY_FUSE).withdraw(
        asset=ETHEREUM_WETH, amount=amount
    )


def build_borrow_action(amount: Amount) -> FuseAction:
    return CompoundV3BorrowFuse(BORROW_FUSE).borrow(amount=amount)


def build_repay_action(amount: Amount) -> FuseAction:
    """A repay is capped at the debt: ``MAX_UINT256`` clears it, interest included,
    without opening a base supply position with the rest of the idle USDC. The
    vault must hold the USDC, or the token transfer reverts."""
    return CompoundV3BorrowFuse(BORROW_FUSE).repay(amount=amount)


# ── Simulation (the whole build + strategy, no broadcast) ────────────────────


def run_simulation(web3: Web3) -> SimulationResult:
    """Build the vault and run the credit lifecycle in one eth_simulateV1 batch."""
    ctx = Web3Context(web3=web3, chain_id=ETHEREUM_CHAIN_ID, signer=OWNER).at_block(
        PINNED_BLOCK
    )
    factory = FusionFactory(ETHEREUM_FUSION_FACTORY)

    # SIMULATION ONLY: the pinned replay has no other clone between this
    # preview and the simulated one, so the predicted stack is the created one.
    # Live, another clone in between moves it: send the clone first, take the
    # stack from factory.decode_clone_receipt(receipt) and build the
    # configuration and operation calldata from those addresses.
    preview = factory.clone(**clone_args()).call(ctx)
    log.info("predicted vault=%s", preview.plasma_vault)

    vault_address = preview.plasma_vault
    plasma_vault = PlasmaVault(vault_address)
    access_manager = AccessManager(preview.access_manager)
    price_manager = PriceOracleMiddlewareManager(preview.price_manager)
    comet = CompoundV3Reader(COMET)
    weth = ERC20(ETHEREUM_WETH)
    usdc = ERC20(ETHEREUM_USDC)

    # Each simulator primitive stands in for a production action, read as
    # "simulation -> production":
    #   - sim.add_call(call=X, from_=OWNER) -> X.send(ctx) by the owner/atomist
    #     (or X.calldata to a multisig / timelock).
    #   - sim.execute([action]) -> plasma_vault.execute([action]).send(ctx) by the alpha.
    #   - read = sim.observe(X), then read.value -> X.call(ctx), a plain read.
    # SIMULATION ONLY (per-call senders): `from_` lets one process act as every
    # actor, because in simulation they are addresses with no keys. In production
    # they are separate signers on separate systems.
    sim = VaultSimulator(
        web3=web3, vault=vault_address, alpha=ALPHA, block=PINNED_BLOCK
    )
    plan: list[str] = []

    # SIMULATION ONLY (storage overrides): these balances stand in for tokens the
    # holders already have. Production has no equivalent -- a real depositor holds
    # their WETH.
    sim.with_erc20_balance(
        ETHEREUM_WETH,
        DEPOSITOR,
        WETH_COLLATERAL,
        slot=erc20_balance_slot(web3, ETHEREUM_WETH, block=PINNED_BLOCK),
    )
    sim.with_erc20_balance(
        ETHEREUM_USDC,
        USDC_SOURCE,
        USDC_INTEREST_COVER,
        slot=erc20_balance_slot(web3, ETHEREUM_USDC, block=PINNED_BLOCK),
    )

    # The clone MUST be the first queued call -- any earlier call that bumps the
    # factory index would change the addresses previewed above.
    sim.add_call(
        call=factory.clone(**clone_args()), from_=OWNER, label="clone", gas=CLONE_GAS
    )
    plan.append("deploy the vault stack (clone)")

    # Role bootstrap order: a fresh clone grants OWNER only OWNER_ROLE. The admin
    # chain is OWNER -> ATOMIST -> {ALPHA, FUSE_MANAGER, WHITELIST,
    # UPDATE_MARKETS_BALANCES}, so OWNER must self-grant ATOMIST before it can
    # grant the rest. Each grant uses Period(0) (no execution delay) to keep the
    # simulation legible; production governance often puts a nonzero delay -- a
    # timelock -- on the sensitive roles. ALPHA must stay a separate address from
    # OWNER in production: a compromised alpha key must not equal governance.
    for role, holder in (
        (Roles.ATOMIST_ROLE, OWNER),
        (Roles.FUSE_MANAGER_ROLE, OWNER),
        (Roles.ALPHA_ROLE, ALPHA),
        (Roles.WHITELIST_ROLE, DEPOSITOR),
    ):
        sim.add_call(
            call=access_manager.grant_role(role, holder, Period(0)), from_=OWNER
        )
    plan.append("grant roles: ATOMIST, FUSE_MANAGER, ALPHA, WHITELIST")

    # Wire market 57. There is no separate collateral fuse: Comet counts every
    # supplied collateral towards the borrow limit on its own.
    sim.add_call(call=plasma_vault.add_fuses([SUPPLY_FUSE, BORROW_FUSE]), from_=OWNER)
    sim.add_call(
        call=plasma_vault.add_balance_fuse(COMPOUND_MARKET, BALANCE_FUSE), from_=OWNER
    )
    sim.add_call(
        call=plasma_vault.grant_market_substrates(
            COMPOUND_MARKET, compound_market_substrates()
        ),
        from_=OWNER,
    )
    # A borrow lowers this market's value and raises the vault's idle USDC in one
    # step. The ERC20 market values that USDC, and the dependency edge refreshes it
    # whenever the Compound market changes. Without the edge, NAV falls by the
    # borrowed amount until someone refreshes the ERC20 market.
    sim.add_call(
        call=plasma_vault.add_balance_fuse(ERC20_MARKET, ERC20_BALANCE_FUSE),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.grant_market_substrates(
            ERC20_MARKET, erc20_market_substrates()
        ),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.update_dependency_balance_graphs(
            [COMPOUND_MARKET], [[ERC20_MARKET]]
        ),
        from_=OWNER,
    )
    plan.append("wire market 57 and the ERC20 market with a dependency edge")

    # Oracle coverage: the balance fuse prices both tokens through the vault's
    # price manager, which delegates to the global middleware.
    weth_price = sim.observe(price_manager.get_asset_price(ETHEREUM_WETH))
    usdc_price = sim.observe(price_manager.get_asset_price(ETHEREUM_USDC))
    plan.append("verify oracle coverage for WETH and USDC")

    # The approve + deposit are exactly what a real depositor signs.
    sim.add_call(call=weth.approve(vault_address, WETH_COLLATERAL), from_=DEPOSITOR)
    sim.add_call(call=plasma_vault.deposit(WETH_COLLATERAL, DEPOSITOR), from_=DEPOSITOR)
    plan.append("deposit 40 WETH")

    sim.execute([build_supply_collateral_action(WETH_COLLATERAL)])
    collateral = sim.observe(comet.collateral_balance_of(vault_address, ETHEREUM_WETH))
    nav_after_supply = sim.observe(plasma_vault.total_assets())
    plan.append("alpha supplies 40 WETH as collateral")

    # Comet enforces the limit: a borrow above the collateral factor reverts
    # NotCollateralized in the Comet, not in the fuse.
    sim.execute([build_borrow_action(USDC_BORROW)])
    idle_usdc_after_borrow = sim.observe(usdc.balance_of(vault_address))
    debt_after_borrow = sim.observe(comet.borrow_balance_of(vault_address))
    market_after_borrow = sim.observe(
        plasma_vault.total_assets_in_market(COMPOUND_MARKET)
    )
    nav_after_borrow = sim.observe(plasma_vault.total_assets())
    plan.append("alpha borrows 20,000 USDC")

    sim.execute([build_repay_action(USDC_PARTIAL_REPAY)])
    debt_after_partial = sim.observe(comet.borrow_balance_of(vault_address))
    nav_after_partial = sim.observe(plasma_vault.total_assets())
    plan.append("alpha repays 5,000 USDC")

    # SIMULATION ONLY (time advancement): next_block shifts block.timestamp so
    # interest accrues inside one batch. There is no production equivalent -- the
    # debt simply grows between transactions.
    sim.next_block(time_shift_seconds=ONE_HOUR_SECONDS)
    debt_with_interest = sim.observe(comet.borrow_balance_of(vault_address))
    # SIMULATION ONLY (the impersonated transfer): the debt now exceeds the USDC the
    # vault borrowed. Production earns or swaps that difference in through a fuse.
    # The repay and withdrawal below are NOT simulation-only.
    sim.add_call(
        call=usdc.transfer(vault_address, USDC_INTEREST_COVER), from_=USDC_SOURCE
    )
    plan.append("an hour of interest accrues; USDC arrives to cover it")

    sim.execute([build_repay_action(Amount(MAX_UINT256))])
    debt_after_full = sim.observe(comet.borrow_balance_of(vault_address))
    base_supply_after_full = sim.observe(comet.balance_of(vault_address))
    idle_usdc_after_full = sim.observe(usdc.balance_of(vault_address))
    plan.append("alpha repays the debt in full")

    # Comet blocks withdrawing collateral that still backs debt, so the order is
    # repay first, then withdraw.
    sim.execute([build_withdraw_collateral_action(WETH_COLLATERAL)])
    collateral_after_exit = sim.observe(
        comet.collateral_balance_of(vault_address, ETHEREUM_WETH)
    )
    idle_weth_after_exit = sim.observe(weth.balance_of(vault_address))
    market_after_exit = sim.observe(
        plasma_vault.total_assets_in_market(COMPOUND_MARKET)
    )
    plan.append("alpha withdraws the 40 WETH")

    log.info("transaction plan:")
    for i, step in enumerate(plan, start=1):
        log.info("  %d. %s", i, step)

    result = sim.run()
    _assert_all_success(result)

    # ── Simulation done. From here the recorded observations are read back and the
    # outcome verified, failing loud on any mismatch. ──────────────────────────
    _check(weth_price.value.amount > 0, "WETH has no oracle price")
    _check(usdc_price.value.amount > 0, "USDC has no oracle price")
    _check(collateral.value == WETH_COLLATERAL, "collateral did not reach the Comet")

    # NAV is preserved across supply, borrow and repay: value only moves between the
    # Compound and ERC20 markets. A balance fuse ignoring the debt would add the
    # 20,000 USDC to NAV here; compare against the deposit, not a lower bound.
    for step, nav in (
        ("supply", nav_after_supply),
        ("borrow", nav_after_borrow),
        ("partial repay", nav_after_partial),
    ):
        _check(
            abs(nav.value - WETH_COLLATERAL) <= NAV_DUST_TOLERANCE,
            f"{step} moved NAV beyond dust: {nav.value}",
        )

    _check(
        idle_usdc_after_borrow.value == USDC_BORROW,
        "the borrowed USDC did not reach the vault",
    )
    _check(
        0 <= debt_after_borrow.value - USDC_BORROW <= USDC_DUST_TOLERANCE,
        f"unexpected debt after the borrow: {debt_after_borrow.value}",
    )
    _check(
        market_after_borrow.value < WETH_COLLATERAL,
        "the Compound market value did not net the debt",
    )
    remaining = USDC_BORROW - USDC_PARTIAL_REPAY
    _check(
        abs(debt_after_partial.value - remaining) <= USDC_DUST_TOLERANCE,
        f"unexpected debt after the partial repay: {debt_after_partial.value}",
    )
    _check(
        debt_with_interest.value > debt_after_partial.value,
        "no interest accrued over the hour",
    )

    # The full repay paid exactly the debt, and the rest of the USDC stayed idle.
    _check(debt_after_full.value == 0, "the debt did not clear")
    _check(base_supply_after_full.value == 0, "the repay opened a base supply position")
    expected_idle = remaining + USDC_INTEREST_COVER - debt_with_interest.value
    _check(
        abs(idle_usdc_after_full.value - expected_idle) <= USDC_DUST_TOLERANCE,
        f"unexpected idle USDC after the full repay: {idle_usdc_after_full.value}",
    )

    _check(collateral_after_exit.value == 0, "collateral left on the Comet")
    _check(idle_weth_after_exit.value == WETH_COLLATERAL, "the WETH did not come back")
    _check(market_after_exit.value == 0, "the Compound market still has value")

    log.info(
        "OK -- borrowed %s USDC, paid %s of debt in full, gas_used=%s",
        USDC_BORROW / 10**6,
        debt_with_interest.value / 10**6,
        result.gas_used,
    )
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_simulation(_connected_web3())


if __name__ == "__main__":
    main()
