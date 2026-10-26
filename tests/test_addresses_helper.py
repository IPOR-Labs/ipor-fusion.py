"""The ``miscased`` test helper returns a spelling EIP-55 rejects, or fails."""

import pytest
from _addresses import miscased

from ipor_fusion.types import parse_checksum_address


def test_skips_a_flip_that_leaves_one_case():
    # Flipping B gives all lowercase, which carries no checksum.
    address = "0xBca0000000000000000000000000000000000000"
    bad = miscased(address)
    assert bad.lower() == address.lower()
    with pytest.raises(ValueError, match="invalid address checksum"):
        parse_checksum_address(bad)


def test_fails_when_no_flip_leaves_mixed_case():
    with pytest.raises(AssertionError, match="no wrong checksum"):
        miscased("0xCc00000000000000000000000000000000000000")
