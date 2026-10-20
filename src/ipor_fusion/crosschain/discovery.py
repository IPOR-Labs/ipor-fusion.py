"""Discovery: what a crosschain vault is made of, on chain.

``discover_deployment`` reads the vault's CROSSCHAIN market grants: every
``EXECUTOR`` substrate is an executor (its transport detected the way the
fuses detect it, its fuses found on the vault by market id and ``enter``
selector, and its served spokes limited to chains with a ``REMOTE_VAULT`` grant
whose ``hasDispatcher`` is true), every
``REMOTE_VAULT`` substrate a spoke vault a command may target. ``open_lanes``
turns that into one ``CrosschainLane`` per executor and spoke; ``open_lane``
does it for one executor address. ``LANES`` is the only place that knows the
concrete lane classes: a new transport is a new subpackage plus one entry.

On-chain configuration that discovery cannot use raises ``CrosschainDiscoveryError``;
``ValueError`` is kept for a bad argument (a transport kind with no lane).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3.exceptions import Web3Exception

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.crosschain.ccip.contracts import CcipCrosschainExecutor
from ipor_fusion.crosschain.ccip.lane import CcipLane
from ipor_fusion.crosschain.contracts import CrosschainExecutor
from ipor_fusion.crosschain.lane import CrosschainLane, LaneFuses
from ipor_fusion.crosschain.messages import CrosschainTransportKind
from ipor_fusion.crosschain.stargate.contracts import StargateCrosschainExecutor
from ipor_fusion.crosschain.stargate.lane import StargateLane
from ipor_fusion.errors import CrosschainDiscoveryError, EmptyCallResultError
from ipor_fusion.fuses.crosschain.base import (
    CrosschainSubstrateLib,
    CrosschainSubstrateType,
)
from ipor_fusion.types import (
    ZERO_ADDRESS,
    ChainId,
    FuseAddress,
    MarketId,
    PlasmaVaultAddress,
)

T = TypeVar("T")

#: The lane class of every supported transport.
LANES: dict[CrosschainTransportKind, type[CrosschainLane]] = {
    StargateLane.transport_kind: StargateLane,
    CcipLane.transport_kind: CcipLane,
}


def _lane_class(transport_kind: CrosschainTransportKind) -> type[CrosschainLane]:
    try:
        return LANES[transport_kind]
    except KeyError:
        raise ValueError(f"unsupported transport {transport_kind.name}") from None


def _probe(call: Call[T], ctx: Web3Context) -> T | None:
    try:
        return call.call(ctx)
    except (Web3Exception, EmptyCallResultError, ValueError):
        return None


def detect_transport(
    ctx: Web3Context, executor: ChecksumAddress
) -> CrosschainTransportKind:
    """Which transport an executor speaks, the way the fuses decide it: a CCIP
    executor reports ``transportKind()``, a Stargate executor exposes a
    non-zero ``STARGATE_POOL()`` and no ``transportKind()``."""
    transport_kind = _probe(CcipCrosschainExecutor(executor).transport_kind(), ctx)
    if transport_kind is not None:
        if transport_kind not in LANES:
            raise CrosschainDiscoveryError(
                f"{executor} reports transportKind {transport_kind.name}, "
                "which has no lane"
            )
        return transport_kind
    pool = _probe(StargateCrosschainExecutor(executor).stargate_pool(), ctx)
    if pool is not None and pool != ZERO_ADDRESS:
        return CrosschainTransportKind.STARGATE_LAYERZERO
    raise CrosschainDiscoveryError(
        f"{executor} is not a crosschain executor of a known transport"
    )


def discover_lane_fuses(
    ctx: Web3Context,
    fuses: Iterable[FuseAddress],
    transport_kind: CrosschainTransportKind,
    market_id: MarketId,
) -> LaneFuses:
    """Pick this transport's supply and command fuse and the claim fuse out of
    a vault's fuse list: a fuse qualifies by ``MARKET_ID()`` (other markets
    share ``enter`` shapes, ERC-4626 supply for instance) and then by the
    ``enter`` selector present in its bytecode."""
    lane_cls = _lane_class(transport_kind)
    selectors = {
        "supply": function_signature_to_4byte_selector(lane_cls.supply_fuse_cls._ENTER),
        "command": function_signature_to_4byte_selector(
            lane_cls.command_fuse_cls._ENTER
        ),
        "claim": function_signature_to_4byte_selector("enter((address,uint256))"),
    }
    found: dict[str, FuseAddress] = {}
    for fuse in fuses:
        if _probe(_market_id_call(fuse), ctx) != market_id:
            continue
        code = bytes(ctx.web3.eth.get_code(fuse, block_identifier=ctx.default_block))
        for role, selector in selectors.items():
            if selector in code:
                if role in found:
                    raise CrosschainDiscoveryError(
                        f"two {role} fuses on market {market_id}: "
                        f"{found[role]} and {fuse}"
                    )
                found[role] = fuse
    missing = [role for role in selectors if role not in found]
    if missing:
        raise CrosschainDiscoveryError(
            f"no {', '.join(missing)} fuse on market {market_id}"
        )
    return LaneFuses(found["supply"], found["command"], found["claim"])


def _market_id_call(fuse: ChecksumAddress) -> Call[int]:
    return Call(
        to=fuse,
        data=function_signature_to_4byte_selector("MARKET_ID()"),
        output_types=["uint256"],
    )


@dataclass(frozen=True)
class CrosschainExecutorInfo:
    """One executor granted on the vault's crosschain market."""

    address: ChecksumAddress
    transport_kind: CrosschainTransportKind
    factory: ChecksumAddress
    fuses: LaneFuses
    balance_proposer: ChecksumAddress
    balance_approver: ChecksumAddress
    #: Spoke chains with both a REMOTE_VAULT grant and a registered dispatcher.
    spoke_chain_ids: tuple[ChainId, ...]


@dataclass(frozen=True)
class CrosschainDeployment:
    """A hub vault's crosschain market as granted on chain."""

    vault: PlasmaVaultAddress
    market_id: MarketId
    executors: tuple[CrosschainExecutorInfo, ...]
    #: Spoke vaults a command may target, per spoke chain.
    remote_vaults: Mapping[ChainId, tuple[ChecksumAddress, ...]]

    @property
    def spoke_chain_ids(self) -> tuple[ChainId, ...]:
        return tuple(sorted(self.remote_vaults))

    def executor(
        self, transport_kind: CrosschainTransportKind
    ) -> CrosschainExecutorInfo:
        """The one executor of ``transport_kind``; raises when there is none
        or more than one."""
        found = [e for e in self.executors if e.transport_kind == transport_kind]
        if len(found) != 1:
            raise CrosschainDiscoveryError(
                f"{len(found)} {transport_kind.name} executors on market "
                f"{self.market_id} of {self.vault}"
            )
        return found[0]


def discover_deployment(
    hub_ctx: Web3Context, vault: PlasmaVaultAddress, market_id: MarketId
) -> CrosschainDeployment:
    """Read ``vault``'s crosschain market: its executors (transport, factory,
    fuses, attestation keys, spokes served) and the remote vaults per spoke."""
    plasma_vault = PlasmaVault(vault)
    executors: list[ChecksumAddress] = []
    remote_vaults: dict[ChainId, list[ChecksumAddress]] = {}
    for raw in plasma_vault.get_market_substrates(market_id).call(hub_ctx):
        raw_bytes = bytes(raw)
        try:
            substrate = CrosschainSubstrateLib.bytes32_to_substrate(raw_bytes)
        except ValueError as exc:
            raise CrosschainDiscoveryError(
                f"substrate {raw_bytes.hex()} on market {market_id} does not "
                f"decode: {exc}"
            ) from exc
        if substrate.substrate_type == CrosschainSubstrateType.EXECUTOR:
            if substrate.chain_id != 0:
                raise CrosschainDiscoveryError(
                    f"executor substrate {raw_bytes.hex()} on market {market_id} "
                    f"has nonzero chain id {substrate.chain_id}"
                )
            executors.append(substrate.substrate_address)
        elif substrate.substrate_type == CrosschainSubstrateType.REMOTE_VAULT:
            if substrate.chain_id == 0:
                raise CrosschainDiscoveryError(
                    f"remote vault substrate {raw_bytes.hex()} on market "
                    f"{market_id} has zero chain id"
                )
            remote_vaults.setdefault(substrate.chain_id, []).append(
                substrate.substrate_address
            )
        else:
            raise CrosschainDiscoveryError(
                f"substrate {raw_bytes.hex()} on market {market_id} has type "
                f"{substrate.substrate_type.name}"
            )
    fuses = plasma_vault.get_fuses().call(hub_ctx)
    infos = []
    for address in executors:
        wrapper = CrosschainExecutor(address)
        manager = wrapper.manager().call(hub_ctx)
        if manager != vault:
            raise CrosschainDiscoveryError(
                f"executor {address} is managed by {manager}, not {vault}"
            )
        transport_kind = detect_transport(hub_ctx, address)
        infos.append(
            CrosschainExecutorInfo(
                address=address,
                transport_kind=transport_kind,
                factory=wrapper.factory().call(hub_ctx),
                fuses=discover_lane_fuses(hub_ctx, fuses, transport_kind, market_id),
                balance_proposer=wrapper.balance_proposer().call(hub_ctx),
                balance_approver=wrapper.balance_approver().call(hub_ctx),
                spoke_chain_ids=tuple(
                    chain_id
                    for chain_id in sorted(remote_vaults)
                    if wrapper.has_dispatcher(chain_id).call(hub_ctx)
                ),
            )
        )
    return CrosschainDeployment(
        vault=vault,
        market_id=market_id,
        executors=tuple(infos),
        remote_vaults={cid: tuple(v) for cid, v in remote_vaults.items()},
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
        vault = CrosschainExecutor(executor).manager().call(hub_ctx)
        fuses = discover_lane_fuses(
            hub_ctx,
            PlasmaVault(vault).get_fuses().call(hub_ctx),
            transport_kind,
            market_id,
        )
    return _lane_class(transport_kind).open(
        hub_ctx, spoke_ctx, executor=executor, fuses=fuses
    )


def open_lanes(
    hub_ctx: Web3Context,
    spoke_ctxs: Mapping[ChainId, Web3Context],
    *,
    deployment: CrosschainDeployment,
) -> list[CrosschainLane]:
    """One lane per executor and per spoke it serves, for the spokes a context
    is given for."""
    return [
        _lane_class(info.transport_kind).open(
            hub_ctx, spoke_ctxs[chain_id], executor=info.address, fuses=info.fuses
        )
        for info in deployment.executors
        for chain_id in info.spoke_chain_ids
        if chain_id in spoke_ctxs
    ]
