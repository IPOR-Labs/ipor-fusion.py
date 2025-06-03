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
        "AaveV3BalanceFuse": [
            "0xa18304cd502be97d2a00a6774951ccc05c7d8e18",
            "0x4cb1c4774ba1b65802c68adb33de99abf8b21228",
        ],
        "CompoundV3BalanceFuse": [
            "0x8cc97a703c302515aa3c3130f5193d1361928853",
            "0xcf730baa5542dc7570907696271ba96019fcd10c",
        ],
        "ERC4626BalanceFuse": [
            "0x8bf42554f882584df5c16b5e60ac1aa741a21567",
            "0x6b80b3dd68f908bb7e2e10127e6174d7d0584991",
            "0xd347f4bb96531b01c8fab953cf8e920419193a8c",
            "0xdb26af00177bfae4439eb881b7742ef47eddfaf6",
        ],
        "GearboxV3FarmBalanceFuse": [
            "0x635a02b828a5c830afbf1e21699a8862dca4f4c0",
            "0xaa6c8db1da40f685e02564de92bc2276c12729f6",
        ],
        "FluidInstadappStakingBalanceFuse": [
            "0xcd15728565c5090a5ba8985a56ee7fb9274bca4e",
            "0xa99ab2d4709c9fc83779bd4b230e888dccaedaf6",
        ],
        "ZeroBalanceFuse": [
            "0x0f0f43b3559be3319a63082755ad05c158d3b011",
            "0x43f3ca257b7e2d6bbe088b85ef2c2c57e2a3142c",
            "0x7ceae29f467db23210c6fdb95247355c87026ed0",
            "0xf8a9e4d0899d6268421fc1af952d91782728b8e5",
        ],
        "UniswapV3Balance": [
            "0xf5f05a86e4fe84033940fc7faeea5e17c6614945",
            "0xc34478138da02e84d004fc84785385783b3b941a",
        ],
        "ERC20BalanceFuse": ["0x1a047137f2d4dae60853f87dc13ae92c0db2c123"],
        "RamsesV2Balance": ["0xd9fd7d42848a97f946f42c62ec3150d24c0a3835"],
        "BurnRequestFeeFuse": [
            "0x292711bc63184318294593c38ad914acfcd1797b",
            "0xee322e49268760878924d18a645278ab08ae245c",
        ],
    },
    "8453": {
        "MoonwellEnableMarketFuse": ["0xd62542ef1abff0ac71a1b5666cb76801e81104ef"],
        "MorphoFlashLoanFuse": ["0x20f305ce4fc12f9171fcd7c2fbcd7d11f6119265"],
        "MoonwellSupplyFuse": ["0xc4a62bd86db7dd61a875611b2220f9ab6e14ffbf"],
        "MoonwellBorrowFuse": [
            "0x8f6951795193c5ae825397ba35e940c5586e7b7d",
            "0x377a5b195e3c074d982bd7bac66b48d4c3006353",
        ],
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
        "MorphoClaimFuse": ["0x26c740247fc4e1462d4c36e90057cf0e168b3b2b"],
        "ZeroBalanceFuse": [
            "0x706ca1ca4ece9cf23301d6ab35ce6fb7cf25da15",
            "0x2ce01779cfe56dc253d0ac1b47e3bb7e597bcaea",
            "0x341d2459606feb164a986767cb72ddd8230744fe",
        ],
        "ERC20BalanceFuse": ["0x9ba147fc382dbf4d73512a45370ba0b70c25f6aa"],
        "MoonwellBalanceFuse": ["0x86417b0b3b03e8bc8b68377994363796b4ccd3bc"],
        "AaveV3BalanceFuse": ["0xf53f3eaffdf67539256365ca7299540a98b60ba9"],
        "CompoundV3BalanceFuse": ["0x62286efb801ae4ee93733c3bc1bfa0746e5103d8"],
        "Erc4626BalanceFuse": [
            "0xc80e5a95540d6ebfe4970a0743e71c639df8c25e",
            "0xf760384518c9157b82cdc4ecb0f53799970728d9",
        ],
        "FluidInstadappStakingBalanceFuse": [
            "0x29d294d3d8bb422dddc925cb95a903d34eeb208a"
        ],
        "MorphoBalanceFuse": ["0x7916856e11e0ca021967d0d4dac49d737b7d73d5"],
        "AaveV3BorrowFuse": ["0x1df60f2a046f3dce8102427e091c1ea99ae1d774"],
        "Erc4626SupplyFuseMarketId200001": [
            "0x33ed640fc033a9bbe96c8469d1f32b106a4ae8c9"
        ],
        "BurnRequestFeeFuse": ["0x8aad082f04d04d1db2e92160baa630e31c22c073"],
        "MorphoBorrowFuse": ["0x35f44ad1d9f2773da05f4664bf574c760ba47bf6"],
        "MorphoCollateralFuse": ["0xde3fd3a25534471e92c5940d418b0582802b17b6"],
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
        "AaveV3BalanceFuse": ["0x05bcb16a50dafe0526fb7b3941b81b1b74a7877e"],
        "ERC4626BalanceFuse": [
            "0xe1fd88a76e95dd735c6dda45b2aba9e5ffa9a7f3",
            "0xa0777a5b44d36ee425dc0ca828549f06e40e0cee",
        ],
        "GearboxV3FarmBalanceFuse": ["0xe88982097ecdf1dcfc4d500e3392ee0eb70b45f2"],
        "SparkBalanceFuse": ["0xb3ca07c9c10374d51046f94e5547a2c501da0ab4"],
        "Erc4626BalanceFuse": [
            "0xcb6bb5ab51cdc6efb3b81c84f252cfe6bfba6566",
            "0x2c10c36028c430f445a4ba9f7dd096a5dcc75d5e",
            "0x933bff1078ff1a0ca3b53dad00d7b1850af8749b",
            "0x318dc5d24bcc71ba0127a45e009b64bdba0c2edf",
        ],
        "MorphoBalanceFuse": ["0x0ad1776b9319a03216a44aba0242cc0bc7e3cac3"],
        "CompoundV3BalanceFuse": [
            "0x7070d0a706bf79a1e6d12706b9a429b9d8099c8b",
            "0x9ef773720bbf05353b1d5e800e529315325a4481",
        ],
        "FluidInstadappStakingBalanceFuse": [
            "0xe9d0e294a0524962c43eedfa935f1e8112a16aba"
        ],
        "BurnRequestFeeFuse": ["0x79e8b115bd41baee318c1940f42f1a2d94d29ab4"],
        "Erc4626SupplyFuseMarketId100001": [
            "0x12fd0ee183c85940caedd4877f5d3fc637515870"
        ],
        "Erc4626SupplyFuseMarketId100002": [
            "0x83be46881aaeba80b3d647e08a47301db2e4e754"
        ],
        "ZeroBalanceFuse": [
            "0xbc2907d76964510a4232878e7ac6e2b18c474efb",
            "0xe9562d7bd06b43e6391c5be4b3c5f5c2bc1e06bf",
            "0xb1b74e885349cd9d1f0effb2e1ce0fb79959d7cf",
            "0x48bd852d83f6e58af59255abc708e3ddecb1d1e6",
        ],
        "UniversalTokenSwapperFuse": ["0x08dfdbb6ecf19f1fc974e0675783e1150b2b650f"],
        "MorphoCollateralFuse": ["0xe1aa89eb42c23f292cda1544566f6ebee3a67eed"],
        "MorphoBorrowFuse": ["0x9981e75b7254fd268c9182631bf89c86101359d6"],
        "MorphoFlashLoanFuse": ["0x9185033e24db36407b9b1a1886cb47b9533433de"],
        "ERC20BalanceFuse": ["0x6cebf3e3392d0860ed174402884b941dcbb30654"],
        "PlasmaVaultRequestSharesFuse": ["0x7130383298822097531cf5cc5e3414dda1e09542"],
        "PlasmaVaultRedeemFromRequestFuse": [
            "0x906af6a42079adaf1abd92f924a5d4263653af0d"
        ],
        "Erc4626SupplyFuseMarketId100003": [
            "0x53ecc250d70c9f8b88edb817a9097c6caac81a6b"
        ],
        "PendleRedeemPTAfterMaturityFuse": [
            "0x40430a509188b71bda9a0c06b132e978ea2015be"
        ],
        "PendleSwapPTFuse": ["0xeea3812b60ca4c6d0e2672a865bf7217ecd49f95"],
        "AaveV3BorrowFuse": ["0x820d879ef89356b93a7c71addbf45c40a0dde453"],
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
