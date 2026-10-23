"""Check a `PlasmaVault.execute` batch before sending it: the caller's role,
each fuse's registration and market, and an `eth_simulateV1` run of the batch.

The static checks mirror what `PlasmaVault.execute` requires on chain: the
caller may call ``execute`` now, every fuse is registered (``UnsupportedFuse``),
answers ``MARKET_ID()``, and every touched market has a balance fuse (the vault
delegatecalls it after the actions; without one it reverts ``AddressEmptyCode``).
The simulation runs the exact batch from the caller and reads ``totalAssets``
before and after. Passing is evidence for that block, not a guarantee for the
block the transaction lands in.
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
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.core.simulation import VaultSimulator, is_simulate_v1_supported
from ipor_fusion.errors import ExecutePreflightError
from ipor_fusion.types import (
    Amount,
    ChainId,
    FuseAddress,
    MarketId,
    PlasmaVaultAddress,
    Severity,
    Verdict,
)

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
    calldata included."""
    if isinstance(calldata, str):
        digits = calldata.strip().removeprefix("0x").removeprefix("0X")
        try:
            calldata = bytes.fromhex(digits)
        except ValueError:
            raise ValueError("calldata is not hex") from None
    if bytes(calldata[:4]) != _EXECUTE_SELECTOR:
        raise ValueError("calldata is not a PlasmaVault.execute call")
    return decode_execute_calldata(calldata)


def _market_id_call(fuse: FuseAddress) -> Call[MarketId]:
    return Call(
        to=fuse, data=_MARKET_ID_SELECTOR, output_types=["uint256"], decoder=MarketId
    )


def _pinned(ctx: Web3Context) -> tuple[Web3Context, int]:
    default = ctx.default_block
    block = default if isinstance(default, int) else ctx.web3.eth.block_number
    return ctx.at_block(block), block


def _permission_finding(
    sender: ChecksumAddress, permission: CallPermission
) -> list[PreflightFinding]:
    if permission.immediate:
        return []
    if permission.delay > 0:
        return [
            PreflightFinding(
                "execute_permission",
                "critical",
                f"{sender} must schedule execute through the access manager first"
                f" (execution delay {permission.delay}s); a direct call reverts",
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
        elif action.market_id not in balance_markets:
            findings.append(
                PreflightFinding(
                    "no_balance_fuse",
                    "critical",
                    f"{where}: market {action.market_id} has no balance fuse;"
                    " execute reverts AddressEmptyCode after the actions",
                )
            )
    return findings


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
    """Check ``actions`` as ``sender``'s `PlasmaVault.execute` at one block
    (``ctx.default_block`` when it is a number, else the latest).

    Two Multicall3 batches and the balance-fuse storage reads cover the static
    checks; ``simulate`` adds one ``eth_simulateV1`` request. Read-only.
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
    findings = _permission_finding(sender, permission) + _action_findings(
        preflight_actions, balance_markets
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
