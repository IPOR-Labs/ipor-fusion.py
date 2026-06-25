"""Shared fixtures/helpers for the EulerSwap v2 Base fork simulate tests.

Centralises the deployed Base addresses, the off-chain hook-flag salt miner,
view-call builders and the clone→configure→fund→deploy setup so the deploy and
reconfigure tests stay focused on the behaviour they assert.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from addresses import BASE_WETH
from constants import ANVIL_WALLET
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    AccessManager,
    ERC20,
    Roles,
    VaultSimulator,
)
from ipor_fusion.core.contract import Call
from ipor_fusion.core.fusion_factory import FusionFactory
from ipor_fusion.fuses import (
    EulerV2SupplyFuse,
    EulerV2SwapDeployFuse,
    EulerSwapStaticParams,
    EulerSwapDynamicParams,
    EulerSwapInitialState,
    FuseAction,
    euler_substrate,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, MarketId, Period

ZERO_ADDRESS = Web3.to_checksum_address("0x0000000000000000000000000000000000000000")

# --- deployed BASE addresses (ipor-abi mainnet-base-fusion) ------------------
BASE_FUSION_FACTORY = Web3.to_checksum_address(
    "0x1455717668fA96534f675856347A973fA907e922"
)
EULER_V2_EVC = Web3.to_checksum_address("0x5301c7dD20bD945D2013b48ed0DEE3A284ca8989")
EULERSWAP_FACTORY = Web3.to_checksum_address(
    "0x6C5f4c239ceD289447737EAB8eEA64523bd9c05E"
)
EULERSWAP_REGISTRY = Web3.to_checksum_address(
    "0x35D410A5052c7362eCdD72cFb65651A71adFaf61"
)
EULER_SWAP_DEPLOY_FUSE = Web3.to_checksum_address(
    "0x0Db8d3fD81900FF95ca25D7bc30a4DA1b289E670"
)
EULER_SWAP_RECONFIGURE_FUSE = Web3.to_checksum_address(
    "0x11187bac7f13475F4ACe42FCeA72A4f3b9FddBF1"
)
EULER_SWAP_REGISTRY_FUSE = Web3.to_checksum_address(
    "0x49b9a2f243ab72a51cE78C0215F4FF9708abF923"
)
EULER_BATCH_FUSE = Web3.to_checksum_address(
    "0x60CE35e58f6CEd1538c16A15FF7fF75B0538898F"
)
EULER_SUPPLY_FUSE = Web3.to_checksum_address(
    "0x598326fcEDE2C1B8E9023a20C18FFf6Dea5306A4"
)
EULER_COLLATERAL_FUSE = Web3.to_checksum_address(
    "0x12c479f8aB53D4884fc76F803dD24eb8B6D17a94"
)
EULER_CONTROLLER_FUSE = Web3.to_checksum_address(
    "0x108c8cFB9e00681FfA1fa3b654937E8b3BCd2E64"
)
EULER_BORROW_FUSE = Web3.to_checksum_address(
    "0x906496F0D4C733275F892b1a6fC92eD56639B379"
)
EULER_BALANCE_FUSE = Web3.to_checksum_address(
    "0xF8A6AA09bB55f2319113b0DA88883F392e66A5fa"
)
MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")

# Base Euler eVaults (read from a live EulerSwap pool): ecbETH-1 / eWETH-1.
EVAULT_CBETH = Web3.to_checksum_address("0x358f25F82644eaBb441d0df4AF8746614fb9ea49")
EVAULT_WETH = Web3.to_checksum_address("0x859160DB5841E5cfB8D3f144C6b3381A85A4b410")

# Underlying assets traded by the pool.
BASE_CBETH = Web3.to_checksum_address("0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22")
# Morpho on Base holds ample cbETH + WETH — used to fund the vault collateral
# via impersonated transfers.
COLLATERAL_WHALE = Web3.to_checksum_address(
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
)
COLLATERAL_FUND_AMOUNT = Amount(12 * 10**18)
SUPPLY_AMOUNT = Amount(10 * 10**18)

# Address we control as the vault's initial owner (validation=False → impersonable).
OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")

SUB_ACCOUNT = 0x01
EULER_MARKET = MarketId(IporFusionMarkets.EULER_V2)

# Uniswap-v4 hook-flag constraint on the pool address (EulerSwap v2):
# low 14 bits must equal exactly beforeInitialize|beforeAddLiquidity|beforeSwap|
# beforeDonate|beforeSwapReturnsDelta.
_HOOK_FLAG_MASK = (1 << 14) - 1
_HOOK_FLAG_REQUIRED = (1 << 13) | (1 << 11) | (1 << 7) | (1 << 5) | (1 << 3)  # 0x28A8

_STATIC_PARAMS_TYPE = "(address,address,address,address,address,address)"
_DYNAMIC_PARAMS_TYPE = (
    "(uint112,uint112,uint112,uint112,uint80,uint80,uint64,uint64,"
    "uint64,uint64,uint40,uint8,address)"
)


def euler_account(plasma_vault: ChecksumAddress, sub_account: int) -> ChecksumAddress:
    """`eulerAccount = plasmaVault XOR subAccount` (EulerFuseLib.generateSubAccountAddress)."""
    return Web3.to_checksum_address(f"0x{int(plasma_vault, 16) ^ sub_account:040x}")


def clone_args() -> dict:
    return {
        "asset_name": "IPOR WETH EulerSwap (e2e)",
        "asset_symbol": "ipWETHe2e",
        "underlying_token": BASE_WETH,
        "redemption_delay_seconds": 0,
        "owner": OWNER,
        "dao_fee_package_index": 0,
    }


def static_params(euler_account_: ChecksumAddress) -> EulerSwapStaticParams:
    """JIT pool mirroring the Solidity EulerV2SwapForkTest: cbETH is asset0, WETH
    is asset1 (cbETH 0x2Ae3.. < WETH 0x4200..); both eVaults supply and borrow."""
    return EulerSwapStaticParams(
        supply_vault0=EVAULT_CBETH,
        supply_vault1=EVAULT_WETH,
        borrow_vault0=EVAULT_CBETH,
        borrow_vault1=EVAULT_WETH,
        euler_account=euler_account_,
        fee_recipient=ZERO_ADDRESS,
    )


def dynamic_params(
    *, fee: int = 3 * 10**15, equilibrium_reserve: int = 5 * 10**18
) -> EulerSwapDynamicParams:
    return EulerSwapDynamicParams(
        equilibrium_reserve0=equilibrium_reserve,
        equilibrium_reserve1=equilibrium_reserve,
        min_reserve0=0,
        min_reserve1=0,
        price_x=1_133_090_000_000_000_000,  # cbETH/USD ÷ ETH/USD
        price_y=10**18,
        concentration_x=5 * 10**17,
        concentration_y=5 * 10**17,
        fee0=fee,
        fee1=fee,
        expiration=0,
        swap_hooked_operations=0,
        swap_hook=ZERO_ADDRESS,
    )


def initial_state(reserve: int = 5 * 10**18) -> EulerSwapInitialState:
    return EulerSwapInitialState(reserve0=reserve, reserve1=reserve)


def mine_salt(
    web3: Web3,
    sp: EulerSwapStaticParams,
    block: int,
    batch: int = 1000,
    max_batches: int = 200,
) -> tuple[bytes, ChecksumAddress]:
    """Mine a CREATE2 salt whose predicted pool address satisfies the hook flags.

    Batches `factory.computePoolAddress(staticParams, salt)` through Multicall3
    so the ~16k expected probes take a handful of eth_calls, not 16k.
    """
    compute_selector = function_signature_to_4byte_selector(
        f"computePoolAddress({_STATIC_PARAMS_TYPE},bytes32)"
    )
    aggregate_selector = function_signature_to_4byte_selector(
        "aggregate3((address,bool,bytes)[])"
    )
    sp_tuple = sp.to_tuple()

    def compute_calldata(salt_int: int) -> bytes:
        return compute_selector + encode(
            [_STATIC_PARAMS_TYPE, "bytes32"], [sp_tuple, salt_int.to_bytes(32, "big")]
        )

    for chunk in range(max_batches):
        calls = [
            (EULERSWAP_FACTORY, True, compute_calldata(chunk * batch + i))
            for i in range(batch)
        ]
        payload = aggregate_selector + encode(["(address,bool,bytes)[]"], [calls])
        raw = web3.eth.call({"to": MULTICALL3, "data": payload}, block_identifier=block)
        (results,) = decode(["(bool,bytes)[]"], raw)
        for i, (ok, ret) in enumerate(results):
            if not ok:
                continue
            (pool,) = decode(["address"], ret)
            if (int(pool, 16) & _HOOK_FLAG_MASK) == _HOOK_FLAG_REQUIRED:
                salt = chunk * batch + i
                return salt.to_bytes(32, "big"), Web3.to_checksum_address(pool)
    raise AssertionError("salt mining failed (no hook-flag match)")


def evc_is_operator_authorized(
    ctx: Web3Context, account: ChecksumAddress, operator: ChecksumAddress
) -> Call[bool]:
    data = function_signature_to_4byte_selector(
        "isAccountOperatorAuthorized(address,address)"
    ) + encode(["address", "address"], [account, operator])
    return Call(to=EULER_V2_EVC, data=data, output_types=["bool"], ctx=ctx)


def factory_deployed_pools(ctx: Web3Context, pool: ChecksumAddress) -> Call[bool]:
    data = function_signature_to_4byte_selector("deployedPools(address)") + encode(
        ["address"], [pool]
    )
    return Call(to=EULERSWAP_FACTORY, data=data, output_types=["bool"], ctx=ctx)


def pool_dynamic_params(ctx: Web3Context, pool: ChecksumAddress) -> Call[tuple]:
    """Read the pool's current mutable curve / fee configuration."""
    data = function_signature_to_4byte_selector("getDynamicParams()")
    return Call(to=pool, data=data, output_types=[_DYNAMIC_PARAMS_TYPE], ctx=ctx)


def registry_pool_by_euler_account(
    ctx: Web3Context, account: ChecksumAddress
) -> Call[ChecksumAddress]:
    """Read the EulerSwap registry's registered pool for an EVC account (0x0 if none)."""
    data = function_signature_to_4byte_selector("poolByEulerAccount(address)") + encode(
        ["address"], [account]
    )
    return Call(to=EULERSWAP_REGISTRY, data=data, output_types=["address"], ctx=ctx)


def evault_debt_of(
    ctx: Web3Context, euler_vault: ChecksumAddress, account: ChecksumAddress
) -> Call[int]:
    """Read an Euler eVault's outstanding debt for an account (underlying units)."""
    data = function_signature_to_4byte_selector("debtOf(address)") + encode(
        ["address"], [account]
    )
    return Call(to=euler_vault, data=data, output_types=["uint256"], ctx=ctx)


@dataclass(frozen=True)
class EulerDeployPlan:
    """Everything a test needs to drive (and reconfigure) a freshly-deployed pool."""

    predicted_pool: ChecksumAddress
    euler_account: ChecksumAddress
    static_params: EulerSwapStaticParams
    supply_actions: list[FuseAction]
    deploy_action: FuseAction


def queue_setup(
    sim: VaultSimulator,
    ctx: Web3Context,
    factory: FusionFactory,
    vault_address: ChecksumAddress,
    access_manager_address: ChecksumAddress,
    web3: Web3,
    block: int,
) -> EulerDeployPlan:
    """Queue clone → governance → fund → wire EULER_V2 market, and return the
    supply + deploy actions (NOT yet executed) so the caller controls execution
    order and observations.
    """
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, access_manager_address)
    deploy_fuse = EulerV2SwapDeployFuse(EULER_SWAP_DEPLOY_FUSE)
    supply_fuse = EulerV2SupplyFuse(EULER_SUPPLY_FUSE)
    cbeth = ERC20(ctx, BASE_CBETH)
    weth = ERC20(ctx, BASE_WETH)

    account = euler_account(vault_address, SUB_ACCOUNT)
    sp = static_params(account)
    salt, predicted_pool = mine_salt(web3, sp, block)

    deploy_action = deploy_fuse.deploy(
        static_params=sp,
        dynamic_params=dynamic_params(),
        initial_state=initial_state(),
        salt=salt,
        predicted_pool=predicted_pool,
        sub_account=SUB_ACCOUNT,
    )
    supply_actions = [
        supply_fuse.supply(
            euler_vault=EVAULT_CBETH, max_amount=SUPPLY_AMOUNT, sub_account=SUB_ACCOUNT
        ),
        supply_fuse.supply(
            euler_vault=EVAULT_WETH, max_amount=SUPPLY_AMOUNT, sub_account=SUB_ACCOUNT
        ),
    ]

    # Create the vault FIRST so the clone index matches the preview.
    sim.add_call(call=factory.clone(**clone_args()), from_=OWNER, label="clone")

    # Governance bootstrap (OWNER starts with OWNER_ROLE; self-grants ATOMIST).
    no_delay = Period(0)
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, OWNER, no_delay),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.FUSE_MANAGER_ROLE, OWNER, no_delay),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, no_delay),
        from_=OWNER,
    )

    # Fund the vault with collateral on both sides (impersonated whale transfers).
    sim.add_call(
        call=cbeth.transfer(vault_address, COLLATERAL_FUND_AMOUNT),
        from_=COLLATERAL_WHALE,
    )
    sim.add_call(
        call=weth.transfer(vault_address, COLLATERAL_FUND_AMOUNT),
        from_=COLLATERAL_WHALE,
    )

    # Wire the EULER_V2 market: supply/deploy/reconfigure/registry fuses, balance fuse, substrates.
    sim.add_call(
        call=plasma_vault.add_fuses(
            [
                EULER_SUPPLY_FUSE,
                EULER_COLLATERAL_FUSE,
                EULER_CONTROLLER_FUSE,
                EULER_BORROW_FUSE,
                EULER_SWAP_DEPLOY_FUSE,
                EULER_SWAP_RECONFIGURE_FUSE,
                EULER_SWAP_REGISTRY_FUSE,
            ]
        ),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.add_balance_fuse(EULER_MARKET, EULER_BALANCE_FUSE),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.grant_market_substrates(
            EULER_MARKET,
            [
                euler_substrate(
                    euler_vault=EVAULT_CBETH,
                    is_collateral=True,
                    can_borrow=True,
                    sub_account=SUB_ACCOUNT,
                ),
                euler_substrate(
                    euler_vault=EVAULT_WETH,
                    is_collateral=True,
                    can_borrow=True,
                    sub_account=SUB_ACCOUNT,
                ),
            ],
        ),
        from_=OWNER,
    )

    return EulerDeployPlan(
        predicted_pool=predicted_pool,
        euler_account=account,
        static_params=sp,
        supply_actions=supply_actions,
        deploy_action=deploy_action,
    )
