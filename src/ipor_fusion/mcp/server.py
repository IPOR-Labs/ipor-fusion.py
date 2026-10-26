"""MCP server exposing IPOR Fusion CLI as tools for Claude Code.

Calls the ipor_fusion SDK directly — no CLI subprocess.
Configuration is loaded from the shared CLI config (~/.config/ipor-fusion/).
"""

from collections.abc import Callable
from functools import partial
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import AfterValidator, BeforeValidator, Field
from web3 import Web3

from ipor_fusion.about import (
    package_version,
    read_changelog,
    repository_url,
)
from ipor_fusion.chains import CHAIN_NAMES, ensure_supported_chain
from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    mask_provider_url,
    resolve_chain_id,
    save_config,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.core.access import build_role_accounts_json, resolve_access_manager
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.explorer import get_contract_name
from ipor_fusion.market_info import (
    build_meta_morpho_json,
    build_morpho_blue_market_json,
)
from ipor_fusion.mcp.guide import register_guide
from ipor_fusion.mcp.models import (
    ActionResult,
    AddressListResponse,
    AddressLookupResponse,
    AddressNamesResponse,
    ChangelogEntryModel,
    ConfigShowResponse,
    ExecutePreflightResponse,
    MetaMorphoVaultResponse,
    MorphoBlueMarketResponse,
    OracleMappingResponse,
    RoleAccountsResponse,
    ServerInfoResponse,
    VaultInfoResponse,
    VaultListEntry,
    VaultValidationResponse,
)
from ipor_fusion.morpho_api import (
    PUBLIC_ALLOCATOR_ADDRESSES,
    MorphoApiError,
    fetch_market,
    fetch_vault,
)
from ipor_fusion.preflight import (
    build_execute_preflight_json,
    parse_execute_actions,
    preflight_execute,
)
from ipor_fusion.readers.morpho import MorphoReader, morpho_blue_address
from ipor_fusion.readers.oracle_mapping import build_oracle_mapping
from ipor_fusion.registry import (
    build_address_list_json,
    build_address_lookup_json,
    build_address_names_json,
    fetch_address_registries,
    fetch_address_registry,
)
from ipor_fusion.types import (
    ChainId,
    PlasmaVaultAddress,
    parse_morpho_market_id,
    parse_plasma_vault_address,
)
from ipor_fusion.validation import validate_vault
from ipor_fusion.vault_data import (
    fetch_deployment_info,
    fetch_vault_data,
)
from ipor_fusion.vault_info import build_vault_info_json, build_vault_validation_json

# chain_id default: detect the chain from the saved vault or the single provider.

mcp = FastMCP(
    "ipor-fusion",
    instructions=(
        "Inspection and configuration tools for IPOR Fusion Plasma Vaults. "
        "Start with config_show / vault_list; vault_info is the "
        "comprehensive per-vault summary. Read the fusion://invariants "
        "resource before writing code that deploys or configures a vault."
    ),
    website_url=repository_url() or None,
)
# FastMCP exposes no version kwarg; left unset, the initialize handshake
# reports the mcp library's version as serverInfo.version.
mcp._mcp_server.version = package_version()
register_guide(mcp)


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _resolve_provider(cfg: FusionConfig, chain_id: ChainId) -> str:
    if provider_url := cfg.providers.get(str(chain_id)):
        return provider_url
    raise ValueError(
        f"No provider for chain {chain_id}. Use config_set_provider to configure one."
    )


def _vault_arg(raw: str) -> PlasmaVaultAddress:
    """A vault address as a tool caller sends it: surrounding whitespace and a
    missing ``0x`` are accepted, as in 3.x; letter case follows
    `parse_checksum_address`, so a wrong EIP-55 checksum is refused."""
    value = raw.strip()
    if value[:2] not in ("0x", "0X"):
        value = "0x" + value
    return parse_plasma_vault_address(value)


def _resolve_chain_id(
    cfg: FusionConfig, vault_address: str, chain_id: ChainId | None
) -> ChainId:
    if (resolved := resolve_chain_id(cfg, vault_address, chain_id)) is not None:
        return resolved
    raise ValueError(
        "Cannot tell the chain: the vault is not saved and there is not exactly one "
        "configured provider. Pass chain_id or save the vault first."
    )


def _build_ctx(
    cfg: FusionConfig, chain_id: ChainId, block_number: int | None = None
) -> tuple[Web3Context, int | None]:
    ctx = Web3Context.from_url(_resolve_provider(cfg, chain_id))
    if block_number is not None:
        ctx = ctx.at_block(block_number)
    return ctx, block_number


def _no_zero_sentinel(
    name: str, omitted: str = "auto-detection"
) -> Callable[[object], object]:
    """Refuse a chain of 0 with an explicit message: clients built against the
    3.x tools still send it meaning auto-detection, and reading it as chain 0
    would fail later with a less helpful error."""

    def check(value: object) -> object:
        if value == 0 and not isinstance(value, bool):
            raise ValueError(
                f"{name}=0 is not a sentinel any more: omit {name} for {omitted}"
            )
        return value

    return check


def _zero_block_is_latest(value: int | None) -> int | None:
    """Read block 0 as the latest block: many clients fill an optional integer
    with 0, and genesis never holds a vault, so 0 has no other useful meaning."""
    return value or None


# Shared argument types, so every tool takes chain and block as the CLI does:
# omitted means auto-detected / latest. A chain of 0 is refused; a block of 0
# reads as latest.
ChainArg = Annotated[
    ChainId | None,
    Field(
        description="Chain ID; omitted: the saved vault's chain, else the chain "
        "of the only configured provider.",
        gt=0,
    ),
    BeforeValidator(_no_zero_sentinel("chain_id")),
]


# The market tools need the chain: no auto-detection, so no omission either.
RequiredChainArg = Annotated[ChainId, Field(description="Chain ID.", gt=0)]
RegistryChainArg = Annotated[
    ChainId | None,
    Field(description="Chain ID; omitted: every chain with a deployment file.", gt=0),
    BeforeValidator(_no_zero_sentinel("chain_id", "every chain")),
]
BlockArg = Annotated[
    int | None,
    Field(description="Block number; 0 or omitted: latest.", ge=0),
    AfterValidator(_zero_block_is_latest),
]


# ---------------------------------------------------------------------------
# Server tools
# ---------------------------------------------------------------------------


# Named after the server rather than the data — a deliberate break from the
# kebab-to-snake naming parity the other tools keep with the CLI, whose sibling
# command is `fusion changelog`. The initialize handshake never reaches the
# model, so a tool is the only reliable answer to "which server is this", and
# version/repository ride along with the changelog.
@mcp.tool()
def server_info(
    since: Annotated[
        str,
        Field(
            description="Return every release newer than this version, e.g. "
            "'3.1.0'. Expects a version, not a date. Default returns only the "
            "running version's entry."
        ),
    ] = "",
) -> ServerInfoResponse:
    """Identify this MCP server, report what changed in its releases and which
    package areas are preview rather than production."""
    return ServerInfoResponse(
        name=mcp.name,
        version=package_version(),
        repository=repository_url(),
        changelog=[
            ChangelogEntryModel.from_entry(entry) for entry in read_changelog(since)
        ],
    )


# ---------------------------------------------------------------------------
# Vault tools
# ---------------------------------------------------------------------------


@mcp.tool()
def vault_info(
    vault_address: Annotated[str, Field(description="Plasma Vault address.")],
    chain_id: ChainArg = None,
    block_number: BlockArg = None,
) -> VaultInfoResponse:
    """Get full on-chain state of a Plasma Vault — the comprehensive summary.

    Returns a structured VaultInfoResponse — see model field descriptions
    for the complete output schema (assets, balance fuses, fees, withdraw
    manager, reconciliation, lending health, substrates, role accounts,
    etc.).
    """
    cfg = load_config()
    checksum = _vault_arg(vault_address)
    chain_id = _resolve_chain_id(cfg, checksum, chain_id)
    # Fail fast (before any RPC) on chains the vault tooling can't handle;
    # UnsupportedChainError doubles as ValueError so FastMCP surfaces it.
    ensure_supported_chain(chain_id)
    ctx, effective_block = _build_ctx(cfg, chain_id, block_number)

    # Cheap single-call probe: raises the typed ContractNotFoundError /
    # NotPlasmaVaultError ("not a vault" in both revert and empty-return
    # flavors) before the expensive fetch. The fetch itself stays catch-free:
    # once the probe passes, any later failure is a real error on a real
    # vault and must stay loud.
    resolve_access_manager(ctx, checksum)

    data = fetch_vault_data(ctx, checksum, block=effective_block)

    api_key = cfg.etherscan_api_key
    (
        data.deployment_block,
        data.deployment_timestamp,
        data.deployment_error,
    ) = fetch_deployment_info(ctx, checksum, api_key=api_key)

    result = build_vault_info_json(
        data,
        resolve_name=partial(
            get_contract_name, chain_id, api_key=api_key, cache=ctx.cache
        ),
    )
    return VaultInfoResponse.model_validate(result)


@mcp.tool()
def vault_validate(
    vault_address: Annotated[str, Field(description="Plasma Vault address.")],
    chain_id: ChainArg = None,
    block_number: BlockArg = None,
) -> VaultValidationResponse:
    """Check a Plasma Vault's health and return an ok / warning / critical verdict.

    Use this to answer "is this vault OK?": the verdict, each finding, and the
    reconciliation, lending-health and ERC20 numbers behind them. Use
    vault_info for what the vault is and holds (roles, fees, fuses,
    substrates). Every read is pinned to one block, so the result is
    consistent. It reads about as much as vault_info and takes about as long.
    """
    cfg = load_config()
    checksum = _vault_arg(vault_address)
    chain_id = _resolve_chain_id(cfg, checksum, chain_id)
    ensure_supported_chain(chain_id)
    ctx, effective_block = _build_ctx(cfg, chain_id, block_number)

    # Same cheap probe as vault_info: "not a vault" fails before the fetch.
    resolve_access_manager(ctx, checksum)

    validation = validate_vault(ctx, checksum, block=effective_block, metadata=True)
    return VaultValidationResponse.model_validate(
        build_vault_validation_json(
            validation, CHAIN_NAMES.get(chain_id, str(chain_id))
        )
    )


@mcp.tool()
def vault_preflight(
    vault_address: Annotated[str, Field(description="Plasma Vault address.")],
    calldata: Annotated[
        str, Field(description="execute((address,bytes)[]) calldata, hex.")
    ],
    sender: Annotated[
        str, Field(description="The account that will send execute (an ALPHA).")
    ],
    chain_id: ChainArg = None,
    block_number: BlockArg = None,
    simulate: Annotated[
        bool,
        Field(
            description="Run the batch through eth_simulateV1; false: static checks."
        ),
    ] = True,
) -> ExecutePreflightResponse:
    """Check an execute batch before it is signed: the sender's permission (or a
    ready scheduled operation), each fuse's registration, the balance fuses of
    every touched and dependent market, and a simulation of the calldata on top
    of the base block. Read-only; a pass is evidence for that state, not a promise."""
    try:
        actions = parse_execute_actions(calldata)
    except ValueError as exc:
        raise ValueError(f"calldata: {exc}") from exc
    if not actions:
        raise ValueError("calldata: the batch has no actions")
    cfg = load_config()
    checksum = _vault_arg(vault_address)
    chain_id = _resolve_chain_id(cfg, checksum, chain_id)
    ensure_supported_chain(chain_id)
    ctx, _ = _build_ctx(cfg, chain_id, block_number)
    result = preflight_execute(
        ctx,
        checksum,
        actions,
        sender=Web3.to_checksum_address(sender),
        simulate=simulate,
    )
    return ExecutePreflightResponse.model_validate(build_execute_preflight_json(result))


# Static mirror of Roles member names: pyright rejects a dynamically built
# enum in annotations, so the drift guard lives in test_mcp_server.py.
RoleName = Literal[
    "ADMIN_ROLE",
    "OWNER_ROLE",
    "GUARDIAN_ROLE",
    "TECH_PLASMA_VAULT_ROLE",
    "IPOR_DAO_ROLE",
    "TECH_CONTEXT_MANAGER_ROLE",
    "TECH_WITHDRAW_MANAGER_ROLE",
    "TECH_VAULT_TRANSFER_SHARES_ROLE",
    "ATOMIST_ROLE",
    "ALPHA_ROLE",
    "FUSE_MANAGER_ROLE",
    "PRE_HOOKS_MANAGER_ROLE",
    "TECH_PERFORMANCE_FEE_MANAGER_ROLE",
    "TECH_MANAGEMENT_FEE_MANAGER_ROLE",
    "CLAIM_REWARDS_ROLE",
    "TECH_REWARDS_CLAIM_MANAGER_ROLE",
    "TRANSFER_REWARDS_ROLE",
    "WHITELIST_ROLE",
    "CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE",
    "WITHDRAW_MANAGER_REQUEST_FEE_ROLE",
    "WITHDRAW_MANAGER_WITHDRAW_FEE_ROLE",
    "UPDATE_MARKETS_BALANCES_ROLE",
    "UPDATE_REWARDS_BALANCE_ROLE",
    "PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE",
    "PUBLIC_ROLE",
]


@mcp.tool()
def vault_role_accounts(
    vault_address: Annotated[str, Field(description="Plasma Vault address.")],
    role: Annotated[
        RoleName | None,
        Field(description="Role to filter by; omit to list all roles."),
    ] = None,
    chain_id: ChainArg = None,
    block_number: BlockArg = None,
) -> RoleAccountsResponse:
    """List confirmed role holders on a Plasma Vault's AccessManager.

    TECH_* roles are held by protocol contracts, not EOAs.
    Cost: scans RoleGranted logs + one hasRole read per unique
    (role, account) pair; the scan pages eth_getLogs within the provider's
    range caps, so it is slower on chains whose providers cap the range.
    """
    cfg = load_config()
    role_id = None if role is None else Roles.resolve(role)
    checksum = _vault_arg(vault_address)
    chain_id = _resolve_chain_id(cfg, checksum, chain_id)
    # Fail fast (before any RPC) on chains the vault tooling is not
    # validated on.
    ensure_supported_chain(chain_id)
    ctx, _ = _build_ctx(cfg, chain_id, block_number)

    access_manager = resolve_access_manager(ctx, checksum)
    accounts = (
        access_manager.read_all_role_accounts(ctx)
        if role_id is None
        else access_manager.read_accounts_with_role(ctx, role_id)
    )
    return RoleAccountsResponse.model_validate(
        build_role_accounts_json(
            accounts,
            vault=checksum,
            access_manager=access_manager.address,
            chain_id=chain_id,
            role=role_id,
        )
    )


@mcp.tool()
def vault_oracle_mapping(
    vault_address: Annotated[str, Field(description="Plasma Vault address.")],
    chain_id: ChainArg = None,
    block_number: BlockArg = None,
    max_depth: Annotated[
        int,
        Field(
            description="Max recursion depth for feeds derived from other assets.", ge=0
        ),
    ] = 6,
) -> OracleMappingResponse:
    """Map how a Plasma Vault prices every configured asset at a block.

    For each asset the vault's price oracle knows about, resolves the source
    price feed, classifies its type by on-chain interface probing, reads the
    effective price, and recursively resolves feeds that derive their price
    from another asset. Unknown feeds are reported as partial, never dropped.

    Source types: DualCrossReferencePriceFeed (X/USD composed from an X/Y and
    a Y/USD component feed, both reported in source_detail),
    ChainlinkAggregator (leaf feed confirmed by the full AggregatorV3Interface
    — latestRoundData, decimals, description, version — with sane metadata),
    chainlink_style (leaf that merely answers latestRoundData, or whose
    evidence is contradicted: synthetic zero roundId/updatedAt, empty
    description, a foreign getter answering; still resolved, but verify the
    address yourself if identity matters), ERC4626PriceFeed (share→asset
    rate, recurses on the underlying), CollateralTokenOnMorphoMarketPriceFeed
    (recurses on the loan token), middleware_fallback (no per-vault source;
    priced by the oracle's underlying global middleware, followed as a
    dependency when discoverable), custom_unknown (partial). Classification is
    heuristic interface probing: it grades on-chain evidence and cannot prove
    a contract's identity or operator.

    Every aggregator-compatible read reports description() and the full
    latestRoundData round in source_detail (description, round_id, answer,
    decimals, started_at, started_at_utc, updated_at, updated_at_utc,
    answered_in_round); Chainlink-tier leaves add aggregator and phase_id
    (proxy-deployment evidence; null when unanswered). Raw values only — no
    staleness judgment is made (composed feeds may return synthetic zero
    timestamps; their *_utc twins are null then, while the raw ints keep
    fidelity), and description is null when the feed does not implement it.
    Use the description to confirm a feed's units before trusting its
    answer. Registry-resolved fallback
    prices carry no per-feed metadata — the registry picks the aggregator
    internally; its address is included, so agents can call getFeed on
    demand.

    Node status: resolved (own feed explained and every dependency resolved),
    partially_resolved (own feed explained, but some descendant is not
    resolved), or partial (the node's own resolution is incomplete — see
    reason). The unresolved array lists partial nodes only; demoted parents
    self-describe. The mapping-level status rolls up the roots: resolved
    (every root resolved; vacuously for zero assets), unresolved (every
    root partial — total failure), else partially_resolved.

    Assets are enumerated via getConfiguredAssets() when the oracle exposes
    it, else by replaying AssetPriceSourceUpdated logs (asset_source:
    'events'). Historical blocks require an archive node.
    """
    cfg = load_config()
    checksum = _vault_arg(vault_address)
    chain_id = _resolve_chain_id(cfg, checksum, chain_id)
    # The asset-source event replay scans logs from block 0 — fail fast
    # (before any RPC) on chains the vault tooling is not validated on.
    ensure_supported_chain(chain_id)
    ctx, _ = _build_ctx(cfg, chain_id, block_number)

    # The event-replay fallback and the output both need a concrete block
    # number, so resolve "latest" up front and pin the context to it.
    effective_block = (
        block_number if block_number is not None else ctx.web3.eth.block_number
    )
    ctx = ctx.at_block(effective_block)

    resolve_access_manager(ctx, checksum)

    mapping = build_oracle_mapping(
        ctx, checksum, effective_block=effective_block, max_depth=max_depth
    )
    return OracleMappingResponse.from_mapping(mapping)


@mcp.tool()
def vault_list() -> list[VaultListEntry]:
    """List all saved Plasma Vaults with their chain, label, and address."""
    cfg = load_config()
    return [
        VaultListEntry(
            address=v.address,
            label=v.label,
            chain=CHAIN_NAMES.get(v.chain_id, str(v.chain_id)),
            chain_id=v.chain_id,
        )
        for v in cfg.vaults
    ]


@mcp.tool()
def vault_add(
    address: str,
    label: str = "",
    chain_id: Annotated[
        ChainId | None,
        Field(
            description="Chain ID; omitted: the chain of the only configured provider.",
            gt=0,
        ),
        BeforeValidator(_no_zero_sentinel("chain_id")),
    ] = None,
) -> ActionResult:
    """Save a Plasma Vault to the local config.

    Args:
        address: Vault address (required).
        label: Human-readable label (fetched from on-chain name() if empty).
    """
    checksum = _vault_arg(address)
    cfg = load_config()

    if chain_id is None:
        if len(cfg.providers) == 1:
            chain_id = ChainId(int(next(iter(cfg.providers))))
        else:
            raise ValueError(
                "Cannot auto-detect chain ID — multiple providers configured. "
                "Provide chain_id."
            )

    if not label:
        provider_url = _resolve_provider(cfg, chain_id)
        ctx = Web3Context.from_url(provider_url)
        try:
            label = PlasmaVault(checksum).name().call(ctx)
        except Exception:
            label = checksum

    for vault_entry in cfg.vaults:
        if vault_entry.address.lower() == checksum.lower():
            vault_entry.label = label
            vault_entry.chain_id = chain_id
            save_config(cfg)
            return ActionResult(message=f"Vault {label} ({checksum}) updated.")

    cfg.vaults.append(VaultEntry(address=checksum, label=label, chain_id=chain_id))
    save_config(cfg)
    return ActionResult(message=f"Vault {label} ({checksum}) added.")


@mcp.tool()
def vault_remove(address: str) -> ActionResult:
    """Remove a Plasma Vault from the local config.

    Args:
        address: Vault address to remove.
    """
    cfg = load_config()
    before = len(cfg.vaults)
    cfg.vaults = [v for v in cfg.vaults if v.address.lower() != address.lower()]
    if len(cfg.vaults) == before:
        return ActionResult(message="Vault not found.")
    save_config(cfg)
    return ActionResult(message="Vault removed.")


# ---------------------------------------------------------------------------
# Config tools
# ---------------------------------------------------------------------------


@mcp.tool()
def config_show() -> ConfigShowResponse:
    """Show current fusion CLI configuration.

    Provider URLs are masked to scheme and host: the stored value may carry an
    API key in its path or query string.
    """
    cfg = load_config()
    return ConfigShowResponse(
        providers={
            chain_id: mask_provider_url(url) for chain_id, url in cfg.providers.items()
        },
        vaults=[
            VaultListEntry(
                address=v.address,
                label=v.label,
                chain=CHAIN_NAMES.get(v.chain_id, str(v.chain_id)),
                chain_id=v.chain_id,
            )
            for v in cfg.vaults
        ],
        etherscan_api_key="***" if cfg.etherscan_api_key else None,
    )


@mcp.tool()
def config_set_provider(
    url: str,
    chain_id: Annotated[
        ChainId | None,
        Field(description="Chain ID; omitted: read from the URL's eth_chainId.", gt=0),
        BeforeValidator(_no_zero_sentinel("chain_id")),
    ] = None,
) -> ActionResult:
    """Set RPC provider URL for a chain.

    Args:
        url: RPC provider URL.
    """
    if chain_id is None:
        web3 = Web3(Web3.HTTPProvider(url))
        chain_id = ChainId(web3.eth.chain_id)

    cfg = load_config()
    cfg.providers[str(chain_id)] = url
    save_config(cfg)
    return ActionResult(message=f"Provider for chain {chain_id} set.")


@mcp.tool()
def config_set_etherscan_key(api_key: str) -> ActionResult:
    """Set Etherscan API key (works for all chains via Etherscan V2).

    Args:
        api_key: Etherscan API key.
    """
    cfg = load_config()
    cfg.etherscan_api_key = api_key
    save_config(cfg)
    return ActionResult(message="Etherscan API key set.")


# ---------------------------------------------------------------------------
# Market tools
# ---------------------------------------------------------------------------


@mcp.tool()
def market_morpho_blue(
    market_id: str,
    chain_id: RequiredChainArg,
    block_number: BlockArg = None,
    no_api: bool = False,
) -> MorphoBlueMarketResponse:
    """Inspect a Morpho Blue market by its 32-byte market ID.

    Returns market parameters, on-chain state + APYs (via IRM `borrowRateView`),
    and — unless `no_api` is set — the MetaMorpho vaults supplying this market
    along with their PublicAllocator flow caps and admin addresses.

    Returned JSON fields:
        market_id, chain_id, public_allocator
        market_params: { loan_token, collateral_token, oracle, irm, lltv }
        state: { total_supply_assets, total_supply_shares, total_borrow_assets,
                 total_borrow_shares, liquidity_assets, fee_wad, last_update }
        rates: { rate_per_second_wad, utilization, borrow_apy, supply_apy }
        loan_asset / collateral_asset: { address, symbol, decimals } (when API used)
        vaults: list of supplying vaults with public_allocator_config flow caps
        api_error: string if Morpho API call failed (state still returned)

    Args:
        market_id: 32-byte market ID (0x-prefixed hex or 64 raw hex chars).
        chain_id: EVM chain ID (1 = Ethereum, 8453 = Base, ...).
        no_api: Skip Morpho API call (no vault discovery / PublicAllocator info).
    """
    mid = parse_morpho_market_id(market_id)

    cfg = load_config()
    ctx, _ = _build_ctx(cfg, chain_id, block_number)
    reader = MorphoReader(morpho_blue_address(chain_id))
    params, state = reader.read_existing_market(ctx, mid)
    rates = reader.read_rates_from(ctx, state, params)

    api_market = None
    api_error: str | None = None
    if not no_api:
        try:
            api_market = fetch_market(mid, chain_id)
        except MorphoApiError as exc:
            api_error = str(exc)

    public_allocator = PUBLIC_ALLOCATOR_ADDRESSES.get(chain_id)
    payload = build_morpho_blue_market_json(
        market_id=mid,
        chain_id=chain_id,
        params=params,
        state=state,
        rates=rates,
        api_market=api_market,
        api_error=api_error,
        public_allocator=public_allocator,
    )
    return MorphoBlueMarketResponse.model_validate(payload)


@mcp.tool()
def market_meta_morpho(
    vault_address: str,
    chain_id: RequiredChainArg,
) -> MetaMorphoVaultResponse:
    """Inspect a MetaMorpho V1 or Morpho Vault V2 by address (Morpho API).

    Auto-detects vault version. Returns roles (owner/curator/allocators/sentinels),
    fees, adapters (V2) or supply allocations (V1), and per-market caps with
    remaining headroom — useful for deciding whether liquidity can be pushed
    into a Morpho Blue market via reallocate.

    Returned JSON fields (shape depends on `version`):
        version: "v1" or "v2"
        chain_id, address, name, symbol
        asset: { address, symbol, decimals }
        total_assets
        owner, curator, allocators
        v2 only: idle_assets, liquidity, share_price, max_apy, performance_fee,
                 performance_fee_recipient, management_fee, management_fee_recipient,
                 sentinels, liquidity_adapter, adapters, caps
        v1 only: fee_wad, guardian, fee_recipient, public_allocator, allocations

    Args:
        vault_address: MetaMorpho V1 or Vault V2 address.
        chain_id: EVM chain ID.
    """
    info = fetch_vault(Web3.to_checksum_address(vault_address), chain_id)
    payload = build_meta_morpho_json(info, chain_id)
    return MetaMorphoVaultResponse.model_validate(payload)


@mcp.tool()
def address_lookup(
    query: str, chain_id: RegistryChainArg = None
) -> AddressLookupResponse:
    """Find IPOR Fusion contracts in the ipor-abi registry by name substring, or
    the names of an address. Reads one file per chain (cached ten minutes)."""
    registries = fetch_address_registries(None if chain_id is None else [chain_id])
    return AddressLookupResponse.model_validate(
        build_address_lookup_json(query, registries)
    )


@mcp.tool()
def address_names(chain_id: RegistryChainArg = None) -> AddressNamesResponse:
    """List the contract names in the ipor-abi registry, with the chains that
    have each; use them with address_lookup."""
    registries = fetch_address_registries(None if chain_id is None else [chain_id])
    return AddressNamesResponse.model_validate(build_address_names_json(registries))


@mcp.tool()
def address_list(chain_id: RequiredChainArg) -> AddressListResponse:
    """One chain's full {name: address} registry: hundreds of entries;
    address_lookup is cheaper for one contract."""
    return AddressListResponse.model_validate(
        build_address_list_json(fetch_address_registry(chain_id))
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
