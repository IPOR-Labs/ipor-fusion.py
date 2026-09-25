"""Crosschain market fuses (``IporFusionMarkets.CROSSCHAIN``): the shared layer.

A source PlasmaVault (the hub) deploys capital into PlasmaVaults on other
chains through an executor it drives with these fuses; a dispatcher at the
same address on every remote chain holds the remote position. Two transports
exist and each has its own supply and command fuse (``stargate`` and ``ccip``
next to this module); the claim fuse and the substrates are shared. Names,
methods and parameters follow ``contracts/fuses/crosschain/**``.

Every fuse is bound to one market id at construction (``MARKET_ID``), and the
deployments so far were built with a keccak-derived id rather than the
library constant, so the market id is a parameter here, never an assumption
(see :func:`crosschain_market_id`).

``CrosschainSupplyFuse`` and ``CrosschainCommandFuse`` are the SDK's
transport-agnostic layer (the contracts have none): the operations both
transports share, with the transport specifics in a ``SendParams``
(``StargateSendParams`` / ``CcipSendParams``). ``CrosschainTransport`` hands
out the right implementation for an executor's transport.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar

from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.crosschain.messages import Command
from ipor_fusion.fuses.base import (
    Fuse,
    FuseAction,
    _substrate_address_bytes,
    _validate_not_zero_address,
)
from ipor_fusion.types import Amount, ChainId, MarketId

_NO_CHAIN = ChainId(0)
_ZERO_AMOUNT = Amount(0)


class SendParams:
    """Marker base of the per-transport send parameters (``StargateSendParams``,
    ``CcipSendParams``): what a transport needs to pay for and deliver one send."""

    __slots__ = ()


def crosschain_market_id(preimage: str) -> MarketId:
    """``uint256(keccak256(preimage))``: the market id a POC deployment baked
    into its fuses (the mainnet POC used ``IPOR_FUSION_CROSSCHAIN_USDC_POC_V1``).
    New deployments use ``IporFusionMarkets.CROSSCHAIN``."""
    return MarketId(int.from_bytes(keccak(text=preimage), "big"))


class CrosschainSubstrateType(IntEnum):
    """``CrosschainSubstrateLib.CrosschainSubstrateType``."""

    UNDEFINED = 0
    EXECUTOR = 1
    REMOTE_VAULT = 2


@dataclass(frozen=True, slots=True)
class CrosschainSubstrate:
    """``CrosschainSubstrateLib.CrosschainSubstrate``: a decoded grant.
    ``chain_id`` is 0 for an EXECUTOR (it lives on the vault's chain)."""

    substrate_type: CrosschainSubstrateType
    chain_id: ChainId
    substrate_address: ChecksumAddress


class CrosschainSubstrateLib:
    """``CrosschainSubstrateLib.sol``: the typed bytes32 substrates of the
    crosschain market, ``type << 248 | chainId << 160 | address``.

    - EXECUTOR: an executor the vault's fuses may drive; the fuses also require
      ``executor.MANAGER() == vault``.
    - REMOTE_VAULT: a ``(chainId, remote PlasmaVault)`` pair the command fuse may
      target or allowlist on a dispatcher. The grant is chain-bound.
    """

    MAX_SUBSTRATE_CHAIN_ID = (1 << 88) - 1

    @classmethod
    def substrate_to_bytes32(cls, substrate: CrosschainSubstrate) -> bytes:
        if not 0 <= substrate.chain_id <= cls.MAX_SUBSTRATE_CHAIN_ID:
            raise ValueError(f"chain id does not fit 88 bits: {substrate.chain_id}")
        _validate_not_zero_address(substrate.substrate_address, "substrate_address")
        return (
            bytes([int(substrate.substrate_type)])
            + int(substrate.chain_id).to_bytes(11, "big")
            + _substrate_address_bytes(substrate.substrate_address)
        )

    @classmethod
    def bytes32_to_substrate(cls, raw: bytes) -> CrosschainSubstrate:
        if len(raw) != 32:
            raise ValueError(f"substrate must be 32 bytes, got {len(raw)}")
        return CrosschainSubstrate(
            substrate_type=CrosschainSubstrateType(raw[0]),
            chain_id=ChainId(int.from_bytes(raw[1:12], "big")),
            substrate_address=Web3.to_checksum_address(raw[12:]),
        )

    @classmethod
    def executor_substrate(cls, executor: ChecksumAddress) -> bytes:
        """``executorSubstrate(executor)``: chain id slot is zero."""
        return cls.substrate_to_bytes32(
            CrosschainSubstrate(CrosschainSubstrateType.EXECUTOR, ChainId(0), executor)
        )

    @classmethod
    def remote_vault_substrate(cls, chain_id: ChainId, vault: ChecksumAddress) -> bytes:
        """``remoteVaultSubstrate(chainId, vault)``."""
        if chain_id == 0:
            raise ValueError("chain_id must not be zero")
        return cls.substrate_to_bytes32(
            CrosschainSubstrate(CrosschainSubstrateType.REMOTE_VAULT, chain_id, vault)
        )


class CrosschainSupplyFuse(Fuse, ABC):
    """Transport-agnostic surface of the supply fuses: ``enter`` bridges
    capital out to a dispatcher, ``exit`` requests it back. The transport
    specifics travel in ``send`` (``StargateSendParams`` or
    ``CcipSendParams``); each implementation rejects the other kind. Not an
    instant-withdrawal fuse: tokens arrive later and are pulled home with
    ``CrosschainClaimFuse``."""

    #: Solidity signature of ``enter``; discovery finds the fuse by its selector.
    _ENTER: ClassVar[str]

    @abstractmethod
    def enter(
        self,
        *,
        executor: ChecksumAddress,
        asset: ChecksumAddress,
        dst_chain_id: ChainId,
        amount: Amount,
        send: SendParams,
    ) -> FuseAction:
        """Transfer up to ``amount`` of ``asset`` (the executor's ``ASSET()``)
        to the executor and bridge it to the dispatcher on ``dst_chain_id``."""

    @abstractmethod
    def exit(
        self,
        *,
        executor: ChecksumAddress,
        dst_chain_id: ChainId,
        amount: Amount,
        min_return: Amount,
        send: SendParams,
    ) -> FuseAction:
        """Request ``amount`` back from the dispatcher on ``dst_chain_id`` with
        a ``min_return`` floor."""

    def _validate_enter(
        self,
        executor: ChecksumAddress,
        asset: ChecksumAddress,
        dst_chain_id: ChainId,
        amount: Amount,
    ) -> None:
        self._validate_address(executor, "executor")
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        _validate_chain_id(dst_chain_id)

    def _validate_exit(
        self,
        executor: ChecksumAddress,
        dst_chain_id: ChainId,
        amount: Amount,
        min_return: Amount,
    ) -> None:
        self._validate_address(executor, "executor")
        self._validate_amount(amount, "amount")
        self._validate_non_negative(min_return, "min_return")
        _validate_chain_id(dst_chain_id)


class CrosschainCommandFuse(Fuse, ABC):
    """Transport-agnostic surface of the command fuses: the operations both
    transports implement with the same semantics. Everything else (chain or
    route registration, balance refresh, retry, cancel) is transport-specific
    and goes through each implementation's ``enter``."""

    #: Solidity signature of ``enter``; discovery finds the fuse by its selector.
    _ENTER: ClassVar[str]

    @abstractmethod
    def send_command(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        command: Command,
        send: SendParams | None = None,
    ) -> FuseAction:
        """Send one business ``command`` to the dispatcher on ``chain_id``; its
        target vault must be a granted REMOTE_VAULT for that chain."""

    @abstractmethod
    def create_dispatcher(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        plasma_vaults: Sequence[ChecksumAddress],
        send: SendParams | None = None,
    ) -> FuseAction:
        """Request a dispatcher on ``chain_id`` with ``plasma_vaults`` as its
        initial allowlist (each granted as a REMOTE_VAULT for that chain)."""

    @abstractmethod
    def update_vaults(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        plasma_vaults: Sequence[ChecksumAddress],
        send: SendParams | None = None,
    ) -> FuseAction:
        """Replace the dispatcher's deposit allowlist with exactly ``plasma_vaults``."""


def _validate_chain_id(chain_id: int) -> None:
    if chain_id <= 0:
        raise ValueError(f"chain id must be positive, got {chain_id}")


class CrosschainClaimFuse(Fuse):
    """``CrosschainClaimFuse``: pull settled idle executor assets back into the
    vault. Transport-agnostic."""

    def enter(self, *, executor: ChecksumAddress, amount: Amount) -> FuseAction:
        """Claim up to ``amount`` (capped at the executor's idle ledger) of the
        executor's asset. The measured receipt is what the balance fuse removes
        from the cached market value."""
        self._validate_address(executor, "executor")
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((address,uint256))", [[executor, amount]])
