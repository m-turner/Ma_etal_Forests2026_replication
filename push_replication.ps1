# push_replication.ps1
#
# Stages and pushes changes in the replication package to the remote git repo.
# Repo: https://github.com/m-turner/Ma_etal_Forests2026_replication
#
# Usage (from project root):
#   .\replication\push_replication.ps1
# Or from within replication/:
#   .\push_replication.ps1
#
# Optional parameters:
#   -Message  "your commit message"   (overrides default)
#   -DryRun                           (shows what would be staged; does not commit or push)

param(
    [string]$Message = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Navigate to the replication folder regardless of where the script was called from.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Replication package push" -ForegroundColor Cyan
Write-Host "  $(Get-Location)" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan

# -------------------------------------------------------
# Show current status before staging anything
# -------------------------------------------------------
Write-Host ""
Write-Host "--- Current status ---" -ForegroundColor Yellow
git status --short

if ($DryRun) {
    Write-Host ""
    Write-Host "DryRun mode -- nothing staged or committed." -ForegroundColor Yellow
    exit 0
}

# -------------------------------------------------------
# Stage all changes (.gitignore handles exclusions)
# -------------------------------------------------------
Write-Host ""
Write-Host "Staging all changes..." -ForegroundColor Yellow
git add -A

Write-Host ""
Write-Host "--- Staged files ---" -ForegroundColor Yellow
git diff --cached --name-status

$stagedCount = (git diff --cached --name-only | Measure-Object -Line).Lines
if ($stagedCount -eq 0) {
    Write-Host ""
    Write-Host "Nothing to commit -- working tree clean." -ForegroundColor Green
    exit 0
}

# -------------------------------------------------------
# Commit message
# -------------------------------------------------------
if ($Message -eq "") {
    Write-Host ""
    $Message = Read-Host "Commit message (Enter for default)"
    if ($Message -eq "") {
        $Message = "Update replication package"
    }
}

# -------------------------------------------------------
# Commit
# -------------------------------------------------------
Write-Host ""
Write-Host "Committing: '$Message'" -ForegroundColor Yellow
git commit -m $Message

# -------------------------------------------------------
# Push
# -------------------------------------------------------
Write-Host ""
Write-Host "Pushing to origin..." -ForegroundColor Yellow
git push

Write-Host ""
Write-Host "Done." -ForegroundColor Green
