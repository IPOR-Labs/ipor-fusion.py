"""The IPOR Fusion custom-error registry: every signature is canonical and
registered, the vault's reverts decode by name, and the generator canonicalizes
Solidity declarations the way the compiler does."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
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
        (_revert("DeriveCapitalFlowLocked(uint8)", 3), "DeriveCapitalFlowLocked(3)"),
        (
            _revert(
                "DeriveOutboxWithdrawalUnknown(bytes32)",
                b"\x11" * 32,
            ),
            f"DeriveOutboxWithdrawalUnknown(0x{'11' * 32})",
        ),
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


BASE = ["Base(uint256)", "Keep()"]
LABEL = "the Demo family from a feature branch"


def _ahead_tree(
    root: Path, first: str = "A", second: str | None = "Gone"
) -> list[Path]:
    _write(
        root,
        "feature/One.sol",
        "enum Kind { X, Y }\ninterface IX {}\n"
        f"contract One {{ error {first}(Kind k, IX x, bytes32 w); }}\n",
    )
    paths = [root / "feature"]
    if second is not None:
        _write(root, "other/Two.sol", f"error {second}(uint amount);\n")
        paths.append(root / "other" / "Two.sol")
    return paths


def _file_text(ahead: tuple[str, list[str]] | None) -> str:
    return _load_script().render(BASE, "10f0f8e4", ahead)


def _load_module(text: str, tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "generated_errors.py"
    path.write_text(text)
    spec = importlib.util.spec_from_file_location(f"generated_{abs(hash(text))}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return vars(module)


def test_the_generator_merges_ahead_sources(tmp_path: Path):
    script = _load_script()
    ahead = script.collect_ahead(_ahead_tree(tmp_path))
    assert ahead == ["A(uint8,address,bytes32)", "Gone(uint256)"]
    text = _file_text((LABEL, ahead))
    assert f"{LABEL} (2 signatures)." in text
    ns = _load_module(text, tmp_path)
    assert ns["_PUBLIC_SIGNATURES"] == tuple(BASE)
    assert ns["_AHEAD_OF_UPSTREAM_SIGNATURES"] == tuple(ahead)
    merged = ns["FUSION_ERROR_SIGNATURES"]
    assert merged == tuple(sorted({*BASE, *ahead}))
    assert len(merged) == len(set(merged))


def test_augment_is_idempotent_and_replaces_the_ahead_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script = _load_script()
    output = tmp_path / "fusion_errors.py"
    output.write_text(_file_text(None))
    monkeypatch.setattr(script, "_OUTPUT", output)

    def run(paths: list[Path]) -> str:
        public, source = script.current_public()
        ahead = script.collect_ahead(paths)
        output.write_text(script.render(public, source, (LABEL, ahead)))
        return output.read_text()

    first = run(_ahead_tree(tmp_path / "t1"))
    assert run(_ahead_tree(tmp_path / "t1")) == first
    assert "from ipor-fusion 10f0f8e4;" in first

    second = run(_ahead_tree(tmp_path / "t2", first="B", second=None))
    ns = _load_module(second, tmp_path)
    assert ns["_AHEAD_OF_UPSTREAM_SIGNATURES"] == ("B(uint8,address,bytes32)",)
    assert ns["_PUBLIC_SIGNATURES"] == tuple(BASE)
    assert "A(" not in second and "Gone(" not in second
    assert "(1 signatures)" in second


def test_full_regeneration_drops_the_ahead_sentence(tmp_path: Path):
    text = _file_text(None)
    assert "Ahead of upstream" not in text
    ns = _load_module(text, tmp_path)
    assert ns["_AHEAD_OF_UPSTREAM_SIGNATURES"] == ()
    assert ns["FUSION_ERROR_SIGNATURES"] == tuple(BASE)


def test_the_ahead_collector_refuses_a_type_it_cannot_canonicalize(tmp_path: Path):
    _write(
        tmp_path,
        "A.sol",
        "struct Pair { uint256 a; }\ncontract A { error E(Pair p); }\n",
    )
    with pytest.raises(SystemExit, match="Pair"):
        _load_script().collect_ahead([tmp_path / "A.sol"])


def test_the_public_and_ahead_sets_are_sorted_disjoint_and_leak_free():
    module = importlib.import_module("ipor_fusion.fusion_errors")
    public, ahead = module._PUBLIC_SIGNATURES, module._AHEAD_OF_UPSTREAM_SIGNATURES
    assert list(public) == sorted(set(public))
    assert list(ahead) == sorted(set(ahead))
    assert not set(public) & set(ahead)
    assert module.FUSION_ERROR_SIGNATURES == tuple(sorted({*public, *ahead}))
    doc = module.__doc__ or ""
    assert not re.search(r"\b[0-9a-f]{40}\b|private|IL-\d+", doc)
    assert ahead and all(s.startswith("Derive") for s in ahead)


EMPTY_AHEAD = "_AHEAD_OF_UPSTREAM_SIGNATURES: tuple[str, ...] = ()\n"


def _ruff_format_clean(text: str) -> bool:
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent))
    assert ruff is not None, "ruff is a dev dependency"
    result = subprocess.run(  # noqa: S603 - fixed ruff binary, text on stdin
        [ruff, "format", "--check", "--stdin-filename", "fusion_errors.py", "-"],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize("ahead", [None, (LABEL, [])], ids=["full", "augment-empty"])
def test_an_empty_ahead_tuple_is_rendered_ruff_format_clean(
    ahead: tuple[str, list[str]] | None, tmp_path: Path
):
    text = _file_text(ahead)
    assert EMPTY_AHEAD in text
    assert "= (\n\n)" not in text
    assert _ruff_format_clean(text)
    ns = _load_module(text, tmp_path)
    assert ns["_AHEAD_OF_UPSTREAM_SIGNATURES"] == ()


def test_a_non_empty_render_is_ruff_format_clean():
    assert _ruff_format_clean(_file_text((LABEL, ["Zed()"])))


def _run_main(script: object, monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["fusion_error_signatures.py", *argv])
    script.main()  # type: ignore[attr-defined]


def test_main_augment_writes_two_tuples_filters_public_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script = _load_script()
    output = tmp_path / "fusion_errors.py"
    output.write_text(_file_text(None))
    monkeypatch.setattr(script, "_OUTPUT", output)
    _write(
        tmp_path,
        "ahead/A.sol",
        "contract A { error Fresh(uint a); error Base(uint256); }\n",
    )
    argv = ("--ahead", str(tmp_path / "ahead"), "--ahead-label", LABEL)

    _run_main(script, monkeypatch, *argv)
    first = output.read_text()
    ns = _load_module(first, tmp_path)
    assert ns["_PUBLIC_SIGNATURES"] == tuple(BASE)
    assert ns["_AHEAD_OF_UPSTREAM_SIGNATURES"] == ("Fresh(uint256)",)
    assert ns["FUSION_ERROR_SIGNATURES"] == tuple(sorted({*BASE, "Fresh(uint256)"}))
    assert "(1 signatures)" in first and LABEL in first
    assert _ruff_format_clean(first)

    _run_main(script, monkeypatch, *argv)
    assert output.read_text() == first


def test_main_augment_with_only_public_errors_leaves_an_empty_clean_ahead_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script = _load_script()
    output = tmp_path / "fusion_errors.py"
    output.write_text(_file_text(None))
    monkeypatch.setattr(script, "_OUTPUT", output)
    _write(tmp_path, "ahead/A.sol", "error Base(uint256);\n")
    _run_main(
        script,
        monkeypatch,
        "--ahead",
        str(tmp_path / "ahead"),
        "--ahead-label",
        LABEL,
    )
    text = output.read_text()
    assert EMPTY_AHEAD in text and "(0 signatures)" in text
    assert _ruff_format_clean(text)


def test_main_full_run_writes_an_empty_clean_ahead_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script = _load_script()
    output = tmp_path / "fusion_errors.py"
    monkeypatch.setattr(script, "_OUTPUT", output)
    _write(tmp_path, "foundry.toml", "[profile.default]\nlibs = []\nremappings = []\n")
    _write(tmp_path, "contracts/A.sol", "contract A { error E(uint a); }\n")
    _run_main(script, monkeypatch, str(tmp_path), "--source", "abc1234")
    text = output.read_text()
    assert "from ipor-fusion abc1234;" in text and EMPTY_AHEAD in text
    assert _ruff_format_clean(text)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["CHECKOUT", "--source", "x", "--ahead", "A.sol"], "full run"),
        (["CHECKOUT", "--source", "x", "--ahead-label", "l"], "full run"),
        (["CHECKOUT"], "full run"),
        (["--ahead", "A.sol"], "augment mode"),
        (["--ahead-label", "l"], "augment mode"),
        (["--ahead", "A.sol", "--ahead-label", "l", "--source", "x"], "augment mode"),
    ],
    ids=[
        "checkout-with-ahead",
        "checkout-with-label",
        "full-run-without-source",
        "augment-without-label",
        "augment-without-ahead",
        "augment-with-source",
    ],
)
def test_main_refuses_mixed_or_incomplete_modes(
    argv: list[str],
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    script = _load_script()
    output = tmp_path / "fusion_errors.py"
    output.write_text("untouched")
    monkeypatch.setattr(script, "_OUTPUT", output)
    argv = [str(tmp_path) if a == "CHECKOUT" else a for a in argv]
    with pytest.raises(SystemExit) as exit_info:
        _run_main(script, monkeypatch, *argv)
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert output.read_text() == "untouched"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_public_tuple_round_trips_through_current_public(count: int):
    script = _load_script()
    public = ["Zed()", "Alpha(uint256)"][:count]
    for ahead in (None, (LABEL, ["Beta()"])):
        text = script.render(sorted(public), "10f0f8e4", ahead)
        assert script.current_public(text) == (sorted(public), "10f0f8e4")
        assert _ruff_format_clean(text)
