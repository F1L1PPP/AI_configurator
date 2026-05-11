#!/usr/bin/env bash
set -euo pipefail

COMMIT_MSG="${1:-}"
if [[ -z "$COMMIT_MSG" ]]; then
    echo "CHECKPOINT ABORTED: commit message required" >&2
    exit 1
fi

abort() { echo "CHECKPOINT ABORTED: $1" >&2; exit 1; }

# 1. Lint
echo "-> ruff check ..."
ruff check . || abort "ruff found errors — fix before checkpoint"

# 2. Tests
echo "-> pytest -q ..."
pytest -q || abort "tests failed — fix before checkpoint"

# 3. Commit
echo "-> git add -A && git commit ..."
git add -A
git commit -m "$COMMIT_MSG" || abort "git commit failed"

# 4. Push
echo "-> git push ..."
git push || abort "git push failed"

# 5. Annotated backup tag
TAG="backup-$(date +%Y%m%d-%H%M)"
echo "-> tagging $TAG ..."
git tag -a "$TAG" -m "Daily backup $TAG"
git push origin "$TAG" || abort "tag push failed"

echo "Checkpoint complete: $TAG"
