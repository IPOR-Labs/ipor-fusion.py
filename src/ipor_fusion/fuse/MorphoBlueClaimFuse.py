from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class MorphoBlueClaimFuse:

    def __init__(self, fuse_address: ChecksumAddress):
        self._fuse_address = fuse_address

    def claim(
        self, universal_rewards_distributor, rewards_token, claimable: int, proof
    ) -> FuseAction:
        if self._fuse_address is None:
            raise ValueError("fuseAddress is required")
        morpho_blue_claim_data = MorphoBlueClaimData(
            universal_rewards_distributor, rewards_token, claimable, proof
        )
        return FuseAction(self._fuse_address, morpho_blue_claim_data.function_call())


class MorphoBlueClaimData:
    universal_rewards_distributor: ChecksumAddress
    rewards_token: ChecksumAddress
    claimable: int
    proof: List[str]

    def __init__(
        self,
        universal_rewards_distributor: ChecksumAddress,
        rewards_token: ChecksumAddress,
        claimable: int,
        proof: List[str],
    ):
        self.universal_rewards_distributor = universal_rewards_distributor
        self.rewards_token = rewards_token
        self.claimable = claimable
        self.proof = proof

    def encode(self) -> bytes:
        proofs = [bytes.fromhex(hash.replace("0x", "")) for hash in self.proof]
        return encode(
            ["address", "address", "uint256", "bytes32[]"],
            [
                self.universal_rewards_distributor,
                self.rewards_token,
                self.claimable,
                proofs,
            ],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector(
            "claim(address,address,uint256,bytes32[])"
        )

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()
