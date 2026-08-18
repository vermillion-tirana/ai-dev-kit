#!/usr/bin/env bash
# Run this repo's ruff lint + format check locally on pre-push, blocking on failure.
#
# This REPLACES the pull_request trigger on .github/workflows/ci.yml (narrowed
# 2026-08-18). The flags below are copied from that workflow verbatim so the two
# cannot drift.
#
# WHY. The vermillion-tirana org is on GitHub Free: 2,000 Actions minutes/month
# shared across all 68 private repos. August 2026 exhausted the allowance on the
# 17th, which killed EVERY workflow in the org. Once it is gone a job cannot
# START, and a job that cannot start still reports a RED check — so a PR trigger
# fails every PR having examined nothing.
#
# Note this repo's workflow had never actually run: it declared
# `runs-on: linux-ubuntu-latest`, which is not a valid GitHub runner label
# (the label is `ubuntu-latest`). Zero runs recorded. So these checks were
# nominally enforced and in practice never executed — running them here is the
# first time they bite.
#
# Escape hatch: CI_SKIP=1 git push ...
set -uo pipefail

[[ -n "${CI_SKIP:-}" ]] && exit 0

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$REPO_ROOT" || exit 0

# `uv tool run`, not `uvx`: the standalone uvx binary at ~/.local/bin/uvx looks
# for a `uv` sibling that isn't there on this machine (uv is Homebrew's, and the
# `uv` on PATH is a plugin shim), so uvx dies with "Could not find the uv binary".
# `uv tool run` resolves through the uv actually in use.
if ! command -v uv >/dev/null 2>&1; then
    printf '\n⚠️  uv not found — skipping ruff. This push is UNCHECKED.\n\n' >&2
    exit 0
fi

TARGETS=(databricks-tools-core/ databricks-mcp-server/ .test/src/)
for t in "${TARGETS[@]}"; do
    [[ -e "$t" ]] || { printf '\n⚠️  %s missing — skipping ruff.\n\n' "$t" >&2; exit 0; }
done

fail() {
    printf 'FAILED\n\n%s\n\n' "$(tail -40 <<<"$2")" >&2
    printf '🚫 Push blocked by %s.\n' "$1" >&2
    printf '   Fix it, or bypass with: CI_SKIP=1 git push ...\n\n' >&2
    exit 1
}

printf '🔍 ruff check… ' >&2
OUT="$(uv tool run ruff check \
    --select=E,F,B,PIE \
    --ignore=E401,E402,F401,F403,B017,B904,ANN,TCH \
    --line-length=120 \
    --target-version=py311 \
    "${TARGETS[@]}" 2>&1)" || fail "ruff check" "$OUT"
printf 'ok\n' >&2

printf '🔍 ruff format… ' >&2
OUT="$(uv tool run ruff format --check \
    --line-length=120 \
    --target-version=py311 \
    "${TARGETS[@]}" 2>&1)" || fail "ruff format" "$OUT"
printf 'ok\n' >&2
