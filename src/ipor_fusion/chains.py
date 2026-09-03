"""Public chain registry: display names and vault-tooling support gate."""

from __future__ import annotations

from ipor_fusion.errors import UnsupportedChainError

CHAIN_NAMES: dict[int, str] = {
    1: "ethereum",
    42161: "arbitrum",
    8453: "base",
    10: "optimism",
    130: "unichain",
    137: "polygon",
    56: "bsc",
    239: "tac",
    9745: "plasma",
    43114: "avalanche",
    250: "fantom",
    143: "monad",
    999: "hyperevm",
    4663: "robinhood",
    747474: "katana",
}

CHAIN_NAME_TO_ID: dict[str, int] = {name: cid for cid, name in CHAIN_NAMES.items()}

# Chains the on-chain vault tooling (vault info/health/substrate fetch) is
# validated on. A provider for another chain may connect fine and still fail
# deeper in the stack — e.g. the withdraw-manager event scan exceeds
# eth_getLogs range caps on Unichain/Plasma and times out on Avalanche.
# Extend only after the full vault_info path passes on that chain.
SUPPORTED_CHAIN_IDS: frozenset[int] = frozenset({1, 42161, 8453})


def ensure_supported_chain(chain_id: int) -> None:
    """Raise :class:`UnsupportedChainError` unless the vault tooling supports the chain."""
    if chain_id in SUPPORTED_CHAIN_IDS:
        return
    name = CHAIN_NAMES.get(chain_id)
    label = f"{chain_id} ({name})" if name else str(chain_id)
    supported = ", ".join(
        f"{CHAIN_NAMES[cid]} ({cid})" for cid in sorted(SUPPORTED_CHAIN_IDS)
    )
    raise UnsupportedChainError(
        f"chain {label} is not supported yet by the vault tooling; "
        f"supported chains: {supported}"
    )
