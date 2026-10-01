"""Compile Solidity contracts with Foundry for deterministic integration tests.

Foundry is an optional external tool: importing and using the SDK does not
require it. Compilation runs only when :func:`compile_foundry_contracts` is
called, respects the target project's ``foundry.toml`` and writes artifacts to
a temporary directory rather than modifying the source checkout.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from eth_abi import encode


class SolidityCompilationError(RuntimeError):
    """Foundry could not produce the requested contract artifacts."""


@dataclass(frozen=True, slots=True)
class FoundryContract:
    """One contract to select from a Foundry compilation."""

    source: str
    name: str


@dataclass(frozen=True, slots=True)
class LibraryReference:
    """One unlinked library placeholder: ``length`` bytes at ``start`` that
    :meth:`SolidityArtifact.link` overwrites with the library's address."""

    name: str
    start: int
    length: int


@dataclass(frozen=True, slots=True)
class SolidityArtifact:
    """Creation/runtime bytecode produced for one Solidity contract.

    Library placeholders are zeroed in the bytecode and listed in
    ``creation_links`` / ``runtime_links``; an artifact stays unusable until
    :meth:`link` has patched every one of them.
    """

    contract: FoundryContract
    creation_code: bytes
    runtime_code: bytes
    immutable_ranges: tuple[tuple[int, int], ...] = ()
    creation_links: tuple[LibraryReference, ...] = ()
    runtime_links: tuple[LibraryReference, ...] = ()

    @property
    def unlinked_libraries(self) -> tuple[str, ...]:
        """Library names still to be linked, sorted."""
        return tuple(
            sorted({ref.name for ref in self.creation_links + self.runtime_links})
        )

    def link(self, libraries: Mapping[str, str]) -> SolidityArtifact:
        """Return a copy with every placeholder patched from ``libraries``
        (library name to deployed address); names it does not need are
        ignored, a missing one raises."""
        missing = [name for name in self.unlinked_libraries if name not in libraries]
        if missing:
            raise ValueError(f"no address for linked libraries: {', '.join(missing)}")
        return replace(
            self,
            creation_code=_patch(self.creation_code, self.creation_links, libraries),
            runtime_code=_patch(self.runtime_code, self.runtime_links, libraries),
            creation_links=(),
            runtime_links=(),
        )

    def init_code(
        self,
        constructor_types: Sequence[str] = (),
        constructor_values: Sequence[Any] = (),
    ) -> bytes:
        """Return creation bytecode with ABI-encoded constructor arguments."""
        if self.creation_links:
            raise ValueError(
                f"{self.contract.name} has unlinked libraries: "
                f"{', '.join(self.unlinked_libraries)}"
            )
        if len(constructor_types) != len(constructor_values):
            raise ValueError(
                "constructor_types and constructor_values must have equal length"
            )
        return self.creation_code + encode(constructor_types, constructor_values)

    def matches_runtime(self, deployed: bytes) -> bool:
        """Compare runtime bytecode while ignoring constructor-patched immutables."""
        if len(deployed) != len(self.runtime_code):
            return False
        expected = bytearray(self.runtime_code)
        actual = bytearray(deployed)
        for start, length in self.immutable_ranges:
            expected[start : start + length] = bytes(length)
            actual[start : start + length] = bytes(length)
        return actual == expected


def compile_foundry_contracts(
    project_root: str | Path,
    contracts: Sequence[FoundryContract],
    *,
    profile: str | None = None,
    remappings: Sequence[str] = (),
    forge_binary: str = "forge",
    offline: bool = True,
    timeout: float = 120.0,
) -> dict[FoundryContract, SolidityArtifact]:
    """Compile ``contracts`` using the project's Foundry configuration.

    Sources must be relative files inside ``project_root``. The compiler and
    project dependencies must already be installed when ``offline`` is true.
    Multiple contracts are compiled in one invocation so their settings and
    dependency graph cannot drift between artifacts.
    """
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Foundry project root is not a directory: {root}")
    requested = tuple(contracts)
    if not requested:
        raise ValueError("contracts must not be empty")
    if len(set(requested)) != len(requested):
        raise ValueError("contracts must not contain duplicates")
    sources = tuple(_source_path(root, contract) for contract in requested)
    forge = _resolve_forge(forge_binary)

    with TemporaryDirectory(prefix="ipor-fusion-foundry-") as temporary:
        work = Path(temporary)
        output = work / "out"
        command = [
            forge,
            "build",
            "--root",
            str(root),
            "--out",
            str(output),
            "--cache-path",
            str(work / "cache"),
        ]
        if offline:
            command.append("--offline")
        if remappings:
            command.extend(("--remappings", *remappings))
        command.append("--")
        command.extend(source.relative_to(root).as_posix() for source in sources)
        environment = {
            key: value
            for key in ("HOME", "PATH", "SVM_HOME", "XDG_CACHE_HOME")
            if (value := os.environ.get(key)) is not None
        }
        if profile is not None:
            environment["FOUNDRY_PROFILE"] = profile
        try:
            completed = subprocess.run(  # noqa: S603 - resolved via shutil.which
                command,
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SolidityCompilationError(
                f"Foundry compilation timed out after {timeout} seconds"
            ) from exc
        except OSError as exc:
            raise SolidityCompilationError(
                f"could not execute Foundry compiler: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise SolidityCompilationError(
                f"Foundry compilation failed with exit code "
                f"{completed.returncode}: {detail}"
            )
        return {
            contract: _read_artifact(
                output, contract, source.relative_to(root).as_posix()
            )
            for contract, source in zip(requested, sources, strict=True)
        }


def git_revision(project_root: str | Path) -> str:
    """``HEAD`` of the checkout at ``project_root``, suffixed ``-dirty`` when a
    tracked file has uncommitted changes.

    Tests that compile from a checkout pin this value: an unpinned working tree
    would move the compiled bytecode with whatever it happens to contain, and
    the test outcome with it, for reasons unrelated to the SDK.
    """
    root = Path(project_root).expanduser().resolve()
    git = shutil.which("git")
    if git is None:
        raise SolidityCompilationError("git was not found on PATH")

    def run(*args: str) -> str:
        completed = subprocess.run(  # noqa: S603 - resolved via shutil.which
            [git, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(f"not a git checkout: {root} ({completed.stderr.strip()})")
        return completed.stdout

    head = run("rev-parse", "HEAD").strip()
    dirty = run("status", "--porcelain", "--untracked-files=no").strip()
    return f"{head}-dirty" if dirty else head


def _source_path(root: Path, contract: FoundryContract) -> Path:
    if not contract.source or not contract.name:
        raise ValueError("contract source and name must not be empty")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", contract.name):
        raise ValueError(f"invalid Solidity contract name: {contract.name}")
    source = (root / contract.source).resolve()
    if not source.is_relative_to(root):
        raise ValueError(f"contract source escapes project root: {contract.source}")
    if not source.is_file():
        raise ValueError(f"contract source does not exist: {contract.source}")
    return source


def _resolve_forge(binary: str) -> str:
    resolved = shutil.which(binary)
    if resolved is None:
        raise SolidityCompilationError(
            f"Foundry executable {binary!r} was not found on PATH"
        )
    return resolved


def _read_artifact(
    output: Path, contract: FoundryContract, source: str
) -> SolidityArtifact:
    matches = []
    for path in output.rglob(f"{contract.name}.json"):
        try:
            data = json.loads(path.read_text())
            target = data["metadata"]["settings"]["compilationTarget"]
            if target.get(source) == contract.name:
                matches.append((path, data))
        except (KeyError, OSError, TypeError, json.JSONDecodeError):
            continue
    if len(matches) != 1:
        raise SolidityCompilationError(
            f"expected one Foundry artifact for {source}:{contract.name}, "
            f"found {len(matches)}"
        )
    path, data = matches[0]
    try:
        creation_code, creation_placeholders = _bytecode(
            data["bytecode"]["object"], "creation", contract
        )
        runtime_code, runtime_placeholders = _bytecode(
            data["deployedBytecode"]["object"], "runtime", contract
        )
        immutable_ranges = _immutable_ranges(
            data["deployedBytecode"].get("immutableReferences", {})
        )
        creation_links = _link_references(data["bytecode"].get("linkReferences", {}))
        runtime_links = _link_references(
            data["deployedBytecode"].get("linkReferences", {})
        )
    except (KeyError, TypeError) as exc:
        raise SolidityCompilationError(
            f"invalid Foundry artifact for {source}:{contract.name} at {path}"
        ) from exc
    # A zeroed placeholder that no reference will patch would ship a call to
    # the zero address; refuse the artifact instead.
    for kind, placeholders, links in (
        ("creation", creation_placeholders, creation_links),
        ("runtime", runtime_placeholders, runtime_links),
    ):
        if placeholders != len(links):
            raise SolidityCompilationError(
                f"{kind} bytecode of {source}:{contract.name} has {placeholders} "
                f"library placeholders but {len(links)} link references"
            )
    return SolidityArtifact(
        contract,
        creation_code,
        runtime_code,
        immutable_ranges,
        creation_links,
        runtime_links,
    )


def _link_references(value: object) -> tuple[LibraryReference, ...]:
    """``linkReferences`` of a Foundry artifact: ``{source: {Library: [{start,
    length}, ...]}}``. Keyed by bare library name; the names are unique within
    one compilation."""
    if not isinstance(value, dict):
        raise SolidityCompilationError("invalid linkReferences in artifact")
    references = []
    try:
        for libraries in value.values():
            for name, ranges in libraries.items():
                for reference in ranges:
                    start = int(reference["start"])
                    length = int(reference["length"])
                    if start < 0 or length <= 0:
                        raise ValueError
                    references.append(LibraryReference(str(name), start, length))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SolidityCompilationError("invalid linkReferences in artifact") from exc
    return tuple(sorted(references, key=lambda ref: (ref.start, ref.name)))


def _patch(
    code: bytes, references: Sequence[LibraryReference], libraries: Mapping[str, str]
) -> bytes:
    patched = bytearray(code)
    for ref in references:
        address = bytes.fromhex(libraries[ref.name].removeprefix("0x"))
        if len(address) != ref.length:
            raise ValueError(
                f"library {ref.name}: address is {len(address)} bytes, "
                f"placeholder is {ref.length}"
            )
        patched[ref.start : ref.start + ref.length] = address
    return bytes(patched)


def _immutable_ranges(value: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, dict):
        raise SolidityCompilationError("invalid immutableReferences in artifact")
    ranges = []
    try:
        for references in value.values():
            for reference in references:
                start = int(reference["start"])
                length = int(reference["length"])
                if start < 0 or length <= 0:
                    raise ValueError
                ranges.append((start, length))
    except (KeyError, TypeError, ValueError) as exc:
        raise SolidityCompilationError(
            "invalid immutableReferences in artifact"
        ) from exc
    return tuple(sorted(set(ranges)))


#: A Foundry library placeholder (``__$<34 hex>$__``); zeroed so the hex
#: parses, and re-filled from ``linkReferences`` by :meth:`SolidityArtifact.link`.
_PLACEHOLDER_RE = re.compile(r"__\$[0-9a-fA-F]{34}\$__")


def _bytecode(value: object, kind: str, contract: FoundryContract) -> tuple[bytes, int]:
    """The bytecode with every library placeholder zeroed, and how many there were."""
    if not isinstance(value, str) or not value.startswith("0x") or len(value) <= 2:
        raise SolidityCompilationError(
            f"{kind} bytecode is empty for {contract.source}:{contract.name}"
        )
    zeroed, placeholders = _PLACEHOLDER_RE.subn("0" * 40, value[2:])
    try:
        return bytes.fromhex(zeroed), placeholders
    except ValueError as exc:
        raise SolidityCompilationError(
            f"{kind} bytecode has unresolved libraries for "
            f"{contract.source}:{contract.name}"
        ) from exc
