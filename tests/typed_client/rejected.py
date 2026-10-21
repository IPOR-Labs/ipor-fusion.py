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
    CcipCrosschainFactory,
    ChainId,
    ExternalStateEvent,
    ExternalStateExecutor,
    HyperCoreEvent,
    HyperCorePendingReader,
    MorphoBlueMarketJson,
    Multicall3,
    PlasmaVault,
    PlasmaVaultAddress,
    VaultInfoJson,
    Web3Context,
    fetch_unpriceable_priced_tokens,
)


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


def selectors_are_not_chain_ids(factory: CcipCrosschainFactory) -> None:
    factory.chain_id_of_selector(42161)  # pyright: ignore[reportArgumentType]


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
