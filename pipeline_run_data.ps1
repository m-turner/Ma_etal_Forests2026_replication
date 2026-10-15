# pipeline_run_data.ps1
#
# Runs the full data-construction pipeline for:
#   "The Effect of China's National Forest City Program on Urban Heat Is Not
#    Robustly Identified" -- Comment on Ma, Geng, and Zhong (2026, Forests)
#
# Covers all data-acquisition, conversion, panel-building, and merging steps.
# After this pipeline finishes, run: .\run_replication.ps1
#
# Usage (from replication root):
#   .\pipeline_run_data.ps1
#
# Parameters can be overridden on the command line:
#   -Conda    conda executable name  (default: "conda"; change to "mamba")
#
# Example:
#   .\pipeline_run_data.ps1 -Conda mamba
#
# ============================================================================
# STEP NOTES
# ============================================================================
# All switches default to OFF. Enable only the steps you need to run.
#
# Step 0b  Build city universe from UN WUP + NFC list.
#          Run once, or again when nfc_treatment_list.csv changes.
#
# Step 0c  Build city covariates (lat, lon, pre-treatment population).
#          Run after 0b if cities_full.csv changes.
#
# Step 1   Create city buffer GeoJSONs (5/10/20/50 km circles).
#          Run after 0b if cities_full.csv changes.
#
# Step 1b  Create prefecture boundary GeoJSON from pf05.shp.
#          Run ONCE after placing pf05.shp in data/create_city_buffers/source/.
#
# Step 2   Download MODIS LST HDF4 tiles from NASA Earthdata.
# Step 2b  Download MODIS Tree Cover HDF4 tiles.
# Step 2d  Download MODIS NDVI HDF4 tiles.
# Step 2e  Download CLCD GeoTIFFs from Zenodo (RUN ONCE -- static dataset).
#
# Steps 3/3b/3d  Convert HDF4 tiles to numpy arrays.
#          Requires environment_data (pyhdf). Run after download steps.
#
# Steps 4/4b/4d/4e  Build per-outcome panels from arrays.
#          Requires environment_geo. Run after convert steps.
#
# Step 5   Merge all outcome panels -> data/merge_panels/generated/city_year_panel.*
#          Run after steps 4/4b/4d/4e.
#
# Step 8a  One-time download of GHCN-Daily China station data.
# Step 8b  Build station temperature panel.
# Step 8c  Create station disk buffers (100m + 500m).
# Step 8d  Extract satellite variables at station locations (~20-40 min).
# Step 8e  Merge station temperature + satellite panels.
# ============================================================================

param([string]$Conda = "conda")

$ErrorActionPreference = "Continue"

# Step switches -- all default OFF; enable the steps you need
$STEP_0B  = (1 -eq 0)   # Build city universe from UN WUP + NFC list
$STEP_0C  = (1 -eq 0)   # Build city covariates
$STEP_1   = (1 -eq 0)   # Create city buffers
$STEP_1B  = (1 -eq 0)   # Create prefecture boundary GeoJSON (run once)
$STEP_2   = (1 -eq 0)   # Download MODIS LST (~300 GB; NASA Earthdata login required)
$STEP_2B  = (1 -eq 0)   # Download MODIS Tree Cover
$STEP_2D  = (1 -eq 0)   # Download MODIS NDVI
$STEP_2E  = (1 -eq 0)   # Download CLCD from Zenodo (run once)
$STEP_3   = (1 -eq 0)   # Convert LST HDF4 to arrays (environment_data)
$STEP_3B  = (1 -eq 0)   # Convert Tree Cover HDF4 to arrays (environment_data)
$STEP_3D  = (1 -eq 0)   # Convert MODIS NDVI HDF4 to arrays (environment_data)
$STEP_4   = (1 -eq 0)   # Build LST panel (environment_geo)
$STEP_4B  = (1 -eq 0)   # Build Tree Cover panel (environment_geo)
$STEP_4D  = (1 -eq 0)   # Build MODIS NDVI panel (environment_geo)
$STEP_4E  = (1 -eq 0)   # Build CLCD panel (environment_geo)
$STEP_5   = (1 -eq 0)   # Merge panels (environment_stats)
$STEP_8A  = (1 -eq 0)   # Download GHCN-Daily China station data (run once)
$STEP_8B  = (1 -eq 0)   # Build station temperature panel (environment_geo)
$STEP_8C  = (1 -eq 0)   # Create station disk buffers (environment_geo)
$STEP_8D  = (1 -eq 0)   # Extract satellite at station locations (~20-40 min, environment_geo)
$STEP_8E  = (1 -eq 0)   # Merge station panels (environment_geo)

# ============================================================================

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
Set-Location $ProjectRoot

$EnvGeo   = "environment_geo"
$EnvStats = "environment_stats"
$EnvData  = "environment_data"

$StepResults = [ordered]@{}

function Invoke-Step {
    param([string]$Name, [string[]]$Cmd)
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host "  $Name" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor Cyan
    $sw  = [System.Diagnostics.Stopwatch]::StartNew()
    $exe  = $Cmd[0]
    $rest = if ($Cmd.Length -gt 1) { $Cmd[1..($Cmd.Length-1)] } else { @() }
    & $exe @rest
    $sw.Stop()
    $ok      = ($LASTEXITCODE -eq 0)
    $elapsed = [int]$sw.Elapsed.TotalSeconds
    if ($ok) { Write-Host "PASSED  [${elapsed}s]" -ForegroundColor Green }
    else     { Write-Host "FAILED  exit=$LASTEXITCODE  [${elapsed}s]" -ForegroundColor Red }
    return $ok
}

# ---------------------------------------------------------------------------
# City universe and covariates
# ---------------------------------------------------------------------------
if ($STEP_0B) {
    $StepResults["0b: city universe"] = Invoke-Step `
        "Step 0b  Build city universe -- code\build_city_universe\build_city_universe_v4.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\build_city_universe\build_city_universe_v4.py")
}

if ($STEP_0C) {
    $StepResults["0c: covariates"] = Invoke-Step `
        "Step 0c  Build city covariates -- code\build_covariates\build_covariates_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\build_covariates\build_covariates_v1.py")
}

if ($STEP_1) {
    $StepResults["1: city buffers"] = Invoke-Step `
        "Step 1  Create city buffers -- code\create_city_buffers\create_city_buffers_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\create_city_buffers\create_city_buffers_v1.py")
}

if ($STEP_1B) {
    $StepResults["1b: prefecture geom"] = Invoke-Step `
        "Step 1b  Prefecture boundaries -- code\create_prefecture_geometries\create_prefecture_geometries_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\create_prefecture_geometries\create_prefecture_geometries_v1.py")
}

# ---------------------------------------------------------------------------
# Satellite downloads (large; require NASA Earthdata login for MODIS)
# ---------------------------------------------------------------------------
if ($STEP_2) {
    Write-Host ""
    Write-Host "NOTE: MODIS LST download requires NASA Earthdata credentials." -ForegroundColor Yellow
    Write-Host "      Configure ~/.netrc or pass credentials as prompted." -ForegroundColor Yellow
    $StepResults["2: download LST"] = Invoke-Step `
        "Step 2  Download MODIS LST -- code\download_modis_lst\download_modis_lst_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\download_modis_lst\download_modis_lst_v1.py")
}

if ($STEP_2B) {
    $StepResults["2b: download tree"] = Invoke-Step `
        "Step 2b  Download MODIS Tree Cover -- code\download_modis_tree_cover\download_modis_tree_cover_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\download_modis_tree_cover\download_modis_tree_cover_v1.py")
}

if ($STEP_2D) {
    $StepResults["2d: download NDVI"] = Invoke-Step `
        "Step 2d  Download MODIS NDVI -- code\download_modis_ndvi\download_modis_ndvi_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\download_modis_ndvi\download_modis_ndvi_v1.py")
}

if ($STEP_2E) {
    Write-Host ""
    Write-Host "NOTE: CLCD is downloaded from Zenodo (record 18180184). No login required." -ForegroundColor Yellow
    $StepResults["2e: download CLCD"] = Invoke-Step `
        "Step 2e  Download CLCD -- code\download_clcd\download_clcd_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\download_clcd\download_clcd_v1.py")
}

# ---------------------------------------------------------------------------
# HDF4 conversion (environment_data -- pyhdf conflicts with environment_geo)
# ---------------------------------------------------------------------------
if ($STEP_3) {
    $StepResults["3: convert LST"] = Invoke-Step `
        "Step 3  Convert LST HDF4 to arrays -- code\convert_hdf_to_arrays\convert_hdf_to_arrays_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvData,
          "python", "code\convert_hdf_to_arrays\convert_hdf_to_arrays_v1.py")
}

if ($STEP_3B) {
    $StepResults["3b: convert tree"] = Invoke-Step `
        "Step 3b  Convert Tree Cover HDF4 -- code\convert_tree_cover_to_arrays\convert_tree_cover_to_arrays_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvData,
          "python", "code\convert_tree_cover_to_arrays\convert_tree_cover_to_arrays_v1.py")
}

if ($STEP_3D) {
    $StepResults["3d: convert NDVI"] = Invoke-Step `
        "Step 3d  Convert MODIS NDVI HDF4 -- code\convert_modis_ndvi_to_arrays\convert_modis_ndvi_to_arrays_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvData,
          "python", "code\convert_modis_ndvi_to_arrays\convert_modis_ndvi_to_arrays_v1.py")
}

# ---------------------------------------------------------------------------
# Panel building (environment_geo; each requires the corresponding convert step)
# ---------------------------------------------------------------------------
if ($STEP_4) {
    $StepResults["4: LST panel"] = Invoke-Step `
        "Step 4  Build LST panel -- code\build_lst_panel\build_lst_panel_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\build_lst_panel\build_lst_panel_v1.py")
}

if ($STEP_4B) {
    $StepResults["4b: tree panel"] = Invoke-Step `
        "Step 4b  Build Tree Cover panel -- code\build_tree_cover_panel\build_tree_cover_panel_v2.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\build_tree_cover_panel\build_tree_cover_panel_v2.py")
}

if ($STEP_4D) {
    $StepResults["4d: NDVI panel"] = Invoke-Step `
        "Step 4d  Build MODIS NDVI panel -- code\build_modis_ndvi_panel\build_modis_ndvi_panel_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\build_modis_ndvi_panel\build_modis_ndvi_panel_v1.py")
}

if ($STEP_4E) {
    $StepResults["4e: CLCD panel"] = Invoke-Step `
        "Step 4e  Build CLCD panel -- code\build_clcd_panel\build_clcd_panel_v2.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\build_clcd_panel\build_clcd_panel_v2.py")
}

if ($STEP_5) {
    $StepResults["5: merge panels"] = Invoke-Step `
        "Step 5  Merge panels -- code\merge_panels\merge_panels_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvStats,
          "python", "code\merge_panels\merge_panels_v1.py")
}

# ---------------------------------------------------------------------------
# Station weather pipeline (Steps 8a-8e)
# ---------------------------------------------------------------------------
if ($STEP_8A) {
    Write-Host ""
    Write-Host "NOTE: Downloading GHCN-Daily data from NOAA (station list in" -ForegroundColor Yellow
    Write-Host "      data/station_temps/source/ghcn_china_stations.csv)." -ForegroundColor Yellow
    $StepResults["8a: download station"] = Invoke-Step `
        "Step 8a  Download GHCN-Daily -- code\download_station_temps\download_ghcn_china_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\download_station_temps\download_ghcn_china_v1.py")
}

if ($STEP_8B) {
    $StepResults["8b: station panel"] = Invoke-Step `
        "Step 8b  Build station panel -- code\build_station_panel\build_station_panel_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\build_station_panel\build_station_panel_v1.py")
}

if ($STEP_8C) {
    $StepResults["8c: station buffers"] = Invoke-Step `
        "Step 8c  Create station buffers -- code\create_station_buffers\create_station_buffers_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\create_station_buffers\create_station_buffers_v1.py")
}

if ($STEP_8D) {
    Write-Host ""
    Write-Host "NOTE: Step 8d extracts satellite data at ~115 station locations." -ForegroundColor Yellow
    Write-Host "      Runtime approximately 20-40 minutes with parallel workers." -ForegroundColor Yellow
    $StepResults["8d: extract RS"] = Invoke-Step `
        "Step 8d  Extract RS at stations -- code\extract_rs_for_stations\extract_rs_for_stations_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\extract_rs_for_stations\extract_rs_for_stations_v1.py")
}

if ($STEP_8E) {
    $StepResults["8e: merge station"] = Invoke-Step `
        "Step 8e  Merge station panels -- code\merge_station_panels\merge_station_panels_v1.py" `
        @($Conda, "run", "--no-capture-output", "-n", $EnvGeo,
          "python", "code\merge_station_panels\merge_station_panels_v1.py")
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 70) -ForegroundColor Cyan
Write-Host "  SUMMARY" -ForegroundColor Cyan
Write-Host ("=" * 70) -ForegroundColor Cyan

if ($StepResults.Count -eq 0) {
    Write-Host "No steps were enabled. Edit the STEP_* switches at the top of" -ForegroundColor Yellow
    Write-Host "this script to enable the steps you want to run." -ForegroundColor Yellow
} else {
    $allPassed = $true
    foreach ($key in $StepResults.Keys) {
        $val = $StepResults[$key]
        if ($val -eq $true) {
            Write-Host ("  {0,-26}  PASSED" -f $key) -ForegroundColor Green
        } else {
            Write-Host ("  {0,-26}  FAILED" -f $key) -ForegroundColor Red
            $allPassed = $false
        }
    }
    Write-Host ""
    if ($allPassed) {
        Write-Host "Data pipeline complete." -ForegroundColor Green
        Write-Host "Analysis inputs are in: data\merge_panels\generated\" -ForegroundColor Cyan
        Write-Host "Next: run .\run_replication.ps1" -ForegroundColor Yellow
    } else {
        Write-Host "One or more steps failed -- see output above." -ForegroundColor Red
    }
}
