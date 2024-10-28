import logging
import os

import pytest

from constants import ARBITRUM, ALPHA_WALLET
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.Roles import Roles
from ipor_fusion.fuse.RamsesClaimFuse import RamsesClaimFuse
from ipor_fusion.fuse.RamsesV2CollectFuse import RamsesV2CollectFuse
from ipor_fusion.fuse.RamsesV2ModifyPositionFuse import RamsesV2ModifyPositionFuse
from ipor_fusion.fuse.RamsesV2NewPositionFuse import RamsesV2NewPositionFuse
from ipor_fusion.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

ARBITRUM_PROVIDER_URL = "ARBITRUM_PROVIDER_URL"
FORK_URL = os.getenv(ARBITRUM_PROVIDER_URL)
if not FORK_URL:
    raise ValueError("Environment variable ARBITRUM_PROVIDER_URL must be set")

ramses_v2_new_position_fuse = RamsesV2NewPositionFuse(
    ARBITRUM.PILOT.V5.RAMSES_V2_NEW_POSITION_FUSE
)
ramses_v2_modify_position_fuse = RamsesV2ModifyPositionFuse(
    ARBITRUM.PILOT.V5.RAMSES_V2_MODIFY_POSITION_FUSE
)
ramses_v2_collect_fuse = RamsesV2CollectFuse(ARBITRUM.PILOT.V5.RAMSES_V2_COLLECT_FUSE)
uniswap_v3_swap_fuse = UniswapV3SwapFuse(ARBITRUM.PILOT.V5.UNISWAP_V3_SWAP_FUSE)
ramses_claim_fuse = RamsesClaimFuse(ARBITRUM.PILOT.V5.RAMSES_V2_CLAIM_FUSE)


@pytest.fixture(scope="module", name="plasma_vault")
def plasma_vault_fixture(test_transaction_executor) -> PlasmaVault:
    return PlasmaVault(
        transaction_executor=test_transaction_executor,
        plasma_vault_address=ARBITRUM.PILOT.V5.PLASMA_VAULT,
    )


@pytest.fixture(name="setup", autouse=True)
def setup_fixture(anvil):
    anvil.reset_fork(266412948)  # 261946538 - 1002 USDC on pilot V5
    anvil.grant_role(ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE)
    yield


def test_should_deposit_to_plasma_vault_v5(
    test_transaction_executor, plasma_vault, usdc
):
    whitelisted = "0x4E3C666F0c898a9aE1F8aBB188c6A2CC151E17fC"

    balance_before = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    test_transaction_executor.set_account(whitelisted)
    plasma_vault.deposit(int(2e6), whitelisted)

    balance_after = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    assert balance_after - balance_before == int(2e6)
