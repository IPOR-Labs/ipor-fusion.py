# pylint: disable=import-outside-toplevel
"""Guarded entry point for the ``fusion`` CLI script."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from ipor_fusion.cli.main import main as _main
    except ImportError:
        print(
            "The CLI requires extra dependencies.\n"
            "Install with: pip install 'ipor_fusion[cli]'"
        )
        sys.exit(1)
    _main()
