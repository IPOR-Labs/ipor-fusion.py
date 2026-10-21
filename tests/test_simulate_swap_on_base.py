"""Swap tests on Base — driven by `eth_simulateV1` instead of anvil.

Mirrors `test_swap_on_base.py`. Linear swap flows (cbBTC→USDC, WETH→PEPE) lend
themselves to eth_simulateV1 batching: setup, execute, observe — one RPC call.
"""

from __future__ import annotations

import logging

from _simulate import assert_all_success
from addresses import BASE_USDC, BASE_WETH
from constants import ANVIL_WALLET, BASE_UNIVERSAL_SWAP_FUSE
from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    PlasmaVault,
    Roles,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.fuses import UniversalTokenSwapperFuse
from ipor_fusion.types import Amount, ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

UNIV3_UNIVERSAL_ROUTER = Web3.to_checksum_address(
    "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
)
CBBTC = Web3.to_checksum_address("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf")


def _build_universal_swap(
    ctx: Web3Context,
    universal: UniversalTokenSwapperFuse,
    token_in: ChecksumAddress,
    token_out: ChecksumAddress,
    amount: int,
    fee: int,
):
    """Build a UniversalTokenSwapperFuse swap action via Uniswap V3 universal router.

    Two sub-calls:
      1. token_in.transfer(router, amount) — encoded via ERC20.transfer_call
      2. router.execute(commands=V3_SWAP_EXACT_IN, inputs=[(recipient, amount, 0, path, false)])
    """
    targets = [token_in, UNIV3_UNIVERSAL_ROUTER]
    transfer_data = (
        ERC20(token_in).transfer(UNIV3_UNIVERSAL_ROUTER, Amount(amount)).data
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

    ctx = Web3Context(
        web3=web3_base, chain_id=ChainId(web3_base.eth.chain_id)
    ).at_block(pinned_block)

    plasma_vault = PlasmaVault(vault_address)
    access_manager = AccessManager(plasma_vault.get_access_manager_address().call(ctx))
    cbbtc = ERC20(CBBTC)
    usdc = ERC20(BASE_USDC)
    atomist = access_manager.read_atomists(ctx)[0]

    deposit_amount = 1_00000000  # 1 cbBTC, exactly as in the original test
    swap_amount = deposit_amount // 2

    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)
    swap_action = _build_universal_swap(
        ctx, universal, CBBTC, BASE_USDC, swap_amount, 500
    )

    sim = VaultSimulator(
        web3=web3_base, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    # atomist grants roles
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0),
        from_=atomist,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, user_account, 0),
        from_=atomist,
    )
    # user_account approves and deposits 1 cbBTC into the vault
    sim.add_call(
        call=cbbtc.approve(vault_address, deposit_amount),
        from_=user_account,
    )
    sim.add_call(
        call=plasma_vault.deposit(deposit_amount, user_account),
        from_=user_account,
    )

    cbbtc_before = sim.observe(cbbtc.balance_of(vault_address))
    usdc_before = sim.observe(usdc.balance_of(vault_address))
    sim.execute([swap_action])
    cbbtc_after = sim.observe(cbbtc.balance_of(vault_address))
    usdc_after = sim.observe(usdc.balance_of(vault_address))

    result = sim.run()

    log.info("success=%s gas_used=%s", result.success, result.gas_used)

    assert_all_success(result)

    # Mirror the original test's assertions verbatim
    assert cbbtc_before.value >= deposit_amount
    assert usdc_before.value < 1_000000
    assert cbbtc_after.value >= swap_amount
    assert usdc_after.value > 45000_000000


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

    ctx = Web3Context(
        web3=web3_base, chain_id=ChainId(web3_base.eth.chain_id)
    ).at_block(pinned_block)

    weth = ERC20(BASE_WETH)
    pepe_token = ERC20(pepe)
    weth_balance = weth.balance_of(vault_address).call(ctx)

    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)
    swap_action = _build_universal_swap(
        ctx, universal, BASE_WETH, pepe, weth_balance, 10000
    )

    sim = VaultSimulator(
        web3=web3_base, vault=vault_address, alpha=alpha_address, block=block_hex
    )
    weth_before = sim.observe(weth.balance_of(vault_address))
    pepe_before = sim.observe(pepe_token.balance_of(vault_address))
    sim.execute([swap_action])
    weth_after = sim.observe(weth.balance_of(vault_address))
    pepe_after = sim.observe(pepe_token.balance_of(vault_address))

    result = sim.run()

    log.info("success=%s gas_used=%s", result.success, result.gas_used)

    assert_all_success(result)

    # Mirror the original test's assertions verbatim
    assert weth_before.value == 5000000000000000
    assert weth_after.value == 0
    assert pepe_before.value == 0
    assert pepe_after.value == 174743647_876992680486051295
