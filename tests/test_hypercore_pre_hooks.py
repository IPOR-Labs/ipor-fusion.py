"""HyperCore pre-hook wiring, offline: the selectors each hook is wired on, the
``setPreHookImplementations`` call that installs both, and the check that the
pair is not swapped."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _multicall import multicall_aware
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import HyperCorePreHookWiringError
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.fuses.hypercore_pre_hooks import (
    HYPERCORE_CAPITAL_FLOW_PRE_HOOK_ID,
    HYPERCORE_CAPITAL_FLOW_SELECTORS,
    HYPERCORE_PENDING_ACTION_SELECTORS,
    HyperCorePreHookWiring,
    capital_flow_pre_hook_id,
)
from ipor_fusion.readers.hypercore import read_hypercore_pre_hook_check
from ipor_fusion.types import ZERO_ADDRESS

TOKEN_A = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
TOKEN_B = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
VAULT_ADDR = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
ZERO_ADDR = Web3.to_checksum_address(ZERO_ADDRESS)


class TestHyperCorePreHookWiring:
    def test_pre_hook_selectors_are_the_vault_entry_points(self):
        vault = PlasmaVault(VAULT_ADDR)
        assert HYPERCORE_CAPITAL_FLOW_SELECTORS == tuple(
            call.calldata[:4]
            for call in (
                vault.deposit(1, TOKEN_A),
                vault.deposit_with_permit(
                    1, TOKEN_A, 1, 27, b"\x00" * 32, b"\x00" * 32
                ),
                vault.mint(1, TOKEN_A),
                vault.withdraw(1, TOKEN_A, TOKEN_A),
                vault.redeem(1, TOKEN_A, TOKEN_A),
                vault.redeem_from_request(1, TOKEN_A, TOKEN_A),
            )
        )
        assert HYPERCORE_PENDING_ACTION_SELECTORS == (
            vault.execute([]).calldata[:4],
            vault.update_markets_balances([]).calldata[:4],
        )

    def test_pre_hook_wiring_pairs_each_hook_with_its_selectors(self):
        wiring = HyperCorePreHookWiring(
            capital_flow_hook=TOKEN_A, pending_action_hook=TOKEN_B
        )
        assert wiring.selectors == (
            HYPERCORE_PENDING_ACTION_SELECTORS + HYPERCORE_CAPITAL_FLOW_SELECTORS
        )
        assert HyperCorePreHookWiring.selectors is wiring.selectors
        # Literal counts pin the contracts' 2 + 6 split; len() of the tuples
        # would silently track a change.
        assert wiring.implementations == (TOKEN_B,) * 2 + (TOKEN_A,) * 6
        assert len(set(wiring.selectors)) == 8

    def test_pre_hook_wiring_rejects_a_missing_or_shared_hook(self):
        for field in ("capital_flow_hook", "pending_action_hook"):
            hooks = {
                "capital_flow_hook": TOKEN_A,
                "pending_action_hook": TOKEN_B,
                field: ZERO_ADDR,
            }
            with pytest.raises(ValueError, match=f"{field} .*must not be zero"):
                HyperCorePreHookWiring(**hooks)
        for spelling in (TOKEN_A, TOKEN_A.lower()):
            with pytest.raises(ValueError, match="must differ"):
                HyperCorePreHookWiring(
                    capital_flow_hook=TOKEN_A, pending_action_hook=spelling
                )
        with pytest.raises(TypeError):
            HyperCorePreHookWiring(TOKEN_A, TOKEN_B)  # type: ignore[misc]


MARKER_SELECTOR = function_signature_to_4byte_selector(
    "HYPERCORE_CAPITAL_FLOW_PRE_HOOK_ID()"
)
REVERT = ContractLogicError("execution reverted")


MARKER = HYPERCORE_CAPITAL_FLOW_PRE_HOOK_ID
SOME_CODE = b"\x60\x80"


def _ctx(
    answers: dict[str, bytes | Exception], *, pending_code: bytes = SOME_CODE
) -> MagicMock:
    """A context whose eth_calls of the marker get ``answers[hook]``, and whose
    ``eth_getCode`` answers ``pending_code``."""

    def handler(to: str, data: bytes) -> bytes:
        assert data == MARKER_SELECTOR
        answer = answers[to]
        if isinstance(answer, Exception):
            raise answer
        return answer

    ctx = MagicMock()
    ctx.default_block = "latest"
    ctx.call.side_effect = multicall_aware(handler)
    ctx.web3.eth.get_code.return_value = pending_code
    return ctx


class TestPreHookCheck:
    wiring = HyperCorePreHookWiring(
        capital_flow_hook=TOKEN_A, pending_action_hook=TOKEN_B
    )

    def test_marker_is_what_the_deployed_hook_returns(self):
        # HYPERCORE_CAPITAL_FLOW_PRE_HOOK_ID() of HyperCoreCapitalFlowPreHook
        # 0xbA999E8b50bDC2672CcE13A1F896aaD7Dc5BE547 on HyperEVM, 2026-11-10.
        assert MARKER == bytes.fromhex(
            "13aa7db4efc6e10a0ffc6b6606a762a6bc11a6dbcc28914bc888588741e104c0"
        )

    def test_marker_call_targets_the_hook(self):
        call = capital_flow_pre_hook_id(TOKEN_A)
        assert (call.to, call.calldata) == (TOKEN_A, MARKER_SELECTOR)

    # Any pending-action answer but the marker passes: the check cannot tell a
    # pending-action hook from another contract that has code.
    @pytest.mark.parametrize(
        "pending_action", [REVERT, b"\x01" * 32], ids=["reverts", "other-value"]
    )
    def test_a_correct_pair_passes_in_one_batch(self, pending_action):
        ctx = _ctx({TOKEN_A: MARKER, TOKEN_B: pending_action})
        read_hypercore_pre_hook_check(ctx, self.wiring)
        assert ctx.call.call_count == 1
        ctx.web3.eth.get_code.assert_called_once_with(
            TOKEN_B, block_identifier="latest"
        )

    @pytest.mark.parametrize(
        ("capital_flow", "pending_action", "problem"),
        [
            (REVERT, MARKER, "the hooks are swapped"),
            # No code: the call succeeds with no data.
            (b"", MARKER, "the hooks are swapped"),
            (b"\x01" * 32, MARKER, "pending_action_hook carries the capital-flow"),
            (REVERT, REVERT, "capital_flow_hook does not carry"),
            (b"", REVERT, "capital_flow_hook does not carry"),
            (b"\x01" * 32, REVERT, "capital_flow_hook does not carry"),
            (MARKER, MARKER, "both hooks carry"),
        ],
        ids=[
            "swapped",
            "swapped-no-code",
            "pending-marked",
            "no-marker",
            "no-code",
            "wrong-marker",
            "both-marked",
        ],
    )
    def test_a_wrong_pair_raises(self, capital_flow, pending_action, problem):
        ctx = _ctx({TOKEN_A: capital_flow, TOKEN_B: pending_action})
        with pytest.raises(HyperCorePreHookWiringError, match=problem) as info:
            read_hypercore_pre_hook_check(ctx, self.wiring)
        assert TOKEN_A in str(info.value) and TOKEN_B in str(info.value)
        # The code is read only for a pair whose markers are right.
        ctx.web3.eth.get_code.assert_not_called()

    def test_a_pending_action_hook_without_code_raises(self):
        ctx = _ctx({TOKEN_A: MARKER, TOKEN_B: b""}, pending_code=b"")
        with pytest.raises(
            HyperCorePreHookWiringError, match="pending_action_hook has no code"
        ):
            read_hypercore_pre_hook_check(ctx, self.wiring)
