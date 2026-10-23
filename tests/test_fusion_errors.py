"""The IPOR Fusion custom-error registry: every signature is canonical and
registered, the vault's reverts decode by name, and the generator canonicalizes
Solidity declarations the way the compiler does."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from eth_abi import encode as abi_encode
from eth_utils import keccak
from hexbytes import HexBytes

from ipor_fusion.core.simulation_result import _decode_revert
from ipor_fusion.errors import CUSTOM_ERRORS, _decode_revert_reason
from ipor_fusion.fusion_errors import FUSION_ERROR_SIGNATURES

SIGNATURE = re.compile(r"^[A-Za-z_]\w*\((\w+(,\w+)*)?\)$")
SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "fusion_error_signatures.py"
)
CALLER = "0x48d3615d78B152819ea0367adF7b9944e399ac9a"


def _revert(signature: str, *args: object) -> bytes:
    types = signature[signature.index("(") + 1 : -1]
    payload = abi_encode(types.split(",") if types else [], list(args))
    return keccak(text=signature)[:4] + payload


def test_every_signature_is_canonical_and_registered():
    assert list(FUSION_ERROR_SIGNATURES) == sorted(set(FUSION_ERROR_SIGNATURES))
    for signature in FUSION_ERROR_SIGNATURES:
        assert SIGNATURE.fullmatch(signature), signature
        name, types = CUSTOM_ERRORS[keccak(text=signature)[:4]]
        assert signature == f"{name}({','.join(types)})"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            _revert("AccessManagedUnauthorized(address)", CALLER),
            f"AccessManagedUnauthorized({CALLER})",
        ),
        (_revert("FailedInnerCall()"), "FailedInnerCall()"),
        (_revert("UnsupportedFuse()"), "UnsupportedFuse()"),
        (
            _revert("AddressEmptyCode(address)", CALLER),
            f"AddressEmptyCode({CALLER})",
        ),
        (
            _revert("ERC20InsufficientBalance(address,uint256,uint256)", CALLER, 1, 2),
            f"ERC20InsufficientBalance({CALLER}, 1, 2)",
        ),
    ],
)
def test_vault_reverts_decode_by_name(data: bytes, expected: str):
    assert _decode_revert_reason(data) == expected
    assert _decode_revert(HexBytes(data), "execution reverted") == expected


def _load_script():
    spec = importlib.util.spec_from_file_location("fusion_error_signatures", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_the_generator_follows_imports_and_canonicalizes(tmp_path: Path):
    _write(
        tmp_path,
        "foundry.toml",
        "[profile.default]\nlibs = ['node_modules']\n"
        "remappings = ['@vendor/=contracts/vendor/']\n",
    )
    _write(
        tmp_path,
        "contracts/Vault.sol",
        'import {Lib} from "@oz/Lib.sol";\n'
        'import "@vendor/Thing.sol";\n'
        "enum Kind { A, B }\n"
        "interface IFuse {}\n"
        "contract Vault {\n"
        "    // error Commented(uint256);\n"
        "    error Bad(uint amount, Kind kind, IFuse fuse,\n"
        "        bytes32 id);\n"
        "    error Plain();\n"
        "}\n",
    )
    _write(tmp_path, "contracts/vendor/Thing.sol", "error Vendored(int x);\n")
    _write(
        tmp_path, "node_modules/@oz/Lib.sol", "library Lib { error Oz(address a); }\n"
    )
    _write(tmp_path, "node_modules/@oz/Unused.sol", "error Unreached();\n")

    assert _load_script().collect(tmp_path) == [
        "Bad(uint256,uint8,address,bytes32)",
        "Oz(address)",
        "Plain()",
        "Vendored(int256)",
    ]


def test_the_generator_refuses_a_type_it_cannot_canonicalize(tmp_path: Path):
    _write(tmp_path, "foundry.toml", "[profile.default]\nlibs = []\nremappings = []\n")
    _write(
        tmp_path,
        "contracts/A.sol",
        "struct Pair { uint256 a; }\ncontract A { error E(Pair p); }\n",
    )
    with pytest.raises(SystemExit, match="Pair"):
        _load_script().collect(tmp_path)
