#!/usr/bin/env bash
# Install this repo's git hooks. Idempotent — safe to re-run.
#
# APPENDS to .git/hooks/pre-push rather than overwriting, because that file is
# owned by the harness (it blocks direct pushes to protected branches) and must
# keep working. Re-running REPLACES our marked block rather than skipping it, so
# a change to the hook script list actually propagates to clones that already
# have it installed.
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_DIR="$(git rev-parse --git-common-dir)/hooks"
[[ "$HOOK_DIR" = /* ]] || HOOK_DIR="$REPO_ROOT/$HOOK_DIR"
mkdir -p "$HOOK_DIR"
PRE_PUSH="$HOOK_DIR/pre-push"
BEGIN="# >>> local CI checks"
END="# <<< local CI checks"

chmod +x "$REPO_ROOT/scripts/hooks/pre-push-ci.sh"
[[ -f "$PRE_PUSH" ]] || printf '#!/usr/bin/env bash\n' > "$PRE_PUSH"

if grep -qF "$BEGIN" "$PRE_PUSH"; then
    cleaned="$(awk -v b="$BEGIN" -v e="$END" '
        index($0, b) { skip = 1 }
        !skip        { print }
        index($0, e) { skip = 0 }
    ' "$PRE_PUSH")"
    printf '%s\n' "$cleaned" > "$PRE_PUSH"
    echo "→ replacing existing block in $PRE_PUSH"
fi

cat >> "$PRE_PUSH" <<'HOOK'

# >>> local CI checks (installed by scripts/hooks/install.sh)
# Replaces the pull_request CI trigger — see pre-push-ci.sh.
# Placed LAST so any harness protected-branch check above still runs first.
_ci="$(git rev-parse --show-toplevel)/scripts/hooks/pre-push-ci.sh"
[ -x "$_ci" ] && { "$_ci" || exit 1; }
# <<< local CI checks
HOOK
chmod +x "$PRE_PUSH"
echo "✓ wired into $PRE_PUSH"
