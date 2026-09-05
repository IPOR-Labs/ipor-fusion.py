"""Build a simple Fusion vault from scratch on Base and supply USDC to Aave V3.

This is the canonical starting example. It shows, end to end and without reading
the test suite, how to:

  1. connect a ``Web3Context`` to Base;
  2. preview a vault deployment with ``FusionFactory.clone(...).call()`` to learn
     its deterministic CREATE2 addresses;
  3. obtain the unsigned creation calldata via ``.calldata``;
  4. bootstrap the vault's roles in the correct order;
  5. pair the Aave V3 supply (functional) fuse with its balance fuse;
  6. grant only the USDC substrate (no unrelated permissions);
  7. verify oracle coverage for USDC;
  8. deposit USDC and run an alpha-driven Aave V3 supply;
  9. confirm the vault NAV is preserved across the supply.

Everything runs inside a single ``eth_simulateV1`` batch against a pinned Base
block -- nothing is signed or broadcast. To put a flow on-chain, hand the
``.calldata`` each builder produces to your own signer; this example never does.

Run it (the shell snippets assume a POSIX shell -- bash or zsh):

    export BASE_PROVIDER_URL="https://base-mainnet.g.alchemy.com/v2/YOUR_KEY"
    uv run python examples/simple_aave_v3_supply_base.py

The provider must be an archive node that implements ``eth_simulateV1`` (Alchemy
and other geth/reth-based providers do).
"""

from __future__ import annotations

import logging
import os
import sys

from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    FuseAction,
    PlasmaVault,
    PriceOracleMiddlewareManager,
    Roles,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    is_simulate_v1_supported,
)
from ipor_fusion.core import FusionFactory
from ipor_fusion.fuses import AaveV3SupplyFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId, Period

log = logging.getLogger("simple_aave_v3_supply_base")

# ── Live IPOR / Base infrastructure (do NOT change) ──────────────────────────
# Every address below is a live, canonical deployment. Provenance for all IPOR
# Fusion addresses: https://github.com/IPOR-Labs/ipor-abi
#   mainnet/mainnet-base-fusion/addresses.json

BASE_CHAIN_ID = ChainId(8453)

# IporFusionFactoryProxy on Base. Its clone(...) takes the six arguments
# assembled in clone_args() below.
BASE_FUSION_FACTORY = Web3.to_checksum_address(
    "0x1455717668fA96534f675856347A973fA907e922"
)

# Native (Circle-issued) USDC on Base, 6 decimals. Not the bridged USDbC, a
# distinct ERC-20 with its own Aave market and price feed.
BASE_USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# Aave V3 market (IporFusionMarkets.AAVE_V3).
AAVE_MARKET = MarketId(IporFusionMarkets.AAVE_V3)

# Aave V3 *supply* fuse (registry name SupplyFuseAaveV3) -- the functional fuse
# that encodes supply/withdraw.
BASE_AAVE_V3_SUPPLY_FUSE = Web3.to_checksum_address(
    "0x26fD6EF391E98C78CfCA27e00c3d15be4D941625"
)
# Aave V3 *balance* fuse (registry name AaveV3WithPriceOracleMiddlewareBalanceFuse)
# -- distinct from the supply fuse; this is what lets
# total_assets_in_market(AAVE_MARKET) value the position.
BASE_AAVE_V3_BALANCE_FUSE = Web3.to_checksum_address(
    "0x952573Ec1B6895a88a95CA523097083d4da4D8e5"
)

# ── Simulation parameters (safe to adjust) ───────────────────────────────────
#
# OWNER, ALPHA, DEPOSITOR and BASE_USDC_WHALE are impersonated simulation
# actors: the simulator runs with validation disabled, so it can act as any
# address without its key. They are NOT accounts you control -- there is nothing
# to replace and nothing to broadcast here.
#
# They are kept as three distinct addresses on purpose, because they are three
# distinct roles that must NOT share an address in production:
#   OWNER    -- governance/atomist; can reconfigure the whole vault. A cold key
#               or multisig. (A fresh clone forces OWNER to self-grant ATOMIST,
#               so owner and atomist coincide during bootstrap; in production the
#               atomist may be delegated to a separate address.)
#   ALPHA    -- the online, least-privilege operator; can only drive execute().
#               Collapsing it into OWNER means a compromised alpha key becomes
#               full governance control.
#   DEPOSITOR-- an end user; only ever holds and deposits funds.

# Governance/atomist. Any address; here it happens to be a random EOA.
OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")

# The alpha operator. Anvil account #0, a well-known test address.
ALPHA = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

# The depositor -- the end user putting funds in. Never drives execute() and
# never holds a governance role. Anvil account #1, the natural sibling to the
# alpha's Anvil account #0.
DEPOSITOR = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")

# A Base address holding ample USDC, used to fund the depositor via an
# impersonated transfer inside the simulation. This is the Morpho Blue core
# contract (~150M USDC) -- deliberately a different protocol from Aave, so
# funding does not perturb the market the strategy supplies into.
BASE_USDC_WHALE = Web3.to_checksum_address("0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb")

# Pinned Base block. Pinning makes the simulation deterministic and immune to
# mainnet state drift. It requires an archive node that can serve state at this
# height -- if your provider cannot (an error mentioning missing trie/state, or
# a pruned block), BUMP THIS to a recent Base block your node can reach.
PINNED_BLOCK = 46538100

ONE_YEAR_SECONDS = 365 * 24 * 3600
DEPOSIT_AMOUNT = Amount(1_000_000_000)  # 1,000 USDC (6 decimals)
# Idle-USDC buffer kept back from the supply to avoid dust-rounding reverts.
SUPPLY_BUFFER = Amount(1_000_000)  # 1 USDC
# NAV must be conserved across the supply up to Aave's scaled-balance dust.
NAV_DUST_TOLERANCE = Amount(100)  # 0.0001 USDC


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
    raise AssertionError(
        f"simulation calls failed: {failed} (reason={result.revert_reason})"
    )


def _connected_web3() -> Web3:
    """Build a Web3 client from BASE_PROVIDER_URL, or exit with a clear message."""
    url = os.environ.get("BASE_PROVIDER_URL")
    if not url:
        sys.exit("BASE_PROVIDER_URL is not set -- export your Base RPC URL first.")
    web3 = Web3(Web3.HTTPProvider(url))
    if not web3.is_connected():
        sys.exit("cannot reach the RPC at BASE_PROVIDER_URL -- check the endpoint.")
    if not is_simulate_v1_supported(web3):
        sys.exit(
            "the provider does not implement eth_simulateV1 -- use an archive node."
        )
    return web3


# ── Pure builders (no chain access -- safe to import and unit-test offline) ───


def clone_args() -> dict:
    """Arguments for FusionFactory.clone(...).

    The preview and the in-batch create MUST use identical args: same args plus
    the same factory index yield the same CREATE2 addresses.
    """
    return {
        "asset_name": "IPOR USDC Vault (example)",
        "asset_symbol": "ipUSDCex",
        "underlying_token": BASE_USDC,
        "redemption_delay_seconds": 0,
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def unsigned_clone_calldata() -> bytes:
    """Unsigned creation calldata (selector + ABI-encoded args) for the deploy.

    Built with a ctx-less encoder, so it needs no provider. Hand these bytes to
    an external signer / multisig instead of calling ``.send()``.
    """
    return FusionFactory.encoder(BASE_FUSION_FACTORY).clone(**clone_args()).calldata


def build_supply_action(amount: Amount) -> FuseAction:
    """Encode the alpha's Aave V3 supply of ``amount`` USDC.

    A fuse method is a pure encoder: it returns a FuseAction and touches no
    chain. Nothing happens until PlasmaVault.execute([...]) runs the batch.
    """
    return AaveV3SupplyFuse(BASE_AAVE_V3_SUPPLY_FUSE).supply(
        asset=BASE_USDC, amount=amount, e_mode=0
    )


# ── Simulation (the whole build + strategy, no broadcast) ────────────────────


def run_simulation(web3: Web3) -> SimulationResult:
    """Build, fund, deposit and supply -- all in one eth_simulateV1 batch."""
    ctx = Web3Context(web3=web3, chain_id=BASE_CHAIN_ID, signer=OWNER)
    ctx.default_block = PINNED_BLOCK
    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)

    # 1. Preview the deterministic addresses (eth_call, no gas, no state change).
    preview = factory.clone(**clone_args()).call()
    log.info(
        "predicted vault=%s access_manager=%s price_manager=%s (index=%d)",
        preview.plasma_vault,
        preview.access_manager,
        preview.price_manager,
        preview.index,
    )
    # 2. The unsigned creation calldata (external-signer path).
    log.info("unsigned clone calldata: 0x%s", unsigned_clone_calldata().hex())

    plasma_vault = PlasmaVault(ctx, preview.plasma_vault)
    access_manager = AccessManager(ctx, preview.access_manager)
    price_manager = PriceOracleMiddlewareManager(ctx, preview.price_manager)
    usdc = ERC20(ctx, BASE_USDC)

    sim = VaultSimulator(
        web3=web3, vault=preview.plasma_vault, alpha=ALPHA, block=PINNED_BLOCK
    )
    plan: list[str] = []

    # The clone MUST be the first queued call -- any earlier call that bumps the
    # factory index would change the addresses we previewed above.
    sim.add_call(call=factory.clone(**clone_args()), from_=OWNER, label="clone")
    plan.append("deploy the vault stack (clone)")

    # Role bootstrap order: a fresh clone grants OWNER only OWNER_ROLE. The admin
    # chain is ADMIN -> OWNER -> ATOMIST -> {ALPHA, FUSE_MANAGER, WHITELIST,
    # UPDATE_MARKETS_BALANCES}, so OWNER must self-grant ATOMIST before it can
    # grant the rest.
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, OWNER, Period(0)),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.FUSE_MANAGER_ROLE, OWNER, Period(0)),
        from_=OWNER,
    )
    # ALPHA must stay a separate address from OWNER in production (see the actor
    # notes above): a compromised alpha key must not equal governance.
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ALPHA, Period(0)),
        from_=OWNER,
    )
    # A fresh clone is WHITELIST-gated (private), so the depositor needs WHITELIST.
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, DEPOSITOR, Period(0)),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(
            Roles.UPDATE_MARKETS_BALANCES_ROLE, ALPHA, Period(0)
        ),
        from_=OWNER,
    )
    plan.append(
        "grant roles: ATOMIST, FUSE_MANAGER, ALPHA, WHITELIST, UPDATE_MARKETS_BALANCES"
    )

    # Wire the Aave market: functional fuse + its balance fuse + the USDC
    # substrate. Granting only USDC keeps the vault from touching other assets.
    sim.add_call(call=plasma_vault.add_fuses([BASE_AAVE_V3_SUPPLY_FUSE]), from_=OWNER)
    sim.add_call(
        call=plasma_vault.add_balance_fuse(AAVE_MARKET, BASE_AAVE_V3_BALANCE_FUSE),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.grant_market_substrates(
            AAVE_MARKET, [_address_substrate(BASE_USDC)]
        ),
        from_=OWNER,
    )
    plan.append("add Aave V3 supply fuse + balance fuse, grant the USDC substrate")

    # Oracle coverage: the vault must be able to price USDC. A fresh vault's
    # price manager delegates to the global middleware for assets without an
    # override, so this reads the effective price.
    sim.observe("usdc_price", price_manager.get_asset_price(BASE_USDC))
    plan.append("verify oracle coverage for USDC")

    # Fund the depositor from the whale, then deposit into the vault.
    sim.add_call(call=usdc.transfer(DEPOSITOR, DEPOSIT_AMOUNT), from_=BASE_USDC_WHALE)
    sim.add_call(
        call=usdc.approve(preview.plasma_vault, DEPOSIT_AMOUNT), from_=DEPOSITOR
    )
    sim.add_call(call=plasma_vault.deposit(DEPOSIT_AMOUNT, DEPOSITOR), from_=DEPOSITOR)
    plan.append("fund the depositor and deposit 1,000 USDC")

    sim.observe("total_assets_after_deposit", plasma_vault.total_assets())
    sim.observe("vault_usdc_after_deposit", usdc.balance_of(preview.plasma_vault))

    # Strategy: the alpha supplies the deposited USDC to Aave V3, keeping a small
    # idle buffer back to avoid dust-rounding reverts.
    supply_amount = Amount(DEPOSIT_AMOUNT - SUPPLY_BUFFER)
    sim.execute([build_supply_action(supply_amount)])
    plan.append("alpha supplies ~999 USDC to Aave V3")

    sim.observe("total_assets_after_supply", plasma_vault.total_assets())
    sim.observe("aave_value_t0", plasma_vault.total_assets_in_market(AAVE_MARKET))
    sim.observe("vault_usdc_after_supply", usdc.balance_of(preview.plasma_vault))

    # Fast-forward a year and refresh the cached market balance so the accrued
    # interest surfaces into the vault's stored NAV.
    sim.next_block(time_shift_seconds=ONE_YEAR_SECONDS)
    sim.add_call(call=plasma_vault.update_markets_balances([AAVE_MARKET]), from_=ALPHA)
    sim.observe("aave_value_t1", plasma_vault.total_assets_in_market(AAVE_MARKET))
    sim.observe("total_assets_t1", plasma_vault.total_assets())
    plan.append("advance one year and re-value the Aave position")

    log.info("transaction plan:")
    for i, step in enumerate(plan, start=1):
        log.info("  %d. %s", i, step)

    result = sim.run()
    _assert_all_success(result)

    # ── Simulation done. Everything above only *described* the batch; sim.run()
    # is the single eth_simulateV1 call that executed it. From here we read the
    # recorded observations back and verify the outcome, failing loud on any
    # mismatch. ─────────────────────────────────────────────────────────────
    supply_amount_int = int(supply_amount)
    usdc_price = result.get("usdc_price")
    total_after_deposit = result.get("total_assets_after_deposit")
    total_after_supply = result.get("total_assets_after_supply")
    vault_usdc_after_supply = result.get("vault_usdc_after_supply")

    _check(usdc_price.amount > 0, "USDC has no oracle price -- vault cannot value it")
    # Deposit credited the vault with the underlying.
    _check(
        total_after_deposit >= DEPOSIT_AMOUNT,
        f"deposit not credited to NAV: {total_after_deposit}",
    )
    _check(
        result.get("vault_usdc_after_deposit") >= DEPOSIT_AMOUNT,
        "vault did not receive the deposited USDC",
    )
    # NAV is preserved across the supply: USDC moves into an Aave position ~1:1,
    # so the total may only move by Aave's scaled-balance dust, not by a real
    # gain or loss. Compare against the pre-supply NAV, not a loose lower bound.
    _check(
        abs(total_after_supply - total_after_deposit) <= NAV_DUST_TOLERANCE,
        f"supply moved NAV beyond dust: {total_after_deposit} -> {total_after_supply}",
    )
    # Exactly the buffered amount stayed behind as idle vault USDC.
    _check(
        vault_usdc_after_supply == DEPOSIT_AMOUNT - supply_amount_int,
        f"unexpected idle USDC after supply: {vault_usdc_after_supply}",
    )
    # The position shows up in the Aave market valuation right after the supply
    # (>= 99% of supplied; Aave's scaled-balance math rounds down by dust).
    _check(
        result.get("aave_value_t0") >= supply_amount_int * 99 // 100,
        "Aave position undervalued right after supply",
    )
    # After a year the position has appreciated, and NAV reflects the growth.
    _check(
        result.get("aave_value_t1") > result.get("aave_value_t0"),
        "Aave position did not accrue interest over the year",
    )
    _check(
        result.get("total_assets_t1") > total_after_supply,
        "NAV did not reflect the accrued interest",
    )

    log.info(
        "OK -- USDC price=%s (dec=%s), NAV t0=%s -> t1=%s, gas_used=%s",
        usdc_price.amount,
        usdc_price.decimals,
        total_after_supply,
        result.get("total_assets_t1"),
        result.gas_used,
    )
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_simulation(_connected_web3())


if __name__ == "__main__":
    main()
