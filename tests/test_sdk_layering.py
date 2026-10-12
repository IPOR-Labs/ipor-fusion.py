"""The SDK core never depends on its CLI layer.

The vault read, validation and vault-info modules are library API: they must
import without click (the ``cli`` extra) and without loading
``ipor_fusion.cli``.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

CORE_MODULES = [
    "ipor_fusion",
    "ipor_fusion.cache",
    "ipor_fusion.explorer",
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
