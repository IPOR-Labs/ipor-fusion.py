"""`fusion changelog` — release notes of the installed package."""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from ipor_fusion.about import ChangelogEntry, package_version, read_changelog

# The CLI's only top-level leaf command: every other one is a group, because
# every other one has more than one verb. Named after the data it prints, like
# `vault info` and `config show` — which is also why there is no
# `fusion server_info` mirroring the MCP tool of that name, and no
# `fusion version` duplicating the existing `--version` flag.


@click.command("changelog")
@click.option(
    "--since",
    metavar="VERSION",
    default="",
    help="Show releases newer than this version, e.g. 3.1.0 "
    "(--since 0 for the full history). Default: the installed version only.",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def changelog(since: str, json_output: bool) -> None:
    """Show release notes for the installed ipor-fusion package."""
    try:
        entries = read_changelog(since)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="'--since'") from exc
    if json_output:
        click.echo(json.dumps([asdict(entry) for entry in entries], indent=2))
        return
    if not entries:
        click.echo(_nothing_to_show(since))
        return
    for index, entry in enumerate(entries):
        if index:
            click.echo()
        _print_entry(entry)


def _nothing_to_show(since: str) -> str:
    """Distinguish "nothing is newer" from "the running version has no section".

    The second case means the lookup failed — the changelog was unreadable, or
    it predates the running version (an unreleased working tree).
    """
    if since:
        return f"(no releases newer than {since})"
    return f"(no changelog entry for version {package_version()})"


def _print_entry(entry: ChangelogEntry) -> None:
    date = f" ({entry.date})" if entry.date else ""
    click.secho(f"v{entry.version}{date}", bold=True)
    click.echo()
    # Printed verbatim: it is semantic-release's markdown, and indenting it
    # would break fenced blocks. An empty body is normal — a release whose only
    # commit was its own version bump has no bullets.
    click.echo(entry.notes or "(no release notes)")
