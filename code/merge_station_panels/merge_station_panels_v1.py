"""
Merge Station Panels

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/station_temps/generated/station_panel.csv   (from build_station_panel_v1.py)
    - data/station_rs/generated/station_rs_panel.csv   (from extract_rs_for_stations_v1.py)

Produces (outputs for later stages):
    - data/merge_panels/generated/station_city_panel.csv

Description:
    Merges the station temperature outcomes panel (station_panel.csv) with the
    satellite RS variables panel (station_rs_panel.csv) on station_id × year.

    Also computes:
        rel_time = year - designation_year  (NaN for control stations)

    The resulting panel has one row per station × year and contains all columns
    needed for run_station_analysis_v1.py:
        - Station identity: station_id, lat, lon, elev_m, name, city_id, city_name
        - Treatment: treated, designation_year, within_*km flags, rel_time
        - Temperature outcomes: tmax_summer_mean, hot_days, extreme_hot_days,
          tmin_summer_mean, n_tmax_obs
        - Satellite at station: lst_summer_mean_nearest, ndvi_summer_mean_nearest,
          tree_cover_nearest, clcd_forest_pct_100m, clcd_forest_pct_500m

Usage:
    conda activate C:\\py_envs\\environment_geo
    python code\\merge_station_panels\\merge_station_panels_v1.py
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT / 'code'))

from utils.config import (
    STATION_PANEL_FILE,
    STATION_RS_PANEL_FILE,
    STATION_CITY_PANEL_FILE,
)

LOG_FILE = PROJECT_ROOT / 'last_run_merge_station_panels.log'

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def check_required_inputs():
    """Fail loudly if any required input is missing (CRITICAL RULE 2)."""
    missing = [str(p) for p in [STATION_PANEL_FILE, STATION_RS_PANEL_FILE]
               if not p.exists()]
    if missing:
        logger.error("FATAL: Required input files are missing:")
        for m in missing:
            logger.error(f"  - {m}")
        logger.error("Run build_station_panel_v1.py and extract_rs_for_stations_v1.py first.")
        sys.exit(1)


def main():
    logger.info("=" * 70)
    logger.info("Merge Station Panels")
    logger.info(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)

    check_required_inputs()

    # ---- Load panels ----
    station_panel = pd.read_csv(STATION_PANEL_FILE, dtype={'station_id': str})
    rs_panel      = pd.read_csv(STATION_RS_PANEL_FILE, dtype={'station_id': str})
    logger.info(f"station_panel:    {len(station_panel):,} rows, "
                f"{station_panel['station_id'].nunique():,} stations")
    logger.info(f"station_rs_panel: {len(rs_panel):,} rows, "
                f"{rs_panel['station_id'].nunique():,} stations")

    # ---- Merge on station_id × year ----
    # Left join: keep all station-year observations that have temperature data;
    # RS variables will be NaN for years where satellite data is unavailable.
    merged = station_panel.merge(rs_panel, on=['station_id', 'year'], how='left')
    logger.info(f"After merge: {len(merged):,} rows")

    # ---- Compute rel_time for event study ----
    # rel_time = year - designation_year for treated stations; NaN for controls.
    # Controls have designation_year = NaN, so rel_time = NaN automatically.
    merged['rel_time'] = merged['year'] - merged['designation_year']

    # ---- Validation ----
    logger.info("\n--- Validation summary ---")
    logger.info(f"Panel shape: {merged.shape[0]:,} rows × {merged.shape[1]} columns")
    logger.info(f"Years:       {merged['year'].min()} – {merged['year'].max()}")
    logger.info(f"Stations:    {merged['station_id'].nunique():,} unique")
    logger.info(f"Cities:      {merged['city_id'].nunique():,} unique")
    logger.info(f"Treated stations: "
                f"{merged[merged['treated']==1]['station_id'].nunique():,}")
    logger.info(f"Control stations: "
                f"{merged[merged['treated']==0]['station_id'].nunique():,}")

    # Check completeness of key outcome columns
    for col in ['tmax_summer_mean', 'hot_days', 'lst_summer_mean_nearest',
                'clcd_forest_pct_100m', 'clcd_forest_pct_500m']:
        if col in merged.columns:
            n_valid = merged[col].notna().sum()
            pct = 100 * n_valid / len(merged)
            logger.info(f"  {col}: {n_valid:,}/{len(merged):,} non-null ({pct:.1f}%)")

    # Warn if very few station-years have RS data (likely a path problem)
    rs_cols = ['lst_summer_mean_nearest', 'clcd_forest_pct_100m']
    for col in rs_cols:
        if col in merged.columns and merged[col].notna().mean() < 0.05:
            logger.warning(f"WARNING: {col} is <5% non-null — check extract_rs_for_stations_v1.py")

    # ---- Save ----
    STATION_CITY_PANEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(STATION_CITY_PANEL_FILE, index=False)
    logger.info(f"\nSaved: {STATION_CITY_PANEL_FILE} ({len(merged):,} rows)")
    logger.info("Next: run run_station_analysis_v1.py")
    logger.info("=" * 70)


if __name__ == '__main__':
    main()
