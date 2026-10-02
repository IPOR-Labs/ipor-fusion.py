"""Shared wrappers of the crosschain executors, dispatchers and factories.

One class per contract, one method per Solidity function (snake_case, same
parameter names), returning a ``Call`` like every other wrapper. The bases
here hold the surface both transports share; ``stargate.contracts`` and
``ccip.contracts`` add what each transport exposes on top. Manager-only
executor functions (``sendAssetToDispatcher``, ``sendCommandToDispatcher``,
``registerChain``, ...) are deliberately absent: the executor's ``MANAGER`` is
the PlasmaVault, so those are reachable only through the fuses in
``ipor_fusion.fuses.crosschain``.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.crosschain.messages import CommandStatus
from ipor_fusion.types import Amount, ChainId


@dataclass(frozen=True, slots=True)
class BalanceObservation:
    """``ICrosschainExecutor.BalanceObservation``: a settled-remote-balance
    attestation for one chain, in the executor's local asset decimals.
    ``state_version`` must equal the executor's acknowledged frontier for
    the chain, ``remote_timestamp`` must be at most ``STALENESS_MAX`` old and
    ``expiry`` must leave at least ``MIN_PROPOSAL_APPROVAL_WINDOW`` (15 min)."""

    chain_id: ChainId
    settled_balance: Amount
    state_version: int
    remote_block: int
    remote_timestamp: int
    expiry: int
    tracked_position_set_hash: bytes

    def as_tuple(self) -> tuple:
        return (
            self.chain_id,
            self.settled_balance,
            self.state_version,
            self.remote_block,
            self.remote_timestamp,
            self.expiry,
            self.tracked_position_set_hash,
        )


def _address(value: str) -> ChecksumAddress:
    return Web3.to_checksum_address(value)


def _address_list(values: list) -> list[ChecksumAddress]:
    return [Web3.to_checksum_address(v) for v in values]


class CrosschainExecutor(ContractWrapper):
    """Read surface shared by both executor transports, plus the attestation
    approve and the claim (``claimAsset`` is manager-only: use
    ``CrosschainClaimFuse`` from the vault; the wrapper is for previews)."""

    def manager(self) -> Call[ChecksumAddress]:
        return self._view("MANAGER()", output_types=["address"], decoder=_address)

    def asset(self) -> Call[ChecksumAddress]:
        return self._view("ASSET()", output_types=["address"], decoder=_address)

    def asset_id(self) -> Call[bytes]:
        return self._view("ASSET_ID()", output_types=["bytes32"], decoder=bytes)

    def factory(self) -> Call[ChecksumAddress]:
        return self._view("FACTORY()", output_types=["address"], decoder=_address)

    def admin(self) -> Call[ChecksumAddress]:
        return self._view("ADMIN()", output_types=["address"], decoder=_address)

    def balance_proposer(self) -> Call[ChecksumAddress]:
        return self._view(
            "BALANCE_PROPOSER()", output_types=["address"], decoder=_address
        )

    def balance_approver(self) -> Call[ChecksumAddress]:
        return self._view(
            "BALANCE_APPROVER()", output_types=["address"], decoder=_address
        )

    def big_change_bps(self) -> Call[int]:
        return self._view("BIG_CHANGE_BPS()", output_types=["uint256"])

    def min_update_interval(self) -> Call[int]:
        return self._view("MIN_UPDATE_INTERVAL()", output_types=["uint256"])

    def transfer_staleness_max(self) -> Call[int]:
        return self._view("TRANSFER_STALENESS_MAX()", output_types=["uint256"])

    def local_decimals(self) -> Call[int]:
        return self._view("LOCAL_DECIMALS()", output_types=["uint8"])

    def shared_decimals(self) -> Call[int]:
        return self._view("SHARED_DECIMALS()", output_types=["uint8"])

    def get_balance(self) -> Call[Amount]:
        """Fail-closed aggregate NAV in local asset decimals: idle plus every
        chain's settled and in-flight buckets. Reverts while a chain with
        exposure is blocked or stale."""
        return self._view("getBalance()", output_types=["uint256"], decoder=Amount)

    def get_balance_by_chain(self, chain_id: ChainId) -> Call[Amount]:
        return self._view(
            "getBalanceByChain(uint256)",
            chain_id,
            output_types=["uint256"],
            decoder=Amount,
        )

    def idle_ledger(self) -> Call[Amount]:
        return self._view("idleLedger()", output_types=["uint256"], decoder=Amount)

    def settled_remote_balance(self, chain_id: ChainId) -> Call[Amount]:
        return self._view(
            "settledRemoteBalance(uint256)",
            chain_id,
            output_types=["uint256"],
            decoder=Amount,
        )

    def outbound_in_flight(self, chain_id: ChainId) -> Call[Amount]:
        return self._view(
            "outboundInFlight(uint256)",
            chain_id,
            output_types=["uint256"],
            decoder=Amount,
        )

    def has_dispatcher(self, chain_id: ChainId) -> Call[bool]:
        return self._view("hasDispatcher(uint256)", chain_id, output_types=["bool"])

    def is_dispatcher_ready(self, chain_id: ChainId) -> Call[bool]:
        return self._view("isDispatcherReady(uint256)", chain_id, output_types=["bool"])

    def chain_blocked(self, chain_id: ChainId) -> Call[bool]:
        return self._view("chainBlocked(uint256)", chain_id, output_types=["bool"])

    def last_approved_observed_at(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "lastApprovedObservedAt(uint256)", chain_id, output_types=["uint64"]
        )

    def approve_balance(self, proposal_id: int) -> Call[None]:
        """``BALANCE_APPROVER`` only."""
        return self._write("approveBalance(uint256)", proposal_id)

    def claim_asset(self, amount: Amount) -> Call[None]:
        """Manager only (the vault); transfers ``min(amount, idle)`` to it."""
        return self._write("claimAsset(uint256)", amount)


class CrosschainDispatcher(ContractWrapper):
    """Read surface shared by both dispatcher transports."""

    def asset(self) -> Call[ChecksumAddress]:
        return self._view("ASSET()", output_types=["address"], decoder=_address)

    def asset_id(self) -> Call[bytes]:
        return self._view("ASSET_ID()", output_types=["bytes32"], decoder=bytes)

    def factory(self) -> Call[ChecksumAddress]:
        return self._view("FACTORY()", output_types=["address"], decoder=_address)

    def executor_chain_id(self) -> Call[ChainId]:
        return self._view(
            "EXECUTOR_CHAIN_ID()", output_types=["uint256"], decoder=ChainId
        )

    def local_decimals(self) -> Call[int]:
        return self._view("LOCAL_DECIMALS()", output_types=["uint8"])

    def shared_decimals(self) -> Call[int]:
        return self._view("SHARED_DECIMALS()", output_types=["uint8"])

    def state_version(self) -> Call[int]:
        return self._view("stateVersion()", output_types=["uint64"])

    def tracked_idle(self) -> Call[Amount]:
        return self._view("trackedIdle()", output_types=["uint256"], decoder=Amount)

    def next_command_sequence(self) -> Call[int]:
        return self._view("nextCommandSequence()", output_types=["uint64"])

    def command_config_epoch(self) -> Call[int]:
        return self._view("commandConfigEpoch()", output_types=["uint64"])

    def is_allowed_vault(self, vault: ChecksumAddress) -> Call[bool]:
        return self._view("isAllowedVault(address)", vault, output_types=["bool"])

    def is_tracked_vault(self, vault: ChecksumAddress) -> Call[bool]:
        return self._view("isTrackedVault(address)", vault, output_types=["bool"])

    def pending_request_shares(self, vault: ChecksumAddress) -> Call[int]:
        return self._view(
            "pendingRequestShares(address)", vault, output_types=["uint256"]
        )

    def command_status(self, command_id: bytes) -> Call[CommandStatus]:
        return self._view(
            "commandStatus(bytes32)",
            command_id,
            output_types=["uint8"],
            decoder=CommandStatus,
        )


class CrosschainFactory(ContractWrapper):
    """Read surface shared by both factory transports."""

    def compute_executor_address(
        self, creator: ChecksumAddress, user_salt: bytes
    ) -> Call[ChecksumAddress]:
        """The CREATE3 address ``creator`` gets for ``user_salt``; independent of
        the executor bytecode, so a new version needs a new salt."""
        return self._view(
            "computeExecutorAddress(address,bytes32)",
            creator,
            user_salt,
            output_types=["address"],
            decoder=_address,
        )

    def executors_of(self, manager: ChecksumAddress) -> Call[list[ChecksumAddress]]:
        """Every executor created with ``manager`` (a PlasmaVault) as its MANAGER."""
        return self._view(
            "executorsOf(address)",
            manager,
            output_types=["address[]"],
            decoder=_address_list,
        )

    def is_executor(self, executor: ChecksumAddress) -> Call[bool]:
        return self._view("isExecutor(address)", executor, output_types=["bool"])

    def salt_of(self, executor: ChecksumAddress) -> Call[bytes]:
        return self._view(
            "saltOf(address)", executor, output_types=["bytes32"], decoder=bytes
        )

    def creation_restricted(self) -> Call[bool]:
        return self._view("creationRestricted()", output_types=["bool"])

    def is_allowed_creator(self, creator: ChecksumAddress) -> Call[bool]:
        return self._view("isAllowedCreator(address)", creator, output_types=["bool"])

    def asset_frozen(self, asset_id: bytes) -> Call[bool]:
        return self._view("assetFrozen(bytes32)", asset_id, output_types=["bool"])

    def creation_codes_configured(self) -> Call[bool]:
        return self._view("creationCodesConfigured()", output_types=["bool"])
