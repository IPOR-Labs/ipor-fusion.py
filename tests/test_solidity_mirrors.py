"""Drift check for the Python mirrors of the ipor-fusion Solidity libraries.

``IporFusionMarkets`` and ``Roles`` mirror ``IporFusionMarkets.sol`` and
``Roles.sol`` in ``IPOR-Labs/ipor-fusion/contracts/libraries/``. These tests
fetch both files at a pinned upstream commit and fail on any missing, extra,
or renumbered constant. Sync the mirrors and bump ``IPOR_FUSION_REF`` in the
same change.
"""

from __future__ import annotations

import re

import pytest
import requests

from ipor_fusion.config.roles import Roles
from ipor_fusion.market_ids import IporFusionMarkets

# IPOR-Labs/ipor-fusion main as of 2026-09-10
IPOR_FUSION_REF = "a1f79d6c3d0d426ace4df55ed0fb8ea340580739"

_RAW_URL = (
    "https://raw.githubusercontent.com/IPOR-Labs/ipor-fusion/"
    "{ref}/contracts/libraries/{name}"
)
_CONSTANT_RE = re.compile(
    r"uint(?:256|64)\s+public\s+constant\s+(?P<name>\w+)\s*=\s*(?P<value>[^;]+);"
)
_TYPE_MAX_RE = re.compile(r"type\(uint(?P<bits>\d+)\)\.max(?:\s*-\s*(?P<offset>\d+))?")


def _parse_solidity_int(value: str) -> int:
    value = value.strip()
    match = _TYPE_MAX_RE.fullmatch(value)
    if match:
        return 2 ** int(match.group("bits")) - 1 - int(match.group("offset") or 0)
    return int(value.replace("_", ""))


def _solidity_constants(file_name: str) -> dict[str, int]:
    url = _RAW_URL.format(ref=IPOR_FUSION_REF, name=file_name)
    try:
        resp = requests.get(url, timeout=15)
    except requests.RequestException as exc:
        pytest.skip(f"cannot fetch {file_name} from GitHub: {exc}")
    if resp.status_code == 404:
        pytest.fail(f"{url} not found — wrong pinned ref or moved file")
    if resp.status_code != 200:
        pytest.skip(f"GitHub returned HTTP {resp.status_code} for {file_name}")
    source = resp.text
    constants = {
        match.group("name"): _parse_solidity_int(match.group("value"))
        for match in _CONSTANT_RE.finditer(source)
    }
    # guard against a silent format change defanging the regex
    assert len(constants) > 20, f"parsed only {len(constants)} constants"
    return constants


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
