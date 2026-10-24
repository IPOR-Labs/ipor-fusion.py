"""HyperCore pre-hook wiring, offline: the selectors each hook is wired on and
the ``setPreHookImplementations`` call that installs both."""

from __future__ import annotations

import pytest
from web3 import Web3

from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.fuses.hypercore_pre_hooks import (
    HYPERCORE_CAPITAL_FLOW_SELECTORS,
    HYPERCORE_PENDING_ACTION_SELECTORS,
    HyperCorePreHookWiring,
)
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
        # Literal counts pin the contracts' 2 + 6 split; len() of the tuples
        # would silently track a change.
        assert wiring.implementations == (TOKEN_B,) * 2 + (TOKEN_A,) * 6
        assert len(set(wiring.selectors)) == 8

    def test_pre_hook_wiring_rejects_a_missing_or_shared_hook(self):
        def wiring(field: str, value: object) -> HyperCorePreHookWiring:
            hooks = {"capital_flow_hook": TOKEN_A, "pending_action_hook": TOKEN_B}
            return HyperCorePreHookWiring(**{**hooks, field: value})  # type: ignore[arg-type]

        for field in ("capital_flow_hook", "pending_action_hook"):
            for zero in (ZERO_ADDR, "0X" + "00" * 20, bytes(20)):
                with pytest.raises(ValueError, match=f"{field} must not be zero"):
                    wiring(field, zero)
            for bad in ("0xzz", "0x1234", TOKEN_A[2:], 5):
                with pytest.raises(ValueError, match=f"{field} is not an address"):
                    wiring(field, bad)
        for spelling in (TOKEN_A, TOKEN_A.lower(), "0X" + TOKEN_A[2:]):
            with pytest.raises(ValueError, match="must differ"):
                wiring("pending_action_hook", spelling)
        with pytest.raises(TypeError):
            HyperCorePreHookWiring(TOKEN_A, TOKEN_B)  # type: ignore[misc]

    def test_pre_hook_wiring_checksums_its_hooks(self):
        wiring = HyperCorePreHookWiring(
            capital_flow_hook=TOKEN_A.lower(),  # type: ignore[arg-type]
            pending_action_hook=bytes.fromhex(TOKEN_B[2:]),  # type: ignore[arg-type]
        )
        assert wiring.capital_flow_hook == TOKEN_A
        assert wiring.pending_action_hook == TOKEN_B
