"""Discovery: which transport an executor speaks, which fuses drive it, and
``open_lane``, the one call that builds a ready ``CrosschainLane``.

This is the only module that knows every concrete lane. It detects the
transport the way the fuses do (``transportKind()`` on CCIP, the
``STARGATE_POOL()`` immutable on Stargate) and, unless told which fuses to
use, finds them on the hub vault by market id and ``enter`` selector.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3.exceptions import Web3Exception

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.crosschain.ccip.contracts import (
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
)
from ipor_fusion.crosschain.ccip.lane import CcipLane
from ipor_fusion.crosschain.contracts import CrosschainExecutor
from ipor_fusion.crosschain.lane import CrosschainLane, LaneFuses
from ipor_fusion.crosschain.messages import CrosschainTransportKind
from ipor_fusion.crosschain.stargate.contracts import (
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
)
from ipor_fusion.crosschain.stargate.lane import StargateLane
from ipor_fusion.errors import EmptyCallResultError
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.fuses.crosschain.ccip import (
    CcipCrosschainCommandFuse,
    CcipCrosschainSupplyFuse,
)
from ipor_fusion.fuses.crosschain.stargate import (
    StargateCrosschainCommandFuse,
    StargateCrosschainSupplyFuse,
)
from ipor_fusion.types import MarketId

T = TypeVar("T")


def _probe(call: Call[T]) -> T | None:
    try:
        return call.call()
    except (Web3Exception, EmptyCallResultError, ValueError):
        return None


def detect_transport(
    ctx: Web3Context, executor: ChecksumAddress
) -> CrosschainTransportKind:
    """Which transport an executor speaks, the way the fuses decide it: a CCIP
    executor reports ``transportKind()``, a Stargate executor exposes a
    non-zero ``STARGATE_POOL()`` and no ``transportKind()``."""
    transport_kind = _probe(CcipCrosschainExecutor(ctx, executor).transport_kind())
    if transport_kind is not None:
        return CrosschainTransportKind(transport_kind)
    pool = _probe(StargateCrosschainExecutor(ctx, executor).stargate_pool())
    if pool is not None and pool != ZERO_ADDRESS:
        return CrosschainTransportKind.STARGATE_LAYERZERO
    raise ValueError(f"{executor} is not a crosschain executor of a known transport")


def discover_lane_fuses(
    ctx: Web3Context,
    fuses: Iterable[ChecksumAddress],
    transport_kind: CrosschainTransportKind,
    market_id: MarketId,
) -> LaneFuses:
    """Pick this transport's supply and command fuse and the claim fuse out of
    a vault's fuse list: a fuse qualifies by ``MARKET_ID()`` (other markets
    share ``enter`` shapes, ERC-4626 supply for instance) and then by the
    ``enter`` selector present in its bytecode."""
    if transport_kind == CrosschainTransportKind.STARGATE_LAYERZERO:
        supply_sig, command_sig = (
            StargateCrosschainSupplyFuse._ENTER,
            StargateCrosschainCommandFuse._ENTER,
        )
    elif transport_kind == CrosschainTransportKind.CHAINLINK_CCIP:
        supply_sig, command_sig = (
            CcipCrosschainSupplyFuse._ENTER,
            CcipCrosschainCommandFuse._ENTER,
        )
    else:
        raise ValueError(f"unsupported transport {transport_kind.name}")
    selectors = {
        "supply": function_signature_to_4byte_selector(supply_sig),
        "command": function_signature_to_4byte_selector(command_sig),
        "claim": function_signature_to_4byte_selector("enter((address,uint256))"),
    }
    found: dict[str, ChecksumAddress] = {}
    for fuse in fuses:
        if _probe(_market_id_call(ctx, fuse)) != market_id:
            continue
        code = bytes(ctx.web3.eth.get_code(fuse, block_identifier=ctx.default_block))
        for role, selector in selectors.items():
            if selector in code:
                if role in found:
                    raise ValueError(f"two {role} fuses on market {market_id}")
                found[role] = fuse
    missing = [role for role in selectors if role not in found]
    if missing:
        raise ValueError(f"no {', '.join(missing)} fuse on market {market_id}")
    return LaneFuses(found["supply"], found["command"], found["claim"])


def _market_id_call(ctx: Web3Context, fuse: ChecksumAddress) -> Call[int]:
    return Call(
        to=fuse,
        data=function_signature_to_4byte_selector("MARKET_ID()"),
        output_types=["uint256"],
        ctx=ctx,
    )


def open_lane(
    hub_ctx: Web3Context,
    spoke_ctx: Web3Context,
    *,
    executor: ChecksumAddress,
    market_id: MarketId,
    fuses: LaneFuses | None = None,
) -> CrosschainLane:
    """Build the lane for ``executor`` towards ``spoke_ctx``'s chain: detect
    the transport, wrap both sides and, unless ``fuses`` is given, find the
    fuses on the executor's vault (its ``MANAGER``) under ``market_id``."""
    transport_kind = detect_transport(hub_ctx, executor)
    if fuses is None:
        vault = CrosschainExecutor(hub_ctx, executor).manager().call()
        fuses = discover_lane_fuses(
            hub_ctx,
            PlasmaVault(hub_ctx, vault).get_fuses().call(),
            transport_kind,
            market_id,
        )
    spoke = spoke_ctx.chain_id
    if transport_kind == CrosschainTransportKind.CHAINLINK_CCIP:
        ccip = CcipCrosschainExecutor(hub_ctx, executor)
        return CcipLane(
            executor=ccip,
            dispatcher=CcipCrosschainDispatcher(spoke_ctx, executor),
            spoke_chain_id=spoke,
            fuses=fuses,
            route=ccip.ccip_route(spoke).call(),
        )
    return StargateLane(
        executor=StargateCrosschainExecutor(hub_ctx, executor),
        dispatcher=StargateCrosschainDispatcher(spoke_ctx, executor),
        spoke_chain_id=spoke,
        fuses=fuses,
    )
