"""Merkl self-unwrapping reward claim on BASE via `eth_simulateV1`.

Mirrors `ipor-fusion/test/fuses/markl/MerklClaimWrapperFuseTest.t.sol` and
doubles as the usage example for `MerklClaimWrapperFuse`: the claimed Merkl
reward is a wrapper token that unwraps on transfer into the rebasing Aave
aToken `aBascbETH`. The fuse must forward the unwrapped token in full to the
RewardsClaimManager — it must NOT linger on the PlasmaVault, which would
inflate share price (the aToken is a market substrate).

The pinned block captures live production state, so no overrides or setup
pranks are needed for the claim itself: the fuse is registered in the
RewardsClaimManager, the alpha holds CLAIM_REWARDS_ROLE, and the aToken is
granted as a MERKL substrate. The block matters because a Merkl claim is
valid only under three block-coupled conditions: the merkle root rotates
every few hours (the proof verifies only against the root active at the
pinned block), the claim amount is CUMULATIVE (the forwarded delta is the
amount minus claimed(vault, wrapper) at the block), and the wrapper pays out
via transferFrom on the aToken funder, whose standing allowance must cover
the delta.

Rather than hunting for a block satisfying all three, the claim data
(amount, proof) is lifted from the calldata of a real successful production
claim — tx 0xff3331617fbf22669300b5a87f388ac9450ea4d446d5a6b4a57bdd94f1d8f88d
(block 49185303), which went through this exact alpha → RewardsClaimManager
→ fuse path — and the simulation replays it one block earlier, where the
real execution proves every condition held.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from _simulate import address_substrate, assert_all_success
from constants import BASE_MERKL_CLAIM_WRAPPER_FUSE
from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    MerklClaimWrapperFuse,
    PlasmaVault,
    RewardsManager,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

VAULT_ADDRESS = Web3.to_checksum_address("0xe883426B4fc84A7f5cc86415CAbBef43E73a4CC8")
ALPHA_ADDRESS = Web3.to_checksum_address("0x48d3615d78B152819ea0367adF7b9944e399ac9a")
# On-chain holder of FUSE_MANAGER_ROLE (300) — only used by the zero-delta
# test to grant an extra substrate; the claim itself needs no governance.
FUSE_MANAGER_HOLDER = Web3.to_checksum_address(
    "0xd556a9FA4dd83aDE79B89f4A431c57169D00D4a6"
)
# Claimed wrapper token (aBascbETH wrapper) that self-unwraps on transfer.
WRAPPER = Web3.to_checksum_address("0xa1A67b55a88ab8Dcc86B765C1Cd85887e24ad7AA")
# Token actually received by the vault after the wrapper unwraps: aBascbETH,
# the Aave Base cbETH aToken (REBASING). Shares its symbol with WRAPPER above.
A_BAS_CB_ETH = Web3.to_checksum_address("0xcf3D55c10DB69f28fD1A75Bd73f3D8A2d9c595ad")

# One block before the real production claim tx (see module docstring).
PINNED_BLOCK = 49185302
# Merkl claims are cumulative: the Distributor is called with the lifetime
# amount and pays out the difference vs claimed(vault, WRAPPER) at the block.
CLAIM_AMOUNT = 8668124815864949902
CLAIMED_BEFORE = 7874776391985530632
EXPECTED_FORWARD = CLAIM_AMOUNT - CLAIMED_BEFORE

# The wrapper transfers the aToken 1:1, but the aToken's rebasing balanceOf
# rounds the measured delta by a wei around the transferred amount — hence a
# small absolute tolerance instead of strict equality.
REBASE_TOLERANCE_WEI = 2

CLAIM_REWARDS_SELECTOR = function_signature_to_4byte_selector(
    "claimRewards((address,bytes)[])"
)
REWARDS_CLAIMED_TOPIC = Web3.keccak(
    text="MerklClaimWrapperFuseRewardsClaimed(address,address,uint256,address)"
).to_0x_hex()
UNSUPPORTED_TOKEN_SELECTOR = bytes(
    Web3.keccak(text="MerklClaimWrapperFuseUnsupportedReceivedToken(address)")[:4]
).hex()

# Merkle proof for (vault, WRAPPER, CLAIM_AMOUNT) — from the real claim tx
# calldata; valid only against the Distributor root active at PINNED_BLOCK.
PROOF: list[str] = [
    "0xa0124096e89cc96a016c3d48328a3dc998e9fba4e2c11f6c21b592abdcc4d247",
    "0x5ea1dbd4cf3fd6b2e26c927b6eb2b796bdf64e38b00962030fcf4352fe3621c7",
    "0x6469130aacf38c61b04cc956d6ed8286902fe0d3b8c62a825276099ec7ea4e42",
    "0x6d52dbd7910af4dbdc61780ec66ffe8cca809ef6d1afd666d8cdb89caab10863",
    "0x21cc43c0fecc6b855387f6a03c64066abf58b4492a06eb915cb2a9590b2a8fca",
    "0xaacd2babfb82ef4ad67d00cb7dc9c36604a1cc1b4faf1688969cc7608737eea8",
    "0x54f64f5bdf27742b07987bb2fc06c3c8844585e46bef2223fb3a301db8ddcc79",
    "0x5579657217f8525060949cfb220d18b74e9ec7ef3806f18a8e58f9459f52d7e2",
    "0x0a180f6af7be3caa3429c1827822d06e3fbe4c76a0e472f4ccd9403a257c4e6c",
    "0x4676bb1123d500e6d02159b4bb92feef5b427a74da270fe3294abded2e8350e7",
    "0xaae6c1e3bc8d7cf8d8e7759b88583289e6063ebb66c2f51b09057c4b21e9f040",
    "0x41c8427a247f8481873e4213ad78aab4e2a29675c1c2aeb57dc597b10b22499d",
    "0xfcb184d7d5d63431aa4e2d741cc6db158b4354c0a6f675ff3e2e7ff1da310371",
    "0x08a6688fd5973ba0400c887424dd64cafcd0be628be0759153c6cb303cc981a7",
    "0x78c49b9378c3b6fb98a25e9362696ec659c47b9d39ae4b86a2cecb419644cc4d",
    "0xe48edeb8e23835c042d5e59ab3fce3404ee252c20a5398a61fe9f806fd2de912",
    "0xefbfbd13bd1916f5b66a884e6972ff89acdf65e9b30d662c12d69454c0d04da6",
    "0xf2c7e7b048279d48a4bf499fc83df46c68f0b3fcc6354ac53d2720422f541881",
    "0x580aa265208af72a2479cf70ac01a4ed71d63cf038594053723d3727fedededa",
    "0xd66d0487e358878f795c2e8524e7490af382da7977e7b5d35a7deed39d8a3d75",
]


@dataclass(slots=True)
class _Setup:
    sim: VaultSimulator
    plasma_vault: PlasmaVault
    rewards: RewardsManager
    reward_token: ERC20
    wrapper_token: ERC20


def _prepared_sim(web3_base: Web3) -> _Setup:
    """Simulator over untouched production state at PINNED_BLOCK.

    Everything the claim needs is already configured on-chain: the fuse is
    registered, the alpha holds the claimRewards role, and the aToken is a
    granted MERKL substrate. The wiring asserts below are real pinned-block
    reads documenting those preconditions.
    """
    ctx = Web3Context(web3=web3_base, chain_id=ChainId(web3_base.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    plasma_vault = PlasmaVault(ctx, VAULT_ADDRESS)
    rewards = RewardsManager(
        ctx, plasma_vault.get_rewards_claim_manager_address().call()
    )
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )

    claim_role = access_manager.get_target_function_role(
        rewards.address, CLAIM_REWARDS_SELECTOR
    ).call()
    assert claim_role == Roles.CLAIM_REWARDS_ROLE
    assert access_manager.has_role(claim_role, ALPHA_ADDRESS).call().is_member
    assert rewards.is_reward_fuse_supported(BASE_MERKL_CLAIM_WRAPPER_FUSE).call()

    sim = VaultSimulator(
        web3=web3_base,
        vault=VAULT_ADDRESS,
        alpha=ALPHA_ADDRESS,
        block=hex(PINNED_BLOCK),
    )
    return _Setup(
        sim=sim,
        plasma_vault=plasma_vault,
        rewards=rewards,
        reward_token=ERC20(ctx, A_BAS_CB_ETH),
        wrapper_token=ERC20(ctx, WRAPPER),
    )


def _claim_action(received_tokens: list) -> object:
    """The example claim: one wrapper token, its cumulative amount and proof."""
    fuse = MerklClaimWrapperFuse(BASE_MERKL_CLAIM_WRAPPER_FUSE)
    return fuse.claim(
        tokens=[WRAPPER],
        amounts=[CLAIM_AMOUNT],
        proofs=[PROOF],
        received_tokens=received_tokens,
    )


def _claimed_event_count(execute_logs: list[dict]) -> int:
    return sum(
        1
        for entry in execute_logs
        if entry.get("topics") and entry["topics"][0].lower() == REWARDS_CLAIMED_TOPIC
    )


def test_simulate_merkl_wrapper_claim_forwards_unwrapped_token(web3_base):
    setup = _prepared_sim(web3_base)
    sim, rewards, reward_token = setup.sim, setup.rewards, setup.reward_token

    sim.observe("rcm_before", reward_token.balance_of(rewards.address))
    sim.observe("vault_before", reward_token.balance_of(VAULT_ADDRESS))

    # Alpha drives RewardsManager.claimRewards([(fuse, calldata)]) — the fuse
    # claims the wrapper from the Merkl Distributor, the wrapper self-unwraps
    # into the aToken on the vault, and the fuse forwards the measured delta.
    sim.execute_call(call=rewards.claim_rewards([_claim_action([A_BAS_CB_ETH])]))

    sim.observe("rcm_after", reward_token.balance_of(rewards.address))
    sim.observe("vault_after", reward_token.balance_of(VAULT_ADDRESS))

    result = sim.run()
    log.info("success=%s gas_used=%s", result.all_success, result.gas_used)
    log.info("revert_reason=%s", result.revert_reason)
    log.info("observations=%s", result.observations)
    assert_all_success(result)

    # KEY regression assertion: the unwrapped aToken must NOT stay on the
    # vault (lingering would inflate share price — it is a market substrate).
    vault_delta = result.get("vault_after") - result.get("vault_before")
    assert abs(vault_delta) <= REBASE_TOLERANCE_WEI, (
        f"vault underlying must not grow, delta={vault_delta}"
    )

    # The unclaimed remainder (cumulative amount minus what the vault had
    # already claimed at the pinned block) lands on the manager in full.
    forwarded = result.get("rcm_after") - result.get("rcm_before")
    assert abs(forwarded - EXPECTED_FORWARD) <= REBASE_TOLERANCE_WEI, (
        f"forwarded amount mismatch: {forwarded} vs {EXPECTED_FORWARD}"
    )

    # Exactly one forwarding event — for the received aToken.
    assert _claimed_event_count(result.execute_logs) == 1


def test_simulate_merkl_wrapper_claim_rejects_ungranted_received_token(web3_base):
    # Only the aToken is granted on the MERKL market in production, so listing
    # the WRAPPER as a received token must trip the substrate gate and revert.
    setup = _prepared_sim(web3_base)
    sim, rewards = setup.sim, setup.rewards

    sim.execute_call(call=rewards.claim_rewards([_claim_action([WRAPPER])]))

    result = sim.run()
    log.info("revert_reason=%s", result.revert_reason)

    assert result.success is False
    assert result.revert_reason == f"custom error 0x{UNSUPPORTED_TOKEN_SELECTOR}"

    # The provider reports the failure as an error object with the revert
    # payload in error.data — the simulator must surface it as the failed
    # call's return_data: the error selector plus the rejected token address.
    revert_payload = bytes(result.failed_calls[0].return_data)
    assert revert_payload[:4].hex() == UNSUPPORTED_TOKEN_SELECTOR
    (rejected_token,) = decode(["address"], revert_payload[4:])
    assert rejected_token.lower() == WRAPPER.lower()


def test_simulate_merkl_wrapper_claim_zero_delta_received_token_is_noop(web3_base):
    # WRAPPER self-unwraps during the claim, so its balance delta is zero —
    # the fuse must skip it: no transfer, no event. Listing it as a received
    # token first requires granting it as a MERKL substrate; the grant
    # replaces the whole list, so pass the aToken and the WRAPPER together
    # (pranked as the on-chain FUSE_MANAGER_ROLE holder — eth_simulateV1
    # without validation allows any `from`).
    setup = _prepared_sim(web3_base)
    sim, rewards = setup.sim, setup.rewards

    sim.add_call(
        setup.plasma_vault.grant_market_substrates(
            IporFusionMarkets.MERKL,
            [address_substrate(token) for token in (A_BAS_CB_ETH, WRAPPER)],
        ),
        from_=FUSE_MANAGER_HOLDER,
        label="grant_market_substrates",
    )

    sim.observe("rcm_wrapper_before", setup.wrapper_token.balance_of(rewards.address))
    sim.observe("rcm_reward_before", setup.reward_token.balance_of(rewards.address))

    sim.execute_call(
        call=rewards.claim_rewards([_claim_action([A_BAS_CB_ETH, WRAPPER])])
    )

    sim.observe("rcm_wrapper_after", setup.wrapper_token.balance_of(rewards.address))
    sim.observe("rcm_reward_after", setup.reward_token.balance_of(rewards.address))

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)

    # The zero-delta WRAPPER must not have been forwarded.
    assert result.get("rcm_wrapper_after") == result.get("rcm_wrapper_before")

    # The positive-delta aToken is forwarded as in the happy path.
    forwarded = result.get("rcm_reward_after") - result.get("rcm_reward_before")
    assert abs(forwarded - EXPECTED_FORWARD) <= REBASE_TOLERANCE_WEI

    # Only the positive-delta token emits MerklClaimWrapperFuseRewardsClaimed.
    assert _claimed_event_count(result.execute_logs) == 1
