param(
    [Parameter(Mandatory = $true)]
    [string]$CommitMessage
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Abort($msg) {
    Write-Error "CHECKPOINT ABORTED: $msg"
    exit 1
}

# 1. Lint
Write-Host "-> ruff check ..."
ruff check .
if ($LASTEXITCODE -ne 0) { Abort "ruff found errors — fix before checkpoint" }

# 2. Tests
Write-Host "-> pytest -q ..."
pytest -q
if ($LASTEXITCODE -ne 0) { Abort "tests failed — fix before checkpoint" }

# 3. Commit
Write-Host "-> git add -A && git commit ..."
git add -A
git commit -m $CommitMessage
if ($LASTEXITCODE -ne 0) { Abort "git commit failed" }

# 4. Push
Write-Host "-> git push ..."
git push
if ($LASTEXITCODE -ne 0) { Abort "git push failed" }

# 5. Annotated backup tag
$tag = "backup-" + (Get-Date -Format "yyyyMMdd-HHmm")
Write-Host "-> tagging $tag ..."
git tag -a $tag -m "Daily backup $tag"
git push origin $tag
if ($LASTEXITCODE -ne 0) { Abort "tag push failed" }

Write-Host "Checkpoint complete: $tag"
