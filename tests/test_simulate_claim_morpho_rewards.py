"""Morpho Blue rewards claim via `eth_simulateV1`.

Mirrors `test_claim_morpho_rewards.py::TestMorphoBlueRewards::test_should_claim_morpho_rewards`.
Single-block flow: alpha calls RewardsManager.claimRewards(...) with a hard-coded
distribution (the original test mocks the Morpho rewards API). The merkle proof
verifies on-chain against the URD merkle root — pinning to the original block is
critical because the root rotates over time.
"""

from __future__ import annotations

import logging

from _simulate import assert_all_success
from constants import ETHEREUM_MORPHO_CLAIM_FUSE
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion import (
    ERC20,
    PlasmaVault,
    RewardsManager,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.fuses import MorphoClaimFuse
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

VAULT_ADDRESS = Web3.to_checksum_address("0xe9385eFf3F937FcB0f0085Da9A3F53D6C2B4fB5F")
ALPHA_ADDRESS = Web3.to_checksum_address("0x6d3BE3f86FB1139d0c9668BD552f05fcB643E6e6")
PINNED_BLOCK = 23168096  # mirrors anvil.reset_fork(...) in the original test

# Hard-coded rewards distribution from the original test's mocked Morpho API
# response. Valid only at PINNED_BLOCK — the merkle root rotates frequently.
REWARDS_TOKEN: ChecksumAddress = Web3.to_checksum_address(
    "0x58D97B57BB95320F9a05dC918Aef65434969c2B2"
)
DISTRIBUTOR: ChecksumAddress = Web3.to_checksum_address(
    "0x330eefa8a787552DC5cAd3C3cA644844B1E61Ddb"
)
CLAIMABLE = 4670003019411856706671
PROOF: list[str] = [
    "0xbcc36476d3972818e27089a34d24c81d4cd58b3947d7049cf6590217c44ed65a",
    "0xb36697e61d8849901a805bfba05bf99141c73dcabe2b3eb7ad0cd7f3c1f71a15",
    "0x395c8f66b9682599b4a805c5d0df972d4168b9180b0a2769fec03ce960f4738a",
    "0x690458051a9045d829305eca26b79ce251d6db8d8f9285eb3fde0f65b4fb2878",
    "0x065f6085c2c45e9cf778837ac367adc21e728d4d1d1a03f07bdcc66ead50ed57",
    "0x1a83ee6eebb168e962043125b6edaf5346713b21eff555a86e0e5246ba1332b9",
    "0xca064df1706c14cb64e19e6b3bbaabce2e65511d584a96eb7847ca122d8a4070",
    "0xcfde623df2a1f16abb064f8eb76e5a2ba081886f4425d717d24c08762675885e",
    "0x2a17ba6c163a88561303db6f97065263536c5564cfffd81cea3a706f62212093",
    "0xd527527143bc4d79899616d4b1defff04984cd22642bfa215eaeca45dc4264bf",
    "0x48fd7d1ae5ae140190392dbd9062cc651dd456643ce1884e5f650af40994a8f1",
    "0x2fec24df16976ee719fbdac1ccb858c2f3692e4f9fd09ea001f7fbbded4d0b30",
    "0x9c9ea5bf79aa65f425f1703be9b35570a617da3ce29e849944c93b09599f9e56",
    "0xb34efee099f67fc081f8324ec479b84a9ecfb6a3e54d2d358d9516ddabae9888",
    "0x5c81d0d79a96726668998eaefa6e55b84b5b035df6907855d4a6ac6a8524e69c",
]


def test_simulate_claim_morpho_rewards(web3_eth):
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(web3_eth.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    plasma_vault = PlasmaVault(ctx, VAULT_ADDRESS)
    rewards = RewardsManager(
        ctx, plasma_vault.get_rewards_claim_manager_address().call()
    )
    rewards_token = ERC20(ctx, REWARDS_TOKEN)

    morpho = MorphoClaimFuse(ETHEREUM_MORPHO_CLAIM_FUSE)
    claim_action = morpho.claim(
        universal_rewards_distributor=DISTRIBUTOR,
        rewards_token=REWARDS_TOKEN,
        claimable=CLAIMABLE,
        proof=PROOF,
    )

    sim = VaultSimulator(
        web3=web3_eth, vault=VAULT_ADDRESS, alpha=ALPHA_ADDRESS, block=block_hex
    )

    sim.observe("rewards_before", rewards_token.balance_of(rewards.address))

    # alpha invokes RewardsManager.claimRewards([(fuse, calldata)]) — same shape
    # as plasma_vault.execute, but on the rewards manager with a different
    # function signature.
    sim.execute_call(call=rewards.claim_rewards([claim_action]))

    sim.observe("rewards_after", rewards_token.balance_of(rewards.address))

    result = sim.run()

    log.info("success=%s gas_used=%s", result.all_success, result.gas_used)
    log.info("revert_reason=%s", result.revert_reason)
    log.info("observations=%s", result.observations)

    assert_all_success(result)

    diff = result.get("rewards_after") - result.get("rewards_before")
    assert diff > 0, "rewards claim did not increase the rewards manager's balance"
