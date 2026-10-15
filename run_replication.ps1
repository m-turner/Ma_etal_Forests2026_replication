# run_replication.ps1
#
# Runs the full replication pipeline for:
#   "The Effect of China's National Forest City Program on Urban Heat Is Not
#    Robustly Identified" -- Comment on Ma, Geng, and Zhong (2026, Forests)
#
# Steps executed:
#   1  City-level BJS                (environment_stats, ~5-15 min)
#   3  Station-level BJS             (environment_stats, ~5-15 min)
#   2  City-level CS/DR via Stata    (environment_geo + Stata, ~30-60 min;
#                                     skipped if Stata not found)
#   T  Table 1 assembly              (environment_stats, <1 min)
#   C  Numeric comparison vs paper LaTeX values
#
# Usage (from project root):
#   .\run_replication.ps1
#
# Parameters can be overridden on the command line:
#   -Conda    conda executable name  (default: "conda"; change to "mamba")
#   -StataExe full path to Stata     (default: C:\Program Files\Stata18\StataMP-64.exe)
#
# Example with mamba:
#   .\run_replication.ps1 -Conda mamba

param(
    [string]$Conda    = "conda",
    [string]$StataExe = "C:\Program Files\Stata18\StataMP-64.exe"
)

$ErrorActionPreference = "Continue"

# Resolve project root from the script's own location so the script works
# regardless of the caller's working directory.
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
Set-Location $ProjectRoot

$EnvStats = "environment_stats"
$EnvGeo   = "environment_geo"

$StepResults = [ordered]@{}

# ---------------------------------------------------------------------------
# Helper: run one step, return $true/$false
# ---------------------------------------------------------------------------
function Invoke-Step {
    param(
        [string]   $Name,
        [string[]] $Cmd
    )
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host "  $Name" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor Cyan

    $sw  = [System.Diagnostics.Stopwatch]::StartNew()
    $exe = $Cmd[0]
    if ($Cmd.Length -gt 1) {
        $rest = $Cmd[1..($Cmd.Length - 1)]
    } else {
        $rest = @()
    }
    & $exe @rest
    $sw.Stop()
    $ok      = ($LASTEXITCODE -eq 0)
    $elapsed = [int]$sw.Elapsed.TotalSeconds

    if ($ok) {
        Write-Host "PASSED  [${elapsed}s]" -ForegroundColor Green
    } else {
        Write-Host "FAILED  exit=$LASTEXITCODE  [${elapsed}s]" -ForegroundColor Red
    }
    return $ok
}

# ---------------------------------------------------------------------------
# Step 1: City-level BJS (environment_stats)
# ---------------------------------------------------------------------------
$StepResults["1: city BJS"] = Invoke-Step `
    "Step 1  City-level BJS -- code\run_analysis\run_analysis_v6.py" `
    @($Conda, "run", "--no-capture-output", "-n", $EnvStats,
      "python", "code\run_analysis\run_analysis_v6.py")

# ---------------------------------------------------------------------------
# Step 3: Station-level BJS (environment_stats)
# ---------------------------------------------------------------------------
$StepResults["3: station BJS"] = Invoke-Step `
    "Step 3  Station-level BJS -- code\run_station_analysis\run_station_analysis_v5.py" `
    @($Conda, "run", "--no-capture-output", "-n", $EnvStats,
      "python", "code\run_station_analysis\run_station_analysis_v5.py")

# ---------------------------------------------------------------------------
# Step 2: City-level CS/DR via Stata (environment_geo; skip if Stata absent)
# ---------------------------------------------------------------------------
if (Test-Path $StataExe) {
    $StepResults["2: Stata CS/DR"] = Invoke-Step `
        "Step 2  City-level CS/DR via Stata -- code\run_analysis_stata\run_analysis_stata_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\run_analysis_stata\run_analysis_stata_v1.py")
} else {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Yellow
    Write-Host ("  Step 2  Stata CS/DR -- SKIPPED  (Stata not found at:" ) -ForegroundColor Yellow
    Write-Host ("    $StataExe)") -ForegroundColor Yellow
    Write-Host ("=" * 70) -ForegroundColor Yellow
    $StepResults["2: Stata CS/DR"] = $null   # $null = skipped (not failed)
}

# ---------------------------------------------------------------------------
# Step 2b: Station-level CS/DR via Stata (skip if Stata absent)
# Produces Figure 3 and the station aggregate ATTs used in the paper.
# ---------------------------------------------------------------------------
if (Test-Path $StataExe) {
    $StepResults["2b: Stata station CS"] = Invoke-Step `
        "Step 2b  Station CS/DR via Stata -- code\run_station_analysis_stata\run_station_analysis_stata_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\run_station_analysis_stata\run_station_analysis_stata_v1.py")
} else {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Yellow
    Write-Host ("  Step 2b  Stata station CS -- SKIPPED  (Stata not found)") -ForegroundColor Yellow
    Write-Host ("=" * 70) -ForegroundColor Yellow
    $StepResults["2b: Stata station CS"] = $null
}

# ---------------------------------------------------------------------------
# Table 1 assembly
# ---------------------------------------------------------------------------
$StepResults["T: Table 1"] = Invoke-Step `
    "Table 1  paper\compute_table1.py" `
    @($Conda, "run", "--no-capture-output", "-n", $EnvStats,
      "python", "paper\compute_table1.py")

# ---------------------------------------------------------------------------
# Numeric comparison: fresh outputs vs paper LaTeX values
#
# Parses Table 1 values from the paper's LaTeX source and compares them
# against fresh estimation outputs.  No git baseline needed.
# ---------------------------------------------------------------------------
$StepResults["C: compare outputs"] = Invoke-Step `
    "Compare  fresh outputs vs paper/comment_nfcp.tex Table 1" `
    @($Conda, "run", "--no-capture-output", "-n", $EnvStats,
      "python", "code\compare_paper\compare_paper_numbers_v1.py")

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 70) -ForegroundColor Cyan
Write-Host "  SUMMARY" -ForegroundColor Cyan
Write-Host ("=" * 70) -ForegroundColor Cyan

$allPassed = $true
foreach ($key in $StepResults.Keys) {
    $val = $StepResults[$key]
    if ($null -eq $val) {
        Write-Host ("  {0,-26}  SKIPPED" -f $key) -ForegroundColor Yellow
    } elseif ($val -eq $true) {
        Write-Host ("  {0,-26}  PASSED" -f $key) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-26}  FAILED" -f $key) -ForegroundColor Red
        $allPassed = $false
    }
}

Write-Host ""
if ($allPassed) {
    Write-Host "Replication check passed." -ForegroundColor Green
} else {
    Write-Host "One or more steps failed -- see output above." -ForegroundColor Red
}

Write-Host ""
Write-Host "Run logs written to project root:" -ForegroundColor Cyan
foreach ($log in @("last_run_analysis.log", "last_run_station_analysis.log",
                   "last_run_stata_city.log")) {
    $p = Join-Path $ProjectRoot $log
    if (Test-Path $p) { Write-Host "  $p" }
}
