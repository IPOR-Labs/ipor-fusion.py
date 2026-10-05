"""Chainlink's own CCIP contracts, as far as the SDK needs them: the ``Router``
a source chain sends through, the ``OnRamp`` of one lane, the
``TokenAdminRegistry`` and a token's pool. Together they answer, before a
supply is sent, whether a message lane exists towards a chain and whether the
token travels on it (``ccip_token_lane``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

from eth_typing import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.crosschain.contracts import _address
from ipor_fusion.fuses.base import ZERO_ADDRESS


class CcipRouter(ContractWrapper):
    """The ``Router`` every ``ccipSend`` on a chain goes through."""

    def type_and_version(self) -> Call[str]:
        return self._view("typeAndVersion()", output_types=["string"])

    def is_chain_supported(self, chain_selector: int) -> Call[bool]:
        """Whether a lane towards ``chain_selector`` is configured."""
        return self._view(
            "isChainSupported(uint64)", chain_selector, output_types=["bool"]
        )

    def get_on_ramp(self, chain_selector: int) -> Call[ChecksumAddress]:
        return self._view(
            "getOnRamp(uint64)",
            chain_selector,
            output_types=["address"],
            decoder=_address,
        )

    def get_off_ramps(self) -> Call[list[tuple[int, ChecksumAddress]]]:
        """Every ``(source chain selector, OffRamp)`` the router accepts
        deliveries from. A source can have several (one per CCIP version
        still draining); tell them apart with the OffRamp's
        ``typeAndVersion``."""
        return self._view(
            "getOffRamps()",
            output_types=["(uint64,address)[]"],
            decoder=lambda ramps: [
                (int(selector), _address(ramp)) for selector, ramp in ramps
            ],
        )


class CcipOnRamp(ContractWrapper):
    """The ``OnRamp`` of one lane. ``getStaticConfig()`` on OnRamp 2.0.0 is
    ``(chainSelector, rmnRemote, maxUSDCentsPerMessage, tokenAdminRegistry)``;
    only the registry is read here. The tuple is verified for OnRamp 2.0.0;
    :func:`ccip_token_lane` checks the version before using it."""

    def type_and_version(self) -> Call[str]:
        return self._view("typeAndVersion()", output_types=["string"])

    def token_admin_registry(self) -> Call[ChecksumAddress]:
        return self._view(
            "getStaticConfig()",
            output_types=["uint64", "address", "uint32", "address"],
            decoder=lambda values: _address(values[3]),
        )


class CcipTokenAdminRegistry(ContractWrapper):
    """``TokenAdminRegistry``: which pool moves a token off this chain."""

    def get_pool(self, token: ChecksumAddress) -> Call[ChecksumAddress]:
        """The token's pool, or the zero address when it has none."""
        return self._view(
            "getPool(address)", token, output_types=["address"], decoder=_address
        )


class CcipTokenPool(ContractWrapper):
    """A token pool (``BurnMint``, ``LockRelease`` or the CCTP ``USDCTokenPoolProxy``)."""

    def type_and_version(self) -> Call[str]:
        return self._view("typeAndVersion()", output_types=["string"])

    def is_supported_chain(self, chain_selector: int) -> Call[bool]:
        """Whether the pool releases or mints on ``chain_selector``."""
        return self._view(
            "isSupportedChain(uint64)", chain_selector, output_types=["bool"]
        )


@dataclass(frozen=True)
class CcipTokenLane:
    """What one source chain's Chainlink contracts say about sending ``token``
    towards ``chain_selector``: ``message_lane`` is the Router's answer,
    ``token_lane`` the token pool's (``False`` when the token has no pool)."""

    chain_selector: int
    message_lane: bool
    on_ramp: ChecksumAddress | None = None
    on_ramp_version: str | None = None
    pool: ChecksumAddress | None = None
    pool_version: str | None = None
    token_lane: bool = False


_SUPPORTED_ON_RAMP_VERSIONS = frozenset({"OnRamp 2.0.0"})


def ccip_token_lane(
    ctx: Web3Context,
    router: ChecksumAddress,
    token: ChecksumAddress,
    chain_selector: int,
) -> CcipTokenLane:
    """Read, on ``router``'s chain, whether a message lane and a ``token`` lane
    exist towards ``chain_selector``. Seven reads at most. Raises ``ValueError``
    before reading static config when the OnRamp version has an unverified
    layout."""
    router_wrapper = CcipRouter(ctx, router)
    if not router_wrapper.is_chain_supported(chain_selector).call():
        return CcipTokenLane(chain_selector, message_lane=False)
    on_ramp = router_wrapper.get_on_ramp(chain_selector).call()
    on_ramp_wrapper = CcipOnRamp(ctx, on_ramp)
    on_ramp_version = on_ramp_wrapper.type_and_version().call()
    if on_ramp_version not in _SUPPORTED_ON_RAMP_VERSIONS:
        raise ValueError(
            f"unsupported CCIP OnRamp version {on_ramp_version!r} at {on_ramp}; "
            "ccip_token_lane supports OnRamp 2.0.0"
        )
    registry = CcipTokenAdminRegistry(
        ctx, on_ramp_wrapper.token_admin_registry().call()
    )
    pool = registry.get_pool(token).call()
    if pool == ZERO_ADDRESS:
        return CcipTokenLane(chain_selector, True, on_ramp, on_ramp_version)
    pool_wrapper = CcipTokenPool(ctx, pool)
    return CcipTokenLane(
        chain_selector,
        True,
        on_ramp,
        on_ramp_version,
        pool,
        pool_wrapper.type_and_version().call(),
        pool_wrapper.is_supported_chain(chain_selector).call(),
    )


class MessageExecutionState(IntEnum):
    """``Internal.MessageExecutionState`` on the OffRamp. ``execute`` accepts a
    message in UNTOUCHED or FAILURE only; a receiver revert reverts the whole
    call, so FAILURE is left only by a successful retry."""

    UNTOUCHED = 0
    IN_PROGRESS = 1
    SUCCESS = 2
    FAILURE = 3


@dataclass(frozen=True, slots=True)
class CcvRequirements:
    """``OffRamp.getCCVsForMessage``: the verifiers a message needs. Every
    ``required`` CCV and ``optional_threshold`` of the ``optional`` ones must
    supply a verifier result."""

    required: tuple[ChecksumAddress, ...]
    optional: tuple[ChecksumAddress, ...]
    optional_threshold: int


def _ccv_requirements(values: tuple) -> CcvRequirements:
    required, optional, threshold = values
    return CcvRequirements(
        tuple(_address(a) for a in required),
        tuple(_address(a) for a in optional),
        int(threshold),
    )


class CcipOffRamp(ContractWrapper):
    """``OffRamp 2.0.0`` (the CCV architecture): the destination entry point.
    Execution is permissionless: anyone holding the message as sent and the
    verifier results of its CCVs can call :meth:`execute`, which is how a
    message the default executor cannot place (for instance one needing more
    gas than a HyperEVM small block holds) is delivered."""

    def type_and_version(self) -> Call[str]:
        return self._view("typeAndVersion()", output_types=["string"])

    def execution_state(self, message_id: bytes) -> Call[MessageExecutionState]:
        return self._view(
            "getExecutionState(bytes32)",
            message_id,
            output_types=["uint8"],
            decoder=MessageExecutionState,
        )

    def ccvs_for_message(self, encoded_message: bytes) -> Call[CcvRequirements]:
        return self._view(
            "getCCVsForMessage(bytes)",
            encoded_message,
            output_types=["address[]", "address[]", "uint8"],
            decoder=_ccv_requirements,
        )

    def execute(
        self,
        encoded_message: bytes,
        ccvs: Sequence[ChecksumAddress],
        verifier_results: Sequence[bytes],
        gas_limit_override: int = 0,
    ) -> Call[None]:
        """Deliver ``encoded_message`` with one verifier result per CCV, in the
        same order. ``gas_limit_override`` of 0 keeps the message's own
        ``ccipReceiveGasLimit``; a nonzero value must not be lower. The
        transaction itself needs that limit plus the OffRamp's own buffer."""
        if not encoded_message:
            raise ValueError("encoded_message must not be empty")
        if len(ccvs) != len(verifier_results) or not ccvs:
            raise ValueError("one verifier result per CCV, at least one CCV")
        if not 0 <= gas_limit_override < 2**32:
            raise ValueError(
                f"gas_limit_override must fit uint32, got {gas_limit_override}"
            )
        return self._write(
            "execute(bytes,address[],bytes[],uint32)",
            encoded_message,
            list(ccvs),
            list(verifier_results),
            gas_limit_override,
        )


class CcipVerifierResolver(ContractWrapper):
    """``VersionedVerifierResolver 2.0.0``: the CCV address a message names;
    it maps the version tag at the head of a verifier result to the verifier
    implementation that checks it."""

    def type_and_version(self) -> Call[str]:
        return self._view("typeAndVersion()", output_types=["string"])

    def inbound_implementation(self, verifier_results: bytes) -> Call[ChecksumAddress]:
        return self._view(
            "getInboundImplementation(bytes)",
            verifier_results,
            output_types=["address"],
            decoder=_address,
        )

    def all_inbound_implementations(
        self,
    ) -> Call[tuple[tuple[bytes, ChecksumAddress], ...]]:
        """``(versionTag, implementation)`` pairs."""
        return self._view(
            "getAllInboundImplementations()",
            output_types=["(bytes4,address)[]"],
            decoder=lambda pairs: tuple(
                (bytes(tag), _address(impl)) for tag, impl in pairs
            ),
        )


class CcipCommitteeVerifier(ContractWrapper):
    """``CommitteeVerifier 2.0.0``: Chainlink's committee (the former CCIP DON)
    as a CCV. Its off-chain nodes publish the verifier results to
    :meth:`storage_locations`; the public indexers wrap those."""

    def type_and_version(self) -> Call[str]:
        return self._view("typeAndVersion()", output_types=["string"])

    def version_tag(self) -> Call[bytes]:
        """The 4-byte tag a verifier result for this verifier starts with."""
        return self._view("versionTag()", output_types=["bytes4"], decoder=bytes)

    def storage_locations(self) -> Call[tuple[str, ...]]:
        return self._view(
            "getStorageLocations()",
            output_types=["string[]"],
            decoder=lambda values: tuple(str(v) for v in values),
        )
