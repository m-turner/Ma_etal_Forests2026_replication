# Replication Package

**The Effect of China's National Forest City Program on Urban Heat Is Not Robustly
Identified: Comment on Ma, Geng, and Zhong (2026, *Forests*)**

Matthew Turner, Brown University
matthew_turner@brown.edu

---

## Overview

This package reproduces all tables and figures in the comment paper.
It supports two levels of replication:

- **Tier 1 (quick start):** Re-run the estimation from pre-built analysis panels.
  Requires Python and optionally Stata; no satellite data needed.
  Total data input: ~15 MB. Runtime: ~1-2 hours (mostly Stata bootstrap).

- **Tier 2 (full pipeline):** Reproduce every step from raw satellite downloads
  through estimation. Requires ~300 GB disk for MODIS rasters; NASA Earthdata
  account; and NOAA internet access for GHCN-Daily.

Pre-generated outputs (figures, tables, compiled PDF) are included so results can
be verified without re-running anything.

---

## Software requirements

### Python environments

Three conda environments are used. The third (`environment_data`) is only needed
for Tier 2 (HDF4 conversion). Install with:

```powershell
.\build_environments.ps1
```

Or individually:

```
conda env create -f environment_stats.yml   # Tier 1 and 2: estimation (BJS, CS/DR)
conda env create -f environment_geo.yml     # Tier 2: panel building, geospatial
conda env create -f environment_data.yml    # Tier 2 only: HDF4 conversion (pyhdf)
```

### Stata

Stata 17 or later. The following user-written packages must be installed once:

```stata
ssc install csdid
ssc install did_imputation
ssc install ftools
ssc install reghdfe
net install drdid, from("https://raw.githubusercontent.com/friosavila/drdid/main/")
```

### LaTeX (optional)

Any standard distribution (TeX Live, MiKTeX) to recompile the paper PDF.

---

## Tier 1 — Quick start: re-run estimation only

All analysis-ready panels are pre-included. Simply run:

```powershell
.\run_replication.ps1
```

This runs all estimation steps and verifies outputs against the committed baseline.
See `run_replication.ps1` for Stata path configuration and parameter options.

### What the estimation script runs

| Step | Script | Output | Runtime |
|------|--------|--------|---------|
| 1 | `code/run_analysis/run_analysis_v6.py` | Figure 1, App. Figure A1; `analysis/tables/aggregate_atts.csv` (BJS cols 1-2) | 5-15 min |
| 2 | `code/run_analysis_stata/run_analysis_stata_v1.py` | App. Figures A2-A3; `analysis/tables/stata_aggregate_atts.csv` (CS cols 3-5) | 30-60 min |
| 2b | `code/run_station_analysis_stata/run_station_analysis_stata_v1.py` | Figure 3; station figures | 15-30 min |
| 3 | `code/run_station_analysis/run_station_analysis_v5.py` | App. Figure A4; station event-study CSVs (Table 1 col 6) | 5-15 min |
| T | `paper/compute_table1.py` | `paper/table1_values.csv` | <1 min |

### Analysis-ready input data

All inputs for estimation are in `data/merge_panels/generated/`:

| File | Description | Size |
|------|-------------|------|
| `city_year_panel.parquet` | City-level panel: LST, NDVI, tree cover, CLCD (2000-2025) | 3 MB |
| `city_year_panel.csv` | Same panel in CSV format | 6.5 MB |
| `station_city_panel.csv` | Station-level panel: Tmax, hot days, NDVI, tree cover (2000-2025) | 334 KB |
| `city_covariates.csv` | Pre-treatment city characteristics (lat, lon, log population) | 19 KB |

`data/build_city_universe/generated/cities_full.csv` contains treatment years
(first year designated as a National Forest City) for each treated city.

### Stata path configuration

Search for `STATA_EXE` near the top of each Stata orchestrator and update it:

- `code/run_analysis_stata/run_analysis_stata_v1.py`
- `code/run_station_analysis_stata/run_station_analysis_stata_v1.py`

Both default to `C:\Program Files\Stata18\StataMP-64.exe`.

---

## Tier 2 — Full pipeline from raw satellite data

> **Disk space:** approximately 300 GB for MODIS rasters plus 200 MB for GHCN-Daily.
> **Access:** NASA Earthdata account required for MODIS downloads.
> **Time:** days to weeks depending on connection speed (MODIS downloads).

### Step 1: Download raw satellite data

Configure NASA Earthdata credentials in `~/.netrc`:

```
machine urs.earthdata.nasa.gov login YOUR_USERNAME password YOUR_PASSWORD
```

Enable the relevant download steps in `pipeline_run_data.ps1` by changing
`(1 -eq 0)` to `(1 -eq 1)` for each step, then run:

```powershell
.\pipeline_run_data.ps1
```

See `pipeline_run_data.ps1` for step-by-step notes and environment assignments.

### Step 2: Run estimation

After the data pipeline completes:

```powershell
.\run_replication.ps1
```

### Pipeline structure

The full data pipeline runs in this order:

```
0b -> 0c -> 1 -> 1b -> 2 -> 2b -> 2d -> 2e (once)
  -> 3 -> 3b -> 3d
  -> 4 -> 4b -> 4d -> 4e
  -> 5
  -> 8a (once) -> 8b -> 8c -> 8d -> 8e
  -> [run_replication.ps1]
```

### What is pre-provided (skip these steps)

The following intermediate outputs are included so replicators can enter the
pipeline at any stage:

| Stage | Pre-provided files | Folder |
|-------|-------------------|--------|
| City universe | `cities_full.csv`, `matching_report.csv` | `data/build_city_universe/generated/` |
| City covariates | `city_covariates.csv` | `data/build_covariates/generated/` |
| City buffers | `city_buffers_{5,10,20,50}km.geojson`, `prefecture_boundaries.geojson` | `data/create_city_buffers/generated/` |
| LST panel | `lst_panel.csv` | `data/build_lst_panel/generated/` |
| NDVI panel | `modis_ndvi_panel.csv` | `data/build_modis_ndvi_panel/generated/` |
| Tree cover panel | `tree_cover_panel.csv` | `data/build_tree_cover_panel/generated/` |
| CLCD panel | `clcd_panel.csv` | `data/build_clcd_panel/generated/` |
| Station inventory | `station_inventory.csv`, `station_panel.csv` | `data/station_temps/generated/` |
| Station buffers | `station_buffers_{100m,500m}.geojson` | `data/station_buffers/generated/` |
| Station RS panel | `station_rs_panel.csv` | `data/station_rs/generated/` |
| Station merged panel | `station_city_panel.csv` | `data/station_panels/generated/` |
| Analysis panels | `city_year_panel.*`, `station_city_panel.csv`, `city_covariates.csv` | `data/merge_panels/generated/` |

### What is NOT included (too large or freely downloadable)

| Data | Size | Reproduced by |
|------|------|---------------|
| MODIS LST HDF4 tiles | ~150 GB | Step 2 (`download_modis_lst_v1.py`); NASA Earthdata |
| MODIS Tree Cover HDF4 | ~70 GB | Step 2b (`download_modis_tree_cover_v1.py`); NASA Earthdata |
| MODIS NDVI HDF4 | ~60 GB | Step 2d (`download_modis_ndvi_v1.py`); NASA Earthdata |
| CLCD GeoTIFFs (1985-2025) | ~20 GB | Step 2e (`download_clcd_v1.py`); Zenodo record 18180184 |
| GHCN-Daily .dly files | ~200 MB | Step 8a (`download_ghcn_china_v1.py`); NOAA |
| HDF-to-array intermediates | ~20 GB | Steps 3/3b/3d (`convert_*_to_arrays_v1.py`) |

---

## File structure

```
replication/
|-- README.md
|-- run_replication.ps1      # Tier 1: run all estimation steps
|-- pipeline_run_data.ps1    # Tier 2: run data construction pipeline
|-- build_environments.ps1   # Create all conda environments
|-- environment_stats.yml    # Python env for estimation (Steps 1, 3)
|-- environment_geo.yml      # Python env for panel building and Stata orchestration
|-- environment_data.yml     # Python env for HDF4 conversion (Steps 3/3b/3d only)
|-- data/
|   |-- merge_panels/generated/      # Analysis-ready panels (main Tier 1 inputs)
|   |-- build_city_universe/         # source/ + generated/
|   |-- build_covariates/            # generated/city_covariates.csv
|   |-- create_city_buffers/         # source/ (pf05.shp, WUP xls) + generated/ (geojsons)
|   |-- build_lst_panel/             # generated/lst_panel.csv
|   |-- build_modis_ndvi_panel/      # generated/modis_ndvi_panel.csv
|   |-- build_tree_cover_panel/      # generated/tree_cover_panel.csv
|   |-- build_clcd_panel/            # generated/clcd_panel.csv
|   |-- station_temps/               # source/ghcn_china_stations.csv + generated/
|   |-- station_buffers/             # generated/ (station buffer geojsons)
|   |-- station_rs/                  # generated/station_rs_panel.csv
|   |-- station_panels/              # generated/station_city_panel.csv
|   `-- [download/convert dirs]      # empty source/ dirs; raw data not included
|-- code/
|   |-- run_analysis/                # City BJS estimation (Step 1)
|   |-- run_analysis_stata/          # City CS/DR via Stata (Step 2)
|   |-- run_station_analysis/        # Station BJS estimation (Step 3)
|   |-- run_station_analysis_stata/  # Station CS/DR via Stata (Step 2b)
|   |-- utils/                       # DiD library (BJS, CS/DR implementations)
|   |-- build_city_universe/         # Step 0b
|   |-- build_covariates/            # Step 0c
|   |-- create_city_buffers/         # Step 1
|   |-- create_prefecture_geometries/# Step 1b
|   |-- download_modis_lst/          # Step 2
|   |-- download_modis_tree_cover/   # Step 2b
|   |-- download_modis_ndvi/         # Step 2d
|   |-- download_clcd/               # Step 2e
|   |-- convert_hdf_to_arrays/       # Step 3 (environment_data)
|   |-- convert_tree_cover_to_arrays/# Step 3b (environment_data)
|   |-- convert_modis_ndvi_to_arrays/# Step 3d (environment_data)
|   |-- build_lst_panel/             # Step 4
|   |-- build_tree_cover_panel/      # Step 4b
|   |-- build_modis_ndvi_panel/      # Step 4d
|   |-- build_clcd_panel/            # Step 4e
|   |-- merge_panels/                # Step 5
|   |-- download_station_temps/      # Step 8a
|   |-- build_station_panel/         # Step 8b
|   |-- create_station_buffers/      # Step 8c
|   |-- extract_rs_for_stations/     # Step 8d
|   `-- merge_station_panels/        # Step 8e
|-- paper/
|   |-- comment_nfcp.tex             # Paper source (LaTeX)
|   |-- response_to_editor.tex       # Response to editor (for reference)
|   |-- refs.bib                     # Bibliography
|   |-- comment_nfcp.pdf             # Compiled paper
|   |-- compute_table1.py            # Table 1 assembly script
|   `-- table1_values.csv            # Pre-generated Table 1 values
`-- analysis/
    |-- figures/                     # Pre-generated event-study figures
    `-- tables/                      # Pre-generated aggregate ATT tables
```

---

## Data sources

| Dataset | Product | Period | Source |
|---------|---------|--------|--------|
| Land surface temperature | MODIS MOD11A2 v061 | 2000-2025 | NASA Earthdata |
| NDVI | MODIS MOD13A2 v061 | 2000-2025 | NASA Earthdata |
| Tree cover | MODIS MOD44B v061 | 2000-2025 | NASA Earthdata |
| Land cover (CLCD) | Yang & Huang (2021) | 1985-2025 | Zenodo record 18180184 |
| Weather station records | GHCN-Daily | 2000-2025 | NOAA |
| NFC designation dates | National Forestry and Grassland Administration | 2004-2020 | Hand-compiled (included) |
| City universe | UN WUP 2018, File 22 | -- | UN Population Division (included) |
| Prefecture boundaries | pf05.shp (2005 boundaries) | -- | Included |

---

## Replication of Figure 3 and Table 1 column 6

Figure 3 and Table 1 column 6 (station BJS) use weather-station data.
Station coverage is limited to ~99 Chinese prefecture-level cities with a
GHCN-Daily station within 50 km. This is approximately one-third of the
243 prefecture-level cities in Ma et al.'s sample; the gap reflects
GHCN-Daily vs. CMA (Chinese Meteorological Administration) network
density and cannot be closed with publicly available data. This
limitation is discussed in the paper's Methods section.
