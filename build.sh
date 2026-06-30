#!/usr/bin/env bash
#
# Install dependencies and run all checks: format, lint, type check, build, tests.
# Mirrors the CI pipeline (.github/workflows/python-build.yml).
#
# Extra arguments are forwarded to pytest, e.g.:
#   ./build.sh -m "cli or mcp"      # offline tests only
#
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Installing dependencies (uv sync --all-extras --locked)"
uv sync --all-extras --locked

echo "==> Format check (ruff format --check)"
uv run ruff format --check ./

echo "==> Lint (ruff check)"
uv run ruff check ./

echo "==> Type check (pyright)"
uv run pyright

echo "==> Build package (uv build)"
uv build

echo "==> Tests (pytest)"
uv run pytest "$@"

echo "==> All checks passed."
