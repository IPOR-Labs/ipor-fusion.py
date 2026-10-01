"""Offline guards for the examples/ vault scripts.

These run without a provider: they import each example module (proving import
has no chain side effects) and exercise its pure calldata builders and static
config. This is what keeps the examples from silently going stale against the
SDK even when no RPC is available. The on-chain simulation path is covered
separately by the RPC-gated test_simulate_* suite.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

import pytest
from eth_abi.abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core import FusionFactory
from ipor_fusion.core.simulation import SimulatedCallResult, SimulationResult
from ipor_fusion.fuses import ExternalStateSubstrates
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import MAX_UINT256, Amount

Loader = Callable[[str], ModuleType]


def _simulated_call(
    label: str,
    *,
    success: bool,
    return_data: bytes = b"",
    logs: list[dict] | None = None,
) -> SimulatedCallResult:
    """One entry in a fake `SimulationResult`, for exercising the fail-loud helpers."""
    return SimulatedCallResult(
        label=label,
        success=success,
        return_data=HexBytes(return_data),
        gas_used=0,
        error=None if success else "execution reverted",
        logs=logs if logs is not None else [],
        decoded=None,
    )


def _simulation_result(calls: list[SimulatedCallResult]) -> SimulationResult:
    """A finished `SimulationResult` over `calls`, with `failed_calls` derived."""
    failed = [c for c in calls if not c.success]
    return SimulationResult(
        success=not failed,
        all_success=not failed,
        revert_reason="execution reverted" if failed else None,
        gas_used=0,
        execute_logs=[],
        observations={},
        calls=calls,
        failed_calls=failed,
    )


class _ExampleOfflineChecks:
    """Offline guards shared by every from-scratch vault example.

    A subclass sets ``module_filename`` (the example to load),
    ``underlying_attr`` (the module constant naming the vault's accounting-asset
    address) and ``canonical_addresses`` (module constant -> its
    mainnet-base-fusion registry value); the autouse fixture here loads the
    module into ``self.mod``, and example-specific encoding/config tests stay in
    the subclass. Not named ``Test*`` so pytest does not collect it on its own --
    it has no module to load until a subclass supplies one.
    """

    mod: ModuleType
    module_filename: str
    underlying_attr: str
    canonical_addresses: dict[str, str]

    @pytest.fixture(autouse=True)
    def _mod(self, load_example: Loader) -> None:
        self.mod = load_example(self.module_filename)

    def test_unsigned_clone_calldata_roundtrips(self) -> None:
        calldata = self.mod.unsigned_clone_calldata()
        assert isinstance(calldata, bytes)
        # Selector matches the deployed 6-arg clone(...).
        assert calldata[:4] == FusionFactory.CLONE_SELECTOR
        # All six args decode back to exactly what the example configured.
        args = FusionFactory.decode_clone_calldata(calldata)
        expected = self.mod.clone_args()
        assert args.asset_name == expected["asset_name"]
        assert args.asset_symbol == expected["asset_symbol"]
        assert args.underlying_token == getattr(self.mod, self.underlying_attr)
        assert args.redemption_delay_seconds == expected["redemption_delay_seconds"]
        assert args.owner == self.mod.OWNER
        assert args.dao_fee_package_index == expected["dao_fee_package_index"]

    def test_connected_web3_exits_without_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BASE_PROVIDER_URL", raising=False)
        with pytest.raises(SystemExit):
            self.mod._connected_web3()

    def test_assert_all_success_raises_on_failure(self) -> None:
        result = _simulation_result([_simulated_call("clone", success=False)])
        with pytest.raises(AssertionError, match="simulation calls failed"):
            self.mod._assert_all_success(result)

    def test_check_raises_only_on_a_false_condition(self) -> None:
        # Rule 7 puts every final-state assertion in an example through _check.
        # If it regressed to a no-op, every outcome check would go silent while
        # the tests still passed -- so pin both branches here, for every example
        # that inherits this class.
        self.mod._check(True, "must not raise")
        with pytest.raises(AssertionError, match="boom"):
            self.mod._check(False, "boom")

    def test_addresses_are_canonical(self) -> None:
        # Pin every address to its mainnet-base-fusion registry value, so a
        # silent drift away from the canonical deployments fails here, not only
        # at simulation time (where add_fuses accepts any address).
        for name, address in self.canonical_addresses.items():
            assert getattr(self.mod, name) == Web3.to_checksum_address(address)


class TestSimpleAaveV3SupplyBase(_ExampleOfflineChecks):
    module_filename = "simple_aave_v3_supply_base.py"
    underlying_attr = "BASE_USDC"
    canonical_addresses = {
        "BASE_AAVE_V3_SUPPLY_FUSE": "0x26fD6EF391E98C78CfCA27e00c3d15be4D941625",
        "BASE_AAVE_V3_BALANCE_FUSE": "0x952573Ec1B6895a88a95CA523097083d4da4D8e5",
        "BASE_FUSION_FACTORY": "0x1455717668fA96534f675856347A973fA907e922",
        "BASE_USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    }

    def test_build_supply_action(self) -> None:
        action = self.mod.build_supply_action(Amount(999_000_000))
        assert isinstance(action, FuseAction)
        assert action.fuse == self.mod.BASE_AAVE_V3_SUPPLY_FUSE
        # Decode enter((address,uint256,uint256)) and check every field, so a
        # swapped asset, amount, or e_mode can't slip past this guard.
        asset, amount, e_mode = decode(
            ["address", "uint256", "uint256"], action.data[4:]
        )
        assert Web3.to_checksum_address(asset) == self.mod.BASE_USDC
        assert amount == 999_000_000
        assert e_mode == 0

    def test_usdc_substrate_is_padded_address(self) -> None:
        substrate = self.mod._address_substrate(self.mod.BASE_USDC)
        assert len(substrate) == 32
        # 12 leading zero bytes, then the 20-byte address in the low bytes.
        assert substrate[:12] == bytes(12)
        assert substrate.endswith(bytes.fromhex(self.mod.BASE_USDC[2:]))


class TestAdvancedEulerV2CreditMarketBase(_ExampleOfflineChecks):
    module_filename = "advanced_euler_v2_credit_market_base.py"
    underlying_attr = "BASE_WETH"
    canonical_addresses = {
        "EULER_SUPPLY_FUSE": "0x598326fcEDE2C1B8E9023a20C18FFf6Dea5306A4",
        "EULER_COLLATERAL_FUSE": "0x12c479f8aB53D4884fc76F803dD24eb8B6D17a94",
        "EULER_CONTROLLER_FUSE": "0x108c8cFB9e00681FfA1fa3b654937E8b3BCd2E64",
        "EULER_BORROW_FUSE": "0x906496F0D4C733275F892b1a6fC92eD56639B379",
        "EULER_BALANCE_FUSE": "0xF8A6AA09bB55f2319113b0DA88883F392e66A5fa",
        "BASE_FUSION_FACTORY": "0x1455717668fA96534f675856347A973fA907e922",
    }

    def test_market_substrates_pack_minimal_capabilities(self) -> None:
        # The typed substrate layout is
        # eulerVault<<96 | isCollateral<<88 | canBorrow<<80 | subAccount<<72.
        # Decode both and assert the *minimal* flags: cbETH collateral-only,
        # WETH borrow-only -- a widened flag (e.g. cbETH borrowable) fails here.
        substrates = self.mod.market_substrates()
        assert len(substrates) == 2
        decoded = {}
        for s in substrates:
            assert len(s) == 32
            val = int.from_bytes(s, "big")
            vault = Web3.to_checksum_address(f"0x{(val >> 96) & ((1 << 160) - 1):040x}")
            decoded[vault] = {
                "is_collateral": (val >> 88) & 0xFF,
                "can_borrow": (val >> 80) & 0xFF,
                "sub_account": (val >> 72) & 0xFF,
            }
        assert decoded[self.mod.EVAULT_CBETH] == {
            "is_collateral": 1,
            "can_borrow": 0,
            "sub_account": self.mod.SUB_ACCOUNT,
        }
        assert decoded[self.mod.EVAULT_WETH] == {
            "is_collateral": 0,
            "can_borrow": 1,
            "sub_account": self.mod.SUB_ACCOUNT,
        }

    def test_credit_lifecycle_actions_encode_correctly(self) -> None:
        sub = bytes([self.mod.SUB_ACCOUNT])
        # (builder, expected fuse address, solidity signature, expected decoded tail)
        # The tail excludes the leading eulerVault, which every action targets.
        cases = [
            (
                self.mod.supply_collateral_action(Amount(10)),
                self.mod.EULER_SUPPLY_FUSE,
                "enter((address,uint256,bytes1))",
                self.mod.EVAULT_CBETH,
                (10, sub),
            ),
            (
                self.mod.enable_collateral_action(),
                self.mod.EULER_COLLATERAL_FUSE,
                "enter((address,bytes1))",
                self.mod.EVAULT_CBETH,
                (sub,),
            ),
            (
                self.mod.enable_controller_action(),
                self.mod.EULER_CONTROLLER_FUSE,
                "enter((address,bytes1))",
                self.mod.EVAULT_WETH,
                (sub,),
            ),
            (
                self.mod.borrow_action(Amount(1)),
                self.mod.EULER_BORROW_FUSE,
                "enter((address,uint256,bytes1))",
                self.mod.EVAULT_WETH,
                (1, sub),
            ),
            (
                self.mod.repay_action(MAX_UINT256),
                self.mod.EULER_BORROW_FUSE,
                "exit((address,uint256,bytes1))",
                self.mod.EVAULT_WETH,
                (MAX_UINT256, sub),
            ),
            (
                self.mod.disable_controller_action(),
                self.mod.EULER_CONTROLLER_FUSE,
                "exit((address,bytes1))",
                self.mod.EVAULT_WETH,
                (sub,),
            ),
            (
                self.mod.disable_collateral_action(),
                self.mod.EULER_COLLATERAL_FUSE,
                "exit((address,bytes1))",
                self.mod.EVAULT_CBETH,
                (sub,),
            ),
            (
                self.mod.withdraw_collateral_action(MAX_UINT256),
                self.mod.EULER_SUPPLY_FUSE,
                "exit((address,uint256,bytes1))",
                self.mod.EVAULT_CBETH,
                (MAX_UINT256, sub),
            ),
        ]
        for action, fuse, signature, vault, tail in cases:
            assert isinstance(action, FuseAction)
            assert action.fuse == fuse
            assert action.data[:4] == function_signature_to_4byte_selector(signature)
            tuple_type = signature[signature.index("(") + 1 : -1]
            (decoded_tuple,) = decode([tuple_type], action.data[4:])
            assert Web3.to_checksum_address(decoded_tuple[0]) == vault
            assert decoded_tuple[1:] == tail

    def test_euler_account_is_vault_xor_sub_account(self) -> None:
        vault = Web3.to_checksum_address("0x000000000000000000000000000000000000ff00")
        # XOR the low byte with SUB_ACCOUNT (0x01) -> ...ff01.
        assert self.mod._euler_account(vault) == Web3.to_checksum_address(
            "0x000000000000000000000000000000000000ff01"
        )

    def test_evault_debt_of_encodes_debtof_call(self) -> None:
        # _evault_debt_of builds calldata with no chain access (ctx may be None),
        # so guard its encoding here rather than only inside the RPC-gated sim: a
        # swapped account/vault or a signature typo would otherwise be invisible.
        account = self.mod.OWNER
        call = self.mod._evault_debt_of(None, self.mod.EVAULT_WETH, account)
        assert call.to == self.mod.EVAULT_WETH
        assert call.data[:4] == function_signature_to_4byte_selector("debtOf(address)")
        # debtOf returns underlying units; a wrong/dropped return type would
        # misdecode the debt read, so pin it too.
        assert call.output_types == ["uint256"]
        (decoded_account,) = decode(["address"], call.data[4:])
        assert Web3.to_checksum_address(decoded_account) == account


def _substrate_address(substrate: bytes) -> str:
    """Checksummed address out of a market-50 address substrate's low 20 bytes."""
    return Web3.to_checksum_address(substrate[12:])


# The operation fuse's enter/exit payload, decoded once for every test that
# inspects one. Deliberately hand-decoded rather than routed back through
# `ExternalStateSubstrates` / `decode_substrate`: encoding and decoding with the
# same SDK code would round-trip a symmetric bug cleanly, and these tests exist
# to catch the *example* granting the wrong thing.
_OPERATION_TUPLE = "(address,uint256,address,(address,bytes)[])"

# An amount that matches no module constant, so a builder ignoring its argument
# and reaching for MARGIN_AMOUNT cannot pass these encoding checks.
_TEST_AMOUNT = Amount(777_000_000)


def _decode_operation(action: FuseAction) -> tuple:
    """`(asset, amount, balance_account, actions)` out of an enter/exit action."""
    return decode([_OPERATION_TUPLE], action.data[4:])[0]


class TestExternalStateMarginLegBase(_ExampleOfflineChecks):
    module_filename = "external_state_margin_leg_base.py"
    underlying_attr = "BASE_USDC"
    canonical_addresses = {
        "BASE_EXTERNAL_STATE_OPERATION_FUSE": (
            "0x01f05736B79C7abcFbeeCe76B1C27e54f7A07D03"
        ),
        "BASE_EXTERNAL_STATE_BALANCE_FUSE": (
            "0xCDEb32ACF766587b5Eac9dbfB875145ec05E3517"
        ),
        "BASE_FUSION_FACTORY": "0x1455717668fA96534f675856347A973fA907e922",
        "BASE_USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    }

    def test_market_substrates_pack_the_minimal_grant(self) -> None:
        # Market-50 substrates are bytes32 with a one-byte type tag in the high
        # byte. Decode every one and assert the exact set: a widened grant (a
        # third custodian, a second asset, an extra TARGET) fails here rather
        # than only at simulation time, where the vault accepts any bytes32.
        substrates = self.mod.market_substrates()
        assert len(substrates) == 9
        assert all(len(s) == 32 for s in substrates)
        by_type: dict[int, list[bytes]] = {}
        for substrate in substrates:
            by_type.setdefault(substrate[0], []).append(substrate)

        # ASSET(1) / BALANCE_ACCOUNT(4): tag, 11 zero bytes, then the address.
        (asset,) = by_type[1]
        assert asset[1:12] == bytes(11)
        assert _substrate_address(asset) == self.mod.BASE_USDC
        (balance_account,) = by_type[4]
        assert balance_account[1:12] == bytes(11)
        assert _substrate_address(balance_account) == self.mod.VENUE_BALANCE_ACCOUNT

        # TARGET(2): tag, 7 zero bytes, the 4-byte selector, then the address --
        # the selector sits *above* the address in this market's layout.
        (target,) = by_type[2]
        assert target[1:8] == bytes(7)
        assert target[8:12] == function_signature_to_4byte_selector(
            "transfer(address,uint256)"
        )
        assert _substrate_address(target) == self.mod.BASE_USDC

        # CUSTODIAN(3): exactly two, and they must differ -- propose and confirm
        # come from different addresses, so one grant makes the market unusable.
        # Count before comparing as sets: if the two constants were ever made
        # equal, both sides would collapse to one element and match.
        assert len(by_type[3]) == 2
        assert self.mod.CUSTODIAN_A != self.mod.CUSTODIAN_B
        custodians = {_substrate_address(s) for s in by_type[3]}
        assert custodians == {self.mod.CUSTODIAN_A, self.mod.CUSTODIAN_B}

        # The four guards: tag, then a 31-byte big-endian value.
        guards = {
            5: self.mod.STALENESS_MAX,
            6: self.mod.BIG_CHANGE_BPS,
            7: self.mod.DUST_THRESHOLD_PERCENT,
            8: self.mod.MIN_UPDATE_INTERVAL,
        }
        for type_tag, expected in guards.items():
            (guard,) = by_type[type_tag]
            assert int.from_bytes(guard[1:], "big") == expected

    def test_mandatory_guard_encoders_reject_zero(self) -> None:
        # The executor reads a zero STALENESS_MAX or BIG_CHANGE_BPS as "unset"
        # and refuses to build its substrate cache, which happens inside the
        # first enter -- so a zero would fail far from its cause. Assert the
        # encoder is what stops it (checking the example's own constants would
        # be tautological: market_substrates() could not have built them).
        for encoder in (
            ExternalStateSubstrates.staleness_max,
            ExternalStateSubstrates.big_change_bps,
        ):
            with pytest.raises(ValueError, match="must not be zero"):
                encoder(0)

    def test_actor_addresses_are_pairwise_distinct(self) -> None:
        # Collapsing ALPHA into OWNER -- the anti-pattern this example spends
        # paragraphs warning against -- would otherwise pass every other check,
        # offline and simulated alike. Same for the two custodians, whose
        # separation is the market's whole security argument.
        actors = {
            name: getattr(self.mod, name)
            for name in (
                "OWNER",
                "ALPHA",
                "DEPOSITOR",
                "CUSTODIAN_A",
                "CUSTODIAN_B",
                "VENUE_DEPOSIT_ADDRESS",
                "VENUE_BALANCE_ACCOUNT",
                "BASE_USDC_WHALE",
            )
        }
        assert len(set(actors.values())) == len(actors), f"actors collide: {actors}"

    def test_enter_action_forwards_the_full_amount_to_the_venue(self) -> None:
        action = self.mod.build_enter_action(_TEST_AMOUNT)
        assert isinstance(action, FuseAction)
        assert action.fuse == self.mod.BASE_EXTERNAL_STATE_OPERATION_FUSE
        signature = f"enter({_OPERATION_TUPLE})"
        assert action.data[:4] == function_signature_to_4byte_selector(signature)
        (asset, amount, balance_account, actions) = _decode_operation(action)
        assert Web3.to_checksum_address(asset) == self.mod.BASE_USDC
        assert amount == _TEST_AMOUNT
        assert (
            Web3.to_checksum_address(balance_account) == self.mod.VENUE_BALANCE_ACCOUNT
        )
        # Exactly one action, and it must move the *whole* amount off the
        # executor: anything left behind fails the custodians' dust check.
        assert len(actions) == 1
        (target, data) = actions[0]
        assert Web3.to_checksum_address(target) == self.mod.BASE_USDC
        assert data[:4] == function_signature_to_4byte_selector(
            "transfer(address,uint256)"
        )
        (to, forwarded) = decode(["address", "uint256"], data[4:])
        assert Web3.to_checksum_address(to) == self.mod.VENUE_DEPOSIT_ADDRESS
        assert forwarded == amount

    def test_exit_action_pulls_back_with_no_actions(self) -> None:
        # The exit carries no actions: the venue -- not the vault -- returns the
        # funds, so by the time the exit runs they are already on the executor.
        action = self.mod.build_exit_action(_TEST_AMOUNT)
        assert action.fuse == self.mod.BASE_EXTERNAL_STATE_OPERATION_FUSE
        signature = f"exit({_OPERATION_TUPLE})"
        assert action.data[:4] == function_signature_to_4byte_selector(signature)
        (asset, amount, balance_account, actions) = _decode_operation(action)
        assert Web3.to_checksum_address(asset) == self.mod.BASE_USDC
        assert amount == _TEST_AMOUNT
        assert (
            Web3.to_checksum_address(balance_account) == self.mod.VENUE_BALANCE_ACCOUNT
        )
        assert actions == ()

    def test_balance_account_is_not_the_deposit_address(self) -> None:
        # They are different things: the deposit address receives the margin and
        # is never a substrate; the balance account is a mapping key that never
        # receives anything. Collapsing them still runs, so pin them apart here.
        assert self.mod.VENUE_BALANCE_ACCOUNT != self.mod.VENUE_DEPOSIT_ADDRESS
        # Only tags 1-4 carry an address; 5-8 are guard scalars whose low bytes
        # would decode as nonsense addresses -- and a guard set to 0xBEEF (48879,
        # a plausible interval) would then collide with VENUE_DEPOSIT_ADDRESS and
        # fail this test for a reason that has nothing to do with substrates.
        granted = {
            _substrate_address(s)
            for s in self.mod.market_substrates()
            if s[0] in (1, 2, 3, 4)
        }
        assert self.mod.VENUE_DEPOSIT_ADDRESS not in granted

    # The decoder carries a hand-written event signature and field order, and the
    # RPC-gated sim is the only other thing that runs it -- so a typo would go
    # unnoticed in a checkout with no provider. The tests below feed synthetic
    # logs whose topic is derived independently, from the literal signature.
    EXECUTOR = Web3.to_checksum_address("0x000000000000000000000000000000000000c0de")
    VAULT = Web3.to_checksum_address("0x000000000000000000000000000000000000fa17")
    IMPOSTOR = Web3.to_checksum_address("0x000000000000000000000000000000000000dead")
    DEPLOYED_TOPIC = Web3.keccak(
        text="ExternalStateExecutorDeployed(address,uint256)"
    ).to_0x_hex()

    @classmethod
    def _deployed_log(cls, emitter: str, executor: str) -> dict:
        """One ExternalStateExecutorDeployed row, shaped as eth_simulateV1 returns it."""
        return {
            "address": emitter,
            "topics": [cls.DEPLOYED_TOPIC],
            "data": "0x" + encode(["address", "uint256"], [executor, 50]).hex(),
        }

    def test_executor_from_logs_reads_the_deploy_event(self) -> None:
        # The emitter arrives lowercase from eth_simulateV1, so the match has to
        # checksum before comparing or it never fires.
        entry = self._deployed_log(self.VAULT.lower(), self.EXECUTOR)
        # A log from an unrelated event must be skipped, not misdecoded.
        noise = {"address": self.VAULT, "topics": ["0x" + "11" * 32], "data": "0x"}
        assert self.mod._executor_from_logs([noise, entry], self.VAULT) == self.EXECUTOR
        with pytest.raises(AssertionError, match="ExternalStateExecutorDeployed"):
            self.mod._executor_from_logs([noise], self.VAULT)

    def test_executor_from_logs_ignores_a_foreign_emitter(self) -> None:
        # execute_logs carries the logs of every execute() call, so a
        # same-signature event from another contract in the enter's call tree
        # must not be mistaken for the vault's -- it would hand back an address
        # that contract chose.
        forged = self._deployed_log(self.IMPOSTOR, self.IMPOSTOR)
        with pytest.raises(AssertionError, match="ExternalStateExecutorDeployed"):
            self.mod._executor_from_logs([forged], self.VAULT)
        # Impostor FIRST, so this pins skip-and-keep-scanning rather than
        # stop-at-the-first-foreign-log: with `break` in place of `continue` the
        # scan would abort here and never reach the vault's own log.
        entry = self._deployed_log(self.VAULT, self.EXECUTOR)
        assert (
            self.mod._executor_from_logs([forged, entry], self.VAULT) == self.EXECUTOR
        )

    def test_executor_from_logs_skips_an_unattributable_log(self) -> None:
        # A row with no address cannot be attributed to the vault, which is not
        # the same as belonging to someone else -- but it is equally not proof it
        # is ours, so it is skipped rather than raising KeyError mid-scan.
        headless = {"topics": [self.DEPLOYED_TOPIC], "data": "0x"}
        topicless = {"address": self.VAULT, "data": "0x"}
        entry = self._deployed_log(self.VAULT, self.EXECUTOR)
        assert (
            self.mod._executor_from_logs([headless, topicless, entry], self.VAULT)
            == self.EXECUTOR
        )
        with pytest.raises(AssertionError, match="ExternalStateExecutorDeployed"):
            self.mod._executor_from_logs([headless, topicless], self.VAULT)

    def test_logs_of_picks_the_labeled_call(self) -> None:
        # Its raise branch never runs under the RPC-gated sim, where every label
        # it is asked for exists -- so a typo'd label would surface there as an
        # empty scan rather than as this assertion.
        wanted = [{"address": self.VAULT, "topics": [self.DEPLOYED_TOPIC]}]
        result = _simulation_result(
            [
                _simulated_call("clone", success=True),
                _simulated_call("propose", success=True, logs=wanted),
            ]
        )
        assert self.mod._logs_of(result, "propose") == wanted
        with pytest.raises(AssertionError, match="no simulated call labeled"):
            self.mod._logs_of(result, "confirm")

    def test_proposal_hash_mismatch_selector_is_the_real_one(self) -> None:
        # The example hand-writes this error signature and only the RPC-gated run
        # consumes it, so a typo would surface as "reverted for the wrong reason"
        # against a live provider and nowhere else. Derive it here independently.
        assert self.mod.PROPOSAL_HASH_MISMATCH_SELECTOR == (
            function_signature_to_4byte_selector(
                "ExternalStateExecutorProposalHashMismatch(bytes32,bytes32)"
            )
        )
        # bytes32, so a shorter literal would encode as a different proposal.
        assert len(self.mod.WRONG_PROPOSAL_HASH) == 32

    def test_assert_only_expected_failure_accepts_just_the_demo(self) -> None:
        # The payload is the real mismatch selector rather than arbitrary bytes,
        # so this doubles as a check that the helper returns it unchanged for
        # run_simulation's "reverted for the wrong reason" comparison.
        revert_data = self.mod.PROPOSAL_HASH_MISMATCH_SELECTOR
        result = _simulation_result(
            [
                _simulated_call("clone", success=True),
                _simulated_call("demo_confirm", success=False, return_data=revert_data),
            ]
        )
        payload = self.mod._assert_only_expected_failure(result, "demo_confirm")
        assert payload == revert_data

    def test_assert_only_expected_failure_rejects_a_successful_demo(self) -> None:
        # Zero failures is the most plausible way the demo breaks: the mismatched
        # confirm goes through, meaning the hash guard did not fire. Without this
        # case, widening the count check to `> 1` passes every other test here
        # and turns this path into an IndexError instead of a clear assertion.
        result = _simulation_result(
            [
                _simulated_call("clone", success=True),
                _simulated_call("demo_confirm", success=True),
            ]
        )
        with pytest.raises(AssertionError, match="expected exactly one failed call"):
            self.mod._assert_only_expected_failure(result, "demo_confirm")

    def test_assert_only_expected_failure_rejects_a_broken_happy_path(self) -> None:
        # A second failure means the lifecycle broke, and the run must not report
        # success just because the demo reverted as designed.
        result = _simulation_result(
            [
                _simulated_call("clone", success=False),
                _simulated_call("demo_confirm", success=False),
            ]
        )
        with pytest.raises(AssertionError, match="expected exactly one failed call"):
            self.mod._assert_only_expected_failure(result, "demo_confirm")

    def test_assert_only_expected_failure_rejects_the_wrong_single_failure(
        self,
    ) -> None:
        # One failure, but not the demo's: the lifecycle broke and the demo never
        # got to run. Without this branch a lone unexpected revert could pass.
        result = _simulation_result([_simulated_call("clone", success=False)])
        with pytest.raises(AssertionError, match="expected exactly one failed call"):
            self.mod._assert_only_expected_failure(result, "demo_confirm")
