"""preflight_execute against a live Base vault at a pinned block: its alpha
passes the static checks, and a stranger is refused both statically and by the
simulated revert. Skipped without an archive ``BASE_PROVIDER_URL`` that serves
``eth_simulateV1``."""

from _live import archive_context
from constants import BASE_AAVE_V3_SUPPLY_FUSE
from web3 import Web3

from ipor_fusion import AaveV3SupplyFuse, preflight_execute
from ipor_fusion.types import Amount, parse_plasma_vault_address

VAULT = parse_plasma_vault_address("0xc4c00d8b323f37527eeda27c87412378be9f68ec")
ALPHA = Web3.to_checksum_address("0x48d3615d78B152819ea0367adF7b9944e399ac9a")
STRANGER = Web3.to_checksum_address("0x" + "12" * 20)
WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
BLOCK = 52973779


def _action():
    return AaveV3SupplyFuse(BASE_AAVE_V3_SUPPLY_FUSE).supply(
        asset=WETH, amount=Amount(1000)
    )


def test_the_alpha_passes_the_static_checks(web3_base):
    ctx = archive_context("BASE_PROVIDER_URL", BLOCK, VAULT)
    result = preflight_execute(
        ctx, VAULT, [_action()], sender=ALPHA, simulate=False, fuse_whitelist=False
    )

    assert result.block_number == BLOCK
    assert result.permission.immediate
    (action,) = result.actions
    assert action.registered
    assert action.market_id == 1
    assert result.findings == ()


def test_a_deprecated_fuse_is_flagged_with_its_replacement(web3_base):
    ctx = archive_context(web3_base, BLOCK)
    result = preflight_execute(ctx, VAULT, [_action()], sender=ALPHA, simulate=False)

    (finding,) = result.findings
    assert (finding.check, finding.severity) == ("fuse_whitelist", "warning")
    assert "is deprecated in the Fuse Whitelist (AaveV3SupplyFuse)" in finding.message
    assert "use 0x26fD6EF391E98C78CfCA27e00c3d15be4D941625" in finding.message


def test_a_stranger_is_refused_and_the_simulation_reverts(web3_base):
    ctx = archive_context("BASE_PROVIDER_URL", BLOCK, VAULT)
    result = preflight_execute(
        ctx, VAULT, [_action()], sender=STRANGER, fuse_whitelist=False
    )

    checks = [f.check for f in result.findings]
    assert checks == ["execute_permission", "simulation_failed"]
    assert result.simulation is not None
    assert not result.simulation.success
    assert result.simulation.total_assets_before == result.simulation.total_assets_after
