# pylint: disable=import-outside-toplevel
"""Guarded entry point for the ``fusion-mcp`` server script."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from ipor_fusion.mcp.server import main as _main
    except ImportError:
        print(
            "The MCP server requires extra dependencies.\n"
            "Install with: pip install 'ipor_fusion[mcp]'"
        )
        sys.exit(1)
    _main()
