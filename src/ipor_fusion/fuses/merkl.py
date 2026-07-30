from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


class MerklClaimWrapperFuse(Fuse):
    """Fuse for claiming self-unwrapping Merkl rewards.

    Handles rewards where the claimed token (a wrapper) unwraps on transfer
    into one or more different "received" tokens — e.g. an aToken wrapper that
    unwraps into the rebasing Aave aToken. Unlike `MerklClaimFuse`, the balance
    delta is measured on the caller-supplied `received_tokens` (the final
    tokens that actually land on the vault), and every positive delta is
    forwarded to the RewardsClaimManager, so unwrapped tokens never linger on
    the PlasmaVault (which would inflate share price when the token is a
    market substrate).

    On-chain constraints (`rewards_fuses/merkl/MerklClaimWrapperFuse.sol`):
      - each received token must be granted as a substrate-as-asset on the
        fuse's market (`IporFusionMarkets.MERKL`), otherwise the claim reverts
        with `MerklClaimWrapperFuseUnsupportedReceivedToken`;
      - route the action through `RewardsManager.claim_rewards(...)`, not
        `PlasmaVault.execute(...)` — the fuse must run in the vault context
        with a configured RewardsClaimManager.
    """

    def claim(
        self,
        *,
        tokens: list[ChecksumAddress],
        amounts: list[Amount],
        proofs: list[list[str]],
        received_tokens: list[ChecksumAddress],
    ) -> FuseAction:
        """Claim `amounts` of wrapper `tokens` with merkle `proofs`, then
        forward the `received_tokens` balance deltas to the RewardsClaimManager.
        """
        self._validate_non_empty_list(tokens, "tokens")
        self._validate_non_empty_list(received_tokens, "received_tokens")
        if len(amounts) != len(tokens) or len(proofs) != len(tokens):
            raise ValueError(
                "tokens, amounts and proofs must have equal lengths, got "
                f"{len(tokens)}, {len(amounts)}, {len(proofs)}"
            )
        for index, token in enumerate(tokens):
            self._validate_address(token, f"tokens[{index}]")
        for index, amount in enumerate(amounts):
            self._validate_amount(amount, f"amounts[{index}]")
        for index, token in enumerate(received_tokens):
            self._validate_address(token, f"received_tokens[{index}]")
        proofs_bytes = [
            [bytes.fromhex(node.removeprefix("0x")) for node in proof]
            for proof in proofs
        ]
        return self._action_raw(
            "claim(address[],uint256[],bytes32[][],address[])",
            [tokens, amounts, proofs_bytes, received_tokens],
        )
