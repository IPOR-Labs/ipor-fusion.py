"""Per-vault ExternalStateExecutor wrapper (external-state market NAV mark).

The executor is deployed once per vault and holds the off-vault capital tracked
by the external-state market. Its address lives in the VAULT's ERC-7201 storage
(no public getter exists on-chain), so `for_vault` reads it directly.

NAV for the external-state market is marked by a dual-custodian propose/confirm
on this executor, then a `update_markets_balances([EXTERNAL_STATE])` refresh on
the vault. This module exposes the reads and the pure `proposal_hash` helper;
the propose/confirm write path builds on top of them.
"""

from __future__ import annotations

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.types import Amount, ChainId


class ExternalStateExecutor(ContractWrapper):
    """Per-vault ExternalStateExecutor: NAV propose/confirm plus its reads.

    Use `for_vault` when you have only the vault address -- it resolves the
    executor from the vault's ERC-7201 storage. Construct directly,
    `ExternalStateExecutor(ctx, executor_address)`, when the executor address is
    already known (config, a cached resolution, a deploy event), to skip the
    storage read.
    """

    # ERC-7201 slot `io.ipor.externalState.Executor`; `executor` is the struct's
    # first field, so it sits at the base slot. No public getter exists on-chain,
    # so resolution reads this slot directly. Mirrors the contract's own
    # pre-computed constant; a unit test re-derives it from the namespace.
    _EXECUTOR_STORAGE_SLOT = (
        0x1781023874512EC457C16827AD102F41A5C5CE1CD7BA8AA8FCD2DA52541D8A00
    )

    @classmethod
    def for_vault(
        cls, ctx: Web3Context, vault_address: ChecksumAddress
    ) -> ExternalStateExecutor:
        """Resolve the per-vault executor from vault storage and wrap it.

        Raises `ValueError` when no executor is deployed yet (the slot reads
        zero) rather than returning a wrapper around the zero address, and lets
        `decode` reject a slot whose high 12 bytes are non-zero (a wrong or
        corrupt slot, not an address-in-slot).
        """
        # Left-pad so an RPC that strips leading zero bytes still decodes; the
        # address is the slot's low 20 bytes, `decode` verifies the rest is zero.
        raw = bytes(
            ctx.get_storage_at(
                Web3.to_checksum_address(vault_address), cls._EXECUTOR_STORAGE_SLOT
            )
        ).rjust(32, b"\x00")
        executor = Web3.to_checksum_address(decode(["address"], raw)[0])
        if executor == ZERO_ADDRESS:
            raise ValueError(
                f"no ExternalStateExecutor deployed for vault {vault_address}"
            )
        return cls(ctx, executor)

    def nonce(self) -> Call[int]:
        """Monotonic proposal nonce, incremented on every `proposeBalance`.

        Read it AFTER a propose to bind that proposal's hash (the contract does
        `++nonce` before storing the pending proposal)."""
        return self._view("nonce()", output_types=["uint256"])

    @staticmethod
    def proposal_hash(
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        balance_account: ChecksumAddress,
        value: Amount,
        proposer: ChecksumAddress,
        proposed_at: int,
        nonce: int,
    ) -> bytes:
        """The confirm hash that `confirmBalance` verifies against:
        ``keccak(abi.encode(executor, chainId, balanceAccount, value, proposer,
        proposedAt, nonce))``.

        `proposed_at` is the propose tx's block timestamp; `nonce` is `nonce()`
        read after the propose. Pure -- no chain access -- so callers can
        pre-compute or verify a hash offline.
        """
        return keccak(
            encode(
                [
                    "address",
                    "uint256",
                    "address",
                    "uint256",
                    "address",
                    "uint64",
                    "uint256",
                ],
                [
                    Web3.to_checksum_address(executor),
                    int(chain_id),
                    Web3.to_checksum_address(balance_account),
                    int(value),
                    Web3.to_checksum_address(proposer),
                    int(proposed_at),
                    int(nonce),
                ],
            )
        )
