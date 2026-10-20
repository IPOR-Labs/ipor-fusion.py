"""Every project type in a public signature has a public import path.

Walks each name of every public ``__all__``: functions, and for classes their
fields, constructor, public methods and properties (inherited from project
base classes too). A project-defined type
(class or ``NewType`` from ``ipor_fusion``) reached through those annotations,
or a project ``TypeAlias`` named in them, must itself be exported, so a caller
can annotate what the SDK hands back without importing an internal module.

Aliases are found by name in string annotations (the modules use
``from __future__ import annotations``); an alias in an eagerly evaluated
annotation is already expanded and is not seen.
"""

import builtins
import functools
import importlib
import inspect
import pkgutil
import re
import typing
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import ipor_fusion

_PUBLIC_PACKAGES = (
    "ipor_fusion",
    "ipor_fusion.config",
    "ipor_fusion.core",
    "ipor_fusion.crosschain",
    "ipor_fusion.crosschain.ccip",
    "ipor_fusion.crosschain.fuses",
    "ipor_fusion.crosschain.stargate",
    "ipor_fusion.fuses",
    "ipor_fusion.readers",
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TYPING_NAMES = frozenset(vars(typing)) | frozenset(vars(builtins))


def _is_project(obj: object) -> bool:
    module = getattr(obj, "__module__", None)
    return isinstance(module, str) and (
        module == "ipor_fusion" or module.startswith("ipor_fusion.")
    )


def _exported() -> dict[str, object]:
    names: dict[str, object] = {}
    for package in _PUBLIC_PACKAGES:
        module = importlib.import_module(package)
        for name in module.__all__:
            names[name] = getattr(module, name)
    return names


def _signatures(obj: object) -> Iterator[tuple[str, Callable[..., object] | type]]:
    """(label, annotated object) pairs to read type hints from."""
    if inspect.isclass(obj):
        yield obj.__qualname__, obj
        members = {
            name: member
            for klass in reversed(obj.__mro__)
            if _is_project(klass)
            for name, member in vars(klass).items()
        }
        for name, member in members.items():
            if name.startswith("_") and name != "__init__":
                continue
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            if isinstance(member, property):
                member = member.fget
            if inspect.isfunction(member) and _is_project(member):
                yield f"{obj.__qualname__}.{name}", member
    elif inspect.isfunction(obj) and _is_project(obj):
        yield obj.__qualname__, obj


def _raw_annotations(target: Callable[..., object] | type) -> Iterator[object]:
    if inspect.isclass(target):
        for klass in target.__mro__:
            if _is_project(klass):
                yield from inspect.get_annotations(klass).values()
    else:
        yield from inspect.get_annotations(target).values()


@functools.cache
def _project_modules() -> tuple[ModuleType, ...]:
    """Every module of the package, imported, whatever other tests loaded."""
    names = [
        info.name
        for info in pkgutil.walk_packages(ipor_fusion.__path__, "ipor_fusion.")
    ]
    return (ipor_fusion, *(importlib.import_module(name) for name in names))


@functools.cache
def _project_namespace() -> dict[str, object]:
    """Every project module's globals, to resolve names imported only under
    ``TYPE_CHECKING``."""
    namespace: dict[str, object] = {}
    for module in _project_modules():
        namespace.update(vars(module))
    return namespace


def _type_hints(target: Callable[..., object] | type) -> dict[str, object]:
    try:
        return typing.get_type_hints(target)
    except NameError:
        return typing.get_type_hints(target, localns=_project_namespace())


def _leaves(hint: object) -> Iterator[object]:
    yield hint
    supertype = getattr(hint, "__supertype__", None)
    if supertype is not None:
        yield from _leaves(supertype)
    for arg in typing.get_args(hint):
        yield from _leaves(arg)


def _referenced(target: Callable[..., object] | type) -> Iterator[tuple[str, object]]:
    """Project types an annotation reaches, by the name they are known under."""
    for hint in _type_hints(target).values():
        for leaf in _leaves(hint):
            if (inspect.isclass(leaf) or hasattr(leaf, "__supertype__")) and (
                _is_project(leaf)
            ):
                yield getattr(leaf, "__name__", repr(leaf)), leaf
    aliases = _project_aliases()
    for raw in _raw_annotations(target):
        if not isinstance(raw, str):
            continue
        for name in _IDENTIFIER.findall(raw):
            if name in aliases:
                yield name, aliases[name]


@functools.cache
def _project_aliases() -> dict[str, object]:
    """Module-level type aliases (``X = Literal[...]``, ``X: TypeAlias = ...``)
    assigned in a project module, not imported into one."""
    aliases: dict[str, object] = {}
    for module in _project_modules():
        source = Path(module.__file__ or "").read_text()
        for name, value in vars(module).items():
            if typing.get_origin(value) is None or name in _TYPING_NAMES:
                continue
            assignment = rf"^{re.escape(name)}\s*(?::\s*TypeAlias\s*)?="
            if re.search(assignment, source, re.MULTILINE):
                aliases[name] = value
    return aliases


def _unreachable() -> list[str]:
    exported = _exported()
    problems: set[str] = set()
    for public_name, obj in exported.items():
        for label, target in _signatures(obj):
            for name, referenced in _referenced(target):
                if name in exported and (
                    exported[name] is referenced or exported[name] == referenced
                ):
                    continue
                problems.add(f"{name} (in {label}, via {public_name})")
    return sorted(problems)


def test_public_signatures_reference_only_exported_types() -> None:
    problems = _unreachable()
    assert not problems, (
        "project types used in public signatures but missing from "
        "ipor_fusion.__all__:\n" + "\n".join(problems)
    )


def test_the_walk_sees_classes_newtypes_and_aliases() -> None:
    reader_hints = dict(_referenced(ipor_fusion.AaveV3Reader.read_position_breakdown))
    finding_hints = dict(_referenced(ipor_fusion.Finding))
    assert (
        reader_hints["AaveV3PositionBreakdown"] is ipor_fusion.AaveV3PositionBreakdown
    )
    assert reader_hints["Web3Context"] is ipor_fusion.Web3Context
    vault_hints = dict(_referenced(ipor_fusion.PlasmaVault.__init__))
    assert vault_hints["PlasmaVaultAddress"] is ipor_fusion.PlasmaVaultAddress
    assert finding_hints["Severity"] == ipor_fusion.Severity
