from ipor_fusion.fuses.base import FuseAction, Fuse, StakeFuse
from ipor_fusion.fuses.aave_v3 import AaveV3SupplyFuse, AaveV3BorrowFuse
from ipor_fusion.fuses.morpho import (
    MorphoSupplyFuse,
    MorphoCollateralFuse,
    MorphoBorrowFuse,
    MorphoFlashLoanFuse,
    MorphoClaimFuse,
)
from ipor_fusion.fuses.uniswap_v3 import (
    UniswapV3SwapFuse,
    UniswapV3NewPositionFuse,
    UniswapV3ModifyPositionFuse,
    UniswapV3CollectFuse,
    UniswapV3NewPositionEvent,
    UniswapV3ClosePositionEvent,
    UniswapV3Events,
)
from ipor_fusion.fuses.compound_v3 import CompoundV3SupplyFuse
from ipor_fusion.fuses.ramses_v2 import (
    RamsesV2NewPositionFuse,
    RamsesV2ModifyPositionFuse,
    RamsesV2CollectFuse,
    RamsesClaimFuse,
    RamsesNewPositionEvent,
    RamsesEvents,
)
from ipor_fusion.fuses.gearbox_v3 import GearboxSupplyFuse, GearboxStakeFuse
from ipor_fusion.fuses.erc4626 import ERC4626SupplyFuse
from ipor_fusion.fuses.universal import UniversalTokenSwapperFuse
from ipor_fusion.fuses.fluid_instadapp import (
    FluidInstadappSupplyFuse,
    FluidInstadappStakingFuse,
)

__all__ = [
    "FuseAction",
    "Fuse",
    "StakeFuse",
    "AaveV3SupplyFuse",
    "AaveV3BorrowFuse",
    "MorphoSupplyFuse",
    "MorphoCollateralFuse",
    "MorphoBorrowFuse",
    "MorphoFlashLoanFuse",
    "MorphoClaimFuse",
    "UniswapV3SwapFuse",
    "UniswapV3NewPositionFuse",
    "UniswapV3ModifyPositionFuse",
    "UniswapV3CollectFuse",
    "UniswapV3NewPositionEvent",
    "UniswapV3ClosePositionEvent",
    "UniswapV3Events",
    "CompoundV3SupplyFuse",
    "RamsesV2NewPositionFuse",
    "RamsesV2ModifyPositionFuse",
    "RamsesV2CollectFuse",
    "RamsesClaimFuse",
    "RamsesNewPositionEvent",
    "RamsesEvents",
    "GearboxSupplyFuse",
    "GearboxStakeFuse",
    "ERC4626SupplyFuse",
    "UniversalTokenSwapperFuse",
    "FluidInstadappSupplyFuse",
    "FluidInstadappStakingFuse",
]
