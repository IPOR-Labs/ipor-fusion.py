"""The shadow-runtime guard: a recompiled HyperCore runtime may differ from the
deployed one only in the redirected precompile and CoreWriter addresses. The
vault cycle on the official deployment runs in ``test_simulate_examples_hyperevm.py``."""

import pytest
from _hypercore_shadow import _assert_only_address_redirects


def test_shadow_runtime_guard_rejects_other_instruction_changes() -> None:
    metadata = bytes.fromhex("a00001")
    original = bytes.fromhex("61080100") + metadata
    _assert_only_address_redirects(original, bytes.fromhex("61090100") + metadata)
    with pytest.raises(ValueError, match="unexpected shadow immediate"):
        _assert_only_address_redirects(original, bytes.fromhex("61090200") + metadata)
    with pytest.raises(ValueError, match="shadow opcode changed"):
        _assert_only_address_redirects(original, bytes.fromhex("60080100") + metadata)
