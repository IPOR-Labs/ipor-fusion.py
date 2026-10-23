"""API misuse that must not type-check. Each line carries the pyright rule it
violates; ``reportUnnecessaryTypeIgnoreComment`` (``pyproject.toml``) fails
the check if one of them starts to type-check. Never executed."""

from typing import Any

from eth_typing import ChecksumAddress

from ipor_fusion import (
    ERC20,
    AccessManager,
    AccessManagerAddress,
    Amount,
    ChainId,
    ExternalStateEvent,
    ExternalStateExecutor,
    MarketId,
    MorphoBlueMarketJson,
    Multicall3,
    PlasmaVault,
    PlasmaVaultAddress,
    VaultInfoJson,
    VaultSimulator,
    Web3Context,
    fetch_unpriceable_priced_tokens,
)
from ipor_fusion.crosschain import CcipCrosschainFactory
from ipor_fusion.hypercore import HyperCoreEvent, HyperCorePendingReader


def execution_needs_a_context(ctx: Web3Context, token: ChecksumAddress) -> None:
    ERC20(token).decimals().call()  # pyright: ignore[reportCallIssue]
    ERC20(token).decimals().call(None)  # pyright: ignore[reportArgumentType]
    ERC20(token).decimals().call(block=1)  # pyright: ignore[reportCallIssue]
    ERC20(token).approve(token, Amount(1)).send()  # pyright: ignore[reportCallIssue]
    ERC20(token).approve(token, Amount(1)).send(None)  # pyright: ignore[reportArgumentType]
    ERC20(token).approve(token, Amount(1)).build_transaction()  # pyright: ignore[reportCallIssue]
    ERC20(token).approve(token, Amount(1)).estimate_gas()  # pyright: ignore[reportCallIssue]
    Multicall3()  # pyright: ignore[reportCallIssue]


def wrappers_take_no_context(
    ctx: Web3Context,
    token: ChecksumAddress,
    vault: PlasmaVaultAddress,
    manager: AccessManagerAddress,
) -> None:
    PlasmaVault(ctx, vault)  # pyright: ignore[reportCallIssue]
    ERC20(None, token)  # pyright: ignore[reportCallIssue]
    AccessManager(ctx, manager)  # pyright: ignore[reportCallIssue]
    PlasmaVault.encoder(vault)  # pyright: ignore[reportAttributeAccessIssue]
    print(ERC20(token).decimals().ctx)  # pyright: ignore[reportAttributeAccessIssue]


def eager_reads_take_the_context(ctx: Web3Context, vault: PlasmaVaultAddress) -> None:
    PlasmaVault(vault).get_balance_fuses()  # pyright: ignore[reportAttributeAccessIssue]
    PlasmaVault(vault).read_balance_fuses()  # pyright: ignore[reportCallIssue]
    PlasmaVault(vault).read_withdraw_manager_address()  # pyright: ignore[reportCallIssue]


def proposal_hashes_name_their_chain(executor: ExternalStateExecutor) -> None:
    executor.pending_proposals(executor.address)  # pyright: ignore[reportCallIssue]
    ExternalStateExecutor.proposal_hash(
        executor=executor.address,
        chain_id=ChainId(1),
        balance_account=executor.address,
        value=Amount(1),
        proposer=executor.address,
        proposed_at=1,  # pyright: ignore[reportArgumentType]
        nonce=1,
    )


def roles_do_not_mix(ctx: Web3Context, vault: PlasmaVaultAddress) -> None:
    pv = PlasmaVault(vault)
    pv.configure_instant_withdrawal_fuses([(pv.address, [])])  # pyright: ignore[reportArgumentType]
    HyperCorePendingReader(pv, pv.address)  # pyright: ignore[reportArgumentType]
    # A market limit is a WAD share of total assets, not an asset amount.
    pv.setup_markets_limits([(MarketId(1), Amount(10**6))])  # pyright: ignore[reportArgumentType]


def selectors_are_not_chain_ids(factory: CcipCrosschainFactory) -> None:
    factory.chain_id_of_selector(42161)  # pyright: ignore[reportArgumentType]


def batch_values_keep_their_types(
    ctx: Web3Context, vault: PlasmaVaultAddress, sim: VaultSimulator
) -> None:
    batch = Multicall3(ctx).batch()
    name = batch.try_add(PlasmaVault(vault).name())
    batch.run()
    required: str = name.value  # pyright: ignore[reportAssignmentType]
    assets: Amount = sim.observe(PlasmaVault(vault).name()).value  # pyright: ignore[reportAssignmentType]
    sim.observe("label", PlasmaVault(vault).total_assets())  # pyright: ignore[reportCallIssue]
    del required, assets


def simulator_overrides_name_wire_keys(
    sim: VaultSimulator, account: ChecksumAddress
) -> None:
    sim.with_state_override(account, balanse="0x1")  # pyright: ignore[reportCallIssue]
    sim.with_state_override(account, balance=1)  # pyright: ignore[reportArgumentType]
    sim.with_block_override(gas_limit=30_000_000)  # pyright: ignore[reportCallIssue]
    sim.with_state_override_provider(lambda n: {account: {"nonse": "0x1"}})  # pyright: ignore[reportArgumentType]
    sim.add_transaction({"from": account, "to": account, "gas": "0x1"})  # pyright: ignore[reportArgumentType]


def options_are_keyword_only(ctx: Web3Context, oracle: ChecksumAddress) -> None:
    Multicall3(ctx, oracle)  # pyright: ignore[reportCallIssue]
    fetch_unpriceable_priced_tokens(ctx, oracle, oracle, [], {}, None)  # pyright: ignore[reportCallIssue]


def documents_are_typed(document: VaultInfoJson, market: MorphoBlueMarketJson) -> None:
    print(document["total_asset"])  # pyright: ignore[reportGeneralTypeIssues]
    print(document["fees"]["management_fee_percent"])  # pyright: ignore[reportOptionalSubscript]
    print(market["vaults"])  # pyright: ignore[reportTypedDictNotRequiredAccess]
    print(document["balance_fuses"][0]["pct_of_total"])  # pyright: ignore[reportTypedDictNotRequiredAccess]
    legacy: dict[str, Any] = document  # pyright: ignore[reportAssignmentType]
    print(legacy)


def event_args_need_narrowing(event: ExternalStateEvent, hc: HyperCoreEvent) -> None:
    print(event.args["signer"])  # pyright: ignore[reportGeneralTypeIssues]
    print(hc.args["evmAsset"])  # pyright: ignore[reportGeneralTypeIssues]
    if event.name == "ExternalStateUnpaused":
        print(event.args["asset"])  # pyright: ignore[reportGeneralTypeIssues]
    if hc.name == "HyperCoreActionSettled":
        print(hc.args["payloadHash"])  # pyright: ignore[reportGeneralTypeIssues]
