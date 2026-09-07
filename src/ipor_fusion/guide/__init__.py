"""Reference text about IPOR Fusion for AI agents and other automated readers.

Four markdown documents ship inside the wheel next to this module — a
glossary, an architecture overview, the invariants whose violation is a
revert, and the quickstart that deploys and operates a vault. Anything that puts text in front of an agent (the bundled MCP server,
a skill, a docs page) reads them from here so there is one copy to keep true.

Plain dataclasses and stdlib only, importable without the optional extras.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

_URI_SCHEME = "fusion"


@dataclass(frozen=True, slots=True)
class GuideResource:
    """One shipped document, addressable as `fusion://<name>`."""

    name: str
    title: str
    description: str
    filename: str
    mime_type: str = "text/markdown"

    @property
    def uri(self) -> str:
        return f"{_URI_SCHEME}://{self.name}"

    @property
    def text(self) -> str:
        return _read(self.filename)


@cache
def _read(filename: str) -> str:
    return resources.files(__package__).joinpath(filename).read_text(encoding="utf-8")


GLOSSARY = GuideResource(
    name="glossary",
    title="IPOR Fusion glossary",
    description=(
        "What a Plasma Vault, a fuse, a balance fuse, a market, a substrate, "
        "the factory proxy, the access manager and the roles are."
    ),
    filename="glossary.md",
)

ARCHITECTURE = GuideResource(
    name="architecture",
    title="IPOR Fusion architecture",
    description=(
        "How a vault, its fuses, markets, oracle and governance fit together; "
        "how vaults are deployed; which operations are read-only and which "
        "need a signing key."
    ),
    filename="architecture.md",
)

INVARIANTS = GuideResource(
    name="invariants",
    title="IPOR Fusion invariants",
    description=(
        "The rules whose violation is a revert — factory proxy not "
        "implementation, roles after clone, fuses then substrates then balance "
        "fuse, the deposit gate — with the revert selectors and the fixes. "
        "Read before writing code that deploys or configures a vault."
    ),
    filename="invariants.md",
)

QUICKSTART = GuideResource(
    name="quickstart",
    title="IPOR Fusion quickstart: deploy and operate a vault",
    description=(
        "The executed walk from nothing to a vault with a live position: "
        "clone from the factory proxy, grant the roles, add fuses, substrates "
        "and the balance fuse, choose the access posture, deposit, execute. "
        "Runnable code plus the factory proxy address for every chain."
    ),
    filename="quickstart.md",
)

RESOURCES: tuple[GuideResource, ...] = (GLOSSARY, ARCHITECTURE, INVARIANTS, QUICKSTART)


def guide_text(name: str) -> str:
    """Return the shipped document called `name` (`glossary`, `architecture`,
    `invariants`, `quickstart`). Raises `KeyError` for any other name."""
    for resource in RESOURCES:
        if resource.name == name:
            return resource.text
    raise KeyError(name)


__all__ = [
    "ARCHITECTURE",
    "GLOSSARY",
    "INVARIANTS",
    "QUICKSTART",
    "RESOURCES",
    "GuideResource",
    "guide_text",
]
