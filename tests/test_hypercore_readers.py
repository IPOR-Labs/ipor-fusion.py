"""HyperCore read side, offline: the precompile ``Call``s, the pending-state
decode through the vault's universal reader, the wad conversion and the NAV
identity against ``HyperCoreValuationLib`` on the HIP-3 test vault's live
state of 2026-10-02 (block 47452386)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _multicall import multicall_aware
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import (
    HyperCoreValuationError,
    PlasmaVault,
    UnsupportedContractVersionError,
)
from ipor_fusion.hypercore import (
    HyperCoreActionClass,
    HyperCoreEffectKind,
    HyperCoreNav,
    HyperCorePendingReader,
    HyperCorePendingState,
    HyperCoreReader,
    HyperCoreSpotBalance,
    HyperCoreTokenInfo,
    convert_to_wad_int,
    perp_px_decimals,
    read_hypercore_nav,
)
from ipor_fusion.readers.hypercore import (
    ACCOUNT_MARGIN_SUMMARY_PRECOMPILE,
    CORE_USER_EXISTS_PRECOMPILE,
    L1_BLOCK_NUMBER_PRECOMPILE,
    PENDING_STATE_TYPE,
    PERP_ASSET_INFO_PRECOMPILE,
    POSITION2_PRECOMPILE,
    SPOT_BALANCE_PRECOMPILE,
    TOKEN_INFO_PRECOMPILE,
    HyperCoreVaultState,
    read_hypercore_vault_state,
)
from ipor_fusion.types import MarketId

ZERO = Web3.to_checksum_address("0x" + "00" * 20)
VAULT = Web3.to_checksum_address("0x41c46c328036fa7865d5cb15d531d7c5120f05c8")
READER = Web3.to_checksum_address("0x01fcd96f5049946bd10396b86e0b3d3ae7fff539")
BALANCE_FUSE = Web3.to_checksum_address("0x66f605b579dbe23c552edcf480de71198f3c4cd1")
ORACLE = Web3.to_checksum_address("0x310ab1069ef88bc7752f3a64299cbdc80cc223f7")
USDC = Web3.to_checksum_address("0xb88339cb7199b77e23db6e890353e22632ba630f")
USDC_CORE_EVM = Web3.to_checksum_address("0x6b9e773128f453f5c2c60935ee2de2cbc5390a24")
MARKET = MarketId(54)

# The vault's market grants on 2026-10-02, as `getMarketSubstrates(54)` returned them.
SUBSTRATES = [
    bytes.fromhex(word)
    for word in (
        "010000000000000000000000b88339cb7199b77e23db6e890353e22632ba630f",
        "020001adb20000000000000000e4e1c000000000000000000000000000000000",
        "03000000000000000000000041c46c328036fa7865d5cb15d531d7c5120f05c8",
        "0300000000000000000000002000000000000000000000000000000000000000",
        "06000000000000000000000000000000000000000000000000000002540be400",
        "0501000000000000000000000000000000000000000000000000000000000002",
        "050200000000000000000000000000000000000000000000000000000000001e",
        "0503000000000000000000000000000000000000000000000000000002625a00",
        "0505000000000000000000000000000000000000000000000000000000000001",
        "0506000000000000000000000000000000000000000000000000000000000001",
        "0504000000000000000000000000000000000000000000000000000000000002",
        "040000000000000000000001cee5c4272e246a424aede992c987966736e0f63b",
    )
]
SPOT_TOTAL = 824_500  # Core wei, 8 decimals
USDC_PRICE = (10**8, 8)
TOKEN_INFO = ("USDC", [], 0, "0x" + "00" * 20, USDC_CORE_EVM, 8, 8, -2)
XYZ_ACCOUNT_VALUE = 123_456  # USD6, signed in the ABI


def _selector(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.default_block = "latest"
    return ctx


class TestPrecompileCalls:
    reader = HyperCoreReader()

    def test_reads_carry_raw_abi_input_without_a_selector(self):
        call = self.reader.spot_balance(VAULT, 0)
        assert call.to == SPOT_BALANCE_PRECOMPILE
        assert call.data == encode(["address", "uint64"], [VAULT, 0])
        assert self.reader.l1_block_number().data == b""
        assert self.reader.l1_block_number().to == L1_BLOCK_NUMBER_PRECOMPILE
        assert self.reader.core_user_exists(VAULT).to == CORE_USER_EXISTS_PRECOMPILE
        assert self.reader.position(VAULT, 10_002).to == POSITION2_PRECOMPILE
        assert self.reader.account_margin_summary(1, VAULT).data == encode(
            ["uint32", "address"], [1, VAULT]
        )

    def test_results_decode_to_the_library_structs(self):
        balance = self.reader.spot_balance(VAULT, 0).decode(
            encode(["uint64", "uint64", "uint64"], [SPOT_TOTAL, 7, 9])
        )
        assert balance == HyperCoreSpotBalance(SPOT_TOTAL, 7, 9)
        summary = self.reader.account_margin_summary(0, VAULT).decode(
            encode(["int64", "uint64", "uint64", "int64"], [-5, 1, 2, -3])
        )
        assert (summary.account_value, summary.raw_usd) == (-5, -3)
        token = self.reader.token_info(0).decode(
            encode(
                ["(string,uint64[],uint64,address,address,uint8,uint8,int8)"],
                [TOKEN_INFO],
            )
        )
        assert token == HyperCoreTokenInfo(
            "USDC",
            (),
            0,
            Web3.to_checksum_address("0x" + "00" * 20),
            USDC_CORE_EVM,
            8,
            8,
            -2,
        )
        info = self.reader.perp_asset_info(10_000).decode(
            encode(
                ["(string,uint32,uint8,uint8,bool)"], [("xyz:XYZ100", 30, 4, 30, False)]
            )
        )
        assert (info.coin, info.max_leverage, info.only_isolated) == (
            "xyz:XYZ100",
            30,
            False,
        )
        assert info.margin_table_id == 30
        position = self.reader.position(VAULT, 10_002).decode(
            encode(
                ["int64", "uint64", "int64", "uint32", "bool"], [-4, 100, -1, 5, True]
            )
        )
        assert (position.szi, position.leverage, position.is_isolated) == (-4, 5, True)
        assert self.reader.perp_asset_info(10_000).to == PERP_ASSET_INFO_PRECOMPILE
        assert self.reader.token_info(0).to == TOKEN_INFO_PRECOMPILE
        assert (
            self.reader.core_user_exists(VAULT).decode(encode(["bool"], [True])) is True
        )

    def test_arguments_are_width_checked(self):
        with pytest.raises(ValueError, match="uint64"):
            self.reader.spot_balance(VAULT, 1 << 64)
        with pytest.raises(ValueError, match="uint32"):
            self.reader.perp_asset_info(1 << 32)


USDC = Web3.to_checksum_address("0xb88339cb7199b77e23db6e890353e22632ba630f")


def _pending_action(**over) -> tuple:
    """``HyperCorePendingLib.PendingAction`` of the released build, a Core ->
    EVM transfer waiting for its EVM balance unless overridden."""
    fields = {
        "pending_until": 1_790_609_642,
        "enqueued_l1_block": 1_164_341_828,
        "enqueued_evm_block": 47_125_670,
        "action_class": 1,  # Transfer
        "deprecated": (0, 0, 0),
        "action_id": 13,  # sendAsset
        "payload_hash": b"\x30" * 32,
        "deprecated_refreshing": False,
        "cached_value_wad": 10**16,
        "action_nonce": 32,
        "effect_kind": 2,  # EVM balance
        "effect_token_index": 0,
        "effect_evm_asset": USDC,
        "timeout_at": 1_790_610_242,
        "expected_balance": 15_000_000,
        "last_order_l1_block": 1_164_000_000,
    }
    fields.update(over)
    return (
        fields["pending_until"],
        fields["enqueued_l1_block"],
        fields["enqueued_evm_block"],
        fields["action_class"],
        *fields["deprecated"],
        fields["action_id"],
        fields["payload_hash"],
        fields["deprecated_refreshing"],
        fields["cached_value_wad"],
        fields["action_nonce"],
        fields["effect_kind"],
        fields["effect_token_index"],
        fields["effect_evm_asset"],
        fields["timeout_at"],
        fields["expected_balance"],
        fields["last_order_l1_block"],
    )


def _pending_state_raw(*, pending: bool, settled: bool, **over) -> bytes:
    inner = encode(
        [PENDING_STATE_TYPE],
        [
            (
                _pending_action(**over),
                pending,
                settled,
                1_168_823_034,
                1_790_931_001,
                True,
            )
        ],
    )
    return encode(["(bytes)"], [(inner,)])


class TestUniversalRead:
    def test_pending_state_reads_through_the_vault(self):
        ctx = _ctx()
        ctx.call.return_value = _pending_state_raw(pending=True, settled=False)
        reader = HyperCorePendingReader(VAULT, READER)

        state = reader.pending_state().call(ctx)

        to, data = ctx.call.call_args.args[:2]
        assert to == VAULT
        assert data == _selector("read(address,bytes)") + encode(
            ["address", "bytes"], [READER, _selector("pendingState()")]
        )
        assert isinstance(state, HyperCorePendingState)
        assert state.action_class is HyperCoreActionClass.TRANSFER
        assert (state.action_id, state.action_nonce, state.cached_value_wad) == (
            13,
            32,
            10**16,
        )
        assert (state.pending, state.settled) == (True, False)
        assert state.effect_kind is HyperCoreEffectKind.EVM_BALANCE
        assert (state.effect_evm_asset, state.expected_balance) == (USDC, 15_000_000)
        assert (state.timeout_at, state.last_order_l1_block) == (
            1_790_610_242,
            1_164_000_000,
        )
        assert (state.current_l1_block, state.l1_block_available) == (
            1_168_823_034,
            True,
        )
        assert state.payload_hash == b"\x30" * 32

    def test_the_released_payload_is_twenty_three_words(self):
        """The pilot reader returned 18 words; decoding the released 23 with
        the pilot layout passed silently and read the L1 block as 0."""
        inner = encode(
            [PENDING_STATE_TYPE],
            [(_pending_action(), True, False, 1, 2, True)],
        )
        assert len(inner) == 23 * 32

    def test_the_earlier_eighteen_word_payload_is_refused_by_name(self):
        ctx = _ctx()
        ctx.call.return_value = encode(["(bytes)"], [(b"\x00" * 18 * 32,)])
        with pytest.raises(UnsupportedContractVersionError, match="576 bytes"):
            HyperCorePendingReader(VAULT, READER).pending_state().call(ctx)

    def test_a_pending_margin_transfer_without_an_effect_stays_pending(self):
        ctx = _ctx()
        ctx.call.return_value = _pending_state_raw(
            pending=True, settled=False, effect_kind=0, effect_evm_asset=ZERO
        )
        state = HyperCorePendingReader(VAULT, READER).pending_state().call(ctx)
        assert state.effect_kind is HyperCoreEffectKind.NONE
        assert state.pending and not state.settled

    def test_the_predicates_and_balance_fuse_value_decode_their_payloads(self):
        ctx = _ctx()
        vault = PlasmaVault(VAULT)
        reader = HyperCorePendingReader(VAULT, READER)
        ctx.call.return_value = encode(["(bytes)"], [(encode(["bool"], [True]),)])
        assert reader.is_action_pending().call(ctx) is True
        assert ctx.call.call_args.args[1][4:] == encode(
            ["address", "bytes"], [READER, _selector("isActionPending()")]
        )
        assert reader.is_order_in_flight().call(ctx) is True
        assert ctx.call.call_args.args[1][4:] == encode(
            ["address", "bytes"], [READER, _selector("isOrderInFlight()")]
        )
        ctx.call.return_value = encode(
            ["(bytes)"], [(encode(["uint256"], [8_245_000_000_000_000]),)]
        )
        assert vault.balance_fuse_value(BALANCE_FUSE).call(ctx) == 8_245_000_000_000_000
        data = ctx.call.call_args.args[1]
        assert data[4:] == encode(
            ["address", "bytes"], [BALANCE_FUSE, _selector("balanceOf()")]
        )
        ctx.call.return_value = encode(["(bytes)"], [(b"\x01\x02",)])
        assert (
            vault.universal_read(READER, b"\xaa\xbb\xcc\xdd").call(ctx) == b"\x01\x02"
        )

    def test_an_order_state_maps_the_enums(self):
        ctx = _ctx()
        ctx.call.return_value = _pending_state_raw(
            pending=True,
            settled=False,
            action_class=2,
            effect_kind=0,
            effect_evm_asset=ZERO,
        )
        decoded = HyperCorePendingReader(VAULT, READER).pending_state().call(ctx)
        assert decoded.action_class is HyperCoreActionClass.ORDER
        assert decoded.pending and not decoded.settled


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (0, 6, 0),
        (5, 18, 5),
        (SPOT_TOTAL * 10**8, 16, 8_245_000_000_000_000),
        (-5, 6, -5 * 10**12),
        (25, 19, 3),  # 2.5 rounds up
        (24, 19, 2),
        (-25, 19, -2),  # -2.5 rounds toward +infinity
        (-26, 19, -3),
        (-24, 19, -2),
    ],
)
def test_convert_to_wad_int_rounds_like_ipor_math(value, decimals, expected):
    assert convert_to_wad_int(value, decimals) == expected


@pytest.mark.parametrize(("sz_decimals", "expected"), [(0, 6), (5, 1), (6, 0)])
def test_perp_px_decimals(sz_decimals, expected):
    assert perp_px_decimals(sz_decimals) == expected


@pytest.mark.parametrize("sz_decimals", [-1, 7])
def test_perp_px_decimals_rejects_out_of_range(sz_decimals):
    with pytest.raises(ValueError, match="sz_decimals"):
        perp_px_decimals(sz_decimals)


def _universal_read(data: bytes) -> bytes:
    """`PlasmaVault.read` double: the pending reader or the balance fuse."""
    target, inner = decode(["address", "bytes"], data[4:])
    if Web3.to_checksum_address(target) == READER:
        return _pending_state_raw(pending=False, settled=True)
    assert Web3.to_checksum_address(target) == BALANCE_FUSE
    assert bytes(inner) == _selector("balanceOf()")
    return encode(["(bytes)"], [(encode(["uint256"], [8_245_000_000_000_000]),)])


def _vault_state_handler(
    *,
    exists: bool = True,
    substrates: list[bytes] | None = None,
    xyz_value: int = XYZ_ACCOUNT_VALUE,
    price: tuple[int, int] | BaseException = USDC_PRICE,
):
    """eth_call double for the vault, the oracle and the precompiles."""
    words = SUBSTRATES if substrates is None else substrates
    vault_views = {
        _selector("getMarketSubstrates(uint256)"): lambda _d: encode(
            ["bytes32[]"], [words]
        ),
        _selector("getPriceOracleMiddleware()"): lambda _d: encode(
            ["address"], [ORACLE]
        ),
        _selector("read(address,bytes)"): _universal_read,
    }

    def margin(data: bytes) -> bytes:
        value = {0: 0, 1: xyz_value}[int.from_bytes(data[:32], "big")]
        return encode(["int64", "uint64", "uint64", "int64"], [value, 0, 0, value])

    def asset_price(data: bytes) -> bytes:
        assert data[:4] == _selector("getAssetPrice(address)")
        if isinstance(price, BaseException):
            raise price
        return encode(["uint256", "uint256"], list(price))

    def perp_asset_info(data: bytes) -> bytes:
        assert int.from_bytes(data[:32], "big") == 10_002
        return encode(
            ["(string,uint32,uint8,uint8,bool)"], [("xyz:NVDA", 20, 3, 20, False)]
        )

    responders = {
        VAULT: lambda data: vault_views[data[:4]](data),
        CORE_USER_EXISTS_PRECOMPILE: lambda _d: encode(["bool"], [exists]),
        ACCOUNT_MARGIN_SUMMARY_PRECOMPILE: margin,
        SPOT_BALANCE_PRECOMPILE: lambda _d: encode(["uint64"] * 3, [SPOT_TOTAL, 0, 0]),
        TOKEN_INFO_PRECOMPILE: lambda _d: encode(
            ["(string,uint64[],uint64,address,address,uint8,uint8,int8)"], [TOKEN_INFO]
        ),
        PERP_ASSET_INFO_PRECOMPILE: perp_asset_info,
        ORACLE: asset_price,
    }

    def handler(to: str, data: bytes) -> bytes:
        if to not in responders:
            raise AssertionError(f"unexpected call to {to} {data[:4].hex()}")
        return responders[to](bytes(data))

    return handler


class TestNavIdentity:
    def test_matches_the_valuation_library_leg_by_leg(self):
        ctx = _ctx()
        ctx.call.side_effect = multicall_aware(_vault_state_handler())

        nav = read_hypercore_nav(ctx, VAULT, MARKET)

        assert isinstance(nav, HyperCoreNav)
        assert nav.core_user_exists and nav.perp_dex_bitmap == 0b10
        (usdc,) = nav.spot
        assert (usdc.token_index, usdc.evm_asset, usdc.total) == (0, USDC, SPOT_TOTAL)
        assert (
            usdc.value_wad == 8_245_000_000_000_000
        )  # = the balance fuse's 0.008245 USD
        assert nav.native_perp is not None and nav.native_perp.value_wad == 0
        (xyz,) = nav.hip3
        assert (xyz.dex, xyz.account_value) == (1, XYZ_ACCOUNT_VALUE)
        assert nav.value_wad == 8_245_000_000_000_000 + XYZ_ACCOUNT_VALUE * 10**12
        assert nav.balance_wad == nav.value_wad

    def test_a_vault_without_a_core_account_values_at_zero(self):
        ctx = _ctx()
        ctx.call.side_effect = multicall_aware(_vault_state_handler(exists=False))
        nav = read_hypercore_nav(ctx, VAULT, MARKET)
        assert (nav.core_user_exists, nav.spot, nav.hip3, nav.value_wad) == (
            False,
            (),
            (),
            0,
        )
        assert nav.native_perp is None

    def test_negative_equity_is_the_contracts_revert(self):
        ctx = _ctx()
        ctx.call.side_effect = multicall_aware(_vault_state_handler(xyz_value=-10_000))
        nav = read_hypercore_nav(ctx, VAULT, MARKET)
        assert nav.value_wad < 0
        with pytest.raises(HyperCoreValuationError, match="negative"):
            _ = nav.balance_wad

    def test_an_unpriced_held_token_raises_like_the_library(self):
        ctx = _ctx()
        ctx.call.side_effect = multicall_aware(
            _vault_state_handler(price=ContractLogicError("no source"))
        )
        with pytest.raises(HyperCoreValuationError, match="prices no"):
            read_hypercore_nav(ctx, VAULT, MARKET)

    def test_an_unsupported_dex_bitmap_is_rejected_before_any_read(self):
        bitmap_word = (5 << 248) | (4 << 240) | (1 << 2)  # dex 2 is not HIP-3-supported
        ctx = _ctx()
        ctx.call.side_effect = multicall_aware(
            _vault_state_handler(
                substrates=[SUBSTRATES[0], bitmap_word.to_bytes(32, "big")]
            )
        )
        with pytest.raises(HyperCoreValuationError, match="unsupported HIP-3"):
            read_hypercore_nav(ctx, VAULT, MARKET)

    def test_a_failed_precompile_read_raises(self):
        handler = _vault_state_handler()

        def failing_spot_balance(to: str, data: bytes) -> bytes:
            if to == SPOT_BALANCE_PRECOMPILE:
                raise ContractLogicError("precompile failed")
            return handler(to, data)

        ctx = _ctx()
        ctx.call.side_effect = multicall_aware(failing_spot_balance)
        with pytest.raises(HyperCoreValuationError, match="spotBalance precompile"):
            read_hypercore_nav(ctx, VAULT, MARKET)


class TestVaultState:
    def test_reads_nav_fuse_value_pending_and_perp_markets(self):
        ctx = _ctx()
        ctx.chain_id = 999
        ctx.call.side_effect = multicall_aware(_vault_state_handler(xyz_value=0))

        state = read_hypercore_vault_state(
            ctx, VAULT, BALANCE_FUSE, MARKET, pending_reader=READER
        )

        assert isinstance(state, HyperCoreVaultState)
        assert (state.market_id, state.balance_fuse) == (MARKET, BALANCE_FUSE)
        assert state.nav.value_wad == 8_245_000_000_000_000
        assert state.balance_fuse_value_wad == 8_245_000_000_000_000
        assert state.nav_matches_balance_fuse is True
        assert state.pending is not None and state.pending.action_nonce == 32
        (market,) = state.perp_markets
        assert (market.asset, market.dex, market.read_index) == (110_002, 1, 10_002)
        assert (market.coin, market.max_notional_usd6) == ("xyz:NVDA", 15_000_000)
        assert market.reduce_only_required is False

    def test_without_a_known_reader_the_pending_state_is_none(self):
        ctx = _ctx()
        ctx.chain_id = 1
        ctx.call.side_effect = multicall_aware(_vault_state_handler(xyz_value=0))
        state = read_hypercore_vault_state(ctx, VAULT, BALANCE_FUSE, MARKET)
        assert state.pending is None and state.nav_matches_balance_fuse is True
