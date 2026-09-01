import json
from unittest.mock import patch

from click.testing import CliRunner

from ipor_fusion.about import ChangelogEntry
from ipor_fusion.cli.main import cli

ENTRY_LATEST = ChangelogEntry(
    version="3.5.0",
    date="2026-07-30",
    notes="### Features\n\n* **mcp**: add a server_info tool",
)
ENTRY_OLDER = ChangelogEntry(
    version="3.4.1",
    date="2026-07-25",
    notes="### Bug Fixes\n\n* **sdk**: export MerklClaimWrapperFuse",
)
ENTRY_EMPTY = ChangelogEntry(version="3.4.0", date="2026-07-20", notes="")
ENTRY_UNDATED = ChangelogEntry(version="3.3.1", date=None, notes="* something")


class TestChangelog:
    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_default_shows_the_running_version_only(self, mock_read):
        mock_read.return_value = [ENTRY_LATEST]

        result = CliRunner().invoke(cli, ["changelog"])

        assert result.exit_code == 0
        mock_read.assert_called_once_with("")
        assert "v3.5.0 (2026-07-30)" in result.output
        assert "add a server_info tool" in result.output

    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_since_is_passed_through(self, mock_read):
        mock_read.return_value = [ENTRY_LATEST, ENTRY_OLDER]

        result = CliRunner().invoke(cli, ["changelog", "--since", "3.4.0"])

        assert result.exit_code == 0
        mock_read.assert_called_once_with("3.4.0")
        assert "v3.5.0" in result.output
        assert "v3.4.1" in result.output

    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_entries_are_blank_line_separated(self, mock_read):
        mock_read.return_value = [ENTRY_LATEST, ENTRY_OLDER]

        result = CliRunner().invoke(cli, ["changelog", "--since", "3.4.0"])

        assert "add a server_info tool\n\nv3.4.1 (2026-07-25)" in result.output

    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_empty_notes_render_a_placeholder(self, mock_read):
        mock_read.return_value = [ENTRY_EMPTY]

        result = CliRunner().invoke(cli, ["changelog"])

        assert result.exit_code == 0
        assert "v3.4.0 (2026-07-20)" in result.output
        assert "(no release notes)" in result.output

    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_missing_date_omits_the_parentheses(self, mock_read):
        mock_read.return_value = [ENTRY_UNDATED]

        result = CliRunner().invoke(cli, ["changelog"])

        assert "v3.3.1\n" in result.output
        assert "(None)" not in result.output

    @patch("ipor_fusion.cli.changelog_cmd.package_version")
    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_no_entry_for_the_running_version(self, mock_read, mock_version):
        mock_read.return_value = []
        mock_version.return_value = "9.9.9"

        result = CliRunner().invoke(cli, ["changelog"])

        assert result.exit_code == 0
        assert "(no changelog entry for version 9.9.9)" in result.output

    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_nothing_newer_than_since(self, mock_read):
        mock_read.return_value = []

        result = CliRunner().invoke(cli, ["changelog", "--since", "3.5.0"])

        assert result.exit_code == 0
        assert "(no releases newer than 3.5.0)" in result.output

    @patch(
        "ipor_fusion.cli.changelog_cmd.read_changelog",
        side_effect=ValueError("'latest' is not a valid version"),
    )
    def test_invalid_since_is_a_usage_error(self, _mock_read):
        result = CliRunner().invoke(cli, ["changelog", "--since", "latest"])

        assert result.exit_code == 2
        assert "not a valid version" in result.output
        assert "--since" in result.output


class TestChangelogJson:
    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_json_carries_every_field(self, mock_read):
        mock_read.return_value = [ENTRY_LATEST, ENTRY_UNDATED]

        result = CliRunner().invoke(cli, ["changelog", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == [
            {
                "version": "3.5.0",
                "date": "2026-07-30",
                "notes": "### Features\n\n* **mcp**: add a server_info tool",
            },
            {"version": "3.3.1", "date": None, "notes": "* something"},
        ]

    @patch("ipor_fusion.cli.changelog_cmd.read_changelog")
    def test_json_empty_stays_machine_readable(self, mock_read):
        mock_read.return_value = []

        result = CliRunner().invoke(cli, ["changelog", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []


class TestChangelogIntegration:
    def test_reads_the_real_changelog(self):
        """No mocks: the shipped CHANGELOG.md must be reachable from the CLI."""
        result = CliRunner().invoke(cli, ["changelog", "--since", "0", "--json"])

        assert result.exit_code == 0
        entries = json.loads(result.output)
        assert len(entries) > 1
        assert all(entry["version"] for entry in entries)
