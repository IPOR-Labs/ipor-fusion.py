#!/usr/bin/env bash
# Shared by the commit-msg hook (.pre-commit-config.yaml) and CI (commit-lint.yml).
#
#   commit-check.sh subject TEXT   conventional format: type(scope)!: subject
#   commit-check.sh leaks          stdin must not match a leak pattern
#   commit-check.sh commit-msg F   hook mode: both checks on a commit message file
#   commit-check.sh staged         hook mode: leak check on the lines a commit adds
#
# Leak patterns: the built-in ones below, plus one extended regex per line from
# $LEAK_PATTERNS (CI, a repository secret) or from the file
# ${XDG_CONFIG_HOME:-~/.config}/ipor/leak-patterns (local). Names that must not
# appear here live only there, since this repository is public.
set -euo pipefail

SUBJECT_PATTERN='^(feat|fix|perf|refactor|test|ci|build|chore|docs|style)(\([a-z0-9._,/-]+\))?!?: .+'
BUILTIN_LEAKS='(^|[^A-Za-z0-9])IL-[0-9]+'

leak_patterns() {
  printf '%s\n' "${BUILTIN_LEAKS}"
  if [ -n "${LEAK_PATTERNS:-}" ]; then
    printf '%s\n' "${LEAK_PATTERNS}"
  else
    local file="${XDG_CONFIG_HOME:-${HOME}/.config}/ipor/leak-patterns"
    [ -f "${file}" ] && cat "${file}"
  fi
  return 0
}

check_subject() {
  printf '%s\n' "$1" | grep -qE "${SUBJECT_PATTERN}" && return 0
  echo "Not in the conventional format (type(scope): subject): $1" >&2
  return 1
}

check_leaks() {
  local patterns hits
  patterns=$(leak_patterns | grep -v '^[[:space:]]*\(#\|$\)')
  hits=$(grep -inE -f <(printf '%s\n' "${patterns}") || true)
  [ -z "${hits}" ] && return 0
  echo "Internal references (Jira keys, internal hosts, private names) do not belong in this public repository:" >&2
  printf '%s\n' "${hits}" >&2
  return 1
}

case "${1:-}" in
  subject) check_subject "$2" ;;
  leaks) check_leaks ;;
  commit-msg)
    # `git commit -v` appends the diff below a scissors line; it is not part
    # of the message (staged mode checks the added lines).
    MESSAGE=$(sed '/^# -* >8 -*$/,$d' "$2" | git stripspace --strip-comments)
    SUBJECT=$(printf '%s\n' "${MESSAGE}" | head -n 1)
    STATUS=0
    # git merge runs this hook too; CI skips merge commits the same way.
    case "${SUBJECT}" in
      "Merge "*|"fixup! "*|"squash! "*) ;;
      *) check_subject "${SUBJECT}" || STATUS=1 ;;
    esac
    printf '%s\n' "${MESSAGE}" | check_leaks || STATUS=1
    exit "${STATUS}"
    ;;
  staged) git diff --cached --no-color -U0 | sed -n '/^+++ /d; s/^+//p' | check_leaks ;;
  *) echo "usage: $0 subject TEXT | leaks | commit-msg FILE | staged" >&2; exit 2 ;;
esac
