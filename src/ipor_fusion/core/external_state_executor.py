"""Per-vault ExternalStateExecutor wrapper (external-state market NAV mark).

The executor is deployed once per vault and holds the off-vault capital tracked
by the external-state market. Its address lives in the VAULT's ERC-7201 storage
(no public getter exists on-chain), so `for_vault` reads it directly.

NAV for the external-state market is marked by a dual-custodian propose/confirm
on this executor, then a `update_markets_balances([EXTERNAL_STATE])` refresh on
the vault. `mark_nav` runs that whole sequence; the propose/confirm Calls and the
pure `proposal_hash` helper are exposed for callers that orchestrate by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId

if TYPE_CHECKING:
    from ipor_fusion.core.plasma_vault import PlasmaVault


@dataclass(frozen=True, slots=True)
class NavMark:
    """Outcome of a `mark_nav` run: the confirmed proposal plus every receipt.

    `refresh_receipt` is `None` when `mark_nav` was called without a `vault`
    (propose + confirm only, no cached-NAV refresh).
    """

    proposed_at: int
    nonce: int
    proposal_hash: bytes
    propose_receipt: TxReceipt
    confirm_receipt: TxReceipt
    refresh_receipt: TxReceipt | None


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

    # topic0 of `proposeBalance`'s event; its non-indexed data carries the exact
    # nonce, proposedAt, and proposalHash the contract stored -- `mark_nav` reads
    # them from the propose receipt rather than re-querying (race-free).
    _BALANCE_PROPOSED_TOPIC = Web3.keccak(
        text="BalanceProposed(address,address,uint256,uint256,uint64,bytes32)"
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

    def propose_balance(
        self, balance_account: ChecksumAddress, value: Amount
    ) -> Call[None]:
        """CUSTODIAN-only: propose `value` (underlying units) as the new tracked
        balance of `balance_account`. Confirm it from a *different* custodian
        with `confirm_balance`; see `mark_nav` for the full sequence."""
        return self._write("proposeBalance(address,uint256)", balance_account, value)

    def confirm_balance(
        self, balance_account: ChecksumAddress, proposal_hash: bytes
    ) -> Call[None]:
        """CUSTODIAN-only: confirm the pending proposal for `balance_account`.
        Must be sent by a custodian other than the proposer, and `proposal_hash`
        must equal `proposal_hash(...)` over the propose tx's timestamp and the
        post-propose nonce."""
        return self._write(
            "confirmBalance(address,bytes32)", balance_account, proposal_hash
        )

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

    def mark_nav(
        self,
        *,
        value: Amount,
        balance_account: ChecksumAddress,
        proposer_ctx: Web3Context,
        confirmer_ctx: Web3Context,
        vault: PlasmaVault | None = None,
    ) -> NavMark:
        """Mark the external-state NAV: propose `value` (custodian A), confirm it
        (custodian B), and optionally refresh the vault's cached NAV.

        `proposer_ctx` signs the propose; `confirmer_ctx` signs the confirm (and,
        when `vault` is given, the `update_markets_balances([EXTERNAL_STATE])`
        refresh). The two signers MUST be different custodians. The confirm hash,
        nonce, and timestamp are taken from the propose tx's `BalanceProposed`
        event -- exactly what the contract stored, so no read can race the
        executor's global nonce. Returns a `NavMark` with the proposal and every
        receipt.

        The marked balance is valued into the vault's `totalAssets` via the
        external-state market (50), so it moves the share price; the refresh is
        what propagates the new value into the vault's cached NAV.

        Sends up to three transactions and blocks on each; see `NavMark`.

        Security: mark_nav composes `propose_balance` + `confirm_balance`, so it
        needs both custodian keys in one process and does not preserve the
        dual-custodian separation. When that separation is the point, run the two
        primitives from separate services instead.
        """
        proposer = proposer_ctx.signer
        confirmer = confirmer_ctx.signer
        if proposer is None:
            raise ValueError("proposer_ctx must have a signer")
        if confirmer is None:
            raise ValueError("confirmer_ctx must have a signer")
        if Web3.to_checksum_address(proposer) == Web3.to_checksum_address(confirmer):
            raise ValueError("proposer and confirmer must be different custodians")

        propose_receipt = self.propose_balance(balance_account, value).send(
            proposer_ctx
        )
        nonce, proposed_at, proposal_hash = self._parse_balance_proposed(
            propose_receipt
        )
        confirm_receipt = self.confirm_balance(balance_account, proposal_hash).send(
            confirmer_ctx
        )

        refresh_receipt: TxReceipt | None = None
        if vault is not None:
            refresh_receipt = vault.update_markets_balances(
                [MarketId(IporFusionMarkets.EXTERNAL_STATE)]
            ).send(confirmer_ctx)

        return NavMark(
            proposed_at=proposed_at,
            nonce=nonce,
            proposal_hash=proposal_hash,
            propose_receipt=propose_receipt,
            confirm_receipt=confirm_receipt,
            refresh_receipt=refresh_receipt,
        )

    def _parse_balance_proposed(self, receipt: TxReceipt) -> tuple[int, int, bytes]:
        """Pull `(nonce, proposed_at, proposal_hash)` from this executor's
        `BalanceProposed` log in a propose receipt -- the exact values the
        contract stored. Raises if the receipt carries no such log."""
        for log in receipt["logs"]:
            if (
                Web3.to_checksum_address(log["address"]) == self._address
                and log["topics"][0] == self._BALANCE_PROPOSED_TOPIC
            ):
                # data fields: balanceAccount, proposer, value, nonce,
                # proposedAt, proposalHash -- only the last three are needed.
                _, _, _, nonce, proposed_at, proposal_hash = decode(
                    ["address", "address", "uint256", "uint256", "uint64", "bytes32"],
                    bytes(log["data"]),
                )
                return nonce, proposed_at, proposal_hash
        raise ValueError("propose receipt has no BalanceProposed log")
