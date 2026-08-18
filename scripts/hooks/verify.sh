#!/usr/bin/env bash
# Confirm the local CI hook is present, executable and actually wired in.
set -uo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_DIR="$(git rev-parse --git-common-dir)/hooks"
[[ "$HOOK_DIR" = /* ]] || HOOK_DIR="$REPO_ROOT/$HOOK_DIR"
rc=0
if [[ -x "$REPO_ROOT/scripts/hooks/pre-push-ci.sh" ]]; then
    echo "✓ pre-push-ci.sh present and executable"
else
    echo "✗ pre-push-ci.sh missing or not executable"; rc=1
fi
if grep -q 'scripts/hooks/pre-push-ci.sh' "$HOOK_DIR/pre-push" 2>/dev/null; then
    echo "✓ wired into $HOOK_DIR/pre-push"
else
    echo "✗ NOT wired into $HOOK_DIR/pre-push — run scripts/hooks/install.sh"; rc=1
fi
command -v uv >/dev/null 2>&1 && echo "✓ uv available" || { echo "✗ uv not found"; rc=1; }
exit $rc
