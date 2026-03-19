import logging
import os

import pytest
from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from constants import ANVIL_WALLET, BASE_UNIVERSAL_SWAP_FUSE
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, PlasmaVault, AccessManager, ERC20
from ipor_fusion.fuses import UniversalTokenSwapperFuse
from ipor_fusion.addresses import BASE_USDC, BASE_WETH

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

fork_url = os.environ["BASE_PROVIDER_URL"]

@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url) as a:
        yield a


def test_should_swap_on_base(anvil):
    anvil.reset_fork(24383840)

    user_account = Web3.to_checksum_address(
        "0x17548bc38669D3D6590C861E505716245b4598bB"
    )
    vault_address = Web3.to_checksum_address(
        "0x55d8d6e5F17F153f3250b229D5AAc9437e908a77"
    )
    cbbtc_address = Web3.to_checksum_address(
        "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
    )

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    # Grant roles
    atomists = access_manager.atomists()
    forked_ctx.prank(atomists[0])
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    forked_ctx.prank(ANVIL_WALLET)

    amount = 1_00000000  # 1 cbBTC

    # Approve and deposit
    forked_ctx.prank(user_account)
    ERC20(forked_ctx, cbbtc_address).approve(plasma_vault.address, amount)
    plasma_vault.deposit(amount, user_account)

    forked_ctx.prank(ANVIL_WALLET)

    # Swap configuration
    uniswap_v3_universal_router = Web3.to_checksum_address(
        "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
    )
    targets = [cbbtc_address, uniswap_v3_universal_router]

    # Transfer cbBTC to router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"],
        [uniswap_v3_universal_router, int(amount / 2)],
    )
    function_call_0 = function_selector_0 + function_args_0

    # Swap path: cbBTC -> USDC with 0.05% fee
    path = encode_packed(
        ["address", "uint24", "address"],
        [cbbtc_address, 500, BASE_USDC],
    )

    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                int(amount / 2),
                0,
                path,
                False,
            ],
        )
    ]

    function_selector_1 = function_signature_to_4byte_selector("execute(bytes,bytes[])")
    function_args_1 = encode(
        ["bytes", "bytes[]"],
        [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs],
    )
    function_call_1 = function_selector_1 + function_args_1

    data = [function_call_0, function_call_1]
    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)
    swap = universal.swap(
        token_in=cbbtc_address,
        token_out=BASE_USDC,
        amount_in=int(amount / 2),
        targets=targets,
        data=data,
    )

    cbbtc_balance_before = ERC20(forked_ctx, cbbtc_address).balance_of(vault_address)
    usdc_balance_before = ERC20(forked_ctx, BASE_USDC).balance_of(vault_address)

    plasma_vault.execute([swap])

    cbbtc_balance_after = ERC20(forked_ctx, cbbtc_address).balance_of(vault_address)
    usdc_balance_after = ERC20(forked_ctx, BASE_USDC).balance_of(vault_address)

    assert cbbtc_balance_before >= amount
    assert usdc_balance_before < 1_000000
    assert cbbtc_balance_after >= (amount / 2)
    assert usdc_balance_after > 45000_000000


def test_should_swap_weth_to_pepe_on_base(anvil):
    anvil.reset_fork(25894923)

    vault_address = Web3.to_checksum_address(
        "0x85b7927B6d721638b575972111F4CE6DaCb7D33C"
    )
    alpha_address = Web3.to_checksum_address(
        "0xd16A8D5bD6B2cD5499bD55239bc980F09991b5fd"
    )
    pepe_address = Web3.to_checksum_address(
        "0x52b492a33E447Cdb854c7FC19F1e57E8BfA1777D"
    )

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    forked_ctx.prank(alpha_address)

    amount = ERC20(forked_ctx, BASE_WETH).balance_of(vault_address)

    uniswap_v3_universal_router = Web3.to_checksum_address(
        "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
    )
    targets = [BASE_WETH, uniswap_v3_universal_router]

    # Transfer WETH to router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"],
        [uniswap_v3_universal_router, amount],
    )
    function_call_0 = function_selector_0 + function_args_0

    # Swap path: WETH -> PEPE with 1% fee
    path = encode_packed(
        ["address", "uint24", "address"],
        [BASE_WETH, 10000, pepe_address],
    )

    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                amount,
                0,
                path,
                False,
            ],
        )
    ]

    function_selector_1 = function_signature_to_4byte_selector("execute(bytes,bytes[])")
    function_args_1 = encode(
        ["bytes", "bytes[]"],
        [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs],
    )
    function_call_1 = function_selector_1 + function_args_1

    data = [function_call_0, function_call_1]
    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)
    swap = universal.swap(
        token_in=BASE_WETH,
        token_out=pepe_address,
        amount_in=amount,
        targets=targets,
        data=data,
    )

    weth_balance_before = ERC20(forked_ctx, BASE_WETH).balance_of(vault_address)
    pepe_balance_before = ERC20(forked_ctx, pepe_address).balance_of(vault_address)

    plasma_vault.execute([swap])

    weth_balance_after = ERC20(forked_ctx, BASE_WETH).balance_of(vault_address)
    pepe_balance_after = ERC20(forked_ctx, pepe_address).balance_of(vault_address)

    assert weth_balance_before == 5000000000000000
    assert weth_balance_after == 0
    assert pepe_balance_before == 0
    assert pepe_balance_after == int(174743647_876992680486051295)
