"""Public chain registry: display names and vault-tooling support gate."""

from __future__ import annotations

from ipor_fusion.errors import UnsupportedChainError
from ipor_fusion.types import ChainId

CHAIN_NAMES: dict[ChainId, str] = {
    ChainId(1): "ethereum",
    ChainId(42161): "arbitrum",
    ChainId(8453): "base",
    ChainId(10): "optimism",
    ChainId(130): "unichain",
    ChainId(137): "polygon",
    ChainId(56): "bsc",
    ChainId(239): "tac",
    ChainId(9745): "plasma",
    ChainId(43114): "avalanche",
    ChainId(250): "fantom",
    ChainId(143): "monad",
    ChainId(999): "hyperevm",
    ChainId(4663): "robinhood",
    ChainId(747474): "katana",
    ChainId(3637): "botanix",
    ChainId(57073): "ink",
    ChainId(14): "flare",
}

CHAIN_NAME_TO_ID: dict[str, ChainId] = {name: cid for cid, name in CHAIN_NAMES.items()}

# Chains the on-chain vault tooling (vault info/health/substrate fetch) is
# validated on. A provider for another chain may connect fine and still fail
# deeper in the stack (a missing Multicall3, an oracle quirk, an eth_getLogs
# cap the adaptive scan cannot page through). Extend only after the full
# vault_info path passes on every live vault of that chain.
SUPPORTED_CHAIN_IDS: frozenset[ChainId] = frozenset(
    ChainId(cid) for cid in (1, 130, 143, 999, 8453, 9745, 42161, 43114, 747474)
)

# First eth_getLogs page size, in blocks, on chains whose providers cap the
# range (10,000 blocks) or time out on wide queries. The scan still shrinks a
# page the provider rejects; a hint only skips the whole-range attempt and the
# probing round trips.
GET_LOGS_RANGE_HINTS: dict[ChainId, int] = {
    ChainId(130): 10_000,
    ChainId(9745): 10_000,
    ChainId(57073): 10_000,
    ChainId(43114): 1_000_000,
    ChainId(747474): 1_000_000,
}


def ensure_supported_chain(chain_id: ChainId) -> None:
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
