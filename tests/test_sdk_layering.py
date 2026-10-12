"""The SDK core never depends on its CLI layer, and the MCP server reaches
into it only for the shared user config.

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


# The market modules leave the CLI in a follow-up change.
MCP_SERVER_CLI_IMPORTS = {
    "ipor_fusion.cli.config_store",
    "ipor_fusion.cli.market_cmd",
    "ipor_fusion.cli.morpho_api",
}


def test_mcp_server_imports_from_the_cli_only_its_config() -> None:
    import ipor_fusion.mcp.server as server

    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("ipor_fusion.cli")
    }
    assert imported <= MCP_SERVER_CLI_IMPORTS, imported - MCP_SERVER_CLI_IMPORTS
