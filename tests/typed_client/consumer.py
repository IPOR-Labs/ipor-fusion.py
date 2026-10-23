"""A typed client of the public SDK API, imported from ``ipor_fusion`` only and
checked by pyright with ``reportArgumentType`` on (``executionEnvironments`` in
``pyproject.toml``).

Public inputs take the nominal types: ``ChainId(...)``, ``MarketId`` (every
``IporFusionMarkets`` member is one), ``RoleId`` or a ``Roles`` member, and a
``ChecksumAddress``. This module pins how a client spells each call; never
executed.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import assert_type

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion import (
    ERC20,
    PUBLIC_ALLOCATOR_ADDRESSES,
    AccessManager,
    Amount,
    BalanceProposal,
    BatchValue,
    Call,
    ChainId,
    Decimals,
    ExternalStateExecutor,
    ExternalStateOperationArgs,
    ExternalStateUnpausedArgs,
    ExternalStateUnpausedEvent,
    FileContractCache,
    FuseAddress,
    IporFusionMarkets,
    LegacyPriceOracleMiddleware,
    MarketId,
    MetaMorphoV1Json,
    MetaMorphoV2Json,
    MorphoReader,
    Multicall3,
    Multicall3Batch,
    NullCache,
    OracleMapping,
    OracleMappingJson,
    Period,
    PlasmaVault,
    PlasmaVaultAddress,
    Price,
    PriceOracleMiddleware,
    RewardsManager,
    RoleId,
    Roles,
    SimulatedCallResult,
    SimulationObservation,
    TransactionError,
    VaultInfoJson,
    VaultSimulator,
    Wad,
    Web3Context,
    build_meta_morpho_json,
    build_morpho_blue_market_json,
    build_vault_info_json,
    decode_substrate,
    ensure_supported_chain,
    external_state_events,
    fetch_deployment_info,
    fetch_market,
    fetch_unpriceable_priced_tokens,
    fetch_vault,
    fetch_vault_data,
    find_orphan_fuse_markets,
    format_market_label,
    get_contract_name,
    market_name,
    morpho_blue_address,
    parse_hex_address,
    parse_morpho_market_id,
    parse_plasma_vault_address,
    parse_tx_hash,
    price_oracle_middleware,
    resolve_token_symbol,
    validate_vault,
    validate_vault_state,
)
from ipor_fusion.crosschain import (
    AssetId,
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
    Command,
    CommandId,
    CrosschainDispatcher,
    CrosschainExecutor,
    CrosschainFactory,
    CrosschainLane,
    LaneObservation,
    ProposalId,
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
    StargateCrosschainFactory,
)
from ipor_fusion.hypercore import (
    HyperCoreActionEnqueuedArgs,
    HyperCoreActionId,
    HyperCoreDepositFuseEnterArgs,
    HyperCoreDepositFuseEnterEvent,
    HyperCorePendingReader,
    hypercore_events,
)

ETHEREUM = ChainId(1)
VAULT = parse_plasma_vault_address("0x43ee0243ea8cf02f7087d8b16c8d2007cc9c7ca2")


def access(ctx: Web3Context, am: AccessManager, account: ChecksumAddress) -> None:
    am.has_role(RoleId(100), account)
    am.has_role(Roles.ATOMIST_ROLE, account)
    am.grant_role(Roles.ALPHA_ROLE, account, Period(0))
    am.revoke_role(Roles.ALPHA_ROLE, account)
    am.read_accounts_with_role(ctx, Roles.resolve("ATOMIST"))


def market_limits(vault: PlasmaVault) -> None:
    vault.setup_markets_limits([(IporFusionMarkets.AAVE_V3, Wad(6 * 10**17))])


def governance(vault: PlasmaVault) -> None:
    vault.set_minimal_execution_delays_for_roles(
        [(Roles.ALPHA_ROLE, Period.HOUR), (RoleId(100), Period(0))]
    )


def vault_reads(ctx: Web3Context, vault: PlasmaVault) -> None:
    ensure_supported_chain(ETHEREUM)
    data = fetch_vault_data(ctx, vault.address, block=1, lending=False)
    fetch_deployment_info(ctx, VAULT, api_key=None)
    vault_info_document(build_vault_info_json(data))
    validate_vault_state(data)
    validate_vault(ctx.at_block(1), VAULT)
    vault.get_fuses().call(ctx, block=1)
    resolve_token_symbol(ctx, VAULT)
    fetch_unpriceable_priced_tokens(
        ctx,
        price_oracle=VAULT,
        underlying=VAULT,
        balance_fuses=[],
        market_substrates={},
        morpho_positions=None,
    )


def caches(web3: Web3) -> None:
    Web3Context(web3, ETHEREUM, cache=NullCache())
    ctx = Web3Context(web3, ETHEREUM, cache=FileContractCache(Path("cache.json")))
    get_contract_name(ETHEREUM, VAULT, cache=ctx.cache)


def lookups() -> None:
    decode_substrate(b"", IporFusionMarkets.AAVE_V3)
    format_market_label(IporFusionMarkets.ERC20_VAULT_BALANCE)
    market_name(MarketId(100_001))
    get_contract_name(ETHEREUM, VAULT)
    get_contract_name(ETHEREUM, parse_hex_address(VAULT.lower()))
    get_contract_name(ETHEREUM, VAULT, cache=NullCache())
    TransactionError("reverted", tx_hash=parse_tx_hash("0x" + "ab" * 32))
    morpho_blue_address(ETHEREUM)
    meta_morpho_document(build_meta_morpho_json(fetch_vault(VAULT, ETHEREUM), ETHEREUM))
    fetch_market(parse_morpho_market_id("0x" + "ab" * 32), ETHEREUM)


def crosschain(
    executor: CrosschainExecutor,
    dispatcher: CrosschainDispatcher,
    factory: CrosschainFactory,
) -> None:
    executor.approve_balance(ProposalId(1))
    dispatcher.command_status(CommandId(bytes(32)))
    factory.asset_frozen(AssetId(bytes(32)))


def crosschain_reads_feed_writes(
    ctx: Web3Context,
    ccip: CcipCrosschainExecutor,
    ccip_dispatcher: CcipCrosschainDispatcher,
    ccip_factory: CcipCrosschainFactory,
    stargate: StargateCrosschainExecutor,
    stargate_factory: StargateCrosschainFactory,
) -> None:
    # Ids read through the SDK go back in without re-wrapping.
    ccip.approve_balance(ccip.active_proposal_id(ETHEREUM).call(ctx))
    stargate.approve_balance(stargate.active_proposal_id(ETHEREUM).call(ctx))
    ccip_factory.asset_frozen(ccip.asset_id().call(ctx))
    ccip_factory.asset_config(ccip_dispatcher.asset_id().call(ctx))
    stargate_factory.asset_config(stargate.asset_id().call(ctx))
    ccip_dispatcher.processed_operation(ccip.active_return(ETHEREUM).call(ctx))
    stargate.deployment_status(stargate.pending_request_by_chain(ETHEREUM).call(ctx))
    stargate_factory.cancel_pending_deployment(
        stargate.pending_request_by_chain(ETHEREUM).call(ctx)
    )


def roles_flow_from_getters_into_writes(ctx: Web3Context) -> None:
    vault = PlasmaVault(VAULT)
    manager = AccessManager(vault.get_access_manager_address().call(ctx))
    manager.has_role(Roles.ATOMIST_ROLE, vault.address)
    fuses = vault.get_fuses().call(ctx)
    vault.remove_fuses(fuses)
    for balance_fuse in vault.read_balance_fuses(ctx):
        vault.balance_fuse_value(balance_fuse.fuse)


def reward_fuses_flow(ctx: Web3Context, manager: RewardsManager) -> None:
    fuses = manager.get_rewards_fuses().call(ctx)
    manager.add_reward_fuses(fuses)
    manager.is_reward_fuse_supported(fuses[0])


def remote_erc4626_destination(
    ctx: Web3Context, dispatcher: StargateCrosschainDispatcher
) -> None:
    # Allowlisted destinations are any ERC4626, not only Plasma Vaults.
    target = dispatcher.get_allowed_vaults().call(ctx)[0]
    Command.deposit(target, Amount(1))
    dispatcher.is_allowed_vault(target)


def layerzero_ids_round_trip(
    ctx: Web3Context,
    executor: StargateCrosschainExecutor,
    factory: StargateCrosschainFactory,
) -> None:
    eid = executor.eid_of(ETHEREUM).call(ctx)
    executor.chain_id_of(eid)
    factory.chain_id_of(factory.eid_of(ETHEREUM).call(ctx))


def offline_wrappers_and_executors(
    ctx: Web3Context, token: ChecksumAddress, account: ChecksumAddress
) -> None:
    # Construction and Call building are offline; execution takes the context.
    vault = PlasmaVault(VAULT)
    calldata: bytes = vault.convert_to_public_vault().calldata
    supply: Call[Amount] = ERC20(token).total_supply()
    doubled: Call[int] = supply.map(lambda amount: amount * 2)
    doubled.call(ctx)
    supply.call(ctx=ctx, block=1)
    supply.build_transaction(ctx)
    supply.estimate_gas(ctx, block="pending")
    vault.convert_to_public_vault().send(ctx)
    decimals: list[Decimals] = Multicall3(ctx).aggregate([ERC20(token).decimals()])
    legacy = LegacyPriceOracleMiddleware(token, Decimals(8))
    price: Price = legacy.get_asset_price(account).call(ctx)
    price_oracle_middleware(ctx, token).get_asset_price(account).call(ctx)
    del calldata, decimals, price


def mixed_batches_keep_their_types(
    ctx: Web3Context, web3: Web3, token: ChecksumAddress
) -> None:
    vault = PlasmaVault(VAULT)
    batch: Multicall3Batch = Multicall3(ctx).batch()
    assets = batch.add(vault.total_assets())
    fuses = batch.add(vault.get_fuses())
    name = batch.try_add(vault.name())
    batch.run()
    assert_type(assets, BatchValue[Amount])
    assert_type(assets.value, Amount)
    assert_type(fuses.value, list[FuseAddress])
    assert_type(name.value, str | None)

    sim = VaultSimulator(web3, vault=VAULT)
    sim.with_state_override(token, balance=hex(10**18), stateDiff={"0x01": "0x02"})
    sim.with_block_override(gasLimit=30_000_000, feeRecipient=token)
    sim.with_state_override_provider(lambda n: {token: {"nonce": hex(n)}})
    before = sim.observe(vault.total_assets())
    decimals = sim.observe(ERC20(token).decimals(), label="decimals")
    sim.run()
    assert_type(before, SimulationObservation[Amount])
    assert_type(before.value, Amount)
    assert_type(decimals.value, Decimals)
    assert_type(before.result, SimulatedCallResult)


def proposals_are_chain_bound(
    ctx: Web3Context, executor: ExternalStateExecutor
) -> None:
    pending: Call[BalanceProposal | None] = executor.pending_proposals(
        VAULT, chain_id=ctx.chain_id
    )
    proposal = pending.call(ctx)
    if proposal is not None:
        ExternalStateExecutor.proposal_hash(
            executor=executor.address,
            chain_id=ctx.chain_id,
            balance_account=proposal.balance_account,
            value=proposal.value,
            proposer=proposal.proposer,
            proposed_at=proposal.proposed_at,
            nonce=proposal.nonce,
        )


def lanes_own_their_contexts(lane: CrosschainLane) -> None:
    lane.get_balance().call(lane.hub_ctx)
    lane.observation().call(lane.spoke_ctx)
    settled: Amount = lane.read_hub_state().settled_remote_balance
    observation: LaneObservation = lane.read_observation()
    del settled, observation


def producers_feed_consumers(ctx: Web3Context, oracle: ChecksumAddress) -> None:
    # A value one SDK call returns goes into the next one without re-wrapping.
    vault = PlasmaVault(VAULT)
    decimals = PriceOracleMiddleware(oracle).base_currency_decimals().call(ctx)
    LegacyPriceOracleMiddleware(oracle, decimals)
    data = fetch_vault_data(ctx, VAULT, lending=False)
    orphans = find_orphan_fuse_markets(data.fuse_markets or {}, set())
    vault.remove_fuses([fuse for fuses in orphans.values() for fuse in fuses])
    vault.configure_instant_withdrawal_fuses(
        [(fuse, [bytes(32)]) for fuse in vault.get_instant_withdrawal_fuses().call(ctx)]
    )
    HyperCorePendingReader(vault.address, oracle).is_pending().call(ctx)


def ccip_selectors_round_trip(ctx: Web3Context, factory: CcipCrosschainFactory) -> None:
    route = factory.ccip_route(ETHEREUM).call(ctx)
    factory.chain_id_of_selector(route.chain_selector)


def morpho_market_document(ctx: Web3Context) -> None:
    market_id = parse_morpho_market_id("0x" + "ab" * 32)
    reader = MorphoReader(morpho_blue_address(ETHEREUM))
    params, state = reader.read_existing_market(ctx, market_id)
    document = build_morpho_blue_market_json(
        market_id=market_id,
        chain_id=ETHEREUM,
        params=params,
        state=state,
        rates=reader.read_rates_from(ctx, state, params),
        public_allocator=PUBLIC_ALLOCATOR_ADDRESSES.get(ETHEREUM),
    )
    lltv_wad: str = document["market_params"]["lltv"]
    supply_apy: float = document["rates"]["supply_apy"]
    for supplier in document.get("vaults", []):
        allocators: list[ChecksumAddress] = supplier["allocators"]
        print(lltv_wad, supply_apy, allocators)


def vault_info_document(document: VaultInfoJson) -> None:
    """The ``vault info`` document is typed block by block: optional blocks
    are ``None``-checked, keys absent from some entries read with ``.get``."""
    vault: PlasmaVaultAddress = document["vault"]
    fees = document["fees"]
    if fees is not None:
        management_fee: float | None = fees["management_fee_percent"]
        print(vault, management_fee)
    for entry in document["balance_fuses"]:
        market_id: MarketId = entry["market_id"]
        share: float | None = entry.get("pct_of_total")
        print(market_id, share, entry["balance"]["raw"])
    for market, substrates in document["substrates"].items():
        print(market, [substrate.get("address") for substrate in substrates])
    print(document["reconciliation"]["delta"]["percent"])
    untyped: Mapping[str, object] = document
    print(untyped)


def meta_morpho_document(document: MetaMorphoV1Json | MetaMorphoV2Json) -> None:
    if document["version"] == "v2":
        print([cap["room"] for cap in document["caps"]])
    else:
        print([allocation["cap_room"] for allocation in document["allocations"]])


def event_args(receipt: TxReceipt) -> None:
    """An event is a union of one class per event: comparing ``name`` with a
    literal narrows the event, and its ``args``, to that event's class."""
    for event in external_state_events(receipt):
        if event.name == "ExternalStateUnpaused":
            assert_type(event, ExternalStateUnpausedEvent)
            assert_type(event.args, ExternalStateUnpausedArgs)
            signer: ChecksumAddress = event.args["signer"]
            balance: Amount = event.args["confirmedTotalBalance"]
            print(signer, balance, event.block_number, event.transaction_hash)
        elif event.name == "ExternalStateOperationFuseEnter":
            assert_type(event.args, ExternalStateOperationArgs)
            print(event.args["valueInUnderlying"], event.log_index)
    for hc_event in hypercore_events(receipt):
        if hc_event.name == "HyperCoreActionEnqueued":
            assert_type(hc_event.args, HyperCoreActionEnqueuedArgs)
            action: HyperCoreActionId = hc_event.args["actionId"]
            print(action, hc_event.args["actionClass"], hc_event.fuse)
        match hc_event:
            case HyperCoreDepositFuseEnterEvent(args=deposit):
                assert_type(deposit, HyperCoreDepositFuseEnterArgs)
                print(deposit["evmAsset"], hc_event.emitter)
            case _:
                pass


def oracle_mapping_document(mapping: OracleMapping) -> None:
    document: OracleMappingJson = mapping.to_dict()
    for node in document["configured_assets"]:
        detail = node["source_detail"]
        if detail is not None and "aggregator" in detail:
            print(detail["description"], detail["phase_id"])
