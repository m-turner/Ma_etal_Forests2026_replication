# push_to_github.ps1
#
# Run this script from the replication/ directory on the HOST (not inside the
# container) to push the replication package to GitHub.
#
# Prerequisites on the host:
#   - git installed and on PATH
#   - GitHub credentials configured (SSH key or credential manager)
#
# Usage:
#   cd C:\path\to\replication
#   .\push_to_github.ps1

$ErrorActionPreference = "Stop"

$REMOTE = "https://github.com/m-turner/Ma_etal_Forests2026_replication.git"
$BRANCH = "main"

# Check we are in the right directory (README.md must exist here)
if (-not (Test-Path "README.md")) {
    Write-Error "Run this script from the replication/ directory."
    exit 1
}

# -----------------------------------------------------------------
# Step 1: initialise git if this is the first push
# -----------------------------------------------------------------
if (-not (Test-Path ".git")) {
    Write-Host "Initialising git repository..."
    git init -b $BRANCH
    git remote add origin $REMOTE
    Write-Host "Git repository initialised and remote set."
} else {
    # Ensure remote is set correctly
    $existing = git remote get-url origin 2>$null
    if ($existing -ne $REMOTE) {
        Write-Host "Updating remote URL to $REMOTE"
        git remote set-url origin $REMOTE
    }
    Write-Host "Using existing git repository."
}

# -----------------------------------------------------------------
# Step 2: stage everything (respects .gitignore)
# -----------------------------------------------------------------
Write-Host "Staging files..."
git add .

# Show a summary of what will be committed
Write-Host ""
Write-Host "Files to be committed:"
git status --short
Write-Host ""

# -----------------------------------------------------------------
# Step 3: commit
# -----------------------------------------------------------------
$msg = "Add replication package: Python BJS scripts, updated did_lib, figures, README"
Write-Host "Committing: $msg"
git commit -m $msg

# -----------------------------------------------------------------
# Step 4: push
# -----------------------------------------------------------------
Write-Host "Pushing to $REMOTE ($BRANCH)..."

# -u sets the upstream tracking branch on first push;
# subsequent pushes can use plain `git push`.
git push -u origin $BRANCH

Write-Host ""
Write-Host "Done. Replication package is live at:"
Write-Host "  https://github.com/m-turner/Ma_etal_Forests2026_replication"
