import logging
import os
from typing import List, Any, Generator
from unittest.mock import patch

from eth_typing import BlockNumber, ChecksumAddress
from web3 import Web3
import requests

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (
    CheatingPlasmaVaultSystemFactory,
)

# Configure logging to display relevant test information
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Retrieve the fork URL from environment variables
fork_url = os.getenv("ETHEREUM_PROVIDER_URL")

# Initialize the Anvil test container with the provided fork URL
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()

class MorphoBlueRewardsDistribution:
    asset_address: ChecksumAddress
    distributor_address: ChecksumAddress
    claimable: int
    proof: List[str]

    def __init__(self, distribution: dict):
        self.rewards_token_address = Web3.to_checksum_address(
            distribution["asset"]["address"]
        )
        self.distributor_address = Web3.to_checksum_address(
            distribution["distributor"]["address"]
        )
        self.claimable = int(distribution["claimable"])
        self.proof = distribution["proof"]


class MorphoBlueRewardsClient:

    @staticmethod
    def get_url(user_address: str) -> str:
        return f"https://rewards.morpho.org/v1/users/{user_address}/distributions"

    @staticmethod
    def get_rewards_distribution(
        user_address: str,
    ) -> Generator[MorphoBlueRewardsDistribution, Any, None]:
        response = requests.get(
            url=MorphoBlueRewardsClient.get_url(user_address),
            timeout=10,
        )
        distributions = response.json()["data"]

        for distribution in distributions:
            yield MorphoBlueRewardsDistribution(distribution)


class TestMorphoBlueRewards:

    def test_should_claim_morpho_rewards(self):
        anvil.reset_fork(BlockNumber(23168096))

        vault_address = Web3.to_checksum_address(
            "0xe9385eFf3F937FcB0f0085Da9A3F53D6C2B4fB5F"
        )
        alpha_address = Web3.to_checksum_address(
            "0x6d3BE3f86FB1139d0c9668BD552f05fcB643E6e6"
        )

        system = CheatingPlasmaVaultSystemFactory(
            provider_url=anvil.get_anvil_http_url(),
            private_key=ANVIL_WALLET_PRIVATE_KEY,
        ).get(vault_address)

        system.prank(alpha_address)

        morpho = system.morpho(
            morpho_blue_claim_fuse_address=Web3.to_checksum_address(
                "0x6820dF665BA09FBbd3240aA303421928EF4C71a1"
            )
        )

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = {
                "timestamp": "1755521728",
                "data": [
                    {
                        "user": "0xe9385eFf3F937FcB0f0085Da9A3F53D6C2B4fB5F",
                        "asset": {
                            "id": "0x58d97b57bb95320f9a05dc918aef65434969c2b2-1",
                            "address": "0x58D97B57BB95320F9a05dC918Aef65434969c2B2",
                            "chain_id": 1,
                        },
                        "distributor": {
                            "id": "0x330eefa8a787552dc5cad3c3ca644844b1e61ddb-1",
                            "address": "0x330eefa8a787552DC5cAd3C3cA644844B1E61Ddb",
                            "chain_id": 1,
                        },
                        "claimable": "4670003019411856706671",
                        "proof": [
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
                        ],
                    }
                ],
            }
            mock_get.return_value.status_code = 200

            distributions = MorphoBlueRewardsClient.get_rewards_distribution(
                vault_address
            )

        for dist in distributions:
            claim_action = morpho.claim_rewards(
                universal_rewards_distributor=dist.distributor_address,
                rewards_token=dist.rewards_token_address,
                claimable=dist.claimable,
                proof=dist.proof,
            )

            before = system.erc20(asset_address=dist.rewards_token_address).balance_of(
                system.rewards_claim_manager().address()
            )
            system.rewards_claim_manager().claim_rewards([claim_action])
            after = system.erc20(asset_address=dist.rewards_token_address).balance_of(
                system.rewards_claim_manager().address()
            )

            diff = after - before
            assert diff > 0
