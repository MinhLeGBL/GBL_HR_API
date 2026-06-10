#!/usr/bin/env bash
# sync-from-staging.sh — POSIX twin of sync-from-staging.ps1.
# Sync the current feature branch with latest staging, keeping ONLY this
# branch's own docs/cr file. Idempotent.
#
# Backend note: only docs/cr/ is branch-isolated. The backend's per-module
# change log lives at app/modules/<x>/CHANGELOG.md (persistent on every branch
# and into deployment) — this script does NOT touch it.
set -euo pipefail

branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$branch" == feature/* ]] || { echo "Run on a feature/* branch (current: $branch)"; exit 1; }

# Feature key: strip 'feature/', lowercase, drop trailing -cr-<n> or -<semver>.
key="${branch#feature/}"
key="$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]' | sed -E 's/-cr-[0-9]+$//; s/-[0-9]+(\.[0-9]+)*$//')"
echo "Feature key: $key"

git fetch origin staging
git merge origin/staging --no-edit

shopt -s nullglob
for f in docs/cr/*.md; do
  b="$(basename "$f" .md)"
  [ "$b" != "$key" ] && git rm -q "$f"   # keeps docs/cr/$key.md
done

git add -A
if git diff --cached --quiet; then
  echo "Sync clean; nothing to strip."
else
  git commit -m "chore(sync): keep only $key CR docs after staging merge"
  echo "Stripped foreign docs. Review, then: git push origin $branch"
fi
