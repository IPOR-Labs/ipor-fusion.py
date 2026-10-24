"""HyperCore pre-hooks (market 55): the vault configuration call that installs
the capital-flow and pending-action pre-hooks on their PlasmaVault selectors.

Status: preview, not production. The contracts behind it are under active development, the SDK follows only their official deployment, and interfaces may change or be removed between minor versions, with no backward compatibility; see ``ipor_fusion.about.PREVIEW_FEATURES``.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.core.contract import Call
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.fuses.base import _validate_not_zero_address
from ipor_fusion.types import parse_checksum_address


def _selectors(*signatures: str) -> tuple[bytes, ...]:
    return tuple(function_signature_to_4byte_selector(s) for s in signatures)


#: PlasmaVault selectors ``HyperCorePendingActionPreHook`` is wired on.
HYPERCORE_PENDING_ACTION_SELECTORS: tuple[bytes, ...] = _selectors(
    "execute((address,bytes)[])",
    "updateMarketsBalances(uint256[])",
)
#: PlasmaVault selectors ``HyperCoreCapitalFlowPreHook`` is wired on: the
#: share-priced deposit and exit entry points.
HYPERCORE_CAPITAL_FLOW_SELECTORS: tuple[bytes, ...] = _selectors(
    "deposit(uint256,address)",
    "depositWithPermit(uint256,address,uint256,uint8,bytes32,bytes32)",
    "mint(uint256,address)",
    "withdraw(uint256,address,address)",
    "redeem(uint256,address,address)",
    "redeemFromRequest(uint256,address,address)",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCorePreHookWiring:
    """The two pre-hooks of a HyperCore vault, each on its own selectors.

    Each hook reverts ``HyperCorePreHookWrongSelector`` under the other's
    selectors. A deposit or exit selector without the capital-flow hook runs no
    HyperCore code, so the action fuses refuse to open Core exposure. Neither
    hook reads substrates. The hook addresses are per deployment (``ipor-abi``).

    The capital-flow hook refreshes the market through the vault's own
    ``updateMarketsBalances``: the vault must hold
    ``Roles.UPDATE_MARKETS_BALANCES_ROLE`` itself, with no execution delay, or
    every deposit and exit reverts, and the deposit and exit selectors must not
    be routed through the ContextManager, whose sender substitution breaks that
    nested call.
    """

    capital_flow_hook: ChecksumAddress
    pending_action_hook: ChecksumAddress

    def __post_init__(self) -> None:
        # Parsed first, so the zero check and the comparison see every
        # spelling of an address in one canonical form.
        for name in ("capital_flow_hook", "pending_action_hook"):
            value = getattr(self, name)
            try:
                hook = parse_checksum_address(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{name} is not an address: {value!r}") from exc
            _validate_not_zero_address(hook, name)
            object.__setattr__(self, name, hook)
        if self.capital_flow_hook == self.pending_action_hook:
            raise ValueError("capital_flow_hook and pending_action_hook must differ")

    @property
    def selectors(self) -> tuple[bytes, ...]:
        """The pending-action selectors, then the capital-flow ones."""
        return HYPERCORE_PENDING_ACTION_SELECTORS + HYPERCORE_CAPITAL_FLOW_SELECTORS

    @property
    def implementations(self) -> tuple[ChecksumAddress, ...]:
        """The hook of each of :attr:`selectors`, in the same order."""
        return (self.pending_action_hook,) * len(HYPERCORE_PENDING_ACTION_SELECTORS) + (
            self.capital_flow_hook,
        ) * len(HYPERCORE_CAPITAL_FLOW_SELECTORS)

    def set_pre_hook_implementations(self, vault: PlasmaVault) -> Call[None]:
        """``setPreHookImplementations`` installing both hooks on ``vault``
        (PRE_HOOKS_MANAGER only). A selector holds one pre-hook, so this
        replaces any hook already on :attr:`selectors`."""
        selectors = self.selectors
        return vault.set_pre_hook_implementations(
            list(selectors), list(self.implementations), [[] for _ in selectors]
        )
