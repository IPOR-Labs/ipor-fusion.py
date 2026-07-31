"""Self-description of the installed package: version and changelog entries.

Plain dataclasses and stdlib only, so this stays importable without the
optional `cli`/`mcp` extras.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from importlib.metadata import PackageNotFoundError, metadata, version
from pathlib import Path

_DISTRIBUTION = "ipor-fusion"
_CHANGELOG_NAME = "CHANGELOG.md"
_REPOSITORY_LABEL = "repository"

# One heading per release, newest first, e.g. `## v3.5.0 (2026-07-30)`.
# The date group is optional so a hand-edited heading still parses.
_VERSION_HEADING = re.compile(r"^## v(\S+?)(?: \(([^)\n]*)\))?[ \t]*$", re.MULTILINE)

_LEADING_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class ChangelogEntry:
    """One release section of the shipped CHANGELOG.md.

    `notes` is the raw markdown between this release's heading and the next
    one, heading excluded — never parsed further. The layout is
    semantic-release's rather than ours and has changed under us before, while
    the only fields worth filtering on (version, date) are already extracted.
    An empty `notes` is normal: a release whose sole commit was its own version
    bump has no bullets.
    """

    version: str
    date: str | None
    notes: str


def package_version() -> str:
    """Installed distribution version, or `"0.0.0"` when not installed."""
    try:
        return version(_DISTRIBUTION)
    except PackageNotFoundError:
        return "0.0.0"


def repository_url() -> str:
    """Repository URL from package metadata, or `""` when it is unavailable.

    Keeps pyproject's `[project.urls]` the single source of the URL: the build
    backend writes each entry into the distribution as `"<label>, <url>"`.
    """
    try:
        entries = metadata(_DISTRIBUTION).get_all("Project-URL") or []
    except PackageNotFoundError:
        return ""
    for entry in entries:
        label, _, url = str(entry).partition(",")
        if label.strip().lower() == _REPOSITORY_LABEL:
            return url.strip()
    return ""


def read_changelog(since_version: str = "") -> list[ChangelogEntry]:
    """Changelog entries, newest first. Never raises; `[]` when unreadable.

    The default returns only the running version's entry, so a caller that
    omits the argument cannot pull the full history by accident. Pass a
    version to get everything strictly newer than it.

    A version with no section yields `[]`; a release whose section is present
    but empty yields one entry with `notes == ""`. Keep those distinguishable
    — the second case is normal, the first means the lookup failed.
    """
    entries = _parse_entries(_changelog_text())
    if not since_version:
        current = package_version()
        return [entry for entry in entries if entry.version == current]
    floor = _version_key(since_version)
    return [entry for entry in entries if _version_key(entry.version) > floor]


def _changelog_text() -> str:
    """CHANGELOG.md from the wheel, falling back to the source checkout."""
    # UnicodeDecodeError is a ValueError, not an OSError — without it a single
    # stray non-UTF-8 byte would propagate out of a function documented never
    # to raise.
    try:
        packaged = resources.files("ipor_fusion") / _CHANGELOG_NAME
        if packaged.is_file():
            return packaged.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ModuleNotFoundError, TypeError):
        pass
    # The wheel force-include does not apply to editable installs or a plain
    # source tree, where the file sits at the repo root.
    try:
        root = Path(__file__).parents[2] / _CHANGELOG_NAME
        return root.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _parse_entries(text: str) -> list[ChangelogEntry]:
    """Slice the file into one entry per version heading, preserving file order."""
    headings = list(_VERSION_HEADING.finditer(text))
    entries: list[ChangelogEntry] = []
    for index, heading in enumerate(headings):
        is_last = index + 1 == len(headings)
        end = len(text) if is_last else headings[index + 1].start()
        entries.append(
            ChangelogEntry(
                version=heading.group(1),
                date=heading.group(2),
                notes=text[heading.end() : end].strip(),
            )
        )
    return entries


def _version_key(raw: str) -> tuple[int, ...]:
    """Segment numbers, ordered by tuple comparison; a digitless segment is 0.

    Compares `3.10.0 > 3.9.0`, which string comparison gets backwards.
    """
    segments = raw.strip().lstrip("v").split(".")
    return tuple(
        int(found.group()) if (found := _LEADING_DIGITS.match(segment)) else 0
        for segment in segments
    )
