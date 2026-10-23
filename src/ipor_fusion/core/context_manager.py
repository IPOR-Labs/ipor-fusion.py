"""IPOR Fusion ContextManager: runs calls on context-aware targets (the vault's
managers and fuses) on behalf of the caller."""

from __future__ import annotations

from collections.abc import Sequence

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper


def _address_list_decoder(value: tuple[str, ...]) -> list[ChecksumAddress]:
    return [Web3.to_checksum_address(item) for item in value]


class ContextManager(ContractWrapper):
    """Runs each ``(target, data)`` call with the caller set as the target's
    context sender, so a contract that reads the context acts for the caller.
    Only approved targets can be called."""

    def get_approved_targets(self) -> Call[list[ChecksumAddress]]:
        return self._view(
            "getApprovedTargets()",
            output_types=["address[]"],
            decoder=_address_list_decoder,
        )

    def is_target_approved(self, target: ChecksumAddress) -> Call[bool]:
        return self._view(
            "isTargetApproved(address)", target, output_types=["bool"], decoder=bool
        )

    def get_nonce(self, sender: ChecksumAddress) -> Call[int]:
        """The last nonce ``sender`` used in a signed context call; the next
        signed call must carry a higher one."""
        return self._view(
            "getNonce(address)", sender, output_types=["uint256"], decoder=int
        )

    def add_approved_targets(self, targets: Sequence[ChecksumAddress]) -> Call[None]:
        return self._write("addApprovedTargets(address[])", list(targets))

    def remove_approved_targets(self, targets: Sequence[ChecksumAddress]) -> Call[None]:
        return self._write("removeApprovedTargets(address[])", list(targets))

    def run_with_context(
        self, calls: Sequence[tuple[ChecksumAddress, bytes]]
    ) -> Call[None]:
        """Run ``calls`` in order, atomically; every target must be approved."""
        return self._write(
            "runWithContext((address[],bytes[]))",
            ([target for target, _ in calls], [data for _, data in calls]),
        )
