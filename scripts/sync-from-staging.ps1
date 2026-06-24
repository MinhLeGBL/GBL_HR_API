# sync-from-staging.ps1
# Sync the current feature branch with the latest staging, keeping ONLY this
# branch's own docs/cr file. Drops every other feature's docs/cr/* that the
# staging merge might pull in (defensive — the docs-janitor workflow should
# already keep staging clean). Idempotent.
#
# Backend note: only docs/cr/ is branch-isolated. The backend's per-module
# change log lives at app/modules/<x>/CHANGELOG.md (persistent on every branch
# and into deployment) — this script does NOT touch it.
#
# Run on a feature/* branch (including a freshly-created one) after which you can
# review and `git push origin <branch>`.

$ErrorActionPreference = 'Stop'

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -notlike 'feature/*') { throw "Run on a feature/* branch (current: $branch)" }

# Feature key: strip 'feature/', lowercase, drop trailing -cr-<n> or -<semver>.
$key = ($branch -replace '^feature/', '').ToLower()
$key = $key -replace '-cr-\d+$', '' -replace '-\d+(\.\d+)*$', ''
Write-Host "Feature key: $key"

git fetch origin staging
git merge origin/staging --no-edit

if (Test-Path docs/cr) {
  Get-ChildItem docs/cr -Filter *.md | ForEach-Object {
    if ($_.BaseName -ne $key) {
      git rm -q ($_.FullName -replace '\\', '/') | Out-Null   # keeps docs/cr/$key.md
    }
  }
}

git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
  git commit -m "chore(sync): keep only $key CR docs after staging merge"
  Write-Host "Stripped foreign docs. Review, then: git push origin $branch"
} else {
  Write-Host "Sync clean; nothing to strip."
}
