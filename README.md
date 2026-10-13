# Replication Package

**The Effect of China's National Forest City Program on Urban Heat Is Not Robustly Identified:
Comment on Ma, Geng, and Zhong (2026, *Forests*)**

Matthew Turner, Brown University
matthew_turner@brown.edu

---

## Overview

This package reproduces all tables and figures in the comment paper.
Estimation uses three steps:

1. **Python -- city-level BJS** (`code/run_analysis/run_analysis_v6.py`):
   Borusyak-Jaravel-Spiess (BJS) imputation DiD with and without controls,
   for all city-level outcomes and buffer sizes. Produces Figure 1 and
   Appendix Figures A1 (BJS 5 km) panels.

2. **Stata via Python -- city-level CS and DR**
   (`code/run_analysis_stata/run_analysis_stata_v1.py`):
   Callaway-Sant'Anna (CS) and doubly-robust (DR) DiD for all city-level
   outcomes, using never-treated cities as the comparison group.
   Produces Appendix Figures A2 (DR 20 km) and A3 (CS prefecture) panels.

3. **Python -- station-level BJS** (`code/run_station_analysis/run_station_analysis_v5.py`):
   BJS with controls for eight weather-station outcomes (Tmax, hot days, NDVI,
   tree cover, CLCD forest cover at 100 m and 500 m buffers, LST, extreme hot
   days). Produces Appendix Figure A4 panels.

Pre-generated outputs (figures, tables, compiled PDF) are included in
`analysis/` and `paper/` so results can be verified without re-running
the full pipeline.

---

## Software requirements

### Python environments

This package uses two Python environments. Install both before running.

**environment_stats** (conda) -- used by Steps 1 and 3 (BJS estimation):
```
conda env create -f environment_stats.yml
```

**environment_geo** (conda) -- used by Step 2 (Stata orchestrator) and any
geospatial processing:
```
conda env create -f environment_geo.yml
```

### Stata

Stata 17 or later. The following user-written packages must be installed
(run once in Stata):
```stata
ssc install csdid
ssc install did_imputation
ssc install ftools
ssc install reghdfe
net install drdid, from("https://raw.githubusercontent.com/friosavila/drdid/main/")
```

### LaTeX (optional)

Any standard distribution (TeX Live, MiKTeX) with the standard packages
(`booktabs`, `geometry`, `natbib`, `graphicx`, `gensymb`).

---

## Data

All analysis-ready data are in `data/merge_panels/generated/`:

| File | Description | Size |
|---|---|---|
| `city_year_panel.parquet` | City-level panel (LST, NDVI, tree cover, CLCD, 2000-2025) | 3 MB |
| `city_year_panel.csv` | Same panel in CSV format | 6.5 MB |
| `station_city_panel.csv` | Station-level panel (Tmax, hot days, NDVI, tree cover, 2000-2025) | 334 KB |
| `city_covariates.csv` | Pre-treatment city characteristics (lat, lon, log population) | 19 KB |

`data/build_city_universe/generated/cities_full.csv` contains the treatment
year (first year designated as a National Forest City) for each treated city.

**Data sources:** MODIS MOD11A2 (land surface temperature), MOD13A3 (NDVI),
MOD44B (tree cover); CLCD land classification (Yang et al. 2019, updated 2023);
GHCN-Daily weather station records; National Forest City designation list
from the National Forestry and Grassland Administration.
Raw satellite downloads and raster-to-panel extraction scripts are not included
(raw data is several hundred GB); only the analysis-ready panels are provided.

---

## Reproducing the results

### Step 1 -- City-level BJS estimation (Figure 1, Appendix Figure A1)

Activate `environment_stats` and run:

```
conda activate environment_stats
python code/run_analysis/run_analysis_v6.py
```

Reads:
- `data/merge_panels/generated/city_year_panel.parquet`
- `data/merge_panels/generated/city_covariates.csv`

Writes:
- `analysis/figures/event_study_bjs_dr_*.png` -- BJS with controls event-study
  panels for all outcomes and buffer sizes
- `analysis/tables/aggregate_atts.csv` -- BJS aggregate ATTs for Table 1
  (columns 1 and 2)
- `last_run_analysis.log` -- run log

Runtime: approximately 5-15 minutes.

### Step 2 -- City-level CS and DR estimation (Appendix Figures A2, A3)

Activate `environment_geo` and run (Stata must be on PATH or configured in
the script; see "Stata path configuration" below):

```
conda activate environment_geo
python code/run_analysis_stata/run_analysis_stata_v1.py
```

Reads:
- `data/merge_panels/generated/city_year_panel.parquet`
- `data/merge_panels/generated/city_covariates.csv`

Writes:
- `analysis/figures/stata/` -- CS and DR event-study PNGs for all outcomes
- `analysis/tables/stata_aggregate_atts.csv` -- CS/DR aggregate ATTs for
  Table 1 (columns 3-5)
- `last_run_stata_city.log` -- run log

Runtime: approximately 30-60 minutes (Stata estimation; varies by machine).

### Step 3 -- Station-level BJS estimation (Appendix Figure A4)

Activate `environment_stats` and run:

```
conda activate environment_stats
python code/run_station_analysis/run_station_analysis_v5.py
```

Reads:
- `data/merge_panels/generated/station_city_panel.csv`
- `data/merge_panels/generated/city_covariates.csv`

Writes:
- `analysis/figures/station_event_study_bjs_dr_*.png` -- BJS with controls
  event-study panels for station outcomes used in Appendix Figure A4
- `analysis/tables/station_aggregate_atts.csv` -- station BJS aggregate ATTs
  for Table 1 (column 6)
- `last_run_station_analysis.log` -- run log

Runtime: approximately 5-15 minutes.

### Step 4 -- Compile the paper (optional)

From the `paper/` directory:
```
pdflatex comment_nfcp.tex
bibtex comment_nfcp
pdflatex comment_nfcp.tex
pdflatex comment_nfcp.tex
```

The compiled PDF is also provided as `paper/comment_nfcp.pdf`.

### Verifying Table 1

Table 1 values are in `paper/table1_values.csv`. The script
`paper/compute_table1.py` assembles this from:
- `analysis/tables/aggregate_atts.csv` (BJS, Steps 1 and 3)
- `analysis/tables/stata_aggregate_atts.csv` (CS/DR, Step 2)

---

## File structure

```
replication/
|-- README.md
|-- environment_stats.yml        # Python conda env for BJS estimation
|-- environment_geo.yml          # Python conda env for geospatial/Stata steps
|-- data/
|   |-- merge_panels/generated/  # analysis-ready panels (main inputs)
|   `-- build_city_universe/generated/  # treatment timing
|-- code/
|   |-- run_analysis/            # city-level BJS (Python; Step 1)
|   |-- run_analysis_stata/      # city-level CS/DR (Stata via Python; Step 2)
|   |-- run_station_analysis/    # station-level BJS (Python; Step 3)
|   |-- run_station_analysis_stata/  # station-level Stata (supplementary)
|   `-- utils/                   # Python DiD library (BJS, CS, DR)
|-- paper/
|   |-- comment_nfcp.tex         # paper source
|   |-- response_to_editor.tex   # response to editor (for reference)
|   |-- refs.bib                 # bibliography
|   |-- comment_nfcp.pdf         # compiled paper
|   |-- compute_table1.py        # Table 1 assembly script
|   `-- table1_values.csv        # pre-generated Table 1 values
`-- analysis/
    |-- figures/                 # pre-generated event-study figures
    |   |-- event_study_bjs_dr_*.png     # city BJS panels (12 PNGs; Steps 1-2)
    |   |-- station_event_study_bjs_dr_*.png  # station BJS panels (4 PNGs; Step 3)
    |   `-- stata/               # Stata CS/DR panels (92 PNGs; Step 2)
    `-- tables/                  # pre-generated aggregate ATT tables
```

---

## Stata path configuration

The orchestrator scripts call Stata as a subprocess. Search for `STATA_EXE`
near the top of each script and update it to match your Stata installation:

- `code/run_analysis_stata/run_analysis_stata_v1.py`
- `code/run_station_analysis_stata/run_station_analysis_stata_v1.py`

Both default to `C:\Program Files\Stata18\StataMP-64.exe`. Change this to
the full path of your Stata executable if it differs
(e.g., `/usr/local/stata18/stata-mp` on Linux/Mac).
