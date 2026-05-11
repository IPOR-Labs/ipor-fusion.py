"""Swap tests on Base — driven by `eth_simulateV1` instead of anvil.

Mirrors `test_swap_on_base.py`. Linear swap flows (cbBTC→USDC, WETH→PEPE) lend
themselves to eth_simulateV1 batching: setup, execute, observe — one RPC call.
"""

from __future__ import annotations

import logging

from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from _simulate import assert_all_success
from addresses import BASE_USDC, BASE_WETH
from constants import ANVIL_WALLET, BASE_UNIVERSAL_SWAP_FUSE
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    AccessManager,
    ERC20,
    Roles,
    VaultSimulator,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.fuses import UniversalTokenSwapperFuse
from ipor_fusion.types import Amount, ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

UNIV3_UNIVERSAL_ROUTER = Web3.to_checksum_address(
    "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
)
CBBTC = Web3.to_checksum_address("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf")


def _encode_call(signature: str, *args) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def _build_universal_swap(
    universal: UniversalTokenSwapperFuse,
    token_in: ChecksumAddress,
    token_out: ChecksumAddress,
    amount: int,
    fee: int,
):
    """Build a UniversalTokenSwapperFuse swap action via Uniswap V3 universal router.

    Two sub-calls:
      1. token_in.transfer(router, amount)
      2. router.execute(commands=V3_SWAP_EXACT_IN, inputs=[(recipient, amount, 0, path, false)])
    """
    targets = [token_in, UNIV3_UNIVERSAL_ROUTER]
    transfer_data = _encode_call(
        "transfer(address,uint256)", UNIV3_UNIVERSAL_ROUTER, amount
    )
    path = encode_packed(["address", "uint24", "address"], [token_in, fee, token_out])
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            ["0x0000000000000000000000000000000000000001", amount, 0, path, False],
        )
    ]
    execute_data = function_signature_to_4byte_selector(
        "execute(bytes,bytes[])"
    ) + encode(
        ["bytes", "bytes[]"],
        [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs],
    )
    return universal.swap(
        token_in=token_in,
        token_out=token_out,
        amount_in=Amount(amount),
        targets=targets,
        data=[transfer_data, execute_data],
    )


def test_simulate_swap_cbbtc_to_usdc(web3_base):
    """cbBTC→USDC swap, pinned to the original test's fork block."""
    pinned_block = 24383840  # mirrors anvil.reset_fork(...) in test_swap_on_base.py
    block_hex = hex(pinned_block)
    vault_address = Web3.to_checksum_address(
        "0x55d8d6e5F17F153f3250b229D5AAc9437e908a77"
    )
    user_account = Web3.to_checksum_address(
        "0x17548bc38669D3D6590C861E505716245b4598bB"
    )

    ctx = Web3Context(web3=web3_base, chain_id=ChainId(web3_base.eth.chain_id))
    ctx.default_block = pinned_block

    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    atomist = access_manager.atomists()[0]

    deposit_amount = 1_00000000  # 1 cbBTC, exactly as in the original test
    swap_amount = deposit_amount // 2

    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)
    swap_action = _build_universal_swap(universal, CBBTC, BASE_USDC, swap_amount, 500)

    sim = VaultSimulator(
        web3=web3_base, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    # atomist grants roles
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=atomist,
    )
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)",
            Roles.WHITELIST_ROLE,
            user_account,
            0,
        ),
        from_=atomist,
    )
    # user_account approves and deposits 1 cbBTC into the vault
    sim.add_call(
        to=CBBTC,
        data=_encode_call("approve(address,uint256)", vault_address, deposit_amount),
        from_=user_account,
    )
    sim.add_call(
        to=vault_address,
        data=_encode_call("deposit(uint256,address)", deposit_amount, user_account),
        from_=user_account,
    )

    sim.observe("cbbtc_before", CBBTC, "balanceOf(address)", (vault_address,))
    sim.observe("usdc_before", BASE_USDC, "balanceOf(address)", (vault_address,))
    sim.execute([swap_action])
    sim.observe("cbbtc_after", CBBTC, "balanceOf(address)", (vault_address,))
    sim.observe("usdc_after", BASE_USDC, "balanceOf(address)", (vault_address,))

    result = sim.run()

    log.info("success=%s gas_used=%s", result.success, result.gas_used)
    log.info("observations=%s", result.observations)

    assert_all_success(result)

    # Mirror the original test's assertions verbatim
    assert result.get("cbbtc_before") >= deposit_amount
    assert result.get("usdc_before") < 1_000000
    assert result.get("cbbtc_after") >= swap_amount
    assert result.get("usdc_after") > 45000_000000


def test_simulate_swap_weth_to_pepe(web3_base):
    """WETH→PEPE swap, pinned to the original test's fork block.

    Vault's pre-loop WETH and resulting PEPE amount are deterministic on this block.
    """
    pinned_block = 25894923  # mirrors anvil.reset_fork(...) in test_swap_on_base.py
    block_hex = hex(pinned_block)
    vault_address = Web3.to_checksum_address(
        "0x85b7927B6d721638b575972111F4CE6DaCb7D33C"
    )
    alpha_address = Web3.to_checksum_address(
        "0xd16A8D5bD6B2cD5499bD55239bc980F09991b5fd"
    )
    pepe = Web3.to_checksum_address("0x52b492a33E447Cdb854c7FC19F1e57E8BfA1777D")

    ctx = Web3Context(web3=web3_base, chain_id=ChainId(web3_base.eth.chain_id))
    ctx.default_block = pinned_block

    weth_balance = ERC20(ctx, BASE_WETH).balance_of(vault_address)

    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)
    swap_action = _build_universal_swap(universal, BASE_WETH, pepe, weth_balance, 10000)

    sim = VaultSimulator(
        web3=web3_base, vault=vault_address, alpha=alpha_address, block=block_hex
    )
    sim.observe("weth_before", BASE_WETH, "balanceOf(address)", (vault_address,))
    sim.observe("pepe_before", pepe, "balanceOf(address)", (vault_address,))
    sim.execute([swap_action])
    sim.observe("weth_after", BASE_WETH, "balanceOf(address)", (vault_address,))
    sim.observe("pepe_after", pepe, "balanceOf(address)", (vault_address,))

    result = sim.run()

    log.info("success=%s gas_used=%s", result.success, result.gas_used)
    log.info("observations=%s", result.observations)

    assert_all_success(result)

    # Mirror the original test's assertions verbatim
    assert result.get("weth_before") == 5000000000000000
    assert result.get("weth_after") == 0
    assert result.get("pepe_before") == 0
    assert result.get("pepe_after") == int(174743647_876992680486051295)
