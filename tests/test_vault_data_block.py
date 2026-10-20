from unittest.mock import MagicMock

import pytest

from ipor_fusion import Web3Context
from ipor_fusion.cache import NullCache
from ipor_fusion.types import ChainId
from ipor_fusion.vault_data import _pinned_default_block


def _ctx() -> tuple[Web3Context, MagicMock]:
    web3 = MagicMock()
    web3.eth.get_block.side_effect = lambda ident: {"number": 4242}
    return Web3Context(web3, ChainId(1), cache=NullCache()), web3


def test_numeric_default_block_is_used_as_is():
    ctx, web3 = _ctx()
    assert _pinned_default_block(ctx.at_block(17)) == 17
    web3.eth.get_block.assert_not_called()


@pytest.mark.parametrize("tag", ["latest", "pending"])
def test_latest_and_pending_read_the_head(tag: str):
    ctx, web3 = _ctx()
    assert _pinned_default_block(ctx.at_block(tag)) is None  # type: ignore[arg-type]
    web3.eth.get_block.assert_not_called()


@pytest.mark.parametrize("tag", ["safe", "finalized", "0x" + "ab" * 32])
def test_tag_or_hash_resolves_to_its_number_once(tag: str):
    ctx, web3 = _ctx()
    assert _pinned_default_block(ctx.at_block(tag)) == 4242  # type: ignore[arg-type]
    web3.eth.get_block.assert_called_once_with(tag)
