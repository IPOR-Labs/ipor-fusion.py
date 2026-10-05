"""Post-pending transfer outcomes from observed Core and EVM state."""

from dataclasses import replace

import pytest
from web3 import Web3

from ipor_fusion import (
    HyperCoreTransferSnapshot,
    HyperCoreTransferStatus,
    compare_hypercore_transfer,
)
from ipor_fusion.types import Amount

VAULT = Web3.to_checksum_address("0xB8De01Ca5ca57A14671E1c696337134a02B4A478")
USDC = Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f")


def _snapshot(**changes: object) -> HyperCoreTransferSnapshot:
    before = HyperCoreTransferSnapshot(
        block_number=100,
        vault=VAULT,
        token_index=0,
        evm_token=USDC,
        action_nonce=7,
        pending=False,
        settled=True,
        core_user_exists=True,
        core_total_wei=1_400_986_500,
        core_hold_wei=0,
        evm_balance=Amount(0),
    )
    return replace(before, **changes)


def test_core_to_evm_reports_applied_with_fee_on_top() -> None:
    before = _snapshot()
    after = _snapshot(
        block_number=105,
        action_nonce=8,
        core_total_wei=4_709_200,
        evm_balance=Amount(13_959_865),
    )
    outcome = compare_hypercore_transfer(before, after, "core_to_evm")
    assert outcome.status == HyperCoreTransferStatus.APPLIED
    assert outcome.evm_delta_units == 13_959_865
    assert outcome.core_delta_wei == -1_396_277_300


def test_core_to_evm_reports_silent_drop_after_settlement() -> None:
    before = _snapshot()
    after = _snapshot(block_number=105, action_nonce=8)
    assert (
        compare_hypercore_transfer(before, after, "core_to_evm").status
        == HyperCoreTransferStatus.DROPPED
    )


def test_first_deposit_reports_activation_even_if_fee_consumes_credit() -> None:
    before = _snapshot(
        core_total_wei=0, evm_balance=Amount(1_000_000), core_user_exists=False
    )
    after = _snapshot(
        block_number=105,
        action_nonce=8,
        core_total_wei=0,
        evm_balance=Amount(0),
        core_user_exists=True,
    )
    outcome = compare_hypercore_transfer(before, after, "evm_to_core")
    assert outcome.status == HyperCoreTransferStatus.APPLIED
    assert outcome.account_activated is True
    assert outcome.core_delta_wei == 0


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"block_number": 101}, HyperCoreTransferStatus.NOT_ENQUEUED),
        (
            {"block_number": 101, "action_nonce": 8, "pending": True},
            HyperCoreTransferStatus.PENDING,
        ),
        ({"block_number": 101, "action_nonce": 9}, HyperCoreTransferStatus.SUPERSEDED),
        (
            {"block_number": 101, "action_nonce": 8, "core_total_wei": 1_000_000_000},
            HyperCoreTransferStatus.INDETERMINATE,
        ),
    ],
)
def test_core_to_evm_does_not_claim_an_unproved_result(
    changes: dict[str, object], status: HyperCoreTransferStatus
) -> None:
    assert (
        compare_hypercore_transfer(
            _snapshot(), _snapshot(**changes), "core_to_evm"
        ).status
        == status
    )


def test_snapshots_must_track_one_action_and_one_vault() -> None:
    with pytest.raises(ValueError, match="same vault and token"):
        compare_hypercore_transfer(
            _snapshot(),
            _snapshot(
                block_number=105,
                vault=Web3.to_checksum_address(
                    "0x0000000000000000000000000000000000000001"
                ),
            ),
            "core_to_evm",
        )
    with pytest.raises(ValueError, match="later block"):
        compare_hypercore_transfer(_snapshot(), _snapshot(), "core_to_evm")
