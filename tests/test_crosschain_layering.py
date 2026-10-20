"""Import layering of the crosschain package.

The crosschain market fuse encoders live in ``ipor_fusion.crosschain.fuses``,
so the dependency direction is ``core`` <- ``crosschain``: ``ipor_fusion.fuses``
holds no crosschain code and neither package imports the other.

Inside ``ipor_fusion.crosschain`` the encoders import the wire vocabulary and
wrappers (``messages``, ``ccip.contracts``, ``stargate.layerzero``); lanes,
discovery and the simulator import the encoders. Python runs a package's
``__init__`` before any of its modules, so importing ``ccip.contracts`` also
runs ``crosschain/ccip/__init__``: a namespace an encoder imports through must
not import the encoders back. ``test_import_graph_is_acyclic`` checks that
statically for the whole SDK, modelling those implicit ``__init__`` edges.
Every import in the crosschain package stays at module level, so the graph
is the whole story: no lazy or ``TYPE_CHECKING`` import hides a cycle.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import ipor_fusion
import ipor_fusion.crosschain as crosschain_pkg

PACKAGE = Path(ipor_fusion.__file__).parent
SRC = PACKAGE.parent
CROSSCHAIN = Path(crosschain_pkg.__file__).parent


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


MODULES = {_module_name(path): path for path in sorted(PACKAGE.rglob("*.py"))}


def _ancestors(name: str) -> Iterator[str]:
    parts = name.split(".")
    for end in range(1, len(parts)):
        yield ".".join(parts[:end])


def _top_level_imports(path: Path) -> Iterator[tuple[str, list[str]]]:
    """``(module, names)`` per module-level import; ``names`` is empty for
    ``import x``."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module, [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, []


def _edges(name: str) -> set[str]:
    """The SDK modules whose top level runs, or must have finished, when
    ``name``'s top level runs.

    Importing ``a.b.c`` runs ``a`` and ``a.b`` first. The importer's own
    ancestors have already started, so those implicit edges are harmless and
    skipped; reading a name from a package namespace is an explicit edge and
    kept, ancestor or not, because a half-initialized ``__init__`` lacks it.
    """
    own = set(_ancestors(name))
    edges: set[str] = set()
    for module, names in _top_level_imports(MODULES[name]):
        if module not in MODULES:
            continue
        submodules = [f"{module}.{n}" for n in names if f"{module}.{n}" in MODULES]
        targets = submodules or [module]
        if len(submodules) < len(names):
            edges.add(module)
        for target in targets:
            edges.add(target)
            edges.update(a for a in _ancestors(target) if a not in own)
    edges.discard(name)
    return {edge for edge in edges if edge.startswith("ipor_fusion")}


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph[node]):
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt) :], nxt]
            if nxt not in state and (cycle := visit(nxt)):
                return cycle
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if node not in state and (cycle := visit(node)):
            return cycle
    return None


def _imports_under(prefix: str) -> list[tuple[str, str]]:
    return [
        (name, module)
        for name, path in MODULES.items()
        if name == prefix or name.startswith(f"{prefix}.")
        for module, _ in _top_level_imports(path)
    ]


def test_import_graph_is_acyclic():
    graph = {name: _edges(name) for name in MODULES}
    assert _find_cycle(graph) is None, " -> ".join(_find_cycle(graph) or [])


def test_cycle_detection_sees_a_package_namespace_reentry():
    """The pre-move layout: ``ccip/__init__`` exporting the lane, which needs
    the encoders, which import ``ccip.contracts``."""
    graph = {
        "ipor_fusion.crosschain.ccip": {"ipor_fusion.crosschain.ccip.lane"},
        "ipor_fusion.crosschain.ccip.lane": {"ipor_fusion.crosschain.fuses.ccip"},
        "ipor_fusion.crosschain.fuses.ccip": {"ipor_fusion.crosschain.ccip"},
    }
    assert _find_cycle(graph) == [
        "ipor_fusion.crosschain.ccip",
        "ipor_fusion.crosschain.ccip.lane",
        "ipor_fusion.crosschain.fuses.ccip",
        "ipor_fusion.crosschain.ccip",
    ]


@pytest.mark.parametrize(
    ("importer", "forbidden"),
    [
        ("ipor_fusion.fuses", "ipor_fusion.crosschain"),
        ("ipor_fusion.crosschain", "ipor_fusion.fuses"),
        ("ipor_fusion.core", "ipor_fusion.crosschain"),
        ("ipor_fusion.core", "ipor_fusion.fuses"),
    ],
)
def test_package_does_not_import(importer: str, forbidden: str):
    offenders = [
        (name, module)
        for name, module in _imports_under(importer)
        if module == forbidden or module.startswith(f"{forbidden}.")
    ]
    assert not offenders


def test_fuses_package_holds_no_crosschain_code():
    assert not [name for name in MODULES if name.startswith("ipor_fusion.fuses.cross")]


@pytest.mark.parametrize(
    "path",
    sorted(CROSSCHAIN.rglob("*.py")),
    ids=lambda path: _module_name(path),
)
def test_every_import_is_at_module_level(path: Path):
    tree = ast.parse(path.read_text())
    top = {id(node) for node in tree.body}
    nested = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top
    ]
    assert not nested, f"{_module_name(path)}: imports inside a block at {nested}"
