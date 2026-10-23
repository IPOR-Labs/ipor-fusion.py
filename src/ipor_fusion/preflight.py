"""Check a `PlasmaVault.execute` batch before sending it: the caller's role,
each fuse's registration and market, and an `eth_simulateV1` run of the batch.

The static checks mirror what `PlasmaVault.execute` requires on chain: the
caller may call ``execute`` now, or has a ready scheduled operation for exactly
this calldata; every fuse is registered (``UnsupportedFuse``) and answers
``MARKET_ID()``; and every touched market but 0, with the markets its balance
dependency graph reaches, has a balance fuse (the vault delegatecalls each after
the actions; without one it reverts ``AddressEmptyCode``).

The static reads are at the base block; the simulation runs the batch from the
caller in the next block, 12 seconds later, and reads ``totalAssets`` before and
after. A pass is evidence for that state, not a guarantee for the block the
transaction lands in, and it covers what the static checks do not (substrates,
pre-hooks, market limits, protocol rules).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.core.access import AccessManager, CallPermission
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.fuse_action import FuseAction
from ipor_fusion.core.fuse_decoding import decode_execute_calldata, fuse_classes_for
from ipor_fusion.core.multicall import Multicall3, Multicall3Batch
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.core.simulation import VaultSimulator, is_simulate_v1_supported
from ipor_fusion.errors import ExecutePreflightError
from ipor_fusion.types import (
    AccessManagerAddress,
    Amount,
    ChainId,
    FuseAddress,
    MarketId,
    PlasmaVaultAddress,
    Severity,
    Verdict,
)

# Market 0 is no market: the vault skips it when it updates balances.
_NO_MARKET = MarketId(0)

PreflightCheck = Literal[
    "execute_permission",
    "fuse_not_registered",
    "fuse_market_unreadable",
    "no_balance_fuse",
    "simulation_failed",
    "simulation_unavailable",
]

_EXECUTE_SELECTOR = function_signature_to_4byte_selector("execute((address,bytes)[])")
_MARKET_ID_SELECTOR = function_signature_to_4byte_selector("MARKET_ID()")
_RANK: dict[Severity, int] = {"ok": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class PreflightFinding:
    check: PreflightCheck
    severity: Severity
    message: str


@dataclass(frozen=True)
class PreflightAction:
    """One action of the batch as the vault sees it: whether its fuse is
    registered, the market it reports and the fuse classes its selector fits."""

    index: int
    fuse: FuseAddress
    registered: bool
    market_id: MarketId | None
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class PreflightSimulation:
    success: bool
    revert_reason: str | None
    gas_used: int
    total_assets_before: Amount | None
    total_assets_after: Amount | None


@dataclass(frozen=True)
class ExecutePreflight:
    vault: PlasmaVaultAddress
    chain_id: ChainId
    block_number: int
    sender: ChecksumAddress
    permission: CallPermission
    scheduled_at: int | None
    actions: tuple[PreflightAction, ...]
    findings: tuple[PreflightFinding, ...]
    simulation: PreflightSimulation | None

    @property
    def verdict(self) -> Verdict:
        worst = max((_RANK[f.severity] for f in self.findings), default=0)
        return next(v for v, rank in _RANK.items() if rank == worst)

    def raise_for_failure(self) -> None:
        """Raise `ExecutePreflightError` listing the critical findings, if any."""
        critical = [f.message for f in self.findings if f.severity == "critical"]
        if critical:
            raise ExecutePreflightError("; ".join(critical))


def parse_execute_actions(calldata: str | bytes) -> list[FuseAction]:
    """The actions of `PlasmaVault.execute` calldata given as bytes or hex (with
    or without ``0x``); raises ``ValueError`` for anything else, ``claimRewards``
    calldata included, and for an encoding the SDK would not produce (trailing
    bytes, non-canonical offsets): the preflight re-encodes the actions, so it
    checks only calldata it reproduces byte for byte."""
    if isinstance(calldata, str):
        digits = calldata.strip().removeprefix("0x").removeprefix("0X")
        try:
            calldata = bytes.fromhex(digits)
        except ValueError:
            raise ValueError("calldata is not hex") from None
    if bytes(calldata[:4]) != _EXECUTE_SELECTOR:
        raise ValueError("calldata is not a PlasmaVault.execute call")
    actions = decode_execute_calldata(calldata)
    canonical = FuseAction.encode_execute_payload(actions, "execute((address,bytes)[])")
    if canonical != bytes(calldata):
        raise ValueError("calldata is not canonically encoded; re-encode it")
    return actions


def _market_id_call(fuse: FuseAddress) -> Call[MarketId]:
    return Call(
        to=fuse, data=_MARKET_ID_SELECTOR, output_types=["uint256"], decoder=MarketId
    )


def _pinned(ctx: Web3Context) -> tuple[Web3Context, int]:
    default = ctx.default_block
    block = default if isinstance(default, int) else ctx.web3.eth.block_number
    return ctx.at_block(block), block


def _permission_finding(
    sender: ChecksumAddress,
    permission: CallPermission,
    scheduled_at: int | None,
    now: int | None,
) -> list[PreflightFinding]:
    if permission.immediate:
        return []
    if permission.delay > 0:
        if scheduled_at and now is not None and scheduled_at <= now:
            return []
        state = (
            f"it is scheduled for {scheduled_at}, after this block ({now})"
            if scheduled_at
            else "no ready schedule exists for this calldata (absent, executed,"
            " cancelled or expired)"
        )
        return [
            PreflightFinding(
                "execute_permission",
                "critical",
                f"{sender} has an execution delay of {permission.delay}s and {state};"
                " a direct call reverts until the operation is scheduled and ready",
            )
        ]
    return [
        PreflightFinding(
            "execute_permission",
            "critical",
            f"{sender} may not call execute (no role, or the vault is closed):"
            " the call reverts AccessManagedUnauthorized",
        )
    ]


def _dependency_closure(
    ctx: Web3Context, vault: PlasmaVault, markets: set[MarketId]
) -> dict[MarketId, MarketId]:
    """Every market the balance dependency graph reaches from ``markets``,
    mapped to the touched market it was reached from (one batch per level)."""
    reached = {market: market for market in markets}
    frontier = sorted(markets)
    while frontier:
        batch: Multicall3Batch = Multicall3(ctx).batch()
        reads = [
            (m, batch.add(vault.get_dependency_balance_graph(m))) for m in frontier
        ]
        batch.run()
        frontier = []
        for market, dependencies in reads:
            for dependency in dependencies.value:
                # Like the vault, never follow market 0 or read its graph.
                if dependency != _NO_MARKET and dependency not in reached:
                    reached[dependency] = reached[market]
                    frontier.append(dependency)
    return reached


def _dependency_findings(
    reached: dict[MarketId, MarketId], balance_markets: set[MarketId]
) -> list[PreflightFinding]:
    return [
        PreflightFinding(
            "no_balance_fuse",
            "critical",
            f"market {market}, a balance dependency of market {origin}, has no"
            " balance fuse; execute reverts AddressEmptyCode after the actions",
        )
        for market, origin in sorted(reached.items())
        if market != origin and market not in balance_markets
    ]


def _action_findings(
    actions: Sequence[PreflightAction], balance_markets: set[MarketId]
) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    for action in actions:
        where = f"action {action.index} ({action.fuse})"
        if not action.registered:
            findings.append(
                PreflightFinding(
                    "fuse_not_registered",
                    "critical",
                    f"{where}: the fuse is not registered on the vault;"
                    " execute reverts UnsupportedFuse",
                )
            )
        if action.market_id is None:
            findings.append(
                PreflightFinding(
                    "fuse_market_unreadable",
                    "critical",
                    f"{where}: MARKET_ID() does not answer; execute reverts",
                )
            )
        elif action.market_id != _NO_MARKET and action.market_id not in balance_markets:
            findings.append(
                PreflightFinding(
                    "no_balance_fuse",
                    "critical",
                    f"{where}: market {action.market_id} has no balance fuse;"
                    " execute reverts AddressEmptyCode after the actions",
                )
            )
    return findings


def _schedule(
    ctx: Web3Context,
    manager: AccessManagerAddress,
    sender: ChecksumAddress,
    vault: PlasmaVaultAddress,
    actions: Sequence[FuseAction],
    permission: CallPermission,
) -> tuple[int | None, int | None]:
    """When a delayed sender's operation for this exact calldata becomes ready,
    and the base block's time; ``(None, None)`` when no schedule applies."""
    if permission.immediate or permission.delay == 0:
        return None, None
    calldata = PlasmaVault(vault).execute(list(actions)).calldata
    operation = AccessManager.hash_operation(sender, vault, calldata)
    scheduled = AccessManager(manager).get_schedule(operation).call(ctx)
    now = ctx.web3.eth.get_block(ctx.default_block)["timestamp"]
    return int(scheduled), int(now)


def _simulate(
    ctx: Web3Context,
    vault: PlasmaVault,
    actions: Sequence[FuseAction],
    sender: ChecksumAddress,
    block: int,
) -> tuple[PreflightSimulation | None, list[PreflightFinding]]:
    if not is_simulate_v1_supported(ctx.web3):
        return None, [
            PreflightFinding(
                "simulation_unavailable",
                "warning",
                "the provider does not support eth_simulateV1: the batch was not run",
            )
        ]
    # The block is the parent: the batch runs in a new block on top of it.
    simulator = VaultSimulator(ctx.web3, vault=vault.address, alpha=sender, block=block)
    before = simulator.observe(vault.total_assets(), label="totalAssets before")
    simulator.execute(list(actions))
    after = simulator.observe(vault.total_assets(), label="totalAssets after")
    result = simulator.run()
    simulation = PreflightSimulation(
        success=result.success,
        revert_reason=result.revert_reason,
        gas_used=result.gas_used,
        total_assets_before=before.value if before.result.success else None,
        total_assets_after=after.value if after.result.success else None,
    )
    if result.success:
        return simulation, []
    return simulation, [
        PreflightFinding(
            "simulation_failed",
            "critical",
            f"the batch reverts: {result.revert_reason or 'no reason returned'}",
        )
    ]


def preflight_execute(
    ctx: Web3Context,
    vault: PlasmaVaultAddress,
    actions: Sequence[FuseAction],
    *,
    sender: ChecksumAddress,
    simulate: bool = True,
) -> ExecutePreflight:
    """Check ``actions`` as ``sender``'s `PlasmaVault.execute` on the state of one
    base block (``ctx.default_block`` when it is a number, else the latest).

    The static checks take a few Multicall3 batches (one per dependency-graph
    level), the ``canCall`` read, the balance-fuse storage reads and, for a
    caller with an execution delay, the schedule of this exact calldata;
    ``simulate`` adds a capability probe and one ``eth_simulateV1`` request.
    Read-only.
    """
    if not actions:
        raise ValueError("preflight needs at least one action")
    ctx, block = _pinned(ctx)
    plasma_vault = PlasmaVault(vault)
    batch = Multicall3(ctx).batch()
    manager = batch.add(plasma_vault.get_access_manager_address())
    fuses = batch.add(plasma_vault.get_fuses())
    unique = list(dict.fromkeys(action.fuse for action in actions))
    markets = {fuse: batch.try_add(_market_id_call(fuse)) for fuse in unique}
    batch.run()
    permission = (
        AccessManager(manager.value)
        .can_call(sender, vault, _EXECUTE_SELECTOR)
        .call(ctx)
    )
    scheduled_at, now = _schedule(
        ctx, manager.value, sender, vault, actions, permission
    )
    balance_markets = {bf.market_id for bf in plasma_vault.read_balance_fuses(ctx)}
    registered = set(fuses.value)
    preflight_actions = tuple(
        PreflightAction(
            index=index,
            fuse=action.fuse,
            registered=action.fuse in registered,
            market_id=markets[action.fuse].value,
            candidates=tuple(cls.__name__ for cls in fuse_classes_for(action)),
        )
        for index, action in enumerate(actions)
    )
    touched = {
        a.market_id
        for a in preflight_actions
        if a.market_id is not None and a.market_id != _NO_MARKET
    }
    findings = (
        _permission_finding(sender, permission, scheduled_at, now)
        + _action_findings(preflight_actions, balance_markets)
        + _dependency_findings(
            _dependency_closure(ctx, plasma_vault, touched), balance_markets
        )
    )
    simulation, simulated = (
        _simulate(ctx, plasma_vault, actions, sender, block) if simulate else (None, [])
    )
    return ExecutePreflight(
        vault=vault,
        chain_id=ctx.chain_id,
        block_number=block,
        sender=sender,
        permission=permission,
        scheduled_at=scheduled_at,
        actions=preflight_actions,
        findings=tuple(findings + simulated),
        simulation=simulation,
    )


# ── The document shared by `fusion vault preflight --json` and MCP ──────────


class PreflightFindingJson(TypedDict):
    check: PreflightCheck
    severity: Severity
    message: str


class PreflightActionJson(TypedDict):
    index: int
    fuse: FuseAddress
    registered: bool
    market_id: MarketId | None
    candidates: list[str]


class PreflightSimulationJson(TypedDict):
    success: bool
    revert_reason: str | None
    gas_used: int
    total_assets_before: Amount | None
    total_assets_after: Amount | None


class ExecutePreflightJson(TypedDict):
    vault: PlasmaVaultAddress
    chain_id: ChainId
    block_number: int
    sender: ChecksumAddress
    verdict: Verdict
    execute_immediate: bool
    execute_delay: int
    execute_scheduled_at: int | None
    actions: list[PreflightActionJson]
    findings: list[PreflightFindingJson]
    simulation: PreflightSimulationJson | None


def build_execute_preflight_json(preflight: ExecutePreflight) -> ExecutePreflightJson:
    """Amounts are raw on-chain integers in the underlying asset's decimals."""
    simulation = preflight.simulation
    return ExecutePreflightJson(
        vault=preflight.vault,
        chain_id=preflight.chain_id,
        block_number=preflight.block_number,
        sender=preflight.sender,
        verdict=preflight.verdict,
        execute_immediate=preflight.permission.immediate,
        execute_delay=int(preflight.permission.delay),
        execute_scheduled_at=preflight.scheduled_at,
        actions=[
            PreflightActionJson(
                index=a.index,
                fuse=a.fuse,
                registered=a.registered,
                market_id=a.market_id,
                candidates=list(a.candidates),
            )
            for a in preflight.actions
        ],
        findings=[
            PreflightFindingJson(check=f.check, severity=f.severity, message=f.message)
            for f in preflight.findings
        ],
        simulation=None
        if simulation is None
        else PreflightSimulationJson(
            success=simulation.success,
            revert_reason=simulation.revert_reason,
            gas_used=simulation.gas_used,
            total_assets_before=simulation.total_assets_before,
            total_assets_after=simulation.total_assets_after,
        ),
    )
