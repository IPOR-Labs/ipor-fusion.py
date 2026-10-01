"""Per-chain Fusion deployment addresses, shipped inside the package.

The data is a snapshot of the `mainnet/mainnet-<chain>-fusion/addresses.json`
files from the ipor-abi registry (https://github.com/IPOR-Labs/ipor-abi),
regenerated with `scripts/sync_ipor_abi_addresses.py`. `source()` reports the
registry commit the snapshot was taken from, so a consumer can tell how old it
is without a network call.

    from ipor_fusion import addresses

    addresses.factory_proxy(8453)            # IporFusionFactoryProxy on Base
    addresses.resolve(8453, "SupplyFuseAaveV3").address
    addresses.balance_fuse(8453, "AAVE_V3")  # registry name differs per chain
    addresses.lookup("factory", chain_id=8453)

Two entries share the `IporFusionFactory` prefix on every chain. Only the
Proxy is callable: `clone()` on the Impl reverts `DaoFeePackagesArrayEmpty()`.
`resolve()` and `lookup()` flag the pair (`deploy_entry_point`, `note`) so a
caller never has to pattern-match the suffix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.errors import AddressNotFoundError, UnsupportedChainError
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import MarketId

FACTORY_PROXY_NAME = "IporFusionFactoryProxy"
FACTORY_IMPL_NAME = "IporFusionFactoryImpl"

DEPLOY_ENTRY_POINT_ROLE = "deploy-entry-point"
FACTORY_IMPL_NOTE = (
    "implementation; reverts DaoFeePackagesArrayEmpty when called directly, "
    "use IporFusionFactoryProxy"
)

#: Registry names that implement the balance fuse of a market, in the order
#: they should be preferred when a chain publishes more than one. The
#: `BalanceFuse<Protocol>` convention is the current one; the older
#: `<Protocol>WithPriceOracleMiddlewareBalanceFuse` names are still the only
#: entry on some chains (Arbitrum for Aave V3, for example).
BALANCE_FUSE_NAMES: dict[int, tuple[str, ...]] = {
    IporFusionMarkets.AAVE_V3: (
        "BalanceFuseAaveV3",
        "AaveV3WithPriceOracleMiddlewareBalanceFuse",
    ),
    IporFusionMarkets.AAVE_V3_LIDO: (
        "BalanceFuseAaveV3Lido",
        "AaveV3LidoWithPriceOracleMiddlewareBalanceFuse",
    ),
    IporFusionMarkets.COMPOUND_V3_USDC: ("BalanceFuseCompoundV3Usdc",),
    IporFusionMarkets.COMPOUND_V3_USDT: ("BalanceFuseCompoundV3Usdt",),
    IporFusionMarkets.COMPOUND_V3_WETH: ("BalanceFuseCompoundV3WEth",),
    IporFusionMarkets.ERC20_VAULT_BALANCE: ("BalanceFuseErc20",),
    IporFusionMarkets.EULER_V2: ("BalanceFuseEulerV2",),
    IporFusionMarkets.FLUID_INSTADAPP_POOL: ("BalanceFuseFluidInstadappPoolFToken",),
    IporFusionMarkets.FLUID_INSTADAPP_STAKING: (
        "BalanceFuseFluidInstadappStakingRewardsFToken",
    ),
    IporFusionMarkets.GEARBOX_POOL_V3: ("BalanceFuseGearboxV3DToken",),
    IporFusionMarkets.GEARBOX_FARM_DTOKEN_V3: ("BalanceFuseGearboxV3FarmDToken",),
    IporFusionMarkets.HARVEST_HARD_WORK: ("BalanceFuseHarvestDoHardWork",),
    IporFusionMarkets.LIQUITY_V2: ("BalanceFuseLiquity",),
    IporFusionMarkets.MOONWELL: ("BalanceFuseMoonwell",),
    IporFusionMarkets.MORPHO: ("BalanceFuseMorpho",),
    IporFusionMarkets.MORPHO_FLASH_LOAN: ("BalanceFuseFlashLoanMorpho",),
    IporFusionMarkets.PENDLE: ("BalanceFusePendle",),
    IporFusionMarkets.UNIVERSAL_TOKEN_SWAPPER: ("BalanceFuseUniversalTokenSwapper",),
    IporFusionMarkets.UNIVERSAL_TOKEN_SWAPPER_V2: (
        "BalanceFuseUniversalTokenSwapperV2",
    ),
}

_ERC4626_MARKET_BASE = 100_000


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    """One registry entry, with the deploy-entry-point annotation."""

    chain_id: int
    chain: str
    name: str
    address: ChecksumAddress
    #: True only for `IporFusionFactoryProxy`, the contract `clone()` is sent to.
    deploy_entry_point: bool = False
    #: Set on `IporFusionFactoryImpl` to explain why it must not be called.
    note: str | None = None

    @property
    def role(self) -> str | None:
        return DEPLOY_ENTRY_POINT_ROLE if self.deploy_entry_point else None


@dataclass(frozen=True, slots=True)
class RegistrySource:
    repository: str
    commit: str
    synced_at: str


@cache
def _registry() -> dict:
    data = resources.files("ipor_fusion.data").joinpath("ipor_abi_addresses.json")
    return json.loads(data.read_text(encoding="utf-8"))


def source() -> RegistrySource:
    """The ipor-abi commit the shipped snapshot was generated from."""
    src = _registry()["source"]
    return RegistrySource(
        repository=src["repository"], commit=src["commit"], synced_at=src["synced_at"]
    )


def chain_ids() -> list[int]:
    """Chains with a Fusion deployment in the snapshot, ascending."""
    return sorted(int(cid) for cid in _registry()["chains"])


def chain_name(chain_id: int) -> str:
    return _chain(chain_id)["name"]


def names(chain_id: int) -> list[str]:
    """Sorted registry names deployed on `chain_id`."""
    return sorted(_chain(chain_id)["contracts"])


def resolve(chain_id: int, name: str) -> ResolvedAddress:
    """Exact-name resolution on one chain. Raises `AddressNotFoundError`."""
    chain = _chain(chain_id)
    address = chain["contracts"].get(name)
    if address is None:
        raise AddressNotFoundError(
            f"{name!r} is not deployed on chain {chain_id} ({chain['name']}) in the "
            f"ipor-abi snapshot @ {source().commit[:12]}; "
            f"see ipor_fusion.addresses.names({chain_id})"
        )
    return _entry(chain_id, chain["name"], name, address)


def factory_proxy(chain_id: int) -> ChecksumAddress:
    """`IporFusionFactoryProxy` for the chain: the address `clone()` is sent to."""
    return resolve(chain_id, FACTORY_PROXY_NAME).address


def factory_impl(chain_id: int) -> ChecksumAddress:
    """`IporFusionFactoryImpl` for the chain. Do not call it; see `factory_proxy`."""
    return resolve(chain_id, FACTORY_IMPL_NAME).address


def balance_fuse(chain_id: int, market: MarketId | int | str) -> ChecksumAddress:
    """The balance fuse registered for `market` on `chain_id`.

    `market` is an `IporFusionMarkets` value, its attribute name (`"AAVE_V3"`)
    or an ERC-4626 market id (`100_001` → `BalanceFuseErc4626Market1`). The
    registry name for the same market differs between chains; this picks the
    first name from `BALANCE_FUSE_NAMES` that the chain publishes.
    """
    market_id = _market_id(market)
    candidates = _balance_fuse_candidates(market_id)
    contracts = _chain(chain_id)["contracts"]
    for name in candidates:
        if name in contracts:
            return Web3.to_checksum_address(contracts[name])
    raise AddressNotFoundError(
        f"no balance fuse for market {market_id} on chain {chain_id} "
        f"({chain_name(chain_id)}); tried {', '.join(candidates)}"
    )


def lookup(query: str, chain_id: int = 0) -> list[ResolvedAddress]:
    """Substring (case-insensitive) or address match across chains.

    An address query ignores `chain_id`, an address identifies a deployment
    globally. `chain_id=0` searches every chain in the snapshot.
    """
    query = query.strip()
    by_address = _is_address(query)
    needle = query.lower()
    chains = _registry()["chains"]
    scope = (
        chains.items()
        if by_address or not chain_id
        else [(str(chain_id), _chain(chain_id))]
    )
    hits: list[ResolvedAddress] = []
    for cid, chain in scope:
        for name, address in chain["contracts"].items():
            if by_address:
                if address.lower() != needle:
                    continue
            elif needle not in name.lower():
                continue
            hits.append(_entry(int(cid), chain["name"], name, address))
    hits.sort(key=lambda hit: (hit.chain_id, hit.name))
    return hits


def is_factory_impl(chain_id: int, address: str) -> bool:
    """True when `address` is the chain's `IporFusionFactoryImpl`."""
    try:
        return Web3.to_checksum_address(address) == factory_impl(chain_id)
    except (AddressNotFoundError, UnsupportedChainError):
        return False


def _chain(chain_id: int) -> dict:
    chain = _registry()["chains"].get(str(chain_id))
    if chain is None:
        known = ", ".join(str(cid) for cid in chain_ids())
        raise UnsupportedChainError(
            f"chain {chain_id} has no Fusion deployment in the ipor-abi snapshot "
            f"@ {source().commit[:12]}; known chains: {known}"
        )
    return chain


def _entry(chain_id: int, chain: str, name: str, address: str) -> ResolvedAddress:
    return ResolvedAddress(
        chain_id=chain_id,
        chain=chain,
        name=name,
        address=Web3.to_checksum_address(address),
        deploy_entry_point=name == FACTORY_PROXY_NAME,
        note=FACTORY_IMPL_NOTE if name == FACTORY_IMPL_NAME else None,
    )


def _market_id(market: MarketId | int | str) -> int:
    if isinstance(market, str):
        value = getattr(IporFusionMarkets, market.upper(), None)
        if not isinstance(value, int):
            raise AddressNotFoundError(f"{market!r} is not an IporFusionMarkets name")
        return value
    return int(market)


def _balance_fuse_candidates(market_id: int) -> tuple[str, ...]:
    if market_id > _ERC4626_MARKET_BASE:
        return (f"BalanceFuseErc4626Market{market_id - _ERC4626_MARKET_BASE}",)
    candidates = BALANCE_FUSE_NAMES.get(market_id)
    if candidates is None:
        raise AddressNotFoundError(
            f"market {market_id} has no balance-fuse registry name in "
            "ipor_fusion.addresses.BALANCE_FUSE_NAMES; resolve it by name instead"
        )
    return candidates


def _is_address(value: str) -> bool:
    return len(value) == 42 and value.startswith("0x") and Web3.is_address(value)
