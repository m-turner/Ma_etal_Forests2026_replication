# build_environments.ps1
#
# Creates all three conda environments needed to run the full pipeline.
# Run once after cloning the repository.
#
# Usage (from replication root):
#   .\build_environments.ps1
#
# Environments:
#   environment_stats  -- BJS and CS/DR estimation (Python; Steps 1, 3 and Table 1)
#   environment_geo    -- Geospatial panel building, MODIS download, Stata orchestration
#                         (Python + GDAL/geopandas/rasterstats; Steps 0b-5, 8a-8e)
#   environment_data   -- HDF4 conversion ONLY (pyhdf conflicts with environment_geo)
#                         (Steps 3, 3b, 3d)
#
# If you are running the estimation-only quick start (Tier 1), you only need
# environment_stats and, if using Stata CS/DR, environment_geo.

param([string]$Conda = "conda")

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
Set-Location $ProjectRoot

Write-Host ""
Write-Host "Building conda environments for NFCP replication package" -ForegroundColor Cyan
Write-Host ""

function New-Env {
    param([string]$YmlFile, [string]$Name)
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  Creating: $Name  (from $YmlFile)" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
    & $Conda env create -f $YmlFile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED to create $Name  (exit $LASTEXITCODE)" -ForegroundColor Red
        Write-Host "If the environment already exists, remove it first:" -ForegroundColor Yellow
        Write-Host "  conda env remove -n $Name" -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
    Write-Host "Created: $Name" -ForegroundColor Green
    Write-Host ""
}

New-Env "environment_stats.yml" "environment_stats"
New-Env "environment_geo.yml"   "environment_geo"
New-Env "environment_data.yml"  "environment_data"

Write-Host "All environments created." -ForegroundColor Green
Write-Host ""
Write-Host "Verify with:" -ForegroundColor Cyan
Write-Host "  conda env list" -ForegroundColor Gray
