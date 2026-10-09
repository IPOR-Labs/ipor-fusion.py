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
    3637: "botanix",
    57073: "ink",
    14: "flare",
}

CHAIN_NAME_TO_ID: dict[str, int] = {name: cid for cid, name in CHAIN_NAMES.items()}

# Chains the on-chain vault tooling (vault info/health/substrate fetch) is
# validated on. A provider for another chain may connect fine and still fail
# deeper in the stack (a missing Multicall3, an oracle quirk, an eth_getLogs
# cap the adaptive scan cannot page through). Extend only after the full
# vault_info path passes on every live vault of that chain.
SUPPORTED_CHAIN_IDS: frozenset[int] = frozenset(
    {1, 130, 143, 999, 8453, 9745, 42161, 43114, 747474}
)

# First eth_getLogs page size, in blocks, on chains whose providers cap the
# range (10,000 blocks) or time out on wide queries. The scan still shrinks a
# page the provider rejects; a hint only skips the whole-range attempt and the
# probing round trips.
GET_LOGS_RANGE_HINTS: dict[int, int] = {
    130: 10_000,
    9745: 10_000,
    57073: 10_000,
    43114: 1_000_000,
    747474: 1_000_000,
}


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
