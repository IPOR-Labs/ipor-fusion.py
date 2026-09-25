"""Import layering of the crosschain packages.

The fuse encoders (``ipor_fusion.fuses.crosschain``) import the crosschain
wire vocabulary and wrappers; lanes, discovery, transports and the simulator
import the encoders. Python runs a package's ``__init__`` before any of its
modules, so the namespaces the encoders import through must never reach back
into the encoders, or the graph re-enters a half-initialized module and every
import in the package would have to go lazy again. These tests pin the rule
statically: no import inside a function, class or ``if`` block anywhere in
the two packages, and the leaf namespace stays fuse-free.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import ipor_fusion.crosschain as crosschain_pkg
import ipor_fusion.fuses.crosschain as fuses_pkg

SRC = Path(crosschain_pkg.__file__).parents[2]
CROSSCHAIN = Path(crosschain_pkg.__file__).parent
FUSES = Path(fuses_pkg.__file__).parent

#: The fuse-free half of ``ipor_fusion.crosschain``: what the encoders may import.
LEAF = {
    "ipor_fusion.crosschain",
    "ipor_fusion.crosschain.contracts",
    "ipor_fusion.crosschain.logs",
    "ipor_fusion.crosschain.messages",
    "ipor_fusion.crosschain.ccip",
    "ipor_fusion.crosschain.ccip.codec",
    "ipor_fusion.crosschain.ccip.contracts",
    "ipor_fusion.crosschain.stargate",
    "ipor_fusion.crosschain.stargate.contracts",
    "ipor_fusion.crosschain.stargate.layerzero",
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_path(name: str) -> Path:
    path = SRC.joinpath(*name.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def _imported(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


MODULES = sorted([*CROSSCHAIN.rglob("*.py"), *FUSES.rglob("*.py")])


@pytest.mark.parametrize("path", MODULES, ids=_module_name)
def test_every_import_is_at_module_level(path: Path):
    tree = ast.parse(path.read_text())
    top = {id(node) for node in tree.body}
    nested = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top
    ]
    assert not nested, f"{_module_name(path)}: imports inside a block at {nested}"


@pytest.mark.parametrize("path", sorted(FUSES.rglob("*.py")), ids=_module_name)
def test_fuse_encoders_import_only_the_leaf_namespace(path: Path):
    offenders = [
        name
        for name in _imported(path)
        if name.startswith("ipor_fusion.crosschain") and name not in LEAF
    ]
    assert not offenders, f"{_module_name(path)} imports {offenders}"


@pytest.mark.parametrize("name", sorted(LEAF))
def test_leaf_namespace_stays_fuse_free(name: str):
    offenders = [
        imported
        for imported in _imported(_module_path(name))
        if imported.startswith("ipor_fusion.fuses.crosschain")
        or (imported.startswith("ipor_fusion.crosschain") and imported not in LEAF)
    ]
    assert not offenders, f"{name} imports {offenders}"
