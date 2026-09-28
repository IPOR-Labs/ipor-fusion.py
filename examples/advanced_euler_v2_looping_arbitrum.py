"""Build a Fusion vault on Arbitrum and open a leveraged Euler V2 WBTC position.

The one-shot flow opens a position using WBTC as collateral and USDC as debt.
Sub-account 1 isolates it from any Euler position on the vault's primary account:

1. clone a WBTC-denominated vault and bootstrap its roles;
2. install the Euler supply, collateral, controller and borrow fuses;
3. install the Morpho flash-loan and Universal Token Swapper V2 fuses;
4. grant narrowly scoped Euler, flash-loan and swapper substrates;
5. register the deployed Morpho callback handler;
6. fund the vault with 0.10 WBTC and enable Euler collateral/controller;
7. flash-borrow 0.05 WBTC, supply 0.15 WBTC to Euler, borrow 4,300 USDC,
   swap it to WBTC and repay Morpho atomically;
8. verify the resulting Euler collateral, debt, token dust and vault NAV.

Everything is simulated through one ``eth_simulateV1`` request at a pinned
Arbitrum block. Nothing is signed or broadcast.

Run it with an archive RPC that supports ``eth_simulateV1``:

    export ARBITRUM_PROVIDER_URL="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
    uv run python examples/advanced_euler_v2_looping_arbitrum.py
"""

from __future__ import annotations

import logging
import os
import sys

from eth_abi import encode
from eth_abi.packed import encode_packed
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
    MorphoFlashLoanFuse,
    UniversalTokenSwapperAbi,
    UniversalTokenSwapperFuse,
    UniversalTokenSwapperSubstrates,
    euler_substrate,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId, Period

log = logging.getLogger("advanced_euler_v2_looping_arbitrum")

# ── Live IPOR / Arbitrum / Euler infrastructure (do NOT change) ──────────────
# IPOR addresses come from the official registry:
# https://github.com/IPOR-Labs/ipor-abi/blob/main/mainnet/
# mainnet-arbitrum-fusion/addresses.json

ARBITRUM_CHAIN_ID = ChainId(42161)
EULER_MARKET = MarketId(IporFusionMarkets.EULER_V2)
SWAPPER_MARKET = MarketId(IporFusionMarkets.UNIVERSAL_TOKEN_SWAPPER_V2)
MORPHO_FLASH_MARKET = MarketId(IporFusionMarkets.MORPHO_FLASH_LOAN)

ARBITRUM_FUSION_FACTORY = Web3.to_checksum_address(
    "0x134fCAce7a2C7Ef3dF2479B62f03ddabAEa922d5"
)
EULER_SUPPLY_FUSE = Web3.to_checksum_address(
    "0x920f6c81666877490A8D6dcFEFd85d151Ef04B7d"
)
EULER_COLLATERAL_FUSE = Web3.to_checksum_address(
    "0x6D35934cB431B36338fF88641BEBF540e826bd7D"
)
EULER_CONTROLLER_FUSE = Web3.to_checksum_address(
    "0x2A02Ae392816441E804aa9eCcf5c0333911Dc989"
)
EULER_BORROW_FUSE = Web3.to_checksum_address(
    "0x10eb9B247e7Ce00F3290F66d8B5E83BfD311375F"
)
EULER_BALANCE_FUSE = Web3.to_checksum_address(
    "0xF7dF7b8eB5B0c6d02b1b8FEC769dFE65D9a27E3b"
)
MORPHO_FLASH_LOAN_FUSE = Web3.to_checksum_address(
    "0xeA36D6478CAAE9a5CDbEA0814bDe98533320d742"
)
MORPHO_FLASH_BALANCE_FUSE = Web3.to_checksum_address(
    "0xAD5EdaEc3cd9a25C8Cc42284dDeF57dCFF40341d"
)
UNIVERSAL_SWAPPER_FUSE_V2 = Web3.to_checksum_address(
    "0x799b979A2377268ac2D5E43247cbd6F797229Ef3"
)
UNIVERSAL_SWAPPER_BALANCE_FUSE_V2 = Web3.to_checksum_address(
    "0x69199E8360b3Baa536Cda2bd71dFFb30F79fBeF7"
)
MORPHO_CALLBACK_HANDLER = Web3.to_checksum_address(
    "0x62E0A527fa7990183C4225f4565B2BfC925CD46e"
)
MORPHO = Web3.to_checksum_address("0x6c247b1F6182318877311737BaC0844bAa518F5e")

# Euler eVaults come from Euler's official Arbitrum Cluster deployment manifest.
# The RPC-gated test also checks each eVault's ``asset()`` at PINNED_BLOCK.
EVAULT_USDC = Web3.to_checksum_address("0x0a1eCC5Fe8C9be3C809844fcBe615B46A869b899")
EVAULT_WBTC = Web3.to_checksum_address("0x889E1c458B2469b70aCcdfb5B59726dC1668896C")

ARBITRUM_USDC = Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
ARBITRUM_WBTC = Web3.to_checksum_address("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f")

# Official Uniswap Universal Router deployment on Arbitrum. The 500-fee pool is
# verified on-chain in the RPC-gated test and is the deepest USDC/WBTC V3 pool at
# the pinned block.
UNISWAP_UNIVERSAL_ROUTER = Web3.to_checksum_address(
    "0x5E325eDA8064b456f4781070C0738d849c824258"
)
UNISWAP_POOL_FEE = 500
UNISWAP_USDC_WBTC_POOL = Web3.to_checksum_address(
    "0x0e4831319a50228b9e450861297ab92dee15b44f"
)

# ── Simulation parameters (safe to adjust) ───────────────────────────────────

OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
ALPHA = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
SUB_ACCOUNT = 0x01

INITIAL_WBTC = Amount(10_000_000)  # 0.10 WBTC, the unlevered equity
FLASH_WBTC = Amount(5_000_000)  # 0.05 WBTC, repaid in the callback
SUPPLY_WBTC = Amount(INITIAL_WBTC + FLASH_WBTC)
# Single-call borrowing is health-checked immediately, so this must fit the 85%
# LTV of the full post-supply collateral; there is no deferred BatchFuse check.
BORROW_USDC = Amount(4_300 * 10**6)
MIN_WBTC_OUT = Amount(FLASH_WBTC)
MAX_SWAP_SLIPPAGE_WAD = 2 * 10**16  # 2%

# Morpho itself holds ample WBTC at this block. Impersonating it to seed the
# fresh vault is simulation-only and leaves ample liquidity for the flash loan.
WBTC_FUNDING_SOURCE = MORPHO

PINNED_BLOCK = 509_713_615  # Arbitrum, 2026-09-28


# ── Inlined helpers ──────────────────────────────────────────────────────────


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_all_success(result: SimulationResult) -> None:
    if result.all_success:
        return
    failed = [(call.label, call.error) for call in result.failed_calls]
    raise AssertionError(
        f"simulation calls failed: {failed} (reason={result.revert_reason})"
    )


def _connected_web3() -> Web3:
    url = os.environ.get("ARBITRUM_PROVIDER_URL")
    if not url:
        sys.exit(
            "ARBITRUM_PROVIDER_URL is not set -- export your Arbitrum RPC URL first."
        )
    web3 = Web3(Web3.HTTPProvider(url))
    if not web3.is_connected():
        sys.exit("cannot reach ARBITRUM_PROVIDER_URL -- check the endpoint.")
    if not is_simulate_v1_supported(web3):
        sys.exit(
            "the provider does not implement eth_simulateV1 -- use an archive node."
        )
    return web3


def _address_substrate(address: ChecksumAddress) -> bytes:
    return bytes(12) + bytes.fromhex(address[2:])


def _euler_account(plasma_vault: ChecksumAddress) -> ChecksumAddress:
    return Web3.to_checksum_address(f"0x{(int(plasma_vault, 16) ^ SUB_ACCOUNT):040x}")


def _debt_of(
    ctx: Web3Context,
    contract: ChecksumAddress,
    account: ChecksumAddress,
) -> Call[int]:
    data = function_signature_to_4byte_selector("debtOf(address)") + encode(
        ["address"], [account]
    )
    return Call(to=contract, data=data, output_types=["uint256"], ctx=ctx)


# ── Pure builders (no chain access) ──────────────────────────────────────────


def clone_args() -> dict:
    return {
        "asset_name": "IPOR WBTC Euler Loop Vault (example)",
        "asset_symbol": "ipWBTCel",
        "underlying_token": ARBITRUM_WBTC,
        "redemption_delay_seconds": 0,
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def unsigned_clone_calldata() -> bytes:
    return FusionFactory.encoder(ARBITRUM_FUSION_FACTORY).clone(**clone_args()).calldata


def euler_market_substrates() -> list[bytes]:
    """Grant WBTC collateral-only and USDC borrow-only on one sub-account."""
    return [
        euler_substrate(
            euler_vault=EVAULT_WBTC,
            is_collateral=True,
            can_borrow=False,
            sub_account=SUB_ACCOUNT,
        ),
        euler_substrate(
            euler_vault=EVAULT_USDC,
            is_collateral=False,
            can_borrow=True,
            sub_account=SUB_ACCOUNT,
        ),
    ]


def swapper_market_substrates() -> list[bytes]:
    """Allow only this pair, the two required call targets and 2% slippage."""
    # ERC20_VAULT_BALANCE is unnecessary here: WBTC is the underlying and the
    # exact-input swap consumes every borrowed USDC. The final assertion pins it.
    return [
        UniversalTokenSwapperSubstrates.token(ARBITRUM_USDC),
        UniversalTokenSwapperSubstrates.token(ARBITRUM_WBTC),
        UniversalTokenSwapperSubstrates.target(ARBITRUM_USDC),
        UniversalTokenSwapperSubstrates.target(UNISWAP_UNIVERSAL_ROUTER),
        UniversalTokenSwapperSubstrates.slippage(MAX_SWAP_SLIPPAGE_WAD),
    ]


def morpho_callback_selector() -> bytes:
    return function_signature_to_4byte_selector("onMorphoFlashLoan(uint256,bytes)")


def enable_euler_actions() -> list[FuseAction]:
    return [
        EulerV2CollateralFuse(EULER_COLLATERAL_FUSE).enable_collateral(
            euler_vault=EVAULT_WBTC, sub_account=SUB_ACCOUNT
        ),
        EulerV2ControllerFuse(EULER_CONTROLLER_FUSE).enable_controller(
            euler_vault=EVAULT_USDC, sub_account=SUB_ACCOUNT
        ),
    ]


def build_swap_action() -> FuseAction:
    """Swap the borrowed USDC through Uniswap V3 and require flash repayment."""
    transfer_data = (
        ERC20.encoder(ARBITRUM_USDC)
        .transfer(UNISWAP_UNIVERSAL_ROUTER, BORROW_USDC)
        .calldata
    )
    path = encode_packed(
        ["address", "uint24", "address"],
        [ARBITRUM_USDC, UNISWAP_POOL_FEE, ARBITRUM_WBTC],
    )
    router_input = encode(
        ["address", "uint256", "uint256", "bytes", "bool"],
        [
            "0x0000000000000000000000000000000000000001",
            BORROW_USDC,
            MIN_WBTC_OUT,
            path,
            False,
        ],
    )
    router_data = function_signature_to_4byte_selector(
        "execute(bytes,bytes[])"
    ) + encode(["bytes", "bytes[]"], [b"\x00", [router_input]])
    return UniversalTokenSwapperFuse(
        UNIVERSAL_SWAPPER_FUSE_V2,
        abi=UniversalTokenSwapperAbi.MIN_AMOUNT_OUT,
    ).swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_WBTC,
        amount_in=BORROW_USDC,
        min_amount_out=MIN_WBTC_OUT,
        targets=[ARBITRUM_USDC, UNISWAP_UNIVERSAL_ROUTER],
        data=[transfer_data, router_data],
    )


def build_loop_action() -> FuseAction:
    """Build the complete flash-loan callback as one immutable FuseAction tree."""
    supply = EulerV2SupplyFuse(EULER_SUPPLY_FUSE).supply(
        euler_vault=EVAULT_WBTC,
        max_amount=SUPPLY_WBTC,
        sub_account=SUB_ACCOUNT,
    )
    borrow = EulerV2BorrowFuse(EULER_BORROW_FUSE).borrow(
        euler_vault=EVAULT_USDC,
        asset_amount=BORROW_USDC,
        sub_account=SUB_ACCOUNT,
    )
    return MorphoFlashLoanFuse(MORPHO_FLASH_LOAN_FUSE).flash_loan(
        asset=ARBITRUM_WBTC,
        amount=FLASH_WBTC,
        actions=[supply, borrow, build_swap_action()],
    )


# ── Simulation ───────────────────────────────────────────────────────────────


def run_simulation(web3: Web3) -> SimulationResult:
    """Build, configure and run the WBTC/USDC leveraged loop without broadcasting."""
    ctx = Web3Context(web3=web3, chain_id=ARBITRUM_CHAIN_ID, signer=OWNER)
    ctx.default_block = PINNED_BLOCK
    factory = FusionFactory(ctx, ARBITRUM_FUSION_FACTORY)
    preview = factory.clone(**clone_args()).call()

    log.info(
        "predicted vault=%s access_manager=%s price_manager=%s",
        preview.plasma_vault,
        preview.access_manager,
        preview.price_manager,
    )
    log.info("unsigned clone calldata: 0x%s", unsigned_clone_calldata().hex())

    vault = PlasmaVault(ctx, preview.plasma_vault)
    access_manager = AccessManager(ctx, preview.access_manager)
    price_manager = PriceOracleMiddlewareManager(ctx, preview.price_manager)
    wbtc = ERC20(ctx, ARBITRUM_WBTC)
    usdc = ERC20(ctx, ARBITRUM_USDC)
    euler_account = _euler_account(preview.plasma_vault)

    sim = VaultSimulator(
        web3=web3,
        vault=preview.plasma_vault,
        alpha=ALPHA,
        block=PINNED_BLOCK,
    )
    sim.add_call(call=factory.clone(**clone_args()), from_=OWNER, label="clone")

    # OWNER is governance; ALPHA is a separate online operator. A production
    # owner should be a cold multisig, never the alpha key.
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, OWNER, Period(0)),
        from_=OWNER,
    )
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

    sim.add_call(
        call=vault.add_fuses(
            [
                EULER_SUPPLY_FUSE,
                EULER_COLLATERAL_FUSE,
                EULER_CONTROLLER_FUSE,
                EULER_BORROW_FUSE,
                MORPHO_FLASH_LOAN_FUSE,
                UNIVERSAL_SWAPPER_FUSE_V2,
            ]
        ),
        from_=OWNER,
    )
    for market, balance_fuse in (
        (EULER_MARKET, EULER_BALANCE_FUSE),
        (MORPHO_FLASH_MARKET, MORPHO_FLASH_BALANCE_FUSE),
        (SWAPPER_MARKET, UNIVERSAL_SWAPPER_BALANCE_FUSE_V2),
    ):
        sim.add_call(call=vault.add_balance_fuse(market, balance_fuse), from_=OWNER)

    sim.add_call(
        call=vault.grant_market_substrates(EULER_MARKET, euler_market_substrates()),
        from_=OWNER,
    )
    sim.add_call(
        call=vault.grant_market_substrates(
            MORPHO_FLASH_MARKET, [_address_substrate(ARBITRUM_WBTC)]
        ),
        from_=OWNER,
    )
    sim.add_call(
        call=vault.grant_market_substrates(SWAPPER_MARKET, swapper_market_substrates()),
        from_=OWNER,
    )
    sim.add_call(
        call=vault.update_callback_handler(
            MORPHO_CALLBACK_HANDLER,
            MORPHO,
            morpho_callback_selector(),
        ),
        from_=OWNER,
    )
    # update_callback_handler exercises FUSE_MANAGER_ROLE, held by OWNER above.

    sim.observe("wbtc_price", price_manager.get_asset_price(ARBITRUM_WBTC))
    sim.observe("usdc_price", price_manager.get_asset_price(ARBITRUM_USDC))
    sim.observe("euler_substrates", vault.get_market_substrates(EULER_MARKET))
    sim.observe("swapper_substrates", vault.get_market_substrates(SWAPPER_MARKET))

    # Simulation-only seed. In production WBTC arrives through normal deposits.
    sim.add_call(
        call=wbtc.transfer(preview.plasma_vault, INITIAL_WBTC),
        from_=WBTC_FUNDING_SOURCE,
    )
    sim.execute(enable_euler_actions())
    sim.execute([build_loop_action()])

    sim.add_call(call=vault.update_markets_balances([EULER_MARKET]), from_=ALPHA)
    sim.observe(
        "collateral_shares",
        ERC20(ctx, EVAULT_WBTC).balance_of(euler_account),
    )
    sim.observe(
        "usdc_debt",
        _debt_of(ctx, EVAULT_USDC, euler_account),
    )
    sim.observe("vault_wbtc", wbtc.balance_of(preview.plasma_vault))
    sim.observe("vault_usdc", usdc.balance_of(preview.plasma_vault))
    sim.observe("euler_value", vault.total_assets_in_market(EULER_MARKET))
    sim.observe("total_assets", vault.total_assets())

    result = sim.run()
    _assert_all_success(result)

    _check(result.get("wbtc_price").amount > 0, "WBTC has no oracle price")
    _check(result.get("usdc_price").amount > 0, "USDC has no oracle price")
    _check(
        {bytes(value) for value in result.get("euler_substrates")}
        == {bytes(value) for value in euler_market_substrates()},
        "Euler substrates differ from the narrow WBTC/USDC grant",
    )
    _check(
        {bytes(value) for value in result.get("swapper_substrates")}
        == {bytes(value) for value in swapper_market_substrates()},
        "swapper substrates differ from the narrow pair/target grant",
    )
    _check(result.get("collateral_shares") > 0, "Euler collateral was not supplied")
    _check(
        result.get("usdc_debt") >= BORROW_USDC,
        f"Euler USDC debt was not opened: {result.get('usdc_debt')}",
    )
    _check(result.get("vault_usdc") == 0, "borrowed USDC was not fully swapped")
    _check(
        result.get("vault_wbtc") < Amount(1_000_000),
        f"unexpected WBTC residue after flash repayment: {result.get('vault_wbtc')}",
    )
    _check(result.get("euler_value") > 0, "Euler net position has no value")
    _check(
        INITIAL_WBTC * 98 // 100 <= result.get("total_assets") <= INITIAL_WBTC,
        f"loop NAV moved outside the 2% safety band: {result.get('total_assets')}",
    )

    log.info(
        "OK -- collateral shares=%s, USDC debt=%s, idle WBTC=%s, NAV=%s, gas=%s",
        result.get("collateral_shares"),
        result.get("usdc_debt"),
        result.get("vault_wbtc"),
        result.get("total_assets"),
        result.gas_used,
    )
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_simulation(_connected_web3())


if __name__ == "__main__":
    main()
