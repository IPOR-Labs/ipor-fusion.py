"""Addresses the shipped guide publishes, parsed out of `fusion://quickstart`.

The from-scratch simulation runs these, so "executed on Base" is a claim CI
re-checks on every run instead of a fork run nobody repeats. Editing an address
in the guide changes what the test executes; dropping one fails the test.
"""

from __future__ import annotations

import re

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.guide import QUICKSTART

_ASSIGNMENT = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*"(?P<address>0x[0-9a-fA-F]{40})"', re.MULTILINE
)

GUIDE_ADDRESSES: dict[str, ChecksumAddress] = {
    match["name"]: Web3.to_checksum_address(match["address"])
    for match in _ASSIGNMENT.finditer(QUICKSTART.text)
}


def guide_address(name: str) -> ChecksumAddress:
    """The address the guide's walk assigns to `name`."""
    return GUIDE_ADDRESSES[name]
