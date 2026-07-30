"""Unit tests for the public chain registry — pure functions, no blockchain needed."""

import pytest

from ipor_fusion.chains import (
    CHAIN_NAMES,
    SUPPORTED_CHAIN_IDS,
    ensure_supported_chain,
)
from ipor_fusion.errors import IporFusionError, UnsupportedChainError


def test_supported_chains_pass():
    for chain_id in SUPPORTED_CHAIN_IDS:
        ensure_supported_chain(chain_id)


def test_unsupported_known_chain_includes_name():
    with pytest.raises(UnsupportedChainError, match=r"chain 130 \(unichain\)"):
        ensure_supported_chain(130)


def test_unsupported_unknown_chain_plain_id():
    with pytest.raises(UnsupportedChainError, match=r"chain 999999 is not supported"):
        ensure_supported_chain(999999)


def test_message_lists_supported_chains():
    with pytest.raises(
        UnsupportedChainError,
        match=r"ethereum \(1\), base \(8453\), arbitrum \(42161\)",
    ):
        ensure_supported_chain(9745)


def test_error_hierarchy():
    # ValueError so MCP adapters can let it propagate unmapped.
    assert issubclass(UnsupportedChainError, IporFusionError)
    assert issubclass(UnsupportedChainError, ValueError)


def test_supported_ids_all_have_names():
    assert SUPPORTED_CHAIN_IDS <= CHAIN_NAMES.keys()


def test_cli_reexport_is_same_object():
    from ipor_fusion.cli.vault_cmd import CHAIN_NAMES as cli_chain_names

    assert cli_chain_names is CHAIN_NAMES
