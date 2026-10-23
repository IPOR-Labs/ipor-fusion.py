"""Calldata and decoding of the governance, fee, rewards, withdraw and context
manager wrappers: each method encodes its Solidity signature exactly."""

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion import (
    ContextManager,
    FeeManager,
    PlasmaVault,
    RecipientFee,
    RewardsManager,
    WithdrawManager,
)
from ipor_fusion.types import (
    Amount,
    Fee,
    FuseAddress,
    MarketId,
    Period,
    PlasmaVaultAddress,
    RoleId,
    Shares,
)

ADDR = Web3.to_checksum_address("0x" + "11" * 20)
OTHER = Web3.to_checksum_address("0x" + "22" * 20)
VAULT = PlasmaVault(PlasmaVaultAddress(ADDR))
FUSE = FuseAddress(OTHER)
SUBSTRATE = b"\x00" * 12 + bytes.fromhex("22" * 20)


def _calldata(signature: str, types: list[str], values: list[object]) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    return selector + (encode(types, values) if types else b"")


WRITES = [
    (
        VAULT.deposit_with_permit(
            Amount(5), OTHER, Timestamp(9), 27, b"\x01" * 32, b"\x02" * 32
        ),
        "depositWithPermit(uint256,address,uint256,uint8,bytes32,bytes32)",
        ["uint256", "address", "uint256", "uint8", "bytes32", "bytes32"],
        [5, OTHER, 9, 27, b"\x01" * 32, b"\x02" * 32],
    ),
    (VAULT.activate_markets_limits(), "activateMarketsLimits()", [], []),
    (VAULT.deactivate_markets_limits(), "deactivateMarketsLimits()", [], []),
    (
        VAULT.set_price_oracle_middleware(OTHER),
        "setPriceOracleMiddleware(address)",
        ["address"],
        [OTHER],
    ),
    (
        VAULT.configure_performance_fee(OTHER, Fee(1000)),
        "configurePerformanceFee(address,uint256)",
        ["address", "uint256"],
        [OTHER, 1000],
    ),
    (
        VAULT.configure_management_fee(OTHER, Fee(200)),
        "configureManagementFee(address,uint256)",
        ["address", "uint256"],
        [OTHER, 200],
    ),
    (
        VAULT.set_rewards_claim_manager_address(OTHER),
        "setRewardsClaimManagerAddress(address)",
        ["address"],
        [OTHER],
    ),
    (VAULT.enable_transfer_shares(), "enableTransferShares()", [], []),
    (
        VAULT.set_minimal_execution_delays_for_roles(
            [(RoleId(100), Period.DAY), (RoleId(200), Period.HOUR)]
        ),
        "setMinimalExecutionDelaysForRoles(uint64[],uint256[])",
        ["uint64[]", "uint256[]"],
        [[100, 200], [86400, 3600]],
    ),
    (
        WithdrawManager(ADDR).update_withdraw_fee(Fee(10**16)),
        "updateWithdrawFee(uint256)",
        ["uint256"],
        [10**16],
    ),
    (
        WithdrawManager(ADDR).update_request_fee(Fee(10**15)),
        "updateRequestFee(uint256)",
        ["uint256"],
        [10**15],
    ),
    (
        RewardsManager(ADDR).transfer_vested_tokens_to_vault(),
        "transferVestedTokensToVault()",
        [],
        [],
    ),
    (
        RewardsManager(ADDR).remove_reward_fuses([FUSE]),
        "removeRewardFuses(address[])",
        ["address[]"],
        [[OTHER]],
    ),
    (
        RewardsManager(ADDR).setup_vesting_time(Period.WEEK),
        "setupVestingTime(uint256)",
        ["uint256"],
        [604800],
    ),
    (FeeManager(ADDR).harvest_all_fees(), "harvestAllFees()", [], []),
    (FeeManager(ADDR).harvest_management_fee(), "harvestManagementFee()", [], []),
    (FeeManager(ADDR).harvest_performance_fee(), "harvestPerformanceFee()", [], []),
    (
        FeeManager(ADDR).update_management_fee([RecipientFee(OTHER, Fee(50))]),
        "updateManagementFee((address,uint256)[])",
        ["(address,uint256)[]"],
        [[(OTHER, 50)]],
    ),
    (
        FeeManager(ADDR).update_performance_fee([RecipientFee(OTHER, Fee(500))]),
        "updatePerformanceFee((address,uint256)[])",
        ["(address,uint256)[]"],
        [[(OTHER, 500)]],
    ),
    (
        FeeManager(ADDR).set_ipor_dao_fee_recipient_address(OTHER),
        "setIporDaoFeeRecipientAddress(address)",
        ["address"],
        [OTHER],
    ),
    (
        FeeManager(ADDR).set_deposit_fee(Fee(10**16)),
        "setDepositFee(uint256)",
        ["uint256"],
        [10**16],
    ),
    (
        FeeManager(ADDR).update_high_water_mark_performance_fee(),
        "updateHighWaterMarkPerformanceFee()",
        [],
        [],
    ),
    (
        FeeManager(ADDR).update_interval_high_water_mark_performance_fee(Period.DAY),
        "updateIntervalHighWaterMarkPerformanceFee(uint32)",
        ["uint32"],
        [86400],
    ),
    (
        ContextManager(ADDR).add_approved_targets([OTHER]),
        "addApprovedTargets(address[])",
        ["address[]"],
        [[OTHER]],
    ),
    (
        ContextManager(ADDR).remove_approved_targets([OTHER]),
        "removeApprovedTargets(address[])",
        ["address[]"],
        [[OTHER]],
    ),
    (
        ContextManager(ADDR).run_with_context([(OTHER, b"\xab\xcd"), (ADDR, b"")]),
        "runWithContext((address[],bytes[]))",
        ["(address[],bytes[])"],
        [([OTHER, ADDR], [b"\xab\xcd", b""])],
    ),
]


@pytest.mark.parametrize(
    ("call", "signature", "types", "values"),
    WRITES,
    ids=[signature for _, signature, _, _ in WRITES],
)
def test_write_encodes_its_signature(call, signature, types, values):
    assert call.to == ADDR
    assert call.calldata == _calldata(signature, types, values)


VIEWS = [
    (VAULT.max_redeem(OTHER), "maxRedeem(address)", ["address"], [OTHER], 7),
    (VAULT.max_deposit(OTHER), "maxDeposit(address)", ["address"], [OTHER], 7),
    (VAULT.max_mint(OTHER), "maxMint(address)", ["address"], [OTHER], 7),
    (VAULT.preview_deposit(Amount(3)), "previewDeposit(uint256)", ["uint256"], [3], 7),
    (VAULT.preview_mint(Shares(3)), "previewMint(uint256)", ["uint256"], [3], 7),
    (
        VAULT.preview_withdraw(Amount(3)),
        "previewWithdraw(uint256)",
        ["uint256"],
        [3],
        7,
    ),
    (VAULT.preview_redeem(Shares(3)), "previewRedeem(uint256)", ["uint256"], [3], 7),
    (
        VAULT.is_fuse_supported(FUSE),
        "isFuseSupported(address)",
        ["address"],
        [OTHER],
        True,
    ),
    (
        VAULT.is_balance_fuse_supported(MarketId(1), FUSE),
        "isBalanceFuseSupported(uint256,address)",
        ["uint256", "address"],
        [1, OTHER],
        True,
    ),
    (
        VAULT.is_market_substrate_granted(MarketId(1), SUBSTRATE),
        "isMarketSubstrateGranted(uint256,bytes32)",
        ["uint256", "bytes32"],
        [1, SUBSTRATE],
        True,
    ),
    (VAULT.is_markets_limits_activated(), "isMarketsLimitsActivated()", [], [], True),
    (
        VAULT.get_market_limit(MarketId(1)),
        "getMarketLimit(uint256)",
        ["uint256"],
        [1],
        5 * 10**17,
    ),
    (
        FeeManager(ADDR).calculate_deposit_fee(Shares(1000)),
        "calculateDepositFee(uint256)",
        ["uint256"],
        [1000],
        10,
    ),
    (
        ContextManager(ADDR).get_nonce(OTHER),
        "getNonce(address)",
        ["address"],
        [OTHER],
        4,
    ),
    (
        ContextManager(ADDR).is_target_approved(OTHER),
        "isTargetApproved(address)",
        ["address"],
        [OTHER],
        True,
    ),
]


@pytest.mark.parametrize(
    ("call", "signature", "types", "values", "decoded"),
    VIEWS,
    ids=[signature for _, signature, _, _, _ in VIEWS],
)
def test_view_encodes_and_decodes(call, signature, types, values, decoded):
    assert call.calldata == _calldata(signature, types, values)
    output_type = "bool" if isinstance(decoded, bool) else "uint256"
    assert call.decode(encode([output_type], [decoded])) == decoded


def test_approved_targets_decode_checksummed():
    call = ContextManager(ADDR).get_approved_targets()
    assert call.calldata == _calldata("getApprovedTargets()", [], [])
    assert call.decode(encode(["address[]"], [[OTHER.lower()]])) == [OTHER]


def test_substrate_must_be_32_bytes():
    with pytest.raises(ValueError, match="32 bytes"):
        VAULT.is_market_substrate_granted(MarketId(1), b"\x00" * 20)
