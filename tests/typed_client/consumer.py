"""A typed client of the public SDK API, checked by pyright with
``reportArgumentType`` on (``executionEnvironments`` in ``pyproject.toml``).

Public inputs take the nominal types: ``ChainId(...)``, ``MarketId`` (every
``IporFusionMarkets`` member is one), ``RoleId`` or a ``Roles`` member, and a
``ChecksumAddress``. This module pins how a client spells each call; never
executed.
"""

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion import (
    IporFusionMarkets,
    Roles,
    build_vault_info_json,
    decode_substrate,
    fetch_deployment_info,
    fetch_vault_data,
    format_market_label,
    market_name,
    parse_morpho_market_id,
    validate_vault,
)
from ipor_fusion.chains import ensure_supported_chain
from ipor_fusion.core.access import AccessManager
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.crosschain.ccip.contracts import (
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
)
from ipor_fusion.crosschain.contracts import (
    CrosschainDispatcher,
    CrosschainExecutor,
    CrosschainFactory,
)
from ipor_fusion.crosschain.stargate.contracts import (
    StargateCrosschainExecutor,
    StargateCrosschainFactory,
)
from ipor_fusion.explorer import get_contract_name
from ipor_fusion.morpho_api import fetch_market, fetch_vault
from ipor_fusion.readers.morpho import morpho_blue_address
from ipor_fusion.readers.unpriceable import fetch_unpriceable_priced_tokens
from ipor_fusion.types import (
    AssetId,
    ChainId,
    CommandId,
    MarketId,
    Period,
    ProposalId,
    RoleId,
)
from ipor_fusion.vault_data import resolve_token_symbol

ETHEREUM = ChainId(1)
VAULT = Web3.to_checksum_address("0x43ee0243ea8cf02f7087d8b16c8d2007cc9c7ca2")


def access(am: AccessManager, account: ChecksumAddress) -> None:
    am.has_role(RoleId(100), account)
    am.has_role(Roles.ATOMIST_ROLE, account)
    am.grant_role(Roles.ALPHA_ROLE, account, Period(0))
    am.revoke_role(Roles.ALPHA_ROLE, account)
    am.get_accounts_with_role(Roles.resolve("ATOMIST"))


def vault_reads(ctx: Web3Context, vault: PlasmaVault) -> None:
    ensure_supported_chain(ETHEREUM)
    data = fetch_vault_data(ctx, vault, chain_id=ETHEREUM)
    fetch_deployment_info(ctx, ETHEREUM, VAULT, None)
    build_vault_info_json(ctx, vault, data, VAULT, ETHEREUM, "ethereum")
    validate_vault(ctx, VAULT)
    resolve_token_symbol(ctx, VAULT)
    fetch_unpriceable_priced_tokens(ctx, VAULT, VAULT, [], {}, None)


def lookups() -> None:
    decode_substrate(b"", IporFusionMarkets.AAVE_V3)
    format_market_label(IporFusionMarkets.ERC20_VAULT_BALANCE)
    market_name(MarketId(100_001))
    get_contract_name(ETHEREUM, VAULT)
    morpho_blue_address(ETHEREUM)
    fetch_vault(VAULT, ETHEREUM)
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
    ccip: CcipCrosschainExecutor,
    ccip_dispatcher: CcipCrosschainDispatcher,
    ccip_factory: CcipCrosschainFactory,
    stargate: StargateCrosschainExecutor,
    stargate_factory: StargateCrosschainFactory,
) -> None:
    # Ids read through the SDK go back in without re-wrapping.
    ccip.approve_balance(ccip.active_proposal_id(ETHEREUM).call())
    stargate.approve_balance(stargate.active_proposal_id(ETHEREUM).call())
    ccip_factory.asset_frozen(ccip.asset_id().call())
    ccip_factory.asset_config(ccip_dispatcher.asset_id().call())
    stargate_factory.asset_config(stargate.asset_id().call())
    ccip_dispatcher.processed_operation(ccip.active_return(ETHEREUM).call())
    stargate.deployment_status(stargate.pending_request_by_chain(ETHEREUM).call())
    stargate_factory.cancel_pending_deployment(
        stargate.pending_request_by_chain(ETHEREUM).call()
    )
