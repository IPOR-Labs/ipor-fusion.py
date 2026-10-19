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
    validate_vault,
)
from ipor_fusion.chains import ensure_supported_chain
from ipor_fusion.core.access import AccessManager
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.explorer import get_contract_name
from ipor_fusion.morpho_api import fetch_vault
from ipor_fusion.readers.morpho import morpho_blue_address
from ipor_fusion.readers.unpriceable import fetch_unpriceable_priced_tokens
from ipor_fusion.types import ChainId, MarketId, Period, RoleId
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
