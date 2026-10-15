"""
Build Ground Station Temperature Panel

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/station_temps/source/ghcn_china_stations.csv   (produced by download_ghcn_china_v1.py)
    - data/station_temps/source/dly/*.dly                 (produced by download_ghcn_china_v1.py)
    - data/build_city_universe/generated/cities_full.csv  (produced by build_city_universe_v3.py)

Produces (outputs for later stages):
    - data/station_temps/generated/station_inventory.csv  (stations matched to NFC cities)
    - data/station_temps/generated/station_panel.csv      (station x year panel with heat outcomes)

Description:
    Parses the raw GHCN-Daily .dly files and builds a station x year panel of
    temperature outcomes. The panel is designed for two purposes:

    1. AIR TEMPERATURE EVENT STUDY: Does NFC designation reduce summer Tmax and
       hot-day counts at ground stations in treated cities?

    2. GAMING TEST: Do stations in NFC cities show MORE cooling than city-wide
       satellite signals would predict? Evidence of strategic tree placement
       near monitors would show as significant station cooling alongside null
       satellite LST.

    The script:
    (a) Loads the station inventory and city universe.
    (b) Matches each station to the nearest study city within MAX_MATCH_KM km
        using the haversine formula. Records the distance for each buffer level
        (5km, 10km, 20km, 50km) as a flag column.
    (c) Parses TMAX and TMIN from each matched station's .dly file, applying
        GHCN quality flags (drops quality-flagged values).
    (d) Computes annual summer (June-August) statistics:
            - tmax_summer_mean: mean of daily TMAX (°C)
            - hot_days:         count of days with TMAX >= 35°C
            - extreme_hot_days: count of days with TMAX >= 38°C
            - tmin_summer_mean: mean of daily TMIN (°C; nighttime warmth)
            - n_tmax_obs:       number of valid TMAX days (data quality gauge)
    (e) Saves a matched station inventory CSV and the panel CSV.

Usage:
    conda activate C:\\py_envs\\environment_geo
    python code\\build_station_panel\\build_station_panel_v1.py

Expected runtime: 5-15 minutes (parsing ~500 .dly files).
"""

import sys
import math
import csv
import logging
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# PATH SETUP
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT / 'code'))

from utils.config import (
    GHCN_STATIONS_FILE,
    STATION_TEMPS_DLY_DIR,
    STATION_TEMPS_GENERATED_DIR,
    BUILD_CITY_UNIVERSE_GENERATED_DIR,
    STUDY_START_YEAR,
    STUDY_END_YEAR,
)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

CITIES_FILE          = BUILD_CITY_UNIVERSE_GENERATED_DIR / 'cities_full.csv'
STATION_INVENTORY_OUT = STATION_TEMPS_GENERATED_DIR / 'station_inventory.csv'
STATION_PANEL_OUT    = STATION_TEMPS_GENERATED_DIR / 'station_panel.csv'

# Maximum distance (km) at which we bother parsing a station's data at all.
# Stations further than this from every study city are dropped entirely.
MAX_MATCH_KM = 50.0

# Buffer levels to record as flag columns in the output
BUFFER_KM_LEVELS = [5, 10, 20, 50]

# Summer months (June, July, August) — peak heat season in China
SUMMER_MONTHS = {6, 7, 8}

# Extreme heat thresholds (°C) — standard thresholds used by China Met. Administration
HOT_THRESHOLD         = 35.0   # 高温日 (hot day)
EXTREME_HOT_THRESHOLD = 38.0   # 酷暑日 (extreme hot day)

# Minimum number of valid summer TMAX observations to include a station-year
MIN_OBS_THRESHOLD = 30  # out of ~92 possible summer days

LOG_FILE = PROJECT_ROOT / 'last_run_build_station_panel.log'

# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# HELPER: Haversine distance between two lat/lon points (returns km)
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance in kilometres between two points
    specified as (lat, lon) in decimal degrees.
    Uses the haversine formula — accurate to within 0.3% for distances under 500 km.
    """
    R = 6371.0  # Earth's mean radius in km
    # Convert degrees to radians
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlam       = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2)**2
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# STEP 1: Load station inventory and city universe
# ---------------------------------------------------------------------------

def check_required_inputs():
    """Fail loudly if any required input file is missing (CRITICAL RULE 2)."""
    missing = []
    for path in [GHCN_STATIONS_FILE, CITIES_FILE]:
        if not path.exists():
            missing.append(str(path))
    if missing:
        logger.error("FATAL: Required input files are missing:")
        for p in missing:
            logger.error(f"  - {p}")
        logger.error("Run download_ghcn_china_v1.py and build_city_universe_v3.py first.")
        sys.exit(1)
    if not STATION_TEMPS_DLY_DIR.exists() or not any(STATION_TEMPS_DLY_DIR.glob('*.dly')):
        logger.error(f"FATAL: No .dly files found in {STATION_TEMPS_DLY_DIR}")
        logger.error("Run download_ghcn_china_v1.py first.")
        sys.exit(1)


def load_stations() -> pd.DataFrame:
    """Load the Chinese station inventory CSV."""
    df = pd.read_csv(GHCN_STATIONS_FILE, dtype={'station_id': str, 'wmo_id': str})
    logger.info(f"Loaded {len(df):,} Chinese stations from inventory.")
    return df


def load_cities() -> pd.DataFrame:
    """Load the study city universe with lat/lon."""
    df = pd.read_csv(CITIES_FILE)
    logger.info(f"Loaded {len(df):,} study cities.")
    return df


# ---------------------------------------------------------------------------
# STEP 2: Match each station to the nearest city within MAX_MATCH_KM
# ---------------------------------------------------------------------------

def match_stations_to_cities(stations: pd.DataFrame, cities: pd.DataFrame) -> pd.DataFrame:
    """
    For each station, find the nearest study city.
    Keep stations within MAX_MATCH_KM of at least one city.
    Adds columns: city_id, city_name, nearest_city_dist_km, treated,
    designation_year, and within_{r}km for each buffer radius.
    """
    logger.info(f"Matching stations to cities (max distance: {MAX_MATCH_KM} km)...")

    # Pre-extract city arrays for fast looping
    city_ids   = cities['city_id'].values
    city_lats  = cities['lat'].values
    city_lons  = cities['lon'].values
    city_names = cities['city_name'].values
    city_treat = cities['treated'].values
    city_dyear = cities['designation_year'].values

    matched_rows = []
    for _, stn in stations.iterrows():
        slat = stn['lat']
        slon = stn['lon']

        # Compute distance from this station to every study city
        dists = [haversine_km(slat, slon, clat, clon)
                 for clat, clon in zip(city_lats, city_lons)]
        min_idx  = int(np.argmin(dists))
        min_dist = dists[min_idx]

        if min_dist > MAX_MATCH_KM:
            continue  # station too far from any study city — drop it

        row = stn.to_dict()
        row['city_id']              = int(city_ids[min_idx])
        row['city_name']            = city_names[min_idx]
        row['nearest_city_dist_km'] = round(min_dist, 2)
        row['treated']              = int(city_treat[min_idx])
        row['designation_year']     = city_dyear[min_idx]

        # Buffer membership flags (True if within that radius)
        for r in BUFFER_KM_LEVELS:
            row[f'within_{r}km'] = (min_dist <= r)

        matched_rows.append(row)

    matched = pd.DataFrame(matched_rows)
    logger.info(f"Matched {len(matched):,} stations within {MAX_MATCH_KM} km of a study city.")

    # Log how many stations fall in each buffer for treated vs control cities
    for r in BUFFER_KM_LEVELS:
        col  = f'within_{r}km'
        n_tr = matched[matched['treated'] == 1][col].sum()
        n_co = matched[matched['treated'] == 0][col].sum()
        logger.info(f"  Within {r:>2}km: {int(n_tr)} near treated, {int(n_co)} near control cities")

    return matched


# ---------------------------------------------------------------------------
# STEP 3: Parse a .dly file and extract daily TMAX/TMIN
# ---------------------------------------------------------------------------

def parse_dly_file(dly_path: Path) -> pd.DataFrame:
    """
    Parse a GHCN-Daily .dly file and return a DataFrame with one row per day.

    .dly format (fixed-width):
        Chars  1-11: Station ID
        Chars 12-15: Year
        Chars 16-17: Month
        Chars 18-21: Element (e.g. 'TMAX', 'TMIN')
        For each day 1-31 (31 groups of 8 chars starting at char 22):
            VALUE (5 chars): measurement in tenths of °C; -9999 = missing
            MFLAG (1 char): measurement flag
            QFLAG (1 char): quality assurance flag (non-blank = failed QA, drop it)
            SFLAG (1 char): source flag

    Returns columns: year, month, day, tmax_c, tmin_c
    """
    records = []
    try:
        with open(dly_path, 'r', encoding='ascii', errors='replace') as f:
            for line in f:
                if len(line) < 21:
                    continue

                element = line[17:21].strip()
                if element not in ('TMAX', 'TMIN'):
                    continue  # we only need temperature; skip PRCP, SNOW, etc.

                year  = int(line[11:15])
                month = int(line[15:17])

                # Skip years outside study window
                if year < STUDY_START_YEAR or year > STUDY_END_YEAR:
                    continue

                # Each day occupies 8 characters starting at position 21 (0-indexed)
                for day in range(1, 32):
                    start = 21 + (day - 1) * 8
                    end   = start + 8
                    if end > len(line):
                        break

                    chunk  = line[start:end]
                    value  = chunk[0:5].strip()
                    qflag  = chunk[6]   # quality flag (char 7 of each group)

                    # Skip missing values and quality-flagged values
                    if value == '-9999' or value == '' or qflag != ' ':
                        continue

                    try:
                        temp_c = int(value) / 10.0  # tenths of °C → °C
                    except ValueError:
                        continue

                    records.append({
                        'year':    year,
                        'month':   month,
                        'day':     day,
                        'element': element,
                        'temp_c':  temp_c,
                    })

    except OSError as e:
        logger.warning(f"  Could not read {dly_path.name}: {e}")
        return pd.DataFrame()

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Pivot so TMAX and TMIN are separate columns (one row per day)
    pivoted = df.pivot_table(
        index=['year', 'month', 'day'],
        columns='element',
        values='temp_c',
        aggfunc='first'   # take first value if duplicates exist
    ).reset_index()
    pivoted.columns.name = None

    # Ensure both columns exist even if one element has no data
    for col in ['TMAX', 'TMIN']:
        if col not in pivoted.columns:
            pivoted[col] = np.nan

    pivoted = pivoted.rename(columns={'TMAX': 'tmax_c', 'TMIN': 'tmin_c'})
    return pivoted


# ---------------------------------------------------------------------------
# STEP 4: Compute annual summer statistics from daily data
# ---------------------------------------------------------------------------

def compute_annual_summer_stats(daily: pd.DataFrame, station_id: str) -> list[dict]:
    """
    Given a DataFrame of daily TMAX/TMIN observations for one station,
    aggregate to annual summer (June-August) statistics.

    Returns a list of dicts, one per year with valid data.
    """
    if daily.empty:
        return []

    # Keep only summer months (June, July, August)
    summer = daily[daily['month'].isin(SUMMER_MONTHS)].copy()
    if summer.empty:
        return []

    rows = []
    for year, grp in summer.groupby('year'):
        # Count valid (non-NaN) TMAX observations
        tmax_valid = grp['tmax_c'].dropna()
        n_obs = len(tmax_valid)

        # Drop years with too few observations — probably large data gaps
        if n_obs < MIN_OBS_THRESHOLD:
            continue

        row = {
            'station_id':          station_id,
            'year':                int(year),
            'tmax_summer_mean':    round(tmax_valid.mean(), 3),
            'hot_days':            int((tmax_valid >= HOT_THRESHOLD).sum()),
            'extreme_hot_days':    int((tmax_valid >= EXTREME_HOT_THRESHOLD).sum()),
            'tmin_summer_mean':    round(grp['tmin_c'].dropna().mean(), 3)
                                   if grp['tmin_c'].notna().any() else np.nan,
            'n_tmax_obs':          n_obs,
        }
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# STEP 5: Main loop — parse all matched stations and build panel
# ---------------------------------------------------------------------------

def build_panel(matched_stations: pd.DataFrame) -> pd.DataFrame:
    """
    Loop over matched stations, parse each .dly file, compute annual stats,
    and return a long-format station × year panel.
    """
    logger.info(f"\nParsing .dly files for {len(matched_stations):,} matched stations...")

    all_rows  = []
    n_parsed  = 0
    n_missing = 0

    for _, stn in matched_stations.iterrows():
        sid      = stn['station_id']
        dly_path = STATION_TEMPS_DLY_DIR / f'{sid}.dly'

        if not dly_path.exists():
            n_missing += 1
            continue

        # Parse daily records
        daily = parse_dly_file(dly_path)
        if daily.empty:
            n_missing += 1
            continue

        # Aggregate to annual summer stats
        annual_rows = compute_annual_summer_stats(daily, sid)
        all_rows.extend(annual_rows)
        n_parsed += 1

    logger.info(f"Parsed {n_parsed:,} stations, {n_missing:,} missing or empty .dly files.")
    logger.info(f"Total station-year observations before merge: {len(all_rows):,}")

    if not all_rows:
        logger.error("FATAL: No temperature data parsed. Check .dly files.")
        sys.exit(1)

    panel = pd.DataFrame(all_rows)

    # Merge in the station metadata (city match, coordinates, etc.)
    meta_cols = [
        'station_id', 'lat', 'lon', 'elev_m', 'name', 'wmo_id',
        'city_id', 'city_name', 'nearest_city_dist_km', 'treated',
        'designation_year',
    ] + [f'within_{r}km' for r in BUFFER_KM_LEVELS]

    panel = panel.merge(
        matched_stations[meta_cols],
        on='station_id',
        how='left',
    )

    # Sort for readability
    panel = panel.sort_values(['city_id', 'station_id', 'year']).reset_index(drop=True)

    return panel


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 70)
    logger.info("Build Ground Station Temperature Panel")
    logger.info(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)

    # Guard: fail early if inputs are missing
    check_required_inputs()

    # Load inputs
    stations = load_stations()
    cities   = load_cities()

    # Match stations to study cities
    matched = match_stations_to_cities(stations, cities)

    if matched.empty:
        logger.error("FATAL: No stations matched to any study city. "
                     "Check MAX_MATCH_KM and city coordinates.")
        sys.exit(1)

    # Save matched station inventory (coordinates + city assignments)
    STATION_TEMPS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    matched.to_csv(STATION_INVENTORY_OUT, index=False)
    logger.info(f"\nStation inventory saved: {STATION_INVENTORY_OUT}")

    # Build panel
    panel = build_panel(matched)

    # Validation summary
    logger.info("\n--- Validation summary ---")
    logger.info(f"Panel shape: {panel.shape[0]:,} rows × {panel.shape[1]} columns")
    logger.info(f"Years covered: {panel['year'].min()} – {panel['year'].max()}")
    logger.info(f"Unique stations: {panel['station_id'].nunique():,}")
    logger.info(f"Unique cities:   {panel['city_id'].nunique():,}")
    logger.info(f"Treated stations: {panel[panel['treated']==1]['station_id'].nunique():,}")
    logger.info(f"Control stations: {panel[panel['treated']==0]['station_id'].nunique():,}")

    # Check for suspicious values
    bad_tmax = (panel['tmax_summer_mean'] > 50) | (panel['tmax_summer_mean'] < -10)
    if bad_tmax.any():
        logger.warning(f"WARNING: {bad_tmax.sum()} station-years have implausible "
                       f"summer Tmax (<-10°C or >50°C). Check data quality.")

    logger.info(f"\ntmax_summer_mean: {panel['tmax_summer_mean'].min():.1f}°C – "
                f"{panel['tmax_summer_mean'].max():.1f}°C "
                f"(mean {panel['tmax_summer_mean'].mean():.1f}°C)")
    logger.info(f"hot_days (≥35°C): mean {panel['hot_days'].mean():.1f} days/summer")

    # Save panel
    panel.to_csv(STATION_PANEL_OUT, index=False)
    logger.info(f"\nStation panel saved: {STATION_PANEL_OUT}")
    logger.info("Next step: add station panel to run_analysis_v1.py for event studies.")
    logger.info("=" * 70)


if __name__ == '__main__':
    main()
