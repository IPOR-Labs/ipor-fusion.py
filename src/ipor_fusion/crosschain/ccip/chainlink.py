"""Chainlink's own CCIP contracts, as far as the SDK needs them: the ``Router``
a source chain sends through, the ``OnRamp`` of one lane, the
``TokenAdminRegistry`` and a token's pool. Together they answer, before a
supply is sent, whether a message lane exists towards a chain and whether the
token travels on it (``ccip_token_lane``).
"""

from __future__ import annotations

from dataclasses import dataclass

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


class CcipOnRamp(ContractWrapper):
    """The ``OnRamp`` of one lane. ``getStaticConfig()`` on OnRamp 2.0.0 is
    ``(chainSelector, rmnRemote, <limit>, tokenAdminRegistry)``; only the
    registry is read here."""

    def type_and_version(self) -> Call[str]:
        return self._view("typeAndVersion()", output_types=["string"])

    def token_admin_registry(self) -> Call[ChecksumAddress]:
        return self._view(
            "getStaticConfig()",
            output_types=["uint64", "address", "uint256", "address"],
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


def ccip_token_lane(
    ctx: Web3Context,
    router: ChecksumAddress,
    token: ChecksumAddress,
    chain_selector: int,
) -> CcipTokenLane:
    """Read, on ``router``'s chain, whether a message lane and a ``token`` lane
    exist towards ``chain_selector``. Five reads at most."""
    router_wrapper = CcipRouter(ctx, router)
    if not router_wrapper.is_chain_supported(chain_selector).call():
        return CcipTokenLane(chain_selector, message_lane=False)
    on_ramp = router_wrapper.get_on_ramp(chain_selector).call()
    on_ramp_wrapper = CcipOnRamp(ctx, on_ramp)
    on_ramp_version = on_ramp_wrapper.type_and_version().call()
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
