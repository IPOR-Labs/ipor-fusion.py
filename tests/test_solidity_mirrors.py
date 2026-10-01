"""Drift check for the Python mirrors of the ipor-fusion Solidity libraries.

``IporFusionMarkets`` and ``Roles`` mirror ``IporFusionMarkets.sol`` and
``Roles.sol`` in ``IPOR-Labs/ipor-fusion/contracts/libraries/``. These tests
fetch both files at a pinned upstream commit and fail on any missing, extra,
or renumbered constant. Sync the mirrors and bump ``IPOR_FUSION_REF`` in the
same change.
"""

from __future__ import annotations

import re
from functools import cache

import pytest
import requests

from ipor_fusion.config.roles import Roles
from ipor_fusion.core.external_state_executor import ExternalStateExecutor
from ipor_fusion.market_ids import IporFusionMarkets

# IPOR-Labs/ipor-fusion main as of 2026-09-10
IPOR_FUSION_REF = "a1f79d6c3d0d426ace4df55ed0fb8ea340580739"

_RAW_URL = "https://raw.githubusercontent.com/IPOR-Labs/ipor-fusion/{ref}/{path}"
_LIBRARIES = "contracts/libraries"
_EXECUTOR_SOL = "contracts/fuses/external_state/ExternalStateExecutor.sol"
_CONSTANT_RE = re.compile(
    r"uint(?:256|64)\s+public\s+constant\s+(?P<name>\w+)\s*=\s*(?P<value>[^;]+);"
)
_TYPE_MAX_RE = re.compile(r"type\(uint(?P<bits>\d+)\)\.max(?:\s*-\s*(?P<offset>\d+))?")
# Stripped before a declaration is parsed. A trailing `// note` otherwise
# becomes a member named `//`, and a `)` or `}` inside a comment truncates the
# body the search captures -- both surface as SDK drift that does not exist.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _parse_solidity_int(value: str) -> int:
    value = value.strip()
    match = _TYPE_MAX_RE.fullmatch(value)
    if match:
        return 2 ** int(match.group("bits")) - 1 - int(match.group("offset") or 0)
    return int(value.replace("_", ""))


@cache
def _solidity_source(path: str) -> str:
    """The contract at `path` (repo-relative), at the pinned upstream commit.

    Cached so the two executor gates share one fetch; `cache` stores nothing
    on a raising path, so a transient failure still re-tries."""
    url = _RAW_URL.format(ref=IPOR_FUSION_REF, path=path)
    try:
        resp = requests.get(url, timeout=15)
    except requests.RequestException as exc:
        pytest.skip(f"cannot fetch {path} from GitHub: {exc}")
    if resp.status_code == 404:
        pytest.fail(f"{url} not found — wrong pinned ref or moved file")
    if resp.status_code != 200:
        pytest.skip(f"GitHub returned HTTP {resp.status_code} for {path}")
    return resp.text


def _solidity_constants(file_name: str) -> dict[str, int]:
    source = _solidity_source(f"{_LIBRARIES}/{file_name}")
    constants = {
        match.group("name"): _parse_solidity_int(match.group("value"))
        for match in _CONSTANT_RE.finditer(source)
    }
    # guard against a silent format change defanging the regex
    assert len(constants) > 20, f"parsed only {len(constants)} constants"
    return constants


def _declared_types(source: str, header: str, terminator: str) -> tuple[str, ...]:
    """The ABI types of a `struct` or `event` body, in declaration order.

    Members are split on their own separators rather than on line ends: the
    sibling `BalanceConfirmed` in this same contract puts every parameter on
    one line, so anchoring per line would parse it as empty.

    Comments are stripped first, so upstream prose neither invents a member nor
    truncates the body at a terminator quoted inside it.

    `indexed` is rejected rather than handled. An indexed parameter belongs in
    the topic0 preimage but NOT in the data `decode()` list, so one type tuple
    could no longer serve both uses -- a split this gate should force someone
    to make deliberately, not absorb silently."""
    body = re.search(
        rf"{re.escape(header)}\s*(?P<body>[^{terminator}]*){re.escape(terminator)}",
        _COMMENT_RE.sub("", source),
    )
    assert body, f"{header!r} not found — the declaration moved or was renamed"
    members = []
    for fragment in re.split(r"[,;]", body.group("body")):
        words = fragment.split()
        if not words:
            continue
        assert "indexed" not in words, (
            f"{header!r} declares an indexed parameter ({fragment.strip()!r}); "
            "the topic0 preimage and the data decode now need separate tuples"
        )
        members.append(words[0])
    assert members, f"parsed no members out of {header!r}"
    return tuple(members)


def test_declared_types_ignores_comments():
    # Needs no network: the hazard is upstream prose, not upstream drift. Both
    # samples carry a terminator inside a comment, which is the case that
    # truncates the captured body rather than merely adding a phantom member.
    struct = """
        struct PendingProposal {
            uint256 value; // underlying units, not shares }
            address proposer;
            /* stamped by proposeBalance */
            uint64 proposedAt;
            uint256 nonce;
        }
    """
    event = """
        event BalanceProposed(
            address balanceAccount, // the account (not the executor)
            uint256 newValue
        );
    """

    assert _declared_types(struct, "struct PendingProposal {", "}") == (
        "uint256",
        "address",
        "uint64",
        "uint256",
    )
    assert _declared_types(event, "event BalanceProposed(", ")") == (
        "address",
        "uint256",
    )


def test_balance_proposed_event_mirrors_the_contract():
    # Guards the topic0 preimage AND the payload decode, which are the same
    # tuple. Asserting them against each other would prove nothing.
    source = _solidity_source(_EXECUTOR_SOL)

    assert (
        _declared_types(source, "event BalanceProposed(", ")")
        == ExternalStateExecutor._BALANCE_PROPOSED_TYPES
    )


def test_pending_proposal_struct_mirrors_the_contract():
    # The auto-getter returns these in declaration order; a swapped pair of
    # same-width fields would decode into plausible garbage.
    source = _solidity_source(_EXECUTOR_SOL)

    assert (
        _declared_types(source, "struct PendingProposal {", "}")
        == ExternalStateExecutor._PENDING_PROPOSAL_TYPES
    )


def test_market_ids_mirror_ipor_fusion_markets_sol():
    solidity = _solidity_constants("IporFusionMarkets.sol")
    aliases = IporFusionMarkets._DEPRECATED_ALIASES
    python = {
        name: value
        for name in dir(IporFusionMarkets)
        if not name.startswith("_")
        and name not in aliases
        and isinstance(value := getattr(IporFusionMarkets, name), int)
    }
    assert python == solidity


def test_roles_mirror_roles_sol():
    solidity = _solidity_constants("Roles.sol")
    python = {role.name: role.value for role in Roles}
    assert python == solidity
