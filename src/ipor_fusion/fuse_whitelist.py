"""The on-chain Fuse Whitelist: which fuses IPOR lists for each market.

Each chain has one registry contract, ``IporFusionFuseWhitelistProxy`` in the
chain's ``ipor-abi`` ``addresses.json``. It is not enforced on-chain (a vault
can add any fuse), but the IPOR app reads it: a vault allocation held through a
fuse that is not listed shows as unknown, and a deprecated fuse raises a warning.
A fuse listed in ``ipor-abi`` is not necessarily whitelisted.

`read_market_fuses` lists a market's fuses (active and deprecated together);
`read_fuse_entries` reads the state of given fuses; `check_fuses` combines both
for the fuses of a vault or an execute batch and `fuse_whitelist_issues` turns
the result into findings with the whitelisted replacement.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal, TypedDict

import requests
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, Web3RPCError

from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.errors import (
    AddressNotInRegistryError,
    AddressRegistryError,
    UnsupportedChainError,
)
from ipor_fusion.registry import fetch_address_registry
from ipor_fusion.substrates import format_market_label
from ipor_fusion.types import ChainId, FuseAddress, MarketId

FUSE_WHITELIST_REGISTRY_NAME = "IporFusionFuseWhitelistProxy"

# What reading the whitelist can raise: the registry download, a chain without a
# registry file, and the whitelist's own RPC reads. Callers that treat the check
# as optional catch exactly these.
FUSE_WHITELIST_READ_ERRORS: tuple[type[Exception], ...] = (
    AddressRegistryError,
    UnsupportedChainError,
    ContractLogicError,
    Web3RPCError,
    TimeExhausted,
    requests.RequestException,
)

FuseRole = Literal["action", "balance"]
FuseIssueKind = Literal["not_whitelisted", "deprecated"]


class FuseState(IntEnum):
    """`FuseWhitelist.getFuseStates()`: 0 means the fuse is not listed."""

    NOT_LISTED = 0
    ACTIVE = 1
    DEPRECATED = 2
    REMOVED = 3


@dataclass(frozen=True)
class WhitelistEntry:
    """One fuse as the whitelist reports it. ``type_name`` is the whitelist's
    name of ``fuse_type`` (``EulerV2BalanceFuse``), empty when not listed."""

    fuse: FuseAddress
    state: FuseState
    fuse_type: int
    type_name: str
    added_at: int

    @property
    def active(self) -> bool:
        return self.state is FuseState.ACTIVE

    @property
    def role(self) -> FuseRole | None:
        """Balance or action fuse by the type name, ``None`` when the name does
        not tell. The whitelist has no role field and its names are not uniform
        (``UniswapV3Balance``, ``BalanceFuseUniversalTokenSwapperV2``, and
        ``BalanceFusesReader``, which is not a fuse)."""
        name = self.type_name
        if not name or name.endswith("Reader") or "Validation" in name:
            return None
        return "balance" if "Balance" in name else "action"


class FuseWhitelist(ContractWrapper):
    """`IporFusionFuseWhitelist` (the proxy address): read-only getters."""

    def get_fuses_by_market_id(
        self, market_id: MarketId
    ) -> Call[tuple[FuseAddress, ...]]:
        return self._view(
            "getFusesByMarketId(uint256)",
            market_id,
            output_types=["address[]"],
            decoder=lambda fuses: tuple(
                FuseAddress(Web3.to_checksum_address(f)) for f in fuses
            ),
        )

    def get_fuse_by_address(
        self, fuse: FuseAddress
    ) -> Call[tuple[int, int, ChecksumAddress, int]]:
        """``(fuseState, fuseType, fuseAddress, timestamp)``; all zero for a fuse
        that is not listed."""
        return self._view(
            "getFuseByAddress(address)",
            fuse,
            output_types=["uint16", "uint16", "address", "uint32"],
        )

    def get_fuse_types(self) -> Call[dict[int, str]]:
        return self._view(
            "getFuseTypes()",
            output_types=["uint16[]", "string[]"],
            decoder=lambda pair: dict(zip(pair[0], pair[1], strict=True)),
        )


def fuse_whitelist_for_chain(chain_id: ChainId) -> FuseWhitelist | None:
    """The chain's whitelist from the ``ipor-abi`` registry, ``None`` when the
    chain publishes none. Raises `AddressRegistryError` when the registry
    cannot be read and `UnsupportedChainError` for a chain without one."""
    registry = fetch_address_registry(chain_id)
    try:
        return FuseWhitelist(registry.address(FUSE_WHITELIST_REGISTRY_NAME))
    except AddressNotInRegistryError:
        return None


def _entry(
    fuse: FuseAddress,
    raw: tuple[int, int, ChecksumAddress, int],
    types: Mapping[int, str],
) -> WhitelistEntry:
    state, fuse_type, _, added_at = raw
    known = (
        FuseState(state)
        if state in FuseState._value2member_map_
        else FuseState.NOT_LISTED
    )
    return WhitelistEntry(
        fuse=fuse,
        state=known,
        fuse_type=fuse_type,
        type_name=types.get(fuse_type, "") if known is not FuseState.NOT_LISTED else "",
        added_at=added_at,
    )


def read_fuse_entries(
    ctx: Web3Context, whitelist: FuseWhitelist, fuses: Iterable[FuseAddress]
) -> dict[FuseAddress, WhitelistEntry]:
    """The whitelist entry of each fuse (state `FuseState.NOT_LISTED` for one
    the registry does not know), in one Multicall3 batch."""
    unique = list(dict.fromkeys(fuses))
    batch = Multicall3(ctx).batch()
    types = batch.add(whitelist.get_fuse_types())
    raws = {fuse: batch.add(whitelist.get_fuse_by_address(fuse)) for fuse in unique}
    batch.run()
    return {fuse: _entry(fuse, raw.value, types.value) for fuse, raw in raws.items()}


def read_market_fuses(
    ctx: Web3Context, whitelist: FuseWhitelist, market_ids: Iterable[MarketId]
) -> dict[MarketId, tuple[WhitelistEntry, ...]]:
    """Every fuse the whitelist lists for each market, active and deprecated
    together, in two Multicall3 batches."""
    markets = list(dict.fromkeys(market_ids))
    if not markets:
        return {}
    batch = Multicall3(ctx).batch()
    listed = {m: batch.add(whitelist.get_fuses_by_market_id(m)) for m in markets}
    batch.run()
    entries = read_fuse_entries(
        ctx, whitelist, (f for value in listed.values() for f in value.value)
    )
    return {m: tuple(entries[f] for f in listed[m].value) for m in markets}


@dataclass(frozen=True)
class FuseWhitelistCheck:
    """The whitelist state of a set of fuses, plus the listed fuses of every
    market where one of them is not active (to name a replacement)."""

    whitelist: ChecksumAddress
    entries: Mapping[FuseAddress, WhitelistEntry]
    market_fuses: Mapping[MarketId, tuple[WhitelistEntry, ...]]


def check_fuses(
    ctx: Web3Context, whitelist: FuseWhitelist, fuses: Mapping[FuseAddress, MarketId]
) -> FuseWhitelistCheck:
    """Read the entries of ``fuses`` (fuse → its market id) and the whitelist of
    each market where one of them is not active."""
    entries = read_fuse_entries(ctx, whitelist, fuses)
    markets = {fuses[f] for f, e in entries.items() if not e.active}
    return FuseWhitelistCheck(
        whitelist=whitelist.address,
        entries=entries,
        market_fuses=read_market_fuses(ctx, whitelist, sorted(markets)),
    )


@dataclass(frozen=True)
class FuseWhitelistIssue:
    """A fuse that is not active in the whitelist, with what to use instead."""

    kind: FuseIssueKind
    fuse: FuseAddress
    market_id: MarketId
    role: FuseRole
    entry: WhitelistEntry
    replacements: tuple[WhitelistEntry, ...]
    message: str


_CONSEQUENCE: dict[FuseIssueKind, str] = {
    "not_whitelisted": "the IPOR app shows the allocation in this market as unknown",
    "deprecated": "the IPOR app warns the vault's Atomist, and will warn depositors",
}


def _replacements(
    entry: WhitelistEntry, role: FuseRole, listed: Iterable[WhitelistEntry]
) -> tuple[WhitelistEntry, ...]:
    active = [e for e in listed if e.active and e.fuse != entry.fuse]
    if entry.state is FuseState.DEPRECATED:
        same_type = [e for e in active if e.fuse_type == entry.fuse_type]
        if same_type:
            return tuple(same_type)
    same_role = [e for e in active if e.role == role]
    return tuple(same_role or active)


def _issue_message(
    issue_kind: FuseIssueKind,
    fuse: FuseAddress,
    market_id: MarketId,
    role: FuseRole,
    entry: WhitelistEntry,
    replacements: tuple[WhitelistEntry, ...],
) -> str:
    what = f"{role} fuse {fuse} of market {format_market_label(market_id)}"
    status = (
        f"is deprecated in the Fuse Whitelist ({entry.type_name})"
        if issue_kind == "deprecated"
        else "is not in the Fuse Whitelist"
        if entry.state is FuseState.NOT_LISTED
        else "was removed from the Fuse Whitelist"
    )
    use = (
        "use " + ", ".join(f"{e.fuse} ({e.type_name})" for e in replacements)
        if replacements
        else "no active whitelisted fuse covers this market; a new fuse needs the"
        " IPOR whitelist review"
    )
    return f"{what} {status}: {_CONSEQUENCE[issue_kind]}; {use}"


def fuse_whitelist_issues(
    check: FuseWhitelistCheck,
    fuses: Mapping[FuseAddress, MarketId],
    balance_fuses: Iterable[FuseAddress] = (),
) -> list[FuseWhitelistIssue]:
    """One issue per fuse of ``fuses`` that is not active, in ``fuses`` order.
    ``balance_fuses`` marks the ones registered as a market's balance fuse."""
    balance = set(balance_fuses)
    issues: list[FuseWhitelistIssue] = []
    for fuse, market_id in fuses.items():
        entry = check.entries.get(fuse)
        if entry is None or entry.active:
            continue
        kind: FuseIssueKind = (
            "deprecated" if entry.state is FuseState.DEPRECATED else "not_whitelisted"
        )
        role: FuseRole = "balance" if fuse in balance else "action"
        replacements = _replacements(entry, role, check.market_fuses.get(market_id, ()))
        issues.append(
            FuseWhitelistIssue(
                kind=kind,
                fuse=fuse,
                market_id=market_id,
                role=role,
                entry=entry,
                replacements=replacements,
                message=_issue_message(
                    kind, fuse, market_id, role, entry, replacements
                ),
            )
        )
    return issues


# ── The document shared by `fusion fuse whitelist --json` and MCP ───────────


class WhitelistEntryJson(TypedDict):
    fuse: str
    state: str
    fuse_type: int
    type_name: str
    role: FuseRole | None
    added_at: int


class FuseWhitelistJson(TypedDict):
    chain_id: int
    chain: str
    whitelist: str
    market_id: int
    market: str
    fuses: list[WhitelistEntryJson]


def build_fuse_whitelist_json(
    chain_id: ChainId,
    whitelist: FuseWhitelist,
    market_id: MarketId,
    entries: Iterable[WhitelistEntry],
    *,
    include_deprecated: bool = False,
) -> FuseWhitelistJson:
    """A market's whitelist, active fuses first; deprecated and removed ones only
    with ``include_deprecated``."""
    shown = [e for e in entries if e.active or include_deprecated]
    shown.sort(key=lambda e: (not e.active, e.type_name, e.fuse))
    return {
        "chain_id": chain_id,
        "chain": CHAIN_NAMES.get(chain_id, str(chain_id)),
        "whitelist": whitelist.address,
        "market_id": market_id,
        "market": format_market_label(market_id),
        "fuses": [
            {
                "fuse": e.fuse,
                "state": e.state.name.lower(),
                "fuse_type": e.fuse_type,
                "type_name": e.type_name,
                "role": e.role,
                "added_at": e.added_at,
            }
            for e in shown
        ],
    }


def fetch_fuse_whitelist_json(
    ctx: Web3Context, market_id: MarketId, *, include_deprecated: bool = False
) -> FuseWhitelistJson:
    """Read the whitelist of ``market_id`` on ``ctx``'s chain and block and build
    its document. Raises `AddressNotInRegistryError` when the chain publishes no
    whitelist, plus what `fuse_whitelist_for_chain` raises."""
    whitelist = fuse_whitelist_for_chain(ctx.chain_id)
    if whitelist is None:
        raise AddressNotInRegistryError(
            f"{CHAIN_NAMES.get(ctx.chain_id, ctx.chain_id)} publishes no"
            f" {FUSE_WHITELIST_REGISTRY_NAME} in the ipor-abi registry"
        )
    entries = read_market_fuses(ctx, whitelist, [market_id])[market_id]
    return build_fuse_whitelist_json(
        ctx.chain_id,
        whitelist,
        market_id,
        entries,
        include_deprecated=include_deprecated,
    )
