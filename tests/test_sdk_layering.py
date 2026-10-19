"""The SDK core never depends on its CLI layer, and the MCP server reaches
into it only for the shared user config. ``ipor_fusion.core`` sits below the
fuse encoders and the crosschain packages: they build on it, never the reverse.

The vault read, validation, vault-info and market modules are library API: they must
import without click (the ``cli`` extra) and without loading
``ipor_fusion.cli``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import ipor_fusion.core as core_pkg

CORE_MODULES = [
    "ipor_fusion",
    "ipor_fusion.cache",
    "ipor_fusion.explorer",
    "ipor_fusion.market_info",
    "ipor_fusion.morpho_api",
    "ipor_fusion.readers.unpriceable",
    "ipor_fusion.validation",
    "ipor_fusion.vault_data",
    "ipor_fusion.vault_info",
]

_PROBE = """
import importlib, sys

class _Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] == "click":
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, _Blocked())
importlib.import_module(sys.argv[1])
leaked = sorted(m for m in sys.modules if m.startswith("ipor_fusion.cli"))
assert not leaked, leaked
"""


@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_module_imports_without_the_cli_layer(module: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PROBE, module],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_mcp_server_loads_only_the_cli_config() -> None:
    probe = """
import sys
import ipor_fusion.mcp.server
loaded = sorted(m for m in sys.modules if m.startswith("ipor_fusion.cli"))
assert loaded == ["ipor_fusion.cli", "ipor_fusion.cli.config_store"], loaded
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "path",
    sorted(Path(core_pkg.__file__).parent.glob("*.py")),
    ids=lambda path: path.stem,
)
def test_core_imports_neither_fuses_nor_crosschain(path: Path) -> None:
    imported = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    offenders = [
        name
        for name in imported
        if name.startswith(("ipor_fusion.fuses", "ipor_fusion.crosschain"))
    ]
    assert not offenders, f"core/{path.name} imports {offenders}"
