#!/usr/bin/env python3
"""Regenerate ``src/ipor_fusion/fusion_errors.py`` from an ipor-fusion checkout.

Collects every ``error`` declaration of the Solidity files reachable by import
from ``contracts/`` (the contracts and the libraries they import, OpenZeppelin
included, resolved through the checkout's ``foundry.toml`` remappings) and
writes them as
canonical ``Name(type,...)`` signatures: enums become ``uint8``, contract,
interface and library types ``address``, ``uint``/``int`` their 256-bit form.
Any other parameter type (a struct, a user-defined value type) stops the run,
because its canonical form needs the compiler.

    uv run python scripts/fusion_error_signatures.py ../ipor-fusion --source <commit>

Augment mode adds errors of contracts that are not in a public commit yet. The
public tuple and its source sentence are read back from the current file and
kept; the ahead-of-upstream tuple is replaced in full on every run, so renamed
or removed errors disappear and a re-run is idempotent:

    uv run python scripts/fusion_error_signatures.py \\
        --ahead <file-or-dir.sol> [--ahead ...] --ahead-label "<public-safe text>"
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
import tomllib
from pathlib import Path

_OUTPUT = Path(__file__).resolve().parent.parent / "src/ipor_fusion/fusion_errors.py"
_IMPORT = re.compile(r'^\s*import\s+(?:[^"\']*\s+from\s+)?["\']([^"\']+)["\']', re.M)
_ERROR = re.compile(r"\berror\s+(\w+)\s*\(([^)]*)\)\s*;")
_ENUM = re.compile(r"\benum\s+(\w+)")
_CONTRACT = re.compile(r"\b(?:abstract\s+)?(?:contract|interface|library)\s+(\w+)")
_ELEMENTARY = re.compile(
    r"^(address|bool|string|bytes([1-9]|[12]\d|3[0-2])?|u?int(8|16|24|32|40|48|56|64"
    r"|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224"
    r"|232|240|248|256)?)$"
)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


class _Resolver:
    """Forge's import resolution: relative paths, then the longest matching
    remapping, then the checkout root and each ``libs`` directory."""

    def __init__(self, root: Path) -> None:
        profile = tomllib.loads((root / "foundry.toml").read_text())["profile"]
        default = profile["default"]
        pairs = [entry.split("=", 1) for entry in default["remappings"]]
        self._root = root
        self._remappings = sorted(pairs, key=lambda pair: -len(pair[0]))
        self._bases = [root, *(root / lib for lib in default["libs"])]

    def __call__(self, spec: str, importer: Path) -> Path:
        if spec.startswith("."):
            return (importer.parent / spec).resolve()
        for prefix, target in self._remappings:
            if spec.startswith(prefix):
                return (self._root / (target + spec.removeprefix(prefix))).resolve()
        for base in self._bases:
            if (base / spec).is_file():
                return (base / spec).resolve()
        return (self._root / spec).resolve()


def _closure(root: Path) -> dict[Path, str]:
    resolve = _Resolver(root)
    pending = sorted((root / "contracts").rglob("*.sol"))
    sources: dict[Path, str] = {}
    while pending:
        path = pending.pop()
        if path in sources:
            continue
        if not path.is_file():
            raise SystemExit(f"unresolved import: {path}")
        text = _COMMENTS.sub("", path.read_text())
        sources[path] = text
        pending.extend(resolve(spec, path) for spec in _IMPORT.findall(text))
    return sources


def _canonical(param: str, enums: set[str], contracts: set[str], where: str) -> str:
    kind = param.split()[0].split(".")[-1]
    if kind in ("uint", "int"):
        return f"{kind}256"
    if _ELEMENTARY.fullmatch(kind):
        return kind
    if kind in enums:
        return "uint8"
    if kind in contracts:
        return "address"
    raise SystemExit(f"{where}: no canonical form for parameter type {kind!r}")


def collect(root: Path) -> list[str]:
    return _signatures(_closure(root))


def collect_ahead(paths: list[Path]) -> list[str]:
    """Error declarations of the given files and directories, without following
    imports; enums and contract types are resolved from those files only."""
    sources: dict[Path, str] = {}
    for path in paths:
        files = sorted(path.rglob("*.sol")) if path.is_dir() else [path]
        if not files and path.is_dir():
            raise SystemExit(f"no Solidity files under {path}")
        for file in files:
            if not file.is_file():
                raise SystemExit(f"not a file: {file}")
            sources[file.resolve()] = _COMMENTS.sub("", file.read_text())
    return _signatures(sources)


def _signatures(sources: dict[Path, str]) -> list[str]:
    enums = {name for text in sources.values() for name in _ENUM.findall(text)}
    contracts = {name for text in sources.values() for name in _CONTRACT.findall(text)}
    signatures: set[str] = set()
    for path, text in sources.items():
        for name, params in _ERROR.findall(text):
            types = [
                _canonical(p.strip(), enums, contracts, f"{path}: {name}")
                for p in params.split(",")
                if p.strip()
            ]
            signatures.add(f"{name}({','.join(types)})")
    return sorted(signatures)


_PUBLIC_TUPLE = re.compile(
    r"^_PUBLIC_SIGNATURES: tuple\[str, \.\.\.\] = \((.*?)\)$", re.M | re.S
)
_SOURCE_SENTENCE = re.compile(r"^Generated by .*? from ipor-fusion (.+?);$", re.M)


def current_public(text: str | None = None) -> tuple[list[str], str]:
    """The public signatures and the source of the current output file."""
    text = _OUTPUT.read_text() if text is None else text
    tuple_match = _PUBLIC_TUPLE.search(text)
    source_match = _SOURCE_SENTENCE.search(text)
    if tuple_match is None or source_match is None:
        raise SystemExit(f"{_OUTPUT}: no public tuple to augment")
    signatures = re.findall(r'"([^"]+)"', tuple_match.group(1))
    return signatures, source_match.group(1)


def _tuple_literal(name: str, signatures: list[str]) -> str:
    """``name: tuple[str, ...] = (...)`` laid out the way ``ruff format`` does:
    an empty or one-element tuple that fits the line stays on one line."""
    head = f"{name}: tuple[str, ...] = "
    if not signatures:
        return f"{head}()"
    if len(signatures) == 1:
        line = f'{head}("{signatures[0]}",)'
        if len(line) <= 88:
            return line
    lines = "\n".join(f'    "{signature}",' for signature in signatures)
    return f"{head}(\n{lines}\n)"


def render(
    signatures: list[str],
    source: str,
    ahead: tuple[str, list[str]] | None = None,
) -> str:
    label, ahead_signatures = ahead if ahead is not None else ("", [])
    overlap = set(signatures) & set(ahead_signatures)
    if overlap:
        raise SystemExit(f"ahead signatures already public: {sorted(overlap)}")
    sentence = ""
    if ahead is not None:
        sentence = (
            "\n"
            + textwrap.fill(
                "Ahead of upstream (replaced by the next regeneration from a public "
                f"commit that has them): {label} ({len(ahead_signatures)} signatures).",
                width=79,
            )
            + "\n"
        )
    return f'''"""Custom errors of the IPOR Fusion contracts and the libraries they import
(OpenZeppelin included), registered by :mod:`ipor_fusion.errors` so revert data
decodes by name (``AccessManagedUnauthorized(0x...)``, ``UnsupportedFuse()``)
instead of a bare selector.

Generated by ``scripts/fusion_error_signatures.py`` from ipor-fusion {source};
do not edit by hand.
{sentence}"""

#: ``Name(type,...)`` signatures of the public commit, sorted; the selector is
#: the keccak of each.
{_tuple_literal("_PUBLIC_SIGNATURES", signatures)}

#: Signatures of contracts not yet in a public commit, sorted and disjoint from
#: the public ones; empty after a full regeneration.
{_tuple_literal("_AHEAD_OF_UPSTREAM_SIGNATURES", ahead_signatures)}

FUSION_ERROR_SIGNATURES: tuple[str, ...] = tuple(
    sorted({{*_PUBLIC_SIGNATURES, *_AHEAD_OF_UPSTREAM_SIGNATURES}})
)
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate fusion_errors.py.")
    parser.add_argument(
        "checkout", type=Path, nargs="?", help="ipor-fusion checkout (full run)"
    )
    parser.add_argument(
        "--source", help="what the checkout is, e.g. a commit (full run)"
    )
    parser.add_argument(
        "--ahead",
        type=Path,
        action="append",
        default=[],
        help="Solidity file or directory with errors not yet public (repeatable)",
    )
    parser.add_argument("--ahead-label", help="public-safe description of --ahead")
    args = parser.parse_args()
    if args.checkout is not None:
        if args.source is None or args.ahead or args.ahead_label:
            parser.error("a full run takes --source and no --ahead options")
        public = collect(args.checkout.resolve())
        _OUTPUT.write_text(render(public, args.source))
        print(f"{len(public)} signatures -> {_OUTPUT}", file=sys.stderr)
        return
    if not args.ahead or not args.ahead_label or args.source:
        parser.error("augment mode needs --ahead and --ahead-label, no --source")
    public, source = current_public()
    ahead = [sig for sig in collect_ahead(args.ahead) if sig not in set(public)]
    _OUTPUT.write_text(render(public, source, (args.ahead_label, ahead)))
    print(f"{len(public)} public + {len(ahead)} ahead -> {_OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
