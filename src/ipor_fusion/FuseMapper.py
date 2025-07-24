from typing import List, Optional

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.error.FuseMappingError import FuseMappingError
from ipor_fusion.error.UnsupportedChainId import UnsupportedChainId

# pylint: disable=consider-using-namedtuple-or-dataclass
mapping = {
    "42161": {
        "UniversalTokenSwapperFuse": ["0xb052b0d983e493b4d40dec75a03d21b70b83c2ca"],
        "RamsesV2NewPositionFuse": ["0xb025cc5e73e2966e12e4d859360b51c1d0f45ea3"],
        "RamsesV2ModifyPositionFuse": ["0xd41501b46a68dea06a460fd79a7bcda9e3b92674"],
        "RamsesV2CollectFuse": ["0x859f5c9d5cb2800a9ff72c56d79323ea01cb30b9"],
        "AaveV3SupplyFuse": [
            "0xd3c752ee5bb80de64f76861b800a8f3b464c50f9",
            "0x9339acd4e73c8a11109f77bc87221bdfc7b7a4fc",
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
            "0xfc938b178e37788cecb6925c9034f589a91783fb",
            "0xd78c731597247883103682eaf51097fb35d819b4",
            "0x5e31e0c31be569b0e8375b35b6354de755525ecc",
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
        "Erc4626SupplyFuseMarketId100001": [
            "0xe7d3a550f0ad32ccbef570c670b5ac004c276f24"
        ],
        "Erc4626SupplyFuseMarketId100002": [
            "0xba4e51a46b562cbaef372c07bf191c7111a067aa"
        ],
        "Erc4626SupplyFuseMarketId100003": [
            "0xf5c5e375d219e573b10f9c90a1815f93951ad275"
        ],
        "Erc4626SupplyFuseMarketId100004": [
            "0x2f34f8566952b055f3cc32acb8b9c8203e91132a"
        ],
        "Erc4626SupplyFuseMarketId100005": [
            "0x28ee4cd5e888f6331b6c6c38959bbbe05ef7d73a"
        ],
        "Erc4626SupplyFuseMarketId100006": [
            "0x0de665bb75556ea407c23b513a5d521064245b3e"
        ],
        "Erc4626BalanceFuse": [
            "0xa57ae6dd2551ede6ead278e47df9df3535935418",
            "0xd3f1f690c9459c1cd7048725f420149668240b7b",
            "0x37189a0f865abacc9445f5e44d2d35426d0f2cf7",
            "0xa1cd5b1112e6700d98a4a4b39a7946fec3a4663b",
        ],
        "Erc4626SupplyFuseMarketId100007": [
            "0x624e2ab4e17aa38b078674eec75dbadcc10e295b"
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
        "CompoundV3SupplyFuse": [
            "0x42fbd4d8f578b902ed9030bf9035a606ddeca26f",
            "0xd72dd19c04362488a4143f43e407ec87a849b72b",
        ],
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
            "0xc8a552bf7279a932b9fca8f527ef292083c5b87f",
        ],
        "ERC20BalanceFuse": ["0x9ba147fc382dbf4d73512a45370ba0b70c25f6aa"],
        "MoonwellBalanceFuse": ["0x86417b0b3b03e8bc8b68377994363796b4ccd3bc"],
        "AaveV3BalanceFuse": ["0xf53f3eaffdf67539256365ca7299540a98b60ba9"],
        "CompoundV3BalanceFuse": ["0x62286efb801ae4ee93733c3bc1bfa0746e5103d8"],
        "Erc4626BalanceFuse": [
            "0xc80e5a95540d6ebfe4970a0743e71c639df8c25e",
            "0xf760384518c9157b82cdc4ecb0f53799970728d9",
            "0x7f4d9efde7efebbafbb506ca3f711764cbc96391",
            "0x3dfe25f60191aaee4213080398d2fdf65ec3cf2f",
            "0xfee84b6af26a481c1819655dade5f5588416e19f",
            "0x903c1abb5a303cf717196e8d12ce87f46de56719",
            "0xe017c7326d371828a6877727e46a203e915af0fd",
            "0xc8c0498e36a4f26744e8222c8749cd0143f6e6eb",
            "0xc5d840b15b8dce3f38989f4be9f42a9308ed8fce",
            "0x684b5c8866aade4e21f8a30697a56a805b108a67",
            "0x32b8389d5cc5e50221c9a127ae2b47968919fd74",
            "0xe1725cc2af00941db5847a59b4aa8bf47ad35bdf",
            "0x9332dafa9376b7e049755fd81b01b23fc772b8a6",
            "0x5f565d142815eb430ab14bc9bda9d12c9fca3c6c",
            "0x1536190e4f647e09198b049ada3e635d413a6fc8",
            "0x22f3ffd6766ee32c63eb3b48439005895ee91d5e",
            "0x03c245c9e611a8dadf26ff8d90fa0d9db1302ed4",
            "0x44b13c4af19d97af4da32c46b533c3e632000bf8",
            "0x4b69ac30feddd922eb26adb30713984d5fb0e972",
            "0x1cb4eaf830874c6d6fcba455a058c55bb7e30591",
            "0xb2713d276a771a8805e25ae539cef29585def021",
            "0xcbd6b2e0c0c7ea4662f6e0d64d531f714c1d1321",
            "0xa515a9a05b4f0011711cc6e3d25e8340697d0cbd",
            "0xfba70e497cf3827152031adaa94c462275d8c087",
            "0xaed326f23f4d93d8e548840eae5f7da463c2a536",
            "0x6343a839a0e7eedfd7d5e820ec64b570e627cf7f",
            "0x9d0376ce38044dfb46fb3ee0dbf17ea9dd788d24",
            "0xf6006a1da8cd7a7d99fc608f3879df65fe06f281",
            "0x0217013cf40b92b2f5841ebbc595ebfbce1f66f3",
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
        "Erc4626SupplyFuseMarketId100001": [
            "0xbe8ab5217f4f251e4a667650fc34a63035c231a8"
        ],
        "Erc4626SupplyFuseMarketId100002": [
            "0xed5ec535e6e6a3051105a8ea2e8bd178951a9eac"
        ],
        "Erc4626SupplyFuseMarketId100003": [
            "0xda0711a0b1b1dd289c4d7c08704dd1e4ccea80c1"
        ],
        "Erc4626SupplyFuseMarketId100004": [
            "0xb187050408857fc2a57be0a5618e39b331425e77"
        ],
        "Erc4626SupplyFuseMarketId100005": [
            "0x633d78849fb91a336077ff25afc3c72c8f6a7045"
        ],
        "Erc4626SupplyFuseMarketId100006": [
            "0xf3ce837d8eba7332ce16b698d8262247d6cb277d"
        ],
        "Erc4626SupplyFuseMarketId100007": [
            "0x928c217e669d9f0f3fc08fb8ac322133a12e1f43",
            "0xb355c889fc2b7e593730e396416e7ab75448d256",
        ],
        "Erc4626SupplyFuseMarketId100008": [
            "0x5b36e95abff98a476fbaeb3e2434dee3eb463f48"
        ],
        "Erc4626SupplyFuseMarketId100009": [
            "0xc3b44addfae29fd170196c324ba0c233c870c77a"
        ],
        "Erc4626SupplyFuseMarketId100010": [
            "0xf4fb53b8831dd70b628f225ab8bbaadc0a93e2dc"
        ],
        "Erc4626SupplyFuseMarketId100011": [
            "0x25c275cbffb6539d81e00afcdbfdbd962f5d3202"
        ],
        "Erc4626SupplyFuseMarketId100012": [
            "0x32df46cff7e6cab610ed6113d7a57f242d061d68"
        ],
        "Erc4626SupplyFuseMarketId100013": [
            "0x7f378328c91f6a4d1dc5dd08daecb6e983f18e61",
            "0x8598432cda45fd6dc836728d6b12f325f78b62b6",
        ],
        "Erc4626SupplyFuseMarketId100014": [
            "0x5cec9bfe577ed59ccd164ec9777093c0663b5170",
            "0xe1cce1485f5a22d64b89d8e0ba2190ca4e8f1ea2",
        ],
        "Erc4626SupplyFuseMarketId100015": [
            "0x55a3d0f6c2e9efe0462f8de232268275fe76e15b",
            "0xddd2ea42b8bccada2dded9db3170ab2f533d1b73",
        ],
        "Erc4626SupplyFuseMarketId100016": [
            "0x1016508d9bb2f546b7f07171311b9de1692576af"
        ],
        "Erc4626SupplyFuseMarketId100017": [
            "0xea1b36c9c655470cbb6e171b9d025783b079da0e"
        ],
        "Erc4626SupplyFuseMarketId100018": [
            "0xd61498ff21c11761b976e8a25d919decb6b42bc2"
        ],
        "Erc4626SupplyFuseMarketId100019": [
            "0xb995c4ea64ed9da2f8ecc9eb6707b6c4dda2293e"
        ],
        "Erc4626SupplyFuseMarketId100020": [
            "0xd76b9c813e5a17879f01d50cf5c90a2ed17a8dbb"
        ],
        "Erc4626SupplyFuseMarketId100021": [
            "0x195f6d86ef29107dbf6270e7f9a7c01e7f03efff"
        ],
        "Erc4626SupplyFuseMarketId100022": [
            "0xe0c43958a9a49a3290de54b4eff72ef7b324c2ee"
        ],
        "Erc4626SupplyFuseMarketId100023": [
            "0xf5c604608df82eb527661ab669973de152eb1f6f"
        ],
        "FluidProofClaimFuse": ["0xb002337c59a4133e328d91ed82c5012472952c6f"],
        "EulerV2SupplyFuse": ["0xfa00806c871558cef982dfc02d7a87e4ad0ec0fa"],
        "PendleSwapPTFuse": ["0x3c715ee10c1cb2c565fd13e35d81df1c986eef76"],
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
            "0xa72f8391d7c9f1991769b76858b8ac54ccee92cf",
            "0xf9a1f7147d04d569af9f9e1b6b713935ca1308fe",
            "0x560c836581476a95b5adf65b1986fba3cf7772f0",
            "0x8c8f2a5250d440bdf6ac21b097be04b07cce78af",
            "0x32971e61678b0a77a07425f617f83c6d5aecf8e7",
            "0x10e2c21205c180654b8eea5b75c3a51014cdb336",
            "0x806b55f731b0bf5d32d9d14785743589ea23fb94",
            "0x19e332aba9cd9387e9310c9645b0a4b03a6e7906",
            "0xf3d20ca7e35687b159c2ea4c3876c89afa27bf11",
            "0x2e3266358674c8a54ead81610c3c41033279e7dd",
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
            "0x759ddf11e56d2915fe10ea8c4dbfc44a8d048e6e",
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
        "MorphoClaimFuse": ["0x6820df665ba09fbbd3240aa303421928ef4c71a1"],
        "Erc4626SupplyFuseMarketId100013": [
            "0x970b4f5522685d4826eceb0377b3ddbf12836dfd"
        ],
        "EulerV2SupplyFuse": [
            "0xdd33b4b6b9a7aa6fcc5f1d1c8ebb649a796fd5b5",
            "0x225d3e01d3ba0ddf904e1fbb46256f7d3a7e7bf0",
        ],
        "EulerV2BalanceFuse": ["0xae9a37dd9229687662834e6696e396e7837baabd"],
        "Erc4626SupplyFuseMarketId100004": [
            "0x06b53af012499d6429741b9d53e868fd89a5d3b2"
        ],
        "Erc4626SupplyFuseMarketId100005": [
            "0x59e58d1a800426df9fddddbd248da0acc4d38f89"
        ],
        "Erc4626SupplyFuseMarketId100006": [
            "0xf492e277d6d6e051f9871e66badfd089fb7bf5e7"
        ],
        "Erc4626SupplyFuseMarketId100007": [
            "0x87e3b7c430368eb4684ef622bae0d4c8c0cd590b"
        ],
        "Erc4626SupplyFuseMarketId100008": [
            "0xbd8a194d188bc27a050f271a923459cab847ca9f"
        ],
        "Erc4626SupplyFuseMarketId100009": [
            "0x62679b25956d525703c810a6c13e2324312649e8"
        ],
        "Erc4626SupplyFuseMarketId100010": [
            "0x01d4fa645f3b98fc9d870dd687de3665f0d45cdf"
        ],
        "Erc4626SupplyFuseMarketId100011": [
            "0x6b9489369015233e049f548ce6c0dedcf17bfb90"
        ],
        "Erc4626SupplyFuseMarketId100012": [
            "0xf16119e669c1fb8264dffd92ecb1ab592f73d8e3"
        ],
        "HarvestDoHardWorkFuse": ["0xda45fe8099358bba400554c9b640170246b43e50"],
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
            FuseMappingError: If no fuse address is found for the given chain_id and fuse_name.
        """
        chain_id_str = str(chain_id)

        if chain_id_str not in mapping:
            raise UnsupportedChainId(chain_id)

        fuse_addresses = mapping.get(chain_id_str, {}).get(fuse_name)

        if not fuse_addresses:
            raise FuseMappingError(f"No fuse address in FUseMapper for {fuse_name}")

        return [Web3.to_checksum_address(address) for address in fuse_addresses]

    @staticmethod
    def find(
        chain_id: int, fuse_name: str, fuses: List[ChecksumAddress]
    ) -> Optional[ChecksumAddress]:
        """
        Find a fuse address from the provided list that matches the given chain ID and fuse name.

        This method searches through the provided list of fuse addresses and returns the first
        address that is found in the mapping for the specified chain ID and fuse name.

        Args:
            chain_id (int): The blockchain ID to search within.
            fuse_name (str): The name of the fuse to search for.
            fuses (List[ChecksumAddress]): List of fuse addresses to search through.

        Returns:
            Optional[ChecksumAddress]: The first matching fuse address if found, None otherwise.
        """
        for fuse in fuses:
            if fuse in FuseMapper.map(chain_id, fuse_name):
                return fuse

        return None
