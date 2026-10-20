"""Public path of the fuse encoder base, defined in `ipor_fusion.core.fuse`."""

from ipor_fusion.core.address import (
    _validate_not_zero_address as _validate_not_zero_address,
)
from ipor_fusion.core.fuse import Fuse as Fuse
from ipor_fusion.core.fuse import StakeFuse as StakeFuse
from ipor_fusion.core.fuse import (
    _encode_address_substrate as _encode_address_substrate,
)
from ipor_fusion.core.fuse import (
    _encode_uint248_substrate as _encode_uint248_substrate,
)
from ipor_fusion.core.fuse import _substrate_address_bytes as _substrate_address_bytes
from ipor_fusion.core.fuse import _validate_selector as _validate_selector
from ipor_fusion.core.fuse_action import FuseAction as FuseAction
from ipor_fusion.types import ZERO_ADDRESS as ZERO_ADDRESS
