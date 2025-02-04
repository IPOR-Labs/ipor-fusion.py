from typing import List

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.error.UnsupportedChainId import UnsupportedChainId

# pylint: disable=consider-using-namedtuple-or-dataclass
mapping = {
    "42161": {
        "UniversalTokenSwapperFuse": ["0xb052b0d983e493b4d40dec75a03d21b70b83c2ca"],
        "RamsesV2NewPositionFuse": ["0xb025cc5e73e2966e12e4d859360b51c1d0f45ea3"],
        "RamsesV2ModifyPositionFuse": ["0xd41501b46a68dea06a460fd79a7bcda9e3b92674"],
        "RamsesV2CollectFuse": ["0x859f5c9d5cb2800a9ff72c56d79323ea01cb30b9"],
        "AaveV3SupplyFuse": [
            "0x9339acd4e73c8a11109f77bc87221bdfc7b7a4fc",
            "0xd3c752ee5bb80de64f76861b800a8f3b464c50f9",
        ],
        "CompoundV3SupplyFuse": [
            "0xb0b3dc1b27c6c8007c9b01a768d6717f6813fe94",
            "0x34bcbc3f10ce46894bb39de0c667257efb35c079",
        ],
        "GearboxV3FarmSupplyFuse": [
            "0xb0fbf6b7d0586c0a5bc1c3b8a98773f4ed02c983",
            "0x50fbc3e2eb2ec49204a41ea47946016703ba358d",
        ],
        "FluidInstadappStakingSupplyFuse": [
            "0x2b83f05e463cbc34861b10cb020b6eb4740bd890",
            "0x962a7f0a2cbe97d4004175036a81e643463b76ec",
        ],
        "AaveV3BorrowFuse": ["0x28264e8b70902f6c55420eaf66aeee12b602302e"],
        "UniswapV2SwapFuse": ["0xada9bf3c599db229601dd1220d0b3ccab6c7db84"],
        "UniswapV3SwapFuse": ["0x84c5ab008c66d664681698a9e4536d942b916f89"],
        "UniswapV3NewPositionFuse": [
            "0x1da7f95e63f12169b3495e2b83d01d0d6592dd86",
            "0x0ce06c57173b7e4079b2afb132cb9ce846ddac9b",
        ],
        "UniswapV3ModifyPositionFuse": ["0xba503b6f2b95a4a47ee9884bbbcd80cace2d2eb3"],
        "UniswapV3CollectFuse": ["0x75781ab6cdce9c505dbd0848f4ad8a97c68f53c1"],
        "FluidInstadappClaimFuse": [
            "0x5c7e10c4d6f65b89c026fc8df69891e6b90a8434",
            "0x12F86cE5a2B95328c882e6A106dE775b04a131bA",
        ],
        "RamsesClaimFuse": ["0x6f292d12a2966c9b796642cafd67549bbbe3d066"],
        "GearboxV3FarmDTokenClaimFuse": ["0xfa209140bba92a64b1038649e7385fa860405099"],
        "CompoundV3ClaimFuse": ["0xfa27f28934d3478f65bcfa158e3096045bfdb1bd"],
        "Erc4626SupplyFuseMarketId3": [
            "0x07cd27531ee9df28292b26eeba3f457609deae07",
            "0xeb58e3adb9e537c06ebe2dee6565b248ec758a93",
        ],
        "Erc4626SupplyFuseMarketId5": [
            "0x4ae8640b3a6b71fa1a05372a59946e66beb05f9f",
            "0x0eA739e6218F67dF51d1748Ee153ae7B9DCD9a25",
        ],
    },
    "8453": {
        "MoonwellEnableMarketFuse": ["0xd62542ef1abff0ac71a1b5666cb76801e81104ef"],
        "MorphoFlashLoanFuse": ["0x20f305ce4fc12f9171fcd7c2fbcd7d11f6119265"],
        "MoonwellSupplyFuse": ["0xc4a62bd86db7dd61a875611b2220f9ab6e14ffbf"],
        "MoonwellBorrowFuse": ["0x8f6951795193c5ae825397ba35e940c5586e7b7d"],
        "UniversalTokenSwapperFuse": ["0xdbc5f9962ce85749f1b3c51ba0473909229e3807"],
        "MoonwellClaimFuse": ["0xd253c5c54248433c7879ac14fb03201e008c5a1e"],
        "AaveV3SupplyFuse": ["0x44dcb8a4c40fa9941d99f409b2948fe91b6c15d5"],
        "CompoundV3SupplyFuse": ["0x42fbd4d8f578b902ed9030bf9035a606ddeca26f"],
        "MorphoSupplyFuse": ["0xae93ef3cf337b9599f0dfc12520c3c281637410f"],
        "Erc4626SupplyFuseMarketId5": ["0x15a1e2950da9ec0da69a704b8940f01bddde86ab"],
        "FluidInstadappStakingSupplyFuse": [
            "0x977e318676158a7695ccfeb00ec18a68c29bf0ef"
        ],
        "FluidInstadappClaimFuse": ["0x4e3139528eba9b85addf1b7e5c36002b7be8c9b2"],
        "CompoundV3ClaimFuse": ["0x10de4b8aa7c363999769f8a8295f4f091a77df4f"],
    },
    "1": {
        "AaveV3SupplyFuse": ["0x465d639eb964158bee11f35e8fc23f704ec936a2"],
        "CompoundV3SupplyFuse": [
            "0x00a220f09c1cf5f549c98fa37c837aed54aba26c",
            "0x4f35094b049e4aa0ea98cfa00fa55f30b12aaf29",
        ],
        "GearboxV3FarmSupplyFuse": ["0xf6016a183745c86dd584488c9e75c00bbd61c34e"],
        "FluidInstadappStakingSupplyFuse": [
            "0xa613249ef6d0c3df83d0593abb63e0638d1d590f"
        ],
        "MorphoSupplyFuse": ["0xd08cb606cee700628e55b0b0159ad65421e6c8df"],
        "SparkSupplyFuse": ["0xb48cf802c2d648c46ac7f752c81e29fa2c955e9b"],
        "Erc4626SupplyFuseMarketId3": ["0x95acdf1c8f4447e655a097aea3f92fb15035485d"],
        "Erc4626SupplyFuseMarketId5": ["0xe49207496bb2cf8c3d4fdadcad8e5f72e780b4ae"],
        "Erc4626SupplyFuseMarketId200001": [
            "0x5e58d1f3c9155c74fc43dbbd0157ef49bafa6a88"
        ],
    },
}


class FuseMapper:

    @staticmethod
    def map(chain_id: int, fuse_name: str) -> List[ChecksumAddress]:
        """
        Load fuse addresses for a given chain_id and fuse_name.

        Args:
            chain_id (int): The blockchain ID.
            fuse_name (str): The name of the fuse.

        Returns:
            List[str]: List of checksum addresses.

        Raises:
            UnsupportedChainId: If the chain_id is not supported.
        """
        chain_id_str = str(chain_id)

        if chain_id_str not in mapping:
            raise UnsupportedChainId(chain_id)

        fuse_addresses = mapping.get(chain_id_str, {}).get(fuse_name)

        if not fuse_addresses:
            return []

        return [Web3.to_checksum_address(address) for address in fuse_addresses]
