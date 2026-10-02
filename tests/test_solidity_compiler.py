from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from _foundry import (
    FoundryContract,
    LibraryReference,
    SolidityArtifact,
    SolidityCompilationError,
    compile_foundry_contracts,
    git_revision,
)


def _source(root: Path, name: str = "Example.sol") -> Path:
    source = root / "contracts" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("contract Example {}")
    return source


def _artifact(source: str, name: str, bytecode: str = "0x6000") -> dict:
    return {
        "metadata": {"settings": {"compilationTarget": {source: name}}},
        "bytecode": {"object": bytecode},
        "deployedBytecode": {"object": "0x6001"},
    }


def test_compile_foundry_contracts_and_encode_constructor(tmp_path, monkeypatch):
    _source(tmp_path)
    contract = FoundryContract("contracts/Example.sol", "Example")
    captured = {}

    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/forge")

    def run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        output = Path(command[command.index("--out") + 1])
        artifact = output / "Example.sol" / "Example.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(json.dumps(_artifact(contract.source, contract.name)))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", run)
    artifacts = compile_foundry_contracts(
        tmp_path,
        (contract,),
        profile="crosschain_deploy",
        remappings=("@example/=vendor/example/",),
    )

    artifact = artifacts[contract]
    assert artifact.creation_code == bytes.fromhex("6000")
    assert artifact.runtime_code == bytes.fromhex("6001")
    assert artifact.init_code(("uint256",), (7,)) == bytes.fromhex("6000") + (
        7
    ).to_bytes(32, "big")
    command = captured["command"]
    assert command[0:2] == ["/usr/bin/forge", "build"]
    assert "--offline" in command
    assert command[-2] == "--"
    assert command[-1] == contract.source
    assert captured["kwargs"]["env"]["FOUNDRY_PROFILE"] == "crosschain_deploy"
    assert "shell" not in captured["kwargs"]


def test_constructor_arguments_must_have_matching_lengths():
    contract = FoundryContract("contracts/Example.sol", "Example")
    artifact = _compiled(contract)
    with pytest.raises(ValueError, match="equal length"):
        artifact.init_code(("uint256",), ())


def test_runtime_comparison_ignores_only_immutable_ranges():
    contract = FoundryContract("contracts/Example.sol", "Example")
    artifact = SolidityArtifact(
        contract,
        b"\x60\x00",
        b"\x01\x00\x00\x04",
        ((1, 2),),
    )

    assert artifact.matches_runtime(b"\x01\xaa\xbb\x04")
    assert not artifact.matches_runtime(b"\x01\xaa\xbb")
    assert not artifact.matches_runtime(b"\x02\xaa\xbb\x04")


def _compiled(contract: FoundryContract):
    return SolidityArtifact(contract, b"\x60\x00", b"\x60\x01")


@pytest.mark.parametrize(
    ("contracts", "message"),
    [
        ((), "must not be empty"),
        (
            (
                FoundryContract("contracts/Example.sol", "Example"),
                FoundryContract("contracts/Example.sol", "Example"),
            ),
            "must not contain duplicates",
        ),
    ],
)
def test_rejects_invalid_contract_collection(tmp_path, contracts, message):
    _source(tmp_path)
    with pytest.raises(ValueError, match=message):
        compile_foundry_contracts(tmp_path, contracts)


@pytest.mark.parametrize(
    ("contract", "message"),
    [
        (FoundryContract("../Outside.sol", "Outside"), "escapes project root"),
        (FoundryContract("contracts/Missing.sol", "Missing"), "does not exist"),
        (FoundryContract("", "Missing"), "must not be empty"),
        (FoundryContract("contracts/Example.sol", ""), "must not be empty"),
        (FoundryContract("contracts/Example.sol", "../Example"), "invalid Solidity"),
    ],
)
def test_rejects_invalid_source(tmp_path, contract, message):
    _source(tmp_path)
    with pytest.raises(ValueError, match=message):
        compile_foundry_contracts(tmp_path, (contract,))


def test_rejects_missing_project_root(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        compile_foundry_contracts(
            tmp_path / "missing",
            (FoundryContract("contracts/Example.sol", "Example"),),
        )


def test_reports_missing_forge(tmp_path, monkeypatch):
    _source(tmp_path)
    monkeypatch.setattr("shutil.which", lambda binary: None)
    with pytest.raises(SolidityCompilationError, match="was not found"):
        compile_foundry_contracts(
            tmp_path,
            (FoundryContract("contracts/Example.sol", "Example"),),
        )


@pytest.mark.parametrize(("stderr", "stdout"), [("compiler error", ""), ("", "out")])
def test_reports_forge_failure(tmp_path, monkeypatch, stderr, stdout):
    _source(tmp_path)
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/forge")
    monkeypatch.setattr(
        "subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout, stderr
        ),
    )
    with pytest.raises(SolidityCompilationError, match=stderr or stdout):
        compile_foundry_contracts(
            tmp_path,
            (FoundryContract("contracts/Example.sol", "Example"),),
            offline=False,
        )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired("forge", 1), "timed out"),
        (OSError("cannot execute"), "could not execute"),
    ],
)
def test_reports_forge_execution_error(tmp_path, monkeypatch, error, message):
    _source(tmp_path)
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/forge")

    def fail(command, **kwargs):
        raise error

    monkeypatch.setattr("subprocess.run", fail)
    with pytest.raises(SolidityCompilationError, match=message):
        compile_foundry_contracts(
            tmp_path,
            (FoundryContract("contracts/Example.sol", "Example"),),
            timeout=1,
        )


@pytest.mark.parametrize(
    ("artifact_data", "message"),
    [
        ({}, "expected one Foundry artifact"),
        (
            _artifact("contracts/Other.sol", "Example"),
            "expected one Foundry artifact",
        ),
        (
            _artifact("contracts/Example.sol", "Example", "0x"),
            "creation bytecode is empty",
        ),
        (
            _artifact("contracts/Example.sol", "Example", "0x__$missing$__"),
            "unresolved libraries",
        ),
        (
            {
                **_artifact("contracts/Example.sol", "Example"),
                "deployedBytecode": {
                    "object": "0x6001",
                    "immutableReferences": {"1": [{"start": -1, "length": 32}]},
                },
            },
            "invalid immutableReferences",
        ),
    ],
)
def test_rejects_invalid_artifact(tmp_path, monkeypatch, artifact_data, message):
    _source(tmp_path)
    contract = FoundryContract("contracts/Example.sol", "Example")
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/forge")

    def run(command, **kwargs):
        output = Path(command[command.index("--out") + 1])
        artifact = output / "Example.sol" / "Example.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(json.dumps(artifact_data))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(SolidityCompilationError, match=message):
        compile_foundry_contracts(tmp_path, (contract,))


def _git(monkeypatch, *, head: str = "abc123", status: str = "", returncode: int = 0):
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/git")
    captured = []

    def run(command, **kwargs):
        captured.append(command)
        stdout = {"rev-parse": f"{head}\n", "status": status}[command[3]]
        return subprocess.CompletedProcess(command, returncode, stdout, "fatal: x")

    monkeypatch.setattr("subprocess.run", run)
    return captured


def test_git_revision_is_head_of_a_clean_checkout(tmp_path, monkeypatch):
    captured = _git(monkeypatch)

    assert git_revision(tmp_path) == "abc123"
    assert [command[:3] for command in captured] == [
        ["/usr/bin/git", "-C", str(tmp_path)]
    ] * 2
    assert captured[1][3:] == ["status", "--porcelain", "--untracked-files=no"]


def test_git_revision_marks_tracked_changes_dirty(tmp_path, monkeypatch):
    # Untracked files are excluded by the status flags, so only a modified
    # tracked file (which is what gets compiled) makes the revision dirty.
    _git(monkeypatch, status=" M contracts/Example.sol\n")

    assert git_revision(tmp_path) == "abc123-dirty"


def test_git_revision_rejects_a_non_checkout(tmp_path, monkeypatch):
    _git(monkeypatch, returncode=128)

    with pytest.raises(ValueError, match="not a git checkout"):
        git_revision(tmp_path)


def test_git_revision_reports_missing_git(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda binary: None)

    with pytest.raises(SolidityCompilationError, match="git was not found"):
        git_revision(tmp_path)


LIBRARY = "0x" + "ab" * 20
PLACEHOLDER = "__$" + "c" * 34 + "$__"


def _linked_artifact(source: str, name: str) -> dict:
    # Creation code: PUSH20 <library> ...; the placeholder sits at byte 1.
    artifact = _artifact(source, name, bytecode="0x73" + PLACEHOLDER + "00")
    artifact["bytecode"]["linkReferences"] = {
        "contracts/Lib.sol": {"Lib": [{"start": 1, "length": 20}]}
    }
    artifact["deployedBytecode"] = {
        "object": "0x6073" + PLACEHOLDER,
        "linkReferences": {"contracts/Lib.sol": {"Lib": [{"start": 2, "length": 20}]}},
    }
    return artifact


def _compile_linked(tmp_path, monkeypatch) -> SolidityArtifact:
    _source(tmp_path)
    contract = FoundryContract("contracts/Example.sol", "Example")
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/forge")

    def run(command, **kwargs):
        output = Path(command[command.index("--out") + 1])
        artifact = output / "Example.sol" / "Example.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            json.dumps(_linked_artifact(contract.source, contract.name))
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", run)
    return compile_foundry_contracts(tmp_path, (contract,))[contract]


def test_placeholders_are_zeroed_and_listed(tmp_path, monkeypatch):
    artifact = _compile_linked(tmp_path, monkeypatch)

    assert artifact.creation_code == bytes.fromhex("73" + "00" * 20 + "00")
    assert artifact.runtime_code == bytes.fromhex("6073" + "00" * 20)
    assert artifact.creation_links == (LibraryReference("Lib", 1, 20),)
    assert artifact.runtime_links == (LibraryReference("Lib", 2, 20),)
    assert artifact.unlinked_libraries == ("Lib",)
    with pytest.raises(ValueError, match="unlinked libraries: Lib"):
        artifact.init_code()


def test_link_patches_every_placeholder(tmp_path, monkeypatch):
    artifact = _compile_linked(tmp_path, monkeypatch)

    linked = artifact.link({"Lib": LIBRARY, "Unused": LIBRARY})

    assert linked.creation_code == bytes.fromhex("73" + "ab" * 20 + "00")
    assert linked.runtime_code == bytes.fromhex("6073" + "ab" * 20)
    assert linked.unlinked_libraries == ()
    assert linked.init_code(("uint8",), (1,)) == linked.creation_code + (1).to_bytes(
        32, "big"
    )
    assert artifact.creation_links, "link() must not mutate the original"


def test_link_rejects_missing_or_malformed_addresses(tmp_path, monkeypatch):
    artifact = _compile_linked(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="no address for linked libraries: Lib"):
        artifact.link({})
    with pytest.raises(ValueError, match="address is 2 bytes, placeholder is 20"):
        artifact.link({"Lib": "0xabcd"})


def test_rejects_a_placeholder_without_a_link_reference(tmp_path, monkeypatch):
    _source(tmp_path)
    contract = FoundryContract("contracts/Example.sol", "Example")
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/forge")

    def run(command, **kwargs):
        output = Path(command[command.index("--out") + 1])
        artifact = output / "Example.sol" / "Example.json"
        artifact.parent.mkdir(parents=True)
        data = _linked_artifact(contract.source, contract.name)
        del data["bytecode"]["linkReferences"]
        artifact.write_text(json.dumps(data))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(SolidityCompilationError, match="1 library placeholders but 0"):
        compile_foundry_contracts(tmp_path, (contract,))


def test_rejects_invalid_link_references(tmp_path, monkeypatch):
    _source(tmp_path)
    contract = FoundryContract("contracts/Example.sol", "Example")
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/forge")

    def run(command, **kwargs):
        output = Path(command[command.index("--out") + 1])
        artifact = output / "Example.sol" / "Example.json"
        artifact.parent.mkdir(parents=True)
        data = _artifact(contract.source, contract.name)
        data["bytecode"]["linkReferences"] = {
            "contracts/Lib.sol": {"Lib": [{"start": -1}]}
        }
        artifact.write_text(json.dumps(data))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(SolidityCompilationError, match="invalid linkReferences"):
        compile_foundry_contracts(tmp_path, (contract,))
