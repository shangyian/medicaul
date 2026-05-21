#!/usr/bin/env bash
#
# Deploy site/dist/ to the `gh-pages` branch via a temporary git worktree.
# Run from the repo root:
#
#   ./deploy.sh
#
# Requires: a clean (or committed) working tree, a configured `origin` remote.
# After the first push, configure GitHub Pages in repo Settings → Pages →
# Source: Deploy from a branch → Branch: gh-pages, Folder: / (root).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BRANCH="gh-pages"
SRC_DIR="site/dist"
WORKTREE="$(mktemp -d -t medicaul-ghpages-XXXX)"

cleanup() {
    git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
    rm -rf "$WORKTREE" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Building site"
uv run python site/build.py

if [[ ! -d "$SRC_DIR" || -z "$(ls -A "$SRC_DIR" 2>/dev/null)" ]]; then
    echo "error: $SRC_DIR is missing or empty after build" >&2
    exit 1
fi

echo "==> Preparing $BRANCH worktree at $WORKTREE"
if git show-ref --verify --quiet "refs/heads/$BRANCH" \
   || git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    # Branch exists locally or on the remote — fetch latest then check out
    git fetch origin "$BRANCH":"$BRANCH" 2>/dev/null || true
    git worktree add "$WORKTREE" "$BRANCH"
else
    # First-time deploy: create orphan branch with no history
    git worktree add --detach "$WORKTREE"
    (cd "$WORKTREE" && git checkout --orphan "$BRANCH" && git rm -rf . >/dev/null 2>&1 || true)
fi

echo "==> Syncing site/dist/ → worktree"
# --delete so files removed locally are removed from the deploy too
rsync -a --delete --exclude=.git "$SRC_DIR"/ "$WORKTREE"/

cd "$WORKTREE"
git add -A

if git diff --cached --quiet; then
    echo "==> No changes; nothing to deploy."
    exit 0
fi

COMMIT_MSG="deploy $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git -c user.name="medicaul deploy" -c user.email="deploy@medicaul.local" \
    commit -m "$COMMIT_MSG"

echo "==> Pushing $BRANCH to origin"
git push origin "$BRANCH"

echo
echo "Deployed. Site will be available at:"
ORIGIN_URL=$(cd "$REPO_ROOT" && git remote get-url origin)
if [[ "$ORIGIN_URL" =~ github\.com[:/]([^/]+)/([^/.]+) ]]; then
    USER="${BASH_REMATCH[1]}"
    REPO="${BASH_REMATCH[2]}"
    echo "  https://${USER}.github.io/${REPO}/"
fi
echo
echo "(First-time only: enable Pages in repo Settings → Pages → Source: gh-pages branch, root.)"
