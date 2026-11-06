"""EIP-712 NAV report helper of the Derive market (preview)."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest
from eth_abi import decode, encode
from eth_account import Account
from eth_utils import function_signature_to_4byte_selector, keccak
from web3 import Web3

from ipor_fusion.derive import (
    DeriveAccount,
    DeriveNavReport,
    derive_report_deadline,
    derive_report_digest,
    derive_report_from_subaccount_values,
    derive_report_typed_data,
    encode_submit_nav_report,
    mtm_usd_e18_from_subaccount_values,
    sign_derive_report,
)
from ipor_fusion.derive.report import DERIVE_REPORT_TYPE

TYPEHASH = "0x0d87760d0ea24a3d6cba26fe42c4b69826922209dd361557c07b83923f903a20"
ACCOUNT = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
VAULT = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
ROOT = bytes.fromhex("5f3b8e1c9d2a4b6f7e8d9c0b1a2f3e4d5c6b7a8998a7b6c5d4e3f2a1b0c9d8e7")
DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

MAIN = DeriveNavReport(
    vault=VAULT,
    subaccount_ids=(123456, 234567),
    mtm_usd_e18=1234567890123456789000,
    state_root=ROOT,
    last_op_timestamp=1762400000,
    deadline=1762400300,
)
BOOTSTRAP = DeriveNavReport(
    vault=VAULT,
    subaccount_ids=(),
    mtm_usd_e18=0,
    state_root=ROOT,
    last_op_timestamp=1762400000,
    deadline=1762400200,
)
MAIN_DIGEST = "d7609e2611f9d44cec1b13c2180a52010c309985fa24342467eaa5ff1ceebe64"
BOOTSTRAP_DIGEST = "f81e86e0d2297404d25f59c98ec2e699272414851f17bfabffc694097b5a7b33"


def _domain_separator(chain_id: int = 1, account: str = ACCOUNT) -> bytes:
    return keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(text=DOMAIN_TYPE),
                keccak(text="DeriveAccount"),
                keccak(text="1"),
                chain_id,
                account,
            ],
        )
    )


def _hand_built_digest(report: DeriveNavReport, chain_id: int = 1) -> bytes:
    # One 32-byte word per id (Solidity pads array elements); NOT encode_packed.
    ids_hash = keccak(b"".join(i.to_bytes(32, "big") for i in report.subaccount_ids))
    struct_hash = keccak(
        encode(
            [
                "bytes32",
                "address",
                "bytes32",
                "int256",
                "bytes32",
                "uint64",
                "uint64",
            ],
            [
                bytes.fromhex(TYPEHASH[2:]),
                report.vault,
                ids_hash,
                report.mtm_usd_e18,
                report.state_root,
                report.last_op_timestamp,
                report.deadline,
            ],
        )
    )
    return keccak(b"\x19\x01" + _domain_separator(chain_id) + struct_hash)


def test_report_typehash_matches_the_contract() -> None:
    assert "0x" + keccak(text=DERIVE_REPORT_TYPE).hex() == TYPEHASH


def test_digest_matches_the_contracts_vector() -> None:
    assert derive_report_digest(MAIN, 1, ACCOUNT).hex() == MAIN_DIGEST
    assert derive_report_digest(BOOTSTRAP, 1, ACCOUNT).hex() == BOOTSTRAP_DIGEST
    assert _domain_separator().hex() == (
        "d87f3319ee5a8140f61d708a92a53c5d8d180f517c3a0f163200ef2f01a0100c"
    )


@pytest.mark.parametrize(
    "ids", [(123456, 234567), (7,), ()], ids=["two ids", "one id", "empty set"]
)
def test_digest_equals_a_hand_built_eip712_hash(ids: tuple[int, ...]) -> None:
    report = DeriveNavReport(
        vault=VAULT,
        subaccount_ids=ids,
        mtm_usd_e18=5 * 10**18,
        state_root=ROOT,
        last_op_timestamp=11,
        deadline=22,
    )
    assert derive_report_digest(report, 1, ACCOUNT) == _hand_built_digest(report)
    assert derive_report_digest(report, 42161, ACCOUNT) == _hand_built_digest(
        report, 42161
    )
    assert derive_report_digest(report, 1, ACCOUNT) != derive_report_digest(
        report, 2, ACCOUNT
    )


def test_ids_hash_is_32_bytes_per_element_not_packed() -> None:
    packed_8 = b"".join(i.to_bytes(8, "big") for i in (123456, 234567))
    assert keccak(packed_8).hex() != (
        "912d5ff550b4780df9fc0b05dd1c455cff395ef68f2839cf2bc78445bd280f01"
    )
    assert keccak(b"".join(i.to_bytes(32, "big") for i in (123456, 234567))).hex() == (
        "912d5ff550b4780df9fc0b05dd1c455cff395ef68f2839cf2bc78445bd280f01"
    )
    assert keccak(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


def test_keeper_signatures_match_the_vectors() -> None:
    sig_a = sign_derive_report(MAIN, 1, ACCOUNT, (0xA11CE).to_bytes(32, "big"))
    sig_b = sign_derive_report(MAIN, 1, ACCOUNT, (0xB0B).to_bytes(32, "big"))
    assert sig_a.hex() == (
        "dab82bd5c4641a632f76c1cd1394bcde661e662b52f35d910ceef4985cdd5bde"
        "4d4a99e373fab999e15254349492501c5cc7d5f30c3d81623be683db566aed72"
        "1c"
    )
    assert Account.from_key((0xA11CE).to_bytes(32, "big")).address == (
        "0xe05fcC23807536bEe418f142D19fa0d21BB0cfF7"
    )
    assert Account.from_key((0xB0B).to_bytes(32, "big")).address == (
        "0x0376AAc07Ad725E01357B1725B5ceC61aE10473c"
    )
    assert sig_b.hex() == (
        "f44884b580c0603aa25901cdfa8dcec3767dfad656f483ce2eeb39334c263142"
        "1a653456776a420fd36eee5c31114fbf65b82eb76de04a3d2ecff86f40ddcdac"
        "1b"
    )


def test_signature_recovers_to_the_keeper() -> None:
    keeper = Account.create()
    signature = sign_derive_report(MAIN, 1, ACCOUNT, keeper.key)
    assert len(signature) == 65
    assert signature[64] in (27, 28)
    assert int.from_bytes(signature[32:64], "big") <= SECP256K1_N // 2
    digest = derive_report_digest(MAIN, 1, ACCOUNT)
    # Recover over the raw digest: sign_message of typed data hashes the same bytes.
    recovered = Account._recover_hash(digest, signature=signature)  # noqa: SLF001
    assert recovered == keeper.address


def test_submit_calldata_round_trips() -> None:
    sig1, sig2 = b"\x01" * 65, b"\x02" * 65
    data = encode_submit_nav_report(MAIN, sig1, sig2)
    selector = function_signature_to_4byte_selector(
        "submitNavReport((address,uint64[],int256,bytes32,uint64,uint64),bytes,bytes)"
    )
    assert data[:4] == selector
    report, s1, s2 = decode(
        ["(address,uint64[],int256,bytes32,uint64,uint64)", "bytes", "bytes"], data[4:]
    )
    assert report == (
        VAULT.lower(),
        (123456, 234567),
        MAIN.mtm_usd_e18,
        ROOT,
        1762400000,
        1762400300,
    )
    assert (s1, s2) == (sig1, sig2)
    call = DeriveAccount(ACCOUNT).submit_nav_report(MAIN, sig1, sig2)
    assert call.data == data
    assert call.to == ACCOUNT


def test_last_accepted_deadline_call() -> None:
    call = DeriveAccount(ACCOUNT).last_accepted_deadline()
    assert call.data == function_signature_to_4byte_selector("lastAcceptedDeadline()")
    assert call.to == ACCOUNT
    assert decode(call.output_types, encode(["uint64"], [1762400300]))[0] == 1762400300


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({1: "-5", 2: "10"}, 10 * 10**18),
        ({1: "-5"}, 0),
        ({1: "3.5", 2: 2}, 5_500_000_000_000_000_000),
        ({}, 0),
    ],
    ids=["underwater beside healthy", "only underwater", "healthy", "empty set"],
)
def test_floor_per_subaccount(values: dict[int, object], expected: int) -> None:
    assert mtm_usd_e18_from_subaccount_values(values) == expected  # type: ignore[arg-type]


def test_mtm_arithmetic_is_exact() -> None:
    value = "10000000000.000000000000000009"
    assert mtm_usd_e18_from_subaccount_values({1: value}) == (
        10000000000000000000000000009
    )
    with localcontext() as ctx:
        ctx.prec = 6
        assert mtm_usd_e18_from_subaccount_values({1: Decimal(value)}) == (
            10000000000000000000000000009
        )
    assert (
        mtm_usd_e18_from_subaccount_values({1: "0.1", 2: "0.2", 3: "0.3"}) == 6 * 10**17
    )
    assert mtm_usd_e18_from_subaccount_values({1: "0.1234567890123456789"}) == (
        123456789012345678
    )
    assert mtm_usd_e18_from_subaccount_values({1: Fraction(1, 3)}) == (
        333333333333333333
    )
    for bad in (Decimal("NaN"), Decimal("Infinity"), "nan", "inf", "abc"):
        with pytest.raises(ValueError):
            mtm_usd_e18_from_subaccount_values({1: bad})  # type: ignore[dict-item]
    for bad_type in (1.5, True, None, [1]):
        with pytest.raises(TypeError):
            mtm_usd_e18_from_subaccount_values({1: bad_type})  # type: ignore[dict-item]


def test_builder_keeps_every_subaccount_sorted() -> None:
    report = derive_report_from_subaccount_values(
        vault=VAULT,
        values={9: "-3", 7: "100.25", 8: "0"},
        state_root=ROOT,
        last_op_timestamp=5,
        deadline=6,
    )
    assert report.subaccount_ids == (7, 8, 9)
    assert report.mtm_usd_e18 == 100250000000000000000
    assert (report.vault, report.state_root) == (VAULT, ROOT)
    assert (report.last_op_timestamp, report.deadline) == (5, 6)
    empty = derive_report_from_subaccount_values(
        vault=VAULT, values={}, state_root=ROOT, last_op_timestamp=1, deadline=2
    )
    assert empty.subaccount_ids == ()
    assert empty.mtm_usd_e18 == 0


def test_report_deadline_rule() -> None:
    assert derive_report_deadline(1000, 300, 500) == 1300
    assert derive_report_deadline(1000, 300, 5000) == 5001
    assert derive_report_deadline(1000, 300, 1299) == 1300
    assert derive_report_deadline(1000, 300, 1300) == 1301
    assert derive_report_deadline(1000, 300, 0) == 1300
    with pytest.raises(ValueError):
        derive_report_deadline(2**64 - 1, 1, 0)


def _report(**overrides: object) -> DeriveNavReport:
    fields: dict[str, object] = {
        "vault": VAULT,
        "subaccount_ids": (1, 2),
        "mtm_usd_e18": 0,
        "state_root": ROOT,
        "last_op_timestamp": 1,
        "deadline": 2,
    }
    fields.update(overrides)
    return DeriveNavReport(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"subaccount_ids": (2, 1)},
        {"subaccount_ids": (1, 1)},
        {"subaccount_ids": tuple(range(33))},
        {"subaccount_ids": (2**64,)},
        {"mtm_usd_e18": -1},
        {"mtm_usd_e18": 2**255},
        {"state_root": ROOT[:31]},
        {"deadline": 2**64},
        {"last_op_timestamp": -1},
        {"deadline": True},
        {"state_root": "0x" + "ab" * 15},
        {"state_root": 5},
        {"mtm_usd_e18": True},
        {"mtm_usd_e18": "1"},
    ],
    ids=[
        "unsorted",
        "duplicate",
        "33 ids",
        "id too large",
        "negative mtm",
        "mtm overflow",
        "short root",
        "deadline overflow",
        "negative timestamp",
        "bool deadline",
        "str root of 32 chars",
        "int root",
        "bool mtm",
        "str mtm",
    ],
)
def test_report_validation(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _report(**overrides)
    assert len(_report().state_root) == 32
    assert _report(subaccount_ids=tuple(range(32))).subaccount_ids[-1] == 31


def test_typed_data_shape() -> None:
    data = derive_report_typed_data(MAIN, 1, ACCOUNT)
    assert data["primaryType"] == "Report"
    assert data["domain"] == {
        "name": "DeriveAccount",
        "version": "1",
        "chainId": 1,
        "verifyingContract": ACCOUNT,
    }
    types = data["types"]
    assert isinstance(types, dict)
    assert [(f["name"], f["type"]) for f in types["Report"]] == [
        ("vault", "address"),
        ("subaccountIds", "uint64[]"),
        ("mtmUsdE18", "int256"),
        ("stateRoot", "bytes32"),
        ("lastOpTimestamp", "uint64"),
        ("deadline", "uint64"),
    ]
