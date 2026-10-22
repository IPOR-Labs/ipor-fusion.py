"""Golden outputs: the exact text a CLI command or an MCP tool emits, pinned in
``tests/fixtures/*_golden/``.

Regenerate them only when an output change is intended, then review the diff:

    UPDATE_GOLDEN=1 uv run pytest -k golden --no-cov

Without ``UPDATE_GOLDEN`` (the default) a golden is only compared, never written.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from mcp.types import TextContent
from pydantic import BaseModel

from ipor_fusion.mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"

# The instant the goldens are rendered at: ages ("N days ago", `age_days`)
# are relative to it.
FROZEN_NOW = datetime(2026, 10, 1, tzinfo=UTC)


class FrozenDatetime(datetime):
    """`datetime` whose `now()` is `FROZEN_NOW`; patched over a module's
    `datetime` import so its rendering does not depend on the wall clock."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> FrozenDatetime:
        return cls.fromtimestamp(FROZEN_NOW.timestamp(), tz)


def assert_golden(path: Path, text: str) -> None:
    """Compare ``text`` with the golden at ``path`` byte for byte; write it
    instead when ``UPDATE_GOLDEN`` is set."""
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.exists(), f"missing golden {path}; generate it with UPDATE_GOLDEN=1"
    assert text == path.read_text(encoding="utf-8"), (
        f"output differs from {path}; if the change is intended, regenerate "
        "with UPDATE_GOLDEN=1 and review the diff"
    )


def mcp_tool_text(
    tool: str, arguments: dict[str, object], model: type[BaseModel]
) -> str:
    """Call ``tool`` through the MCP server, as a client does, and return the
    text it emits. Checks that the text is ``model_dump_json(indent=2)`` of the
    tool's response model and carries the same document as its structured
    content."""
    result = asyncio.run(mcp.call_tool(tool, arguments))
    # A tool with a typed return answers (content blocks, structured content).
    assert isinstance(result, tuple) and len(result) == 2
    content, structured = result
    assert isinstance(content, list) and len(content) == 1
    block = content[0]
    assert isinstance(block, TextContent)
    assert json.loads(block.text) == structured
    assert block.text == model.model_validate(structured).model_dump_json(indent=2)
    return block.text
