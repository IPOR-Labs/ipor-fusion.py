"""Tests for package self-description (version + changelog parsing)."""

import importlib.metadata
import re
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import MagicMock, patch

from ipor_fusion import ChangelogEntry, package_version, read_changelog
from ipor_fusion.about import _changelog_text, _parse_entries, _version_key

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE = """# CHANGELOG

<!-- version list -->

## v3.5.0 (2026-07-30)

### Bug Fixes

- **sdk**: Surface eth_simulateV1 error objects as revert reasons
  ([`4d44f5a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4d44f5a))


## v3.4.1 (2026-07-29)


## v3.4.0 (2026-07-28)

### Features

- **cli**: Add a thing
  ([`abc1234`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/abc1234))


## v0.2.0 (2024-10-29)

- Initial Release
"""

# semantic-release always dates its headings; the parser tolerates one that
# is missing so a hand-edited file still yields entries.
UNDATED = "## v9.9.9\n\n- Hand-written\n"


def _pyproject_version() -> str:
    """Read project.version without tomllib (absent on Python 3.10)."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert found, "pyproject.toml has no top-level version"
    return found.group(1)


class TestPackageVersion:
    def test_matches_distribution_metadata(self):
        assert package_version() == importlib.metadata.version("ipor-fusion")

    @patch("ipor_fusion.about.version", side_effect=PackageNotFoundError)
    def test_falls_back_when_not_installed(self, _version):
        assert package_version() == "0.0.0"


class TestChangelogText:
    """Both lookup paths. Only the fallback runs in a source checkout."""

    def _packaged(self, text: str) -> MagicMock:
        traversable = MagicMock()
        packaged = traversable.__truediv__.return_value
        packaged.is_file.return_value = True
        packaged.read_text.return_value = text
        return traversable

    def test_prefers_the_copy_shipped_in_the_wheel(self):
        with patch("ipor_fusion.about.resources.files") as files:
            files.return_value = self._packaged(SAMPLE)
            assert _changelog_text() == SAMPLE

    def test_falls_back_to_the_checkout_when_not_packaged(self):
        on_disk = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        with patch(
            "ipor_fusion.about.resources.files", side_effect=ModuleNotFoundError
        ):
            assert _changelog_text() == on_disk

    def test_survives_a_file_that_is_not_utf8(self):
        broken = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with (
            patch("ipor_fusion.about.resources.files", side_effect=ModuleNotFoundError),
            patch("pathlib.Path.read_text", side_effect=broken),
        ):
            assert _changelog_text() == ""

    def test_returns_empty_string_when_the_file_is_nowhere(self):
        with (
            patch("ipor_fusion.about.resources.files", side_effect=ModuleNotFoundError),
            patch("pathlib.Path.read_text", side_effect=OSError),
        ):
            assert _changelog_text() == ""


class TestParseEntries:
    def test_splits_on_version_headings(self):
        entries = _parse_entries(SAMPLE)
        assert [entry.version for entry in entries] == [
            "3.5.0",
            "3.4.1",
            "3.4.0",
            "0.2.0",
        ]

    def test_extracts_date_and_leaves_notes_raw(self):
        entry = _parse_entries(SAMPLE)[0]
        assert entry.date == "2026-07-30"
        assert entry.notes.startswith("### Bug Fixes")
        assert "[`4d44f5a`]" in entry.notes
        assert "## v3.4.1" not in entry.notes

    def test_present_section_with_no_body_yields_empty_notes(self):
        entry = next(e for e in _parse_entries(SAMPLE) if e.version == "3.4.1")
        assert entry.notes == ""

    def test_oldest_entry(self):
        entry = _parse_entries(SAMPLE)[-1]
        assert entry == ChangelogEntry("0.2.0", "2024-10-29", "- Initial Release")

    def test_heading_without_date(self):
        assert _parse_entries(UNDATED) == [
            ChangelogEntry("9.9.9", None, "- Hand-written")
        ]

    def test_no_headings(self):
        assert _parse_entries("# CHANGELOG\n\nnothing here\n") == []


class TestReadChangelog:
    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    @patch("ipor_fusion.about.package_version", return_value="3.4.0")
    def test_default_returns_only_the_running_version(self, _version, _text):
        entries = read_changelog()
        assert [entry.version for entry in entries] == ["3.4.0"]

    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    @patch("ipor_fusion.about.package_version", return_value="3.4.1")
    def test_running_version_with_empty_notes_still_returns_an_entry(
        self, _version, _text
    ):
        entries = read_changelog()
        assert len(entries) == 1
        assert entries[0].notes == ""

    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    @patch("ipor_fusion.about.package_version", return_value="9.9.9")
    def test_running_version_absent_from_file_returns_empty_list(self, _version, _text):
        assert read_changelog() == []

    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    def test_since_version_returns_strictly_newer_newest_first(self, _text):
        entries = read_changelog("3.4.0")
        assert [entry.version for entry in entries] == ["3.5.0", "3.4.1"]

    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    def test_since_version_newer_than_everything(self, _text):
        assert read_changelog("4.0.0") == []

    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    def test_since_version_accepts_a_v_prefix(self, _text):
        assert [entry.version for entry in read_changelog("v3.4.1")] == ["3.5.0"]

    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    def test_truncated_since_version_acts_as_a_prefix_floor(self, _text):
        # "3" means the 3.x line and later: (3,) sorts below (3, 0, 0), so
        # 3.0.0 itself counts as newer while 2.9.9 does not.
        assert [entry.version for entry in read_changelog("3")] == [
            "3.5.0",
            "3.4.1",
            "3.4.0",
        ]

    @patch("ipor_fusion.about._changelog_text", return_value="")
    def test_unreadable_file_returns_empty_list(self, _text):
        assert read_changelog() == []
        assert read_changelog("1.0.0") == []

    # Two accepted limitations, pinned so they stay visible: a since_version
    # that is not a version at all widens the result instead of narrowing it,
    # and a prerelease excludes the release it precedes. Only a caller reaches
    # either — the changelog never supplies such a string — and the schema asks
    # for a version, so both were left as they are.
    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    def test_unparseable_since_version_returns_everything(self, _text):
        assert [entry.version for entry in read_changelog("latest")] == [
            "3.5.0",
            "3.4.1",
            "3.4.0",
            "0.2.0",
        ]

    @patch("ipor_fusion.about._changelog_text", return_value=SAMPLE)
    def test_prerelease_since_version_sorts_above_its_own_release(self, _text):
        # "0-rc" yields 0 and the trailing "1" becomes a fourth segment, so
        # (3, 5, 0, 1) > (3, 5, 0) and 3.5.0 itself is excluded.
        assert read_changelog("3.5.0-rc.1") == []


class TestVersionKey:
    def test_orders_numerically_not_lexically(self):
        assert _version_key("3.10.0") > _version_key("3.9.0")

    def test_tolerates_junk_segments(self):
        assert _version_key("3.x.1") == (3, 0, 1)


class TestShippedChangelog:
    """Guard: the running version must be documented in the changelog we ship.

    Catches format drift in semantic-release's output and a changelog that
    stopped being written at release time. One release late by construction —
    the file only changes when a release happens, after CI has run.
    """

    def test_pyproject_version_has_a_changelog_section(self):
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## v{_pyproject_version()} (" in changelog

    def test_read_changelog_finds_the_running_version(self):
        entries = read_changelog()
        assert [entry.version for entry in entries] == [package_version()]

    def test_changelog_is_readable_from_the_installed_package(self):
        assert _changelog_text().startswith("# CHANGELOG")

    def test_wheel_force_includes_the_changelog(self):
        # The checkout fallback in _changelog_text() masks a missing
        # force-include, so assert the packaging config directly.
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "[tool.hatch.build.targets.wheel.force-include]" in pyproject
        assert '"CHANGELOG.md" = "ipor_fusion/CHANGELOG.md"' in pyproject
