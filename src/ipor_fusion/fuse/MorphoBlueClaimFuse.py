from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class MorphoBlueClaimFuse:

    _fuse_address: ChecksumAddress

    def __init__(self, fuse_address: ChecksumAddress):
        self._fuse_address = fuse_address

    def claim(
        self,
        universal_rewards_distributor: ChecksumAddress,
        rewards_token: ChecksumAddress,
        claimable: int,
        proof: List[str],
    ) -> FuseAction:
        if self._fuse_address is None:
            raise ValueError("fuseAddress is required")
        morpho_blue_claim_data = MorphoBlueClaimData(
            universal_rewards_distributor, rewards_token, claimable, proof
        )
        return FuseAction(self._fuse_address, morpho_blue_claim_data.function_call())


class MorphoBlueClaimData:
    _universal_rewards_distributor: ChecksumAddress
    _rewards_token: ChecksumAddress
    _claimable: int
    _proof: List[str]

    def __init__(
        self,
        universal_rewards_distributor: ChecksumAddress,
        rewards_token: ChecksumAddress,
        claimable: int,
        proof: List[str],
    ):
        if universal_rewards_distributor is None:
            raise ValueError("universal_rewards_distributor cannot be None")
        if rewards_token is None:
            raise ValueError("rewards_token cannot be None")
        if claimable is None:
            raise ValueError("claimable cannot be None")
        if proof is None:
            raise ValueError("proof cannot be None")
        if not len(proof) > 0:
            raise ValueError("proof list cannot be empty")

        self._universal_rewards_distributor = universal_rewards_distributor
        self._rewards_token = rewards_token
        self._claimable = claimable
        self._proof = proof

    def encode(self) -> bytes:
        proofs = [bytes.fromhex(hash.replace("0x", "")) for hash in self._proof]
        return encode(
            ["address", "address", "uint256", "bytes32[]"],
            [
                self._universal_rewards_distributor,
                self._rewards_token,
                self._claimable,
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
