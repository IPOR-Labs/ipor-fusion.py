"""End-to-end on BASE: clone a fresh PlasmaVault, configure it, deposit, supply to
Aave V3, fast-forward a year, and assert the position appreciated — all in one
`eth_simulateV1` batch.

Clone addresses are CREATE2-deterministic, so we preview `clone(...)` via eth_call
to learn them, then queue the real `clone(...)` as the FIRST call in the batch —
any prior clone would bump the factory index and change the addresses.

Governance bootstrap: a fresh clone grants the `owner` param only OWNER_ROLE. The
role-admin chain is ADMIN → OWNER → ATOMIST → {ALPHA, FUSE_MANAGER, WHITELIST,
UPDATE_MARKETS_BALANCES}, so OWNER must grant itself ATOMIST *before* it can grant
FUSE_MANAGER. A fresh clone is also WHITELIST-gated (not public), so the depositor needs
WHITELIST.

`total_assets_in_market` is a *cached* value refreshed by the post-execute hook;
advancing block.timestamp alone won't move it. After the +1y block we call
`update_markets_balances` to re-read the interest-accrued Aave position before
asserting growth.
"""

from __future__ import annotations

import logging

from _simulate import address_substrate, assert_all_success
from addresses import BASE_USDC
from constants import ANVIL_WALLET, BASE_AAVE_V3_SUPPLY_FUSE
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    PlasmaVault,
    Roles,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.core import FusionFactory
from ipor_fusion.fuses import AaveV3SupplyFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# --- deployed BASE addresses (known) ----------------------------------------

BASE_FUSION_FACTORY = Web3.to_checksum_address(
    "0x1455717668fA96534f675856347A973fA907e922"
)
# Address we control as the vault's initial owner in the simulation. Any address
# works since validation=False lets us impersonate it.
OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")

AAVE_MARKET = MarketId(IporFusionMarkets.AAVE_V3)
ONE_YEAR_SECONDS = 365 * 24 * 3600

# --- deployment-specific values (BASE mainnet) ------------------------------

# A recent BASE block, after the factory + Aave fuses were deployed. Pinning
# makes the archive reads deterministic (requires an archive node).
PINNED_BLOCK: int = 46538100

# BASE deployment address of the Aave V3 *balance* fuse (market id 1). Distinct
# from the *supply* fuse — it's what lets `total_assets_in_market(AAVE_MARKET)`
# value the position. Read from an existing BASE vault's `get_balance_fuses()`.
BASE_AAVE_V3_BALANCE_FUSE: str = "0x952573Ec1B6895a88a95CA523097083d4da4D8e5"

# A BASE address holding ample USDC, used to fund the depositor via impersonated
# transfer. Morpho Blue (~150M USDC) — deliberately a different protocol from
# Aave so funding doesn't perturb the market our strategy supplies into.
BASE_USDC_WHALE: str = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"

DEPOSIT_AMOUNT = Amount(1_000_000_000)  # 1,000 USDC (6 decimals)


def _clone_args() -> dict:
    """Identical args for the preview and the in-batch create — same args +
    same factory index → same CREATE2 addresses."""
    return {
        "asset_name": "IPOR USDC Vault (e2e)",
        "asset_symbol": "ipUSDCe2e",
        "underlying_token": BASE_USDC,
        "redemption_delay_seconds": 0,
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def test_simulate_vault_from_scratch_supply_aave_v3(web3_base):
    balance_fuse = Web3.to_checksum_address(BASE_AAVE_V3_BALANCE_FUSE)
    whale = Web3.to_checksum_address(BASE_USDC_WHALE)

    ctx = Web3Context(web3=web3_base, chain_id=ChainId(8453), signer=OWNER)
    ctx.default_block = PINNED_BLOCK
    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)

    # ── 1. Predict the deterministic addresses ──────────────────────────────
    preview = factory.clone(**_clone_args()).call()
    vault_address = preview.plasma_vault
    access_manager_address = preview.access_manager
    log.info(
        "predicted vault=%s access_manager=%s (index=%d)",
        vault_address,
        access_manager_address,
        preview.index,
    )

    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, access_manager_address)
    usdc = ERC20(ctx, BASE_USDC)
    aave = AaveV3SupplyFuse(BASE_AAVE_V3_SUPPLY_FUSE)

    sim = VaultSimulator(
        web3=web3_base, vault=vault_address, alpha=ANVIL_WALLET, block=hex(PINNED_BLOCK)
    )

    # ── 2. Create the vault (MUST be the first call → index matches preview) ─
    sim.add_call(call=factory.clone(**_clone_args()), from_=OWNER, label="clone")

    # ── 3. Configure (impersonating OWNER, who starts with OWNER_ROLE) ───────
    # Order matters: OWNER must hold ATOMIST before it can grant the roles ATOMIST
    # administers (FUSE_MANAGER / ALPHA / WHITELIST / UPDATE_MARKETS_BALANCES).
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, OWNER, 0), from_=OWNER
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.FUSE_MANAGER_ROLE, OWNER, 0), from_=OWNER
    )
    # ALPHA drives execute(); the depositor needs WHITELIST (fresh clones are
    # private, not public); UPDATE_MARKETS_BALANCES lets us refresh NAV later.
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0), from_=OWNER
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, ANVIL_WALLET, 0),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(
            Roles.UPDATE_MARKETS_BALANCES_ROLE, ANVIL_WALLET, 0
        ),
        from_=OWNER,
    )
    # Wire the Aave market: action fuse + balance fuse + the USDC substrate.
    sim.add_call(call=plasma_vault.add_fuses([BASE_AAVE_V3_SUPPLY_FUSE]), from_=OWNER)
    sim.add_call(
        call=plasma_vault.add_balance_fuse(AAVE_MARKET, balance_fuse), from_=OWNER
    )
    sim.add_call(
        call=plasma_vault.grant_market_substrates(
            AAVE_MARKET, [address_substrate(BASE_USDC)]
        ),
        from_=OWNER,
    )

    # ── 4. Fund the depositor, then deposit into the vault ───────────────────
    sim.add_call(call=usdc.transfer(ANVIL_WALLET, DEPOSIT_AMOUNT), from_=whale)
    sim.add_call(call=usdc.approve(vault_address, DEPOSIT_AMOUNT), from_=ANVIL_WALLET)
    sim.add_call(
        call=plasma_vault.deposit(DEPOSIT_AMOUNT, ANVIL_WALLET), from_=ANVIL_WALLET
    )

    sim.observe("total_assets_after_deposit", plasma_vault.total_assets())
    sim.observe("vault_usdc_after_deposit", usdc.balance_of(vault_address))

    # ── 5. Strategy: supply the deposited USDC to Aave V3 (alpha-driven) ─────
    # 1 USDC buffer below the deposit to avoid dust-rounding reverts.
    supply_amount = Amount(DEPOSIT_AMOUNT - 1_000_000)
    sim.execute([aave.supply(asset=BASE_USDC, amount=supply_amount, e_mode=0)])

    sim.observe("total_assets_after_supply", plasma_vault.total_assets())
    sim.observe("aave_value_t0", plasma_vault.total_assets_in_market(AAVE_MARKET))
    sim.observe("vault_usdc_after_supply", usdc.balance_of(vault_address))

    # ── 6. Fast-forward one year, refresh cached balances, re-read value ─────
    # The Aave aToken accrues interest against block.timestamp; opening a later
    # block and re-running the balance fuse via update_markets_balances surfaces
    # the appreciation into the vault's stored NAV.
    sim.next_block(time_shift_seconds=ONE_YEAR_SECONDS)
    sim.add_call(
        call=plasma_vault.update_markets_balances([AAVE_MARKET]), from_=ANVIL_WALLET
    )
    sim.observe("aave_value_t1", plasma_vault.total_assets_in_market(AAVE_MARKET))
    sim.observe("total_assets_t1", plasma_vault.total_assets())

    # ── 7. Run the whole batch and assert ────────────────────────────────────
    result = sim.run()
    log.info(
        "all_success=%s gas_used=%s observations=%s",
        result.all_success,
        result.gas_used,
        result.observations,
    )
    assert_all_success(result)

    # Deposit credited the vault with the underlying.
    assert result.get("total_assets_after_deposit") >= DEPOSIT_AMOUNT
    assert result.get("vault_usdc_after_deposit") >= DEPOSIT_AMOUNT
    # NAV is preserved across the supply (moved USDC -> Aave position, ~1:1).
    assert result.get("total_assets_after_supply") >= supply_amount
    # The position shows up in the Aave market valuation right after supply.
    # `* 99 // 100` is the repo idiom (see test_simulate_pilot_v3.py) for "≈ the
    # supplied amount" — Aave's scaled-balance math rounds down by a dust unit.
    assert result.get("aave_value_t0") >= supply_amount * 99 // 100
    # Most raw USDC left the vault into Aave.
    assert result.get("vault_usdc_after_supply") < DEPOSIT_AMOUNT
    # After a year, the Aave position has appreciated (accrued interest).
    assert result.get("aave_value_t1") > result.get("aave_value_t0")
    # And total NAV reflects that growth.
    assert result.get("total_assets_t1") > result.get("total_assets_after_supply")
