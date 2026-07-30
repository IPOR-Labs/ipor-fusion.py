"""Merkl self-unwrapping reward claim on BASE via `eth_simulateV1`.

Mirrors `ipor-fusion/test/fuses/markl/MerklClaimWrapperFuseTest.t.sol` and
doubles as the usage example for `MerklClaimWrapperFuse`: the claimed Merkl
reward is a wrapper token that unwraps on transfer into the rebasing Aave
aToken `aBascbETH`. The fuse must forward the unwrapped token in full to the
RewardsClaimManager — it must NOT linger on the PlasmaVault, which would
inflate share price (the aToken is a market substrate).

The pinned block is critical: the merkle proof only verifies against the
Merkl Distributor root active at that block, and the claim must not have been
executed yet. The fuse contract was deployed AFTER the pinned block, so the
simulation injects its current runtime code (immutables baked in: VERSION =
its own address, DISTRIBUTOR, MARKET_ID) at the same address via a state
override, then drives the normal on-chain flow:

  - `RewardsClaimManager.addRewardFuses` and `grantMarketSubstrates` pranked
    as the real on-chain FUSE_MANAGER_ROLE holder (no role minting);
  - `claimRewards` as the vault's real alpha, granted CLAIM_REWARDS role via
    an AccessManager storage-slot override — the same technique as the
    Solidity test's `_grantRoleViaStorage` (no active role holder exists at
    the pinned block).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from _simulate import address_substrate, assert_all_success
from constants import BASE_MERKL_CLAIM_WRAPPER_FUSE
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    PlasmaVault,
    RewardsManager,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.fuses import MerklClaimWrapperFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

VAULT_ADDRESS = Web3.to_checksum_address("0xe883426B4fc84A7f5cc86415CAbBef43E73a4CC8")
ALPHA_ADDRESS = Web3.to_checksum_address("0x48d3615d78B152819ea0367adF7b9944e399ac9a")
# Existing on-chain holder of FUSE_MANAGER_ROLE (300) for this vault.
FUSE_MANAGER_HOLDER = Web3.to_checksum_address(
    "0xd556a9FA4dd83aDE79B89f4A431c57169D00D4a6"
)
# Claimed wrapper token (aBascbETH wrapper) that self-unwraps on transfer.
WRAPPER = Web3.to_checksum_address("0xa1A67b55a88ab8Dcc86B765C1Cd85887e24ad7AA")
# Token actually received by the vault after the wrapper unwraps: aBascbETH,
# the Aave Base cbETH aToken (REBASING). Shares its symbol with WRAPPER above.
A_BAS_CB_ETH = Web3.to_checksum_address("0xcf3D55c10DB69f28fD1A75Bd73f3D8A2d9c595ad")

PINNED_BLOCK = 46766277
CLAIM_AMOUNT = 480280871240291748

# The Solidity fork test measures the forwarded delta as exactly
# CLAIM_AMOUNT + 1 wei (aToken rebasing balanceOf rounding) at the fork-block
# timestamp. eth_simulateV1 executes in a fresh next block whose timestamp is
# slightly later, so the liquidity index differs and the rounding can shift by
# a wei — hence a small absolute tolerance instead of strict equality.
REBASE_TOLERANCE_WEI = 2

# OZ AccessManager (non-upgradeable, as used by IporFusionAccessManager):
# `_roles` is the 2nd state variable => slot 1.
ACCESS_MANAGER_ROLES_SLOT = 1

CLAIM_REWARDS_SELECTOR = function_signature_to_4byte_selector(
    "claimRewards((address,bytes)[])"
)
REWARDS_CLAIMED_TOPIC = Web3.keccak(
    text="MerklClaimWrapperFuseRewardsClaimed(address,address,uint256,address)"
).to_0x_hex()
UNSUPPORTED_TOKEN_SELECTOR = bytes(
    Web3.keccak(text="MerklClaimWrapperFuseUnsupportedReceivedToken(address)")[:4]
).hex()

# Merkle proof for (vault, WRAPPER, CLAIM_AMOUNT) — valid only at PINNED_BLOCK.
PROOF: list[str] = [
    "0x8881ed89944863e9fa4444106149b612b72d52a6fe527e9b423a0c971e56903f",
    "0x47cc40690a04f7e483aec30fd75ba3f201f535cfdfddfb4f4f16b7765ed01fbc",
    "0x30869b377f2897d0d381e80f1b68797f28656e4ca306b8e765f76c558505b67e",
    "0x62d20777c8bf3688fb72868bb60ef1e24c8a99c317d5100c84cf2ae61f4c40c9",
    "0x0fd393a7b8ed13e8ec313ab44c0f408996f14fd5d3ae42fad3fe043bf3f4a246",
    "0x0bac710962a5711f07174c62375758cda642db0e130af21058cd8db0b296cb23",
    "0x8ba60e3f51a6072226b0f65eb506cb82835296bf57cba1459cc80731fec7d8dd",
    "0x40de0d9c12d8a4578649171b85aa44f1e98ffbdb56b2020bfac3f2b15e27fc97",
    "0x23dad8d9155505c61345e33d382b2964a414191f404f0b86a94f9684b23528d1",
    "0x9e318c333d30d9aaeb48906088febf824caad6fb3b0613d38af2d5f5e8dc7f7b",
    "0xe6ad5d2d3a3c000792c2ce568178355b09793819bc34892dc85cadda2e17467a",
    "0x584bb3a00cf9167d00867e88562798f024b4c1ad6f0eb89230353c45cc9d22e7",
    "0x739c138837f9cca36fef2c9f8e827f7ef73e3885c4fdd948a2be710c8fe214b4",
    "0xdad9299e5e627735ad13124d1ddabdf504b4d5dcce8ce1f40d6dfea6b52a261e",
    "0x762459f1d0ff419029a8c3fc429bcb3fb73378e10cae65e84f765f348ca32be4",
    "0x7e861a3d59aba2f9bdcb8cc9df6002ca939a5777c0734d6e53ee2b9cbe0a47da",
    "0x4e97f332c6f947fd4a0c876916eba7bd55e7d6491484cccfccf5fa959c9bd678",
    "0x4cb561cb4c2fb897721d536ad161997d0aadb00040d6ae4ed3316de09e56a1ca",
    "0x41715bd59cfae1d86c8c4ecba32d39be6babb3130ef626a2fb4646d5f9b27ac2",
    "0xb4df1dbeb127166522caaf08a4a78fcb7346ce69eb5864d4a5fbc5eaa5a1d49c",
]


@dataclass(slots=True)
class _Setup:
    sim: VaultSimulator
    rewards: RewardsManager
    reward_token: ERC20
    wrapper_token: ERC20


def _role_member_slot(role_id: int, account: str) -> str:
    """AccessManager storage slot of `_roles[role_id].members[account]`.

    Writing 1 there packs {since=1, delay=0} — a past timepoint, so the role
    is active immediately (mirrors the Solidity test's `_grantRoleViaStorage`).
    """
    role_base = Web3.keccak(
        encode(["uint256", "uint256"], [role_id, ACCESS_MANAGER_ROLES_SLOT])
    )
    return Web3.keccak(encode(["address", "bytes32"], [account, role_base])).to_0x_hex()


def _prepared_sim(web3_base: Web3, granted_tokens: list) -> _Setup:
    """Simulator at PINNED_BLOCK with the fuse injected and governance queued.

    `granted_tokens` is the FULL set of received tokens allowed on the MERKL
    market — `grantMarketSubstrates` replaces the existing list, so callers
    must pass everything in one call.
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

    # claimRewards has no active role holder at the pinned block, so grant its
    # target role to the vault's real alpha via a storage-slot state override.
    # Real pinned-block read — also asserts the on-chain wiring: the
    # claimRewards target role on this vault is the canonical CLAIM_REWARDS_ROLE.
    claim_role = access_manager.get_target_function_role(
        rewards.address, CLAIM_REWARDS_SELECTOR
    ).call()
    assert claim_role == Roles.CLAIM_REWARDS_ROLE

    # The fuse was deployed after PINNED_BLOCK — inject its live runtime code
    # at the same address so the pinned-block simulation can execute it.
    fuse_code = web3_base.eth.get_code(BASE_MERKL_CLAIM_WRAPPER_FUSE)
    assert len(fuse_code) > 0, "MerklClaimWrapperFuse not deployed at latest block"

    sim = VaultSimulator(
        web3=web3_base,
        vault=VAULT_ADDRESS,
        alpha=ALPHA_ADDRESS,
        block=hex(PINNED_BLOCK),
    )
    sim.with_state_override(BASE_MERKL_CLAIM_WRAPPER_FUSE, code=fuse_code.to_0x_hex())
    sim.with_state_override(
        access_manager.address,
        stateDiff={
            _role_member_slot(claim_role, ALPHA_ADDRESS): "0x" + f"{1:064x}",
        },
    )

    # Normal governance flow, pranked as the existing on-chain FUSE_MANAGER_ROLE
    # holder (eth_simulateV1 without validation allows any `from`). The
    # before/after observations prove against real chain state that our
    # addRewardFuses calldata actually registers the fuse.
    sim.observe(
        "fuse_supported_before",
        rewards.is_reward_fuse_supported(BASE_MERKL_CLAIM_WRAPPER_FUSE),
    )
    sim.add_call(
        rewards.add_reward_fuses([BASE_MERKL_CLAIM_WRAPPER_FUSE]),
        from_=FUSE_MANAGER_HOLDER,
        label="add_reward_fuses",
    )
    sim.observe(
        "fuse_supported_after",
        rewards.is_reward_fuse_supported(BASE_MERKL_CLAIM_WRAPPER_FUSE),
    )
    sim.add_call(
        plasma_vault.grant_market_substrates(
            IporFusionMarkets.MERKL,
            [address_substrate(token) for token in granted_tokens],
        ),
        from_=FUSE_MANAGER_HOLDER,
        label="grant_market_substrates",
    )

    return _Setup(
        sim=sim,
        rewards=rewards,
        reward_token=ERC20(ctx, A_BAS_CB_ETH),
        wrapper_token=ERC20(ctx, WRAPPER),
    )


def _claim_action(received_tokens: list) -> object:
    """The example claim: one wrapper token, its pinned amount and proof."""
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
    setup = _prepared_sim(web3_base, granted_tokens=[A_BAS_CB_ETH])
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

    # The fuse was not registered at the pinned block; addRewardFuses did it.
    assert result.get("fuse_supported_before") is False
    assert result.get("fuse_supported_after") is True

    # KEY regression assertion: the unwrapped aToken must NOT stay on the
    # vault (lingering would inflate share price — it is a market substrate).
    vault_delta = result.get("vault_after") - result.get("vault_before")
    assert abs(vault_delta) <= REBASE_TOLERANCE_WEI, (
        f"vault underlying must not grow, delta={vault_delta}"
    )

    # The full claim (modulo aToken rebase rounding) lands on the manager.
    forwarded = result.get("rcm_after") - result.get("rcm_before")
    assert abs(forwarded - CLAIM_AMOUNT) <= REBASE_TOLERANCE_WEI, (
        f"forwarded amount mismatch: {forwarded} vs {CLAIM_AMOUNT}"
    )

    # Exactly one forwarding event — for the received aToken.
    assert _claimed_event_count(result.execute_logs) == 1


def test_simulate_merkl_wrapper_claim_rejects_ungranted_received_token(web3_base):
    # WRAPPER is intentionally NOT granted on the MERKL market — only the
    # aToken is — so listing it as a received token must trip the substrate
    # gate and revert the whole claim.
    setup = _prepared_sim(web3_base, granted_tokens=[A_BAS_CB_ETH])
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
    # Both received tokens must be granted in ONE grantMarketSubstrates call
    # (the grant replaces the list). WRAPPER self-unwraps during the claim, so
    # its balance delta is zero — the fuse must skip it: no transfer, no event.
    setup = _prepared_sim(web3_base, granted_tokens=[A_BAS_CB_ETH, WRAPPER])
    sim, rewards = setup.sim, setup.rewards

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
    assert abs(forwarded - CLAIM_AMOUNT) <= REBASE_TOLERANCE_WEI

    # Only the positive-delta token emits MerklClaimWrapperFuseRewardsClaimed.
    assert _claimed_event_count(result.execute_logs) == 1
