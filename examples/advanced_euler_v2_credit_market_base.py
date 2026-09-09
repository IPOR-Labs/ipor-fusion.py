"""Build a Fusion vault from scratch on Base and run an Euler V2 credit position.

It composes *four* Euler V2 functional fuses, typed Euler substrates,
sub-accounts, and a strict operation order to open and unwind a real
collateralized borrow. It shows, end to end and without reading the test suite,
how to:

  1. connect a ``Web3Context`` to Base;
  2. preview a vault deployment with ``FusionFactory.clone(...).call()`` and read
     its deterministic CREATE2 addresses, plus the unsigned creation ``.calldata``;
  3. bootstrap the vault's roles in the correct order;
  4. install the Euler V2 supply / collateral / controller / borrow fuses and
     pair them with the shared Euler V2 balance fuse;
  5. grant only the precise Euler substrates -- cbETH usable as collateral, WETH
     usable as a borrow -- on a single sub-account, and nothing else;
  6. verify oracle coverage for every tracked asset (cbETH and WETH);
  7. fund the vault with cbETH collateral;
  8. run the credit lifecycle as the alpha: supply collateral -> enable collateral
     + controller -> borrow WETH -> repay -> unwind (controller BEFORE collateral)
     -> withdraw the collateral, fully exiting the position;
  9. read the outstanding debt straight from the eVault's ``debtOf`` and confirm
     it opened then cleared, the position fully exited, and no unrelated
     permissions were granted.

Everything runs inside a single ``eth_simulateV1`` batch against a pinned Base
block -- nothing is signed or broadcast. To put a flow on-chain, hand the
``.calldata`` each builder produces to your own signer -- each step signed by the
role authorized for it (see the simulation->production mapping in
``run_simulation``); this example never does.

Run it (the shell snippets assume a POSIX shell -- bash or zsh):

    export BASE_PROVIDER_URL="https://base-mainnet.g.alchemy.com/v2/YOUR_KEY"
    uv run python examples/advanced_euler_v2_credit_market_base.py

The provider must be an archive node that implements ``eth_simulateV1`` (Alchemy
and other geth/reth-based providers do).
"""

from __future__ import annotations

import logging
import os
import sys

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    PlasmaVault,
    PriceOracleMiddlewareManager,
    Roles,
    SimulationResult,
    VaultSimulator,
    Web3Context,
    addresses,
    is_simulate_v1_supported,
)
from ipor_fusion.core import FusionFactory
from ipor_fusion.core.contract import Call
from ipor_fusion.fuses import (
    EulerV2BorrowFuse,
    EulerV2CollateralFuse,
    EulerV2ControllerFuse,
    EulerV2SupplyFuse,
    FuseAction,
    euler_substrate,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import MAX_UINT256, Amount, ChainId, MarketId, Period

log = logging.getLogger("advanced_euler_v2_credit_market_base")

# ── Live IPOR / Base / Euler infrastructure (do NOT change) ──────────────────
# Every IPOR Fusion address below is a live, canonical deployment. Provenance:
# https://github.com/IPOR-Labs/ipor-abi -> mainnet/mainnet-base-fusion/addresses.json
# (the registry name is quoted next to each). The Euler eVaults are NOT IPOR
# deployments -- see their note below.

BASE_CHAIN_ID = ChainId(8453)
EULER_MARKET = MarketId(IporFusionMarkets.EULER_V2)

# IporFusionFactoryProxy on Base. Its clone(...) takes the six args in clone_args().
BASE_FUSION_FACTORY = addresses.factory_proxy(8453)  # IporFusionFactoryProxy

# The four Euler V2 *functional* fuses (registry names quoted). Each is a
# stateless encoder for one EVC operation; the balance fuse below is what values
# the resulting position for NAV.
EULER_SUPPLY_FUSE = Web3.to_checksum_address(  # "SupplyFuseEulerV2"
    "0x598326fcEDE2C1B8E9023a20C18FFf6Dea5306A4"
)
EULER_COLLATERAL_FUSE = Web3.to_checksum_address(  # "CollateralFuseEulerV2"
    "0x12c479f8aB53D4884fc76F803dD24eb8B6D17a94"
)
EULER_CONTROLLER_FUSE = Web3.to_checksum_address(  # "ControllerFuseEulerV2"
    "0x108c8cFB9e00681FfA1fa3b654937E8b3BCd2E64"
)
EULER_BORROW_FUSE = Web3.to_checksum_address(  # "BorrowFuseEulerV2"
    "0x906496F0D4C733275F892b1a6fC92eD56639B379"
)
# The Euler V2 *balance* fuse (registry name "BalanceFuseEulerV2") -- distinct
# from the functional fuses; this is what lets total_assets_in_market(EULER_MARKET)
# value the collateral net of debt.
EULER_BALANCE_FUSE = Web3.to_checksum_address(
    "0xF8A6AA09bB55f2319113b0DA88883F392e66A5fa"
)

# Euler V2 eVaults on Base: the collateral eVault (cbETH) and the debt eVault
# (WETH). These are Euler protocol (EVK) deployments, NOT IPOR Fusion addresses,
# so they are not in the ipor-abi registry -- they are a live cbETH/WETH pair
# whose LTV allows the borrow below. The RPC-gated test asserts each eVault's
# IEVault.asset() equals the expected underlying, which is their provenance check.
EVAULT_CBETH = Web3.to_checksum_address("0x358f25F82644eaBb441d0df4AF8746614fb9ea49")
EVAULT_WETH = Web3.to_checksum_address("0x859160DB5841E5cfB8D3f144C6b3381A85A4b410")

# Underlying assets of those eVaults (registry names "cbETH" / "wETH").
BASE_CBETH = Web3.to_checksum_address("0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22")
BASE_WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")

# A Base address holding ample cbETH, used to fund the vault's collateral via an
# impersonated transfer inside the simulation. This is the Morpho Blue core
# contract (registry name "Morpho") -- a large, unrelated holder, so funding does
# not perturb the Euler markets the strategy uses.
BASE_CBETH_WHALE = Web3.to_checksum_address(
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
)

# ── Simulation parameters (safe to adjust) ───────────────────────────────────
#
# OWNER, ALPHA and BASE_CBETH_WHALE are impersonated simulation actors: the
# simulator runs with validation disabled, so it can act as any address without
# its key. They are NOT accounts you control -- there is nothing to replace and
# nothing to broadcast here.
#
# OWNER and ALPHA are kept distinct on purpose, because they are distinct roles
# that must NOT share an address in production:
#   OWNER -- governance/atomist; can reconfigure the whole vault. A cold key or
#            multisig. (A fresh clone forces OWNER to self-grant ATOMIST, so owner
#            and atomist coincide during bootstrap; in production the atomist may
#            be delegated to a separate address.)
#   ALPHA -- the online operator; drives execute() and refreshes market balances
#            (update_markets_balances), and nothing else -- least-privilege, it
#            cannot reconfigure the vault. Collapsing it into OWNER means a
#            compromised alpha key becomes full governance control.
# (There is no depositor role here: this example funds collateral directly and
# focuses on the credit lifecycle rather than the deposit -> shares path.)

# Governance/atomist. Any address; here it happens to be a random EOA.
OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")

# The alpha operator. Anvil account #0, a well-known test address.
ALPHA = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

# One owner address controls 256 Euler sub-accounts; the vault's EVC account for
# this position is `plasma_vault XOR SUB_ACCOUNT`. Using a non-zero sub-account
# shows the packing is real, not decorative.
SUB_ACCOUNT = 0x01

COLLATERAL_FUND_AMOUNT = Amount(12 * 10**18)  # cbETH transferred to the vault
COLLATERAL_SUPPLY_AMOUNT = Amount(10 * 10**18)  # cbETH supplied as collateral
BORROW_AMOUNT = Amount(
    1 * 10**18
)  # 1 WETH borrowed against ~10 cbETH (well within LTV)
# Tolerance for the fully-exited state: ERC4626 supply/withdraw rounds down, so
# the market value lands at dust rather than an exact zero. 1e12 = 1e-6 token.
EULER_EXIT_DUST = Amount(10**12)

# Pinned Base block. Pinning makes the simulation deterministic and immune to
# mainnet state drift. It requires an archive node that can serve state at this
# height -- if your provider cannot (an error mentioning missing trie/state, or a
# pruned block), BUMP THIS to a recent Base block your node can reach.
PINNED_BLOCK = 46538100  # Base, ~2026-05-27


# ── Inlined helpers (kept local so this file is self-contained) ──────────────


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


def _euler_account(plasma_vault: ChecksumAddress) -> ChecksumAddress:
    """The EVC account for this position: ``plasma_vault XOR SUB_ACCOUNT``.

    Mirrors ``EulerFuseLib.generateSubAccountAddress`` -- Euler derives 256
    sub-accounts per owner by XOR-ing the low byte. All reads of the position
    (e.g. ``debtOf``) address this account, not the raw vault.
    """
    return Web3.to_checksum_address(f"0x{(int(plasma_vault, 16) ^ SUB_ACCOUNT):040x}")


def _evault_debt_of(
    ctx: Web3Context, euler_vault: ChecksumAddress, account: ChecksumAddress
) -> Call[int]:
    """Read an Euler eVault's outstanding debt for an account (underlying units).

    ``debtOf`` is the outstanding-debt read: the live truth of what the position
    owes, straight from the debt eVault rather than from any vault accounting. It
    is the amount owed, not a health factor (health would weigh it against the
    collateral value and liquidation threshold).
    """
    data = function_signature_to_4byte_selector("debtOf(address)") + encode(
        ["address"], [account]
    )
    return Call(to=euler_vault, data=data, output_types=["uint256"], ctx=ctx)


# ── Pure builders (no chain access -- safe to import and unit-test offline) ───


def clone_args() -> dict:
    """Arguments for FusionFactory.clone(...).

    The preview and the in-batch create MUST use identical args: same args plus
    the same factory index yield the same CREATE2 addresses. The accounting asset
    is WETH -- the asset this vault borrows and denominates its NAV in.
    """
    return {
        "asset_name": "IPOR WETH Euler Credit Vault (example)",
        "asset_symbol": "ipWETHec",
        "underlying_token": BASE_WETH,
        "redemption_delay_seconds": 0,
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def unsigned_clone_calldata() -> bytes:
    """Unsigned creation calldata (selector + ABI-encoded args) for the deploy.

    Built with a ctx-less encoder, so it needs no provider. Hand these bytes to
    an external signer / multisig instead of calling ``.send()``. NOTE: the owner
    encoded here is the simulation ``OWNER`` (a throwaway EOA) -- set your real
    owner in ``clone_args()`` before deploying, or the vault is owned by it.
    """
    return FusionFactory.encoder(BASE_FUSION_FACTORY).clone(**clone_args()).calldata


def market_substrates() -> list[bytes]:
    """The two typed Euler substrates this vault is allowed to touch -- and only
    these two. Each packs (eulerVault, isCollateral, canBorrow, subAccount) into a
    bytes32 via ``euler_substrate``.

    The flags are the minimum each fuse requires (verified against EulerFuseLib):
      - the cbETH eVault is granted as COLLATERAL only (isCollateral, no borrow):
        the collateral fuse's enter checks ``canCollateral`` (needs isCollateral),
        and supplying only needs the vault present;
      - the WETH eVault is granted as BORROWABLE only (canBorrow, not collateral):
        the controller and borrow fuses' enter check ``canBorrow``.
    Granting each capability narrowly -- not "both flags on both vaults" -- is the
    point: the vault can supply/collateralize cbETH and borrow WETH, nothing else.
    """
    return [
        euler_substrate(
            euler_vault=EVAULT_CBETH,
            is_collateral=True,
            can_borrow=False,
            sub_account=SUB_ACCOUNT,
        ),
        euler_substrate(
            euler_vault=EVAULT_WETH,
            is_collateral=False,
            can_borrow=True,
            sub_account=SUB_ACCOUNT,
        ),
    ]


# Each builder returns a FuseAction -- a pure (fuse_address, calldata) pair that
# touches no chain. Nothing happens until PlasmaVault.execute([...]) runs the
# batch as the alpha.


def supply_collateral_action(amount: Amount) -> FuseAction:
    """Supply ``amount`` cbETH into the ecbETH eVault (deposit, not yet collateral)."""
    return EulerV2SupplyFuse(EULER_SUPPLY_FUSE).supply(
        euler_vault=EVAULT_CBETH, max_amount=amount, sub_account=SUB_ACCOUNT
    )


def enable_collateral_action() -> FuseAction:
    """Mark the ecbETH balance as collateral in the EVC (backs future debt)."""
    return EulerV2CollateralFuse(EULER_COLLATERAL_FUSE).enable_collateral(
        euler_vault=EVAULT_CBETH, sub_account=SUB_ACCOUNT
    )


def enable_controller_action() -> FuseAction:
    """Enable the eWETH eVault as the EVC controller (required before borrowing)."""
    return EulerV2ControllerFuse(EULER_CONTROLLER_FUSE).enable_controller(
        euler_vault=EVAULT_WETH, sub_account=SUB_ACCOUNT
    )


def borrow_action(amount: Amount) -> FuseAction:
    """Borrow ``amount`` WETH from the eWETH eVault against the cbETH collateral."""
    return EulerV2BorrowFuse(EULER_BORROW_FUSE).borrow(
        euler_vault=EVAULT_WETH, asset_amount=amount, sub_account=SUB_ACCOUNT
    )


def repay_action(max_amount: Amount) -> FuseAction:
    """Repay up to ``max_amount`` WETH; MAX_UINT256 caps at the outstanding debt."""
    return EulerV2BorrowFuse(EULER_BORROW_FUSE).repay(
        euler_vault=EVAULT_WETH, max_asset_amount=max_amount, sub_account=SUB_ACCOUNT
    )


def disable_controller_action() -> FuseAction:
    """Release the eWETH controller -- only valid once the debt is fully repaid."""
    return EulerV2ControllerFuse(EULER_CONTROLLER_FUSE).disable_controller(
        euler_vault=EVAULT_WETH, sub_account=SUB_ACCOUNT
    )


def disable_collateral_action() -> FuseAction:
    """Un-mark the ecbETH balance as collateral (done after the controller is off)."""
    return EulerV2CollateralFuse(EULER_COLLATERAL_FUSE).disable_collateral(
        euler_vault=EVAULT_CBETH, sub_account=SUB_ACCOUNT
    )


def withdraw_collateral_action(max_amount: Amount) -> FuseAction:
    """Withdraw cbETH from the ecbETH eVault back to the vault; MAX_UINT256 pulls
    the whole balance. This is the reverse of the initial supply and the final
    step that fully exits the Euler position."""
    return EulerV2SupplyFuse(EULER_SUPPLY_FUSE).withdraw(
        euler_vault=EVAULT_CBETH, max_amount=max_amount, sub_account=SUB_ACCOUNT
    )


# ── Simulation (the whole build + credit lifecycle, no broadcast) ────────────


def run_simulation(web3: Web3) -> SimulationResult:
    """Build, fund and run the full Euler V2 credit lifecycle in one batch."""
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
    cbeth = ERC20(ctx, BASE_CBETH)
    euler_account = _euler_account(preview.plasma_vault)

    # The whole flow runs as ONE eth_simulateV1 batch; nothing here is signed or
    # broadcast (see the module docstring). Each simulator primitive stands in for
    # a production action, read as "simulation -> production":
    #   - sim.add_call(call=X, from_=OWNER) -> X.send() by the owner/atomist
    #     (or X.calldata to a multisig / timelock).
    #   - sim.execute([action]) -> plasma_vault.execute([action]).send() by the alpha.
    #   - sim.observe("key", X) -> X.call(), a plain read.
    sim = VaultSimulator(
        web3=web3, vault=preview.plasma_vault, alpha=ALPHA, block=PINNED_BLOCK
    )
    plan: list[str] = []

    # The clone MUST be the first queued call -- any earlier call that bumps the
    # factory index would change the addresses we previewed above.
    sim.add_call(call=factory.clone(**clone_args()), from_=OWNER, label="clone")
    plan.append("deploy the vault stack (clone)")

    # Role bootstrap order: a fresh clone grants OWNER only OWNER_ROLE. The admin
    # chain is ADMIN -> OWNER -> ATOMIST -> {ALPHA, FUSE_MANAGER,
    # UPDATE_MARKETS_BALANCES}, so OWNER must self-grant ATOMIST before it can
    # grant the rest. ALPHA must stay a separate address from OWNER in production:
    # a compromised alpha key must not equal governance. Each grant uses Period(0)
    # (no execution delay) to keep the simulation legible; production governance
    # often puts a nonzero delay -- a timelock -- on the sensitive roles.
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, OWNER, Period(0)),
        from_=OWNER,
    )
    # FUSE_MANAGER can install fuses, and a malicious fuse can drain the vault --
    # it is as sensitive as ATOMIST. Colocating it on the owner is fine; delegate
    # it only to an equally-protected key (multisig/timelock), never an online one.
    sim.add_call(
        call=access_manager.grant_role(Roles.FUSE_MANAGER_ROLE, OWNER, Period(0)),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ALPHA, Period(0)),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(
            Roles.UPDATE_MARKETS_BALANCES_ROLE, ALPHA, Period(0)
        ),
        from_=OWNER,
    )
    plan.append("grant roles: ATOMIST, FUSE_MANAGER, ALPHA, UPDATE_MARKETS_BALANCES")

    # Wire the Euler market: all four functional fuses, the balance fuse, and the
    # two narrow substrates. Installing the fuses is separate from granting the
    # substrates -- a fuse can be installed yet do nothing until its (vault,
    # capability) substrate is granted.
    sim.add_call(
        call=plasma_vault.add_fuses(
            [
                EULER_SUPPLY_FUSE,
                EULER_COLLATERAL_FUSE,
                EULER_CONTROLLER_FUSE,
                EULER_BORROW_FUSE,
            ]
        ),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.add_balance_fuse(EULER_MARKET, EULER_BALANCE_FUSE),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.grant_market_substrates(EULER_MARKET, market_substrates()),
        from_=OWNER,
    )
    plan.append(
        "install 4 Euler fuses + balance fuse; grant cbETH-collateral & WETH-borrow"
    )

    # Read the granted substrate set back so we can assert it is EXACTLY our two.
    sim.observe("granted_substrates", plasma_vault.get_market_substrates(EULER_MARKET))
    plan.append("read the granted substrate set back")

    # Oracle coverage: the vault must be able to price every tracked asset. A fresh
    # vault's price manager delegates to the global middleware for assets without
    # an override, so these read the effective prices.
    sim.observe("cbeth_price", price_manager.get_asset_price(BASE_CBETH))
    sim.observe("weth_price", price_manager.get_asset_price(BASE_WETH))
    plan.append("verify oracle coverage for cbETH and WETH")

    # Fund the vault with cbETH collateral. SIMULATION ONLY: an impersonated
    # transfer from a whale (the simulator disables validation), with NO
    # production equivalent -- a real vault receives its assets from depositors,
    # not by moving someone else's tokens.
    sim.add_call(
        call=cbeth.transfer(preview.plasma_vault, COLLATERAL_FUND_AMOUNT),
        from_=BASE_CBETH_WHALE,
    )
    sim.observe("vault_cbeth_funded", cbeth.balance_of(preview.plasma_vault))
    plan.append("fund the vault with cbETH collateral")

    # ── Credit lifecycle, driven by the alpha via PlasmaVault.execute(...). ──

    # Supply cbETH into the ecbETH eVault (a plain deposit; not yet collateral).
    sim.execute([supply_collateral_action(COLLATERAL_SUPPLY_AMOUNT)])
    plan.append("supply cbETH into the ecbETH eVault")

    # Enable cbETH as collateral and eWETH as the borrow controller, in one batch.
    sim.execute([enable_collateral_action(), enable_controller_action()])
    plan.append("enable cbETH collateral + WETH controller")

    # Borrow WETH against the cbETH collateral; debt is now open.
    sim.execute([borrow_action(BORROW_AMOUNT)])
    sim.observe("debt_after_borrow", _evault_debt_of(ctx, EVAULT_WETH, euler_account))
    plan.append("borrow 1 WETH against the cbETH collateral")

    # Re-value the market while the position is open so the balance fuse's
    # collateral-minus-debt valuation surfaces into the vault's stored NAV.
    sim.add_call(call=plasma_vault.update_markets_balances([EULER_MARKET]), from_=ALPHA)
    sim.observe("euler_value_open", plasma_vault.total_assets_in_market(EULER_MARKET))
    plan.append("re-value the open Euler position")

    # Repay the full debt. MAX_UINT256 caps at the outstanding amount; with no time
    # advance in this run, interest is negligible and the borrowed WETH covers it.
    # (Advancing time would accrue interest, requiring a small extra WETH buffer.)
    sim.execute([repay_action(MAX_UINT256)])
    sim.observe("debt_after_repay", _evault_debt_of(ctx, EVAULT_WETH, euler_account))
    plan.append("repay the full WETH debt")

    # Unwind in reverse of setup: disable the WETH controller, then the cbETH
    # collateral. The eVault only lets an account release it as controller once
    # the debt to it is zero -- which is why the repay above must come first.
    # (With debt cleared the EVC would accept the two disables in either order,
    # since its account-status check passes at zero debt; tearing the controller
    # down first is the habit that stays correct for a partial unwind.)
    sim.execute([disable_controller_action(), disable_collateral_action()])
    plan.append("unwind: disable controller, then collateral")

    # Withdraw the cbETH back to the vault -- the reverse of the initial supply --
    # to fully exit the position, then re-value the market so the exit surfaces.
    sim.execute([withdraw_collateral_action(MAX_UINT256)])
    sim.add_call(call=plasma_vault.update_markets_balances([EULER_MARKET]), from_=ALPHA)
    sim.observe("euler_value_closed", plasma_vault.total_assets_in_market(EULER_MARKET))
    sim.observe("vault_cbeth_closed", cbeth.balance_of(preview.plasma_vault))
    plan.append("withdraw cbETH; the Euler position is fully exited")

    log.info("transaction plan:")
    for i, step in enumerate(plan, start=1):
        log.info("  %d. %s", i, step)

    result = sim.run()
    _assert_all_success(result)

    # ── Simulation done. Everything above only *described* the batch; sim.run()
    # is the single eth_simulateV1 call that executed it. From here we read the
    # recorded observations back and verify the outcome, failing loud on any
    # mismatch. ─────────────────────────────────────────────────────────────
    cbeth_price = result.get("cbeth_price")
    weth_price = result.get("weth_price")
    _check(cbeth_price.amount > 0, "cbETH has no oracle price -- vault cannot value it")
    _check(weth_price.amount > 0, "WETH has no oracle price -- vault cannot value it")

    # No unrelated permissions: the granted set is EXACTLY the two substrates we
    # built, and nothing else. Compare as sets (grant order is not guaranteed).
    granted = {bytes(s) for s in result.get("granted_substrates")}
    _check(
        granted == {bytes(s) for s in market_substrates()},
        f"unexpected market substrates granted: {sorted(s.hex() for s in granted)}",
    )

    _check(
        result.get("vault_cbeth_funded") >= COLLATERAL_FUND_AMOUNT,
        "vault did not receive the cbETH collateral",
    )
    # borrow opened debt; repay cleared it -- read straight from the eWETH eVault.
    debt_open = result.get("debt_after_borrow")
    _check(debt_open >= BORROW_AMOUNT, f"borrow did not open debt: {debt_open}")
    _check(
        result.get("debt_after_repay") == 0,
        f"repay did not clear debt: {result.get('debt_after_repay')}",
    )
    # The balance fuse nets the debt against the collateral. The open position is
    # worth the supplied cbETH MINUS the WETH debt, in the vault's WETH terms. To
    # prove the debt was actually subtracted, value the collateral from the two
    # oracle prices and pin the GAP between that and the position to about one
    # debt: too narrow a gap means the debt was ignored (a bare "> 0" or "< value"
    # would miss it, since the collateral valuation rounds down), too wide means it
    # was double-counted. The band is +/-50% of one debt to absorb rounding.
    euler_value_open = result.get("euler_value_open")
    collateral_value = (
        COLLATERAL_SUPPLY_AMOUNT * cbeth_price.amount // weth_price.amount
    )
    debt_gap = collateral_value - euler_value_open
    _check(
        BORROW_AMOUNT // 2 <= debt_gap <= 3 * BORROW_AMOUNT // 2,
        f"position {euler_value_open} vs collateral {collateral_value}: gap "
        f"{debt_gap} is not ~{BORROW_AMOUNT} (the netted debt)",
    )

    # Fully exited after the withdraw: the Euler market values to dust, and the
    # supplied cbETH is back on the vault (the ~2 cbETH idle buffer plus the ~10
    # withdrawn -> roughly the funded amount, minus ERC4626 round-down dust).
    euler_value_closed = result.get("euler_value_closed")
    _check(
        euler_value_closed <= EULER_EXIT_DUST,
        f"Euler position not fully exited: market value {euler_value_closed}",
    )
    _check(
        result.get("vault_cbeth_closed") >= COLLATERAL_FUND_AMOUNT - EULER_EXIT_DUST,
        f"withdrawn cbETH not returned to the vault: {result.get('vault_cbeth_closed')}",
    )

    log.info(
        "OK -- cbETH price=%s WETH price=%s, debt %s -> %s, "
        "position value %s -> %s (exited), gas_used=%s",
        cbeth_price.amount,
        weth_price.amount,
        debt_open,
        result.get("debt_after_repay"),
        euler_value_open,
        euler_value_closed,
        result.gas_used,
    )
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_simulation(_connected_web3())


if __name__ == "__main__":
    main()
