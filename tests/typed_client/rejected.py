"""API misuse that must not type-check. Each line carries the pyright rule it
violates; ``reportUnnecessaryTypeIgnoreComment`` (``pyproject.toml``) fails
the check if one of them starts to type-check. Never executed."""

from eth_typing import ChecksumAddress

from ipor_fusion import (
    ERC20,
    AccessManager,
    AccessManagerAddress,
    Amount,
    ExternalStateExecutor,
    Multicall3,
    PlasmaVault,
    PlasmaVaultAddress,
    Web3Context,
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
