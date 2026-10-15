"""
Build City Universe

Updated from build_city_universe_v3 to build_city_universe_v4 on 2026-06-28.
Main changes in this version:
  - Restrict city universe to prefecture-level cities (地级市 and above) for
    both treated AND control.  County-level cities (县级市) that happen to
    appear in the WUP dataset (because they have >=300k urban populations) are
    now excluded.  The NFC treatment programme already targets prefecture-level
    cities exclusively; the control pool now matches that administrative level.
  - New source file: data/build_city_universe/source/prefecture_level_cities.csv
    lists the WUP city names that are prefecture-level.  The WUP dataset is
    filtered to that list BEFORE the NFC matching step runs.
  - The filter is applied via load_prefecture_filter(); missing file → fatal
    error.  Cities that are in the NFC treatment list but absent from the filter
    file produce a WARNING (never a silent exclusion) so the filter file can be
    incrementally corrected.
Previous version saved in _old/ as build_city_universe_v3.py.

Environment: environment_geo

Requires (inputs from earlier stages):
    - WUP Excel file (path set in config.py as WUP_FILE)
      Placed manually in data/create_city_buffers/source/
    - data/build_city_universe/source/nfc_treatment_list.csv
      Required columns: city_name_en, city_name_zh, province_en, province_zh,
                        lat, lon, designation_year
    - data/build_city_universe/source/prefecture_level_cities.csv  [NEW]
      One column (city_name_wup), one name per row, # = comment lines.
      Lists WUP city names that are prefecture-level (地级市) or above.
    - data/build_city_universe/source/manual_overrides.csv  [optional]
      Two columns, no header: nfc_name_en,wup_city_name

Produces (outputs for later stages):
    - data/build_city_universe/generated/cities_full.csv
    - data/build_city_universe/generated/matching_report.csv

Usage:
    python code/build_city_universe/build_city_universe_v4.py

Expected runtime: < 1 minute
"""

import sys
import math
import unicodedata
import re
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    WUP_FILE,
    NFC_TREATMENT_LIST_FILE,
    MANUAL_OVERRIDES_FILE,
    PREFECTURE_LEVEL_CITIES_FILE,
    CITIES_FULL_FILE,
    MATCHING_REPORT_FILE,
    CHINA_BBOX,
)

# ---------------------------------------------------------------------------
# Thresholds (unchanged from v3)
# ---------------------------------------------------------------------------

NAME_SCORE_THRESHOLD = 0.75
DISTANCE_KM_THRESHOLD = 50.0

STATUS_SORT_ORDER = {
    'conflict':        0,
    'unmatched':       1,
    'name_only':       2,
    'distance_only':   3,
    'manual_override': 4,
    'confirmed':       5,
}


# ---------------------------------------------------------------------------
# Name normalisation (unchanged from v3)
# ---------------------------------------------------------------------------

def normalise_name(name: str) -> str:
    """
    Lower-case, strip diacritics, remove punctuation and spaces, then drop
    trailing administrative suffixes so "Xi'an", "Xian", "Xi An" all
    compare equal, and "Guiyang City" matches "Guiyang".
    """
    if not isinstance(name, str):
        return ''
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_only = ''.join(c for c in nfkd if not unicodedata.combining(c))
    clean = re.sub(r"['\-\s\.]+", '', ascii_only.lower())
    clean = re.sub(r'(city|shi)$', '', clean)
    return clean


# ---------------------------------------------------------------------------
# Distance (unchanged from v3)
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) pairs."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Prefecture-level filter (NEW in v4)
# ---------------------------------------------------------------------------

def load_prefecture_filter(path: Path) -> set:
    """
    Load the list of prefecture-level WUP city names from a CSV file.

    The file has one column (city_name_wup), one city per row.  Lines that
    begin with '#' are treated as comments and skipped.  The returned set
    contains the stripped city names exactly as they appear in the file, which
    must match the WUP city_name_wup column.

    Fails loudly if the file does not exist or contains zero valid rows.
    """
    if not path.exists():
        print(f"ERROR: Prefecture-level filter file not found: {path}")
        print("  Create data/build_city_universe/source/prefecture_level_cities.csv")
        print("  with one column (city_name_wup) listing prefecture-level WUP city names.")
        sys.exit(1)

    # comment='#' makes pandas skip comment lines automatically
    df = pd.read_csv(path, comment='#', dtype=str)

    if 'city_name_wup' not in df.columns:
        print(f"ERROR: Prefecture filter file must have column 'city_name_wup'.")
        print(f"  Found columns: {list(df.columns)}")
        sys.exit(1)

    names = set(df['city_name_wup'].dropna().str.strip())
    if not names:
        print("ERROR: Prefecture filter file is empty (no valid city names).")
        sys.exit(1)

    print(f"  Loaded {len(names)} prefecture-level city names from filter file")
    return names


# ---------------------------------------------------------------------------
# Data loading (unchanged from v3)
# ---------------------------------------------------------------------------

def load_wup(wup_path: Path) -> pd.DataFrame:
    """
    Read the UN WUP Excel file and return Chinese cities with lat/lon and
    population columns.  Column detection is flexible: searches for any column
    containing 'country' and any column containing 'city' or 'agglomeration'.
    """
    print(f"  Reading WUP file: {wup_path.name}")
    engine = 'xlrd' if wup_path.suffix.lower() == '.xls' else 'openpyxl'
    print(f"  Engine: {engine}")

    df          = None
    country_col = None
    city_col    = None
    for header_row in range(25):
        try:
            candidate = pd.read_excel(wup_path, header=header_row, engine=engine)
            cols      = [str(c).strip() for c in candidate.columns]
            c_country = next((c for c in cols if 'country' in c.lower()), None)
            c_city    = next((c for c in cols
                              if 'city' in c.lower() or 'agglomeration' in c.lower()), None)
            if c_country and c_city:
                df          = candidate
                country_col = c_country
                city_col    = c_city
                print(f"  Header row   : {header_row}")
                print(f"  Country col  : '{country_col}'")
                print(f"  City col     : '{city_col}'")
                break
        except Exception:
            continue

    if df is None:
        print("ERROR: Could not identify header row in WUP file.")
        sys.exit(1)

    china = df[df[country_col].astype(str).str.strip() == 'China'].copy()
    if len(china) == 0:
        print("ERROR: No rows found for 'China' in WUP file.")
        sys.exit(1)
    print(f"  Found {len(china)} Chinese cities in WUP")

    lat_col = next((c for c in df.columns if 'lat' in str(c).lower()), None)
    lon_col = next((c for c in df.columns if 'lon' in str(c).lower()), None)
    if not lat_col or not lon_col:
        print("ERROR: Latitude/Longitude columns not found.")
        sys.exit(1)

    pop_year_cols = [c for c in df.columns
                     if str(c).strip().isdigit() and 1950 <= int(str(c).strip()) <= 2035]
    keep_years = [y for y in [2000, 2005, 2010, 2015, 2020, 2025]
                  if str(y) in [str(c).strip() for c in pop_year_cols]]

    out = pd.DataFrame()
    out['city_name_wup'] = china[city_col].astype(str).str.strip()
    out['lat_wup']       = pd.to_numeric(china[lat_col], errors='coerce')
    out['lon_wup']       = pd.to_numeric(china[lon_col], errors='coerce')
    out['name_key']      = out['city_name_wup'].apply(normalise_name)

    for yr in keep_years:
        col = next((c for c in pop_year_cols if str(c).strip() == str(yr)), None)
        if col is not None:
            out[f'pop_{yr}'] = (
                pd.to_numeric(china[col], errors='coerce') * 1000
            ).round(0).astype('Int64')

    out = out.dropna(subset=['lat_wup', 'lon_wup']).reset_index(drop=True)
    return out


def load_nfc_list(nfc_path: Path) -> pd.DataFrame:
    """Read the manually compiled NFC treatment list (# lines are comments)."""
    print(f"  Reading NFC list: {nfc_path.name}")
    rows = []
    with open(nfc_path, encoding='utf-8') as fh:
        header = None
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if header is None:
                header = [h.strip() for h in line.split(',')]
                continue
            rows.append(dict(zip(header, [v.strip() for v in line.split(',')])))

    df = pd.DataFrame(rows)
    required = {'city_name_en', 'city_name_zh', 'lat', 'lon', 'designation_year'}
    missing_cols = required - set(df.columns)
    if missing_cols:
        print(f"ERROR: NFC list missing columns: {sorted(missing_cols)}")
        sys.exit(1)

    df['lat']              = pd.to_numeric(df['lat'], errors='coerce')
    df['lon']              = pd.to_numeric(df['lon'], errors='coerce')
    df['designation_year'] = pd.to_numeric(df['designation_year'], errors='coerce').astype('Int64')
    df['name_key']         = df['city_name_en'].apply(normalise_name)

    no_coords = df[df['lat'].isna() | df['lon'].isna()]
    if len(no_coords):
        print(f"  WARNING: {len(no_coords)} NFC cities have no coordinates "
              f"-- distance signal unavailable for them:")
        for _, r in no_coords.iterrows():
            print(f"    {r['city_name_en']}")

    print(f"  Found {len(df)} NFC-designated cities")
    return df


def load_manual_overrides(path: Path) -> dict:
    """Optional file that bypasses both matching signals for listed cities."""
    if not path.exists():
        return {}
    print(f"  Loading manual overrides: {path.name}")
    overrides = {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split(',', 1)]
            if len(parts) == 2:
                overrides[parts[0]] = parts[1]
    print(f"  Loaded {len(overrides)} manual overrides")
    return overrides


# ---------------------------------------------------------------------------
# Matching (unchanged from v3)
# ---------------------------------------------------------------------------

def find_best_name_match(nfc_key: str, wup_df: pd.DataFrame):
    """Find the WUP city with the highest name similarity to nfc_key."""
    best_score = 0.0
    best_name  = ''
    for _, row in wup_df.iterrows():
        score = SequenceMatcher(None, nfc_key, row['name_key']).ratio()
        if score > best_score:
            best_score = score
            best_name  = row['city_name_wup']
    return best_name, round(best_score, 3)


def find_nearest_wup(lat: float, lon: float, wup_df: pd.DataFrame):
    """Find the WUP city closest to (lat, lon) by haversine distance."""
    best_dist = float('inf')
    best_name = ''
    for _, row in wup_df.iterrows():
        d = haversine_km(lat, lon, row['lat_wup'], row['lon_wup'])
        if d < best_dist:
            best_dist = d
            best_name = row['city_name_wup']
    return best_name, round(best_dist, 1)


def match_nfc_to_wup(nfc_df: pd.DataFrame, wup_df: pd.DataFrame,
                     overrides: dict) -> pd.DataFrame:
    """Run dual-signal matching; return one-row-per-NFC-city report sorted worst-first."""
    rows = []

    for _, nfc in nfc_df.iterrows():
        nfc_en     = nfc['city_name_en']
        nfc_key    = nfc['name_key']
        nfc_lat    = nfc['lat']
        nfc_lon    = nfc['lon']
        has_coords = not (pd.isna(nfc_lat) or pd.isna(nfc_lon))

        if nfc_en in overrides:
            wup_name = overrides[nfc_en]
            if has_coords:
                _, d_km = find_nearest_wup(nfc_lat, nfc_lon, wup_df)
            else:
                d_km = ''
            rows.append(_row(nfc, 'manual_override', '', wup_name,
                             wup_name, '', wup_name, d_km))
            continue

        name_wup, name_score = find_best_name_match(nfc_key, wup_df)
        name_ok = name_score >= NAME_SCORE_THRESHOLD

        if has_coords:
            dist_wup, dist_km = find_nearest_wup(nfc_lat, nfc_lon, wup_df)
            dist_ok = dist_km <= DISTANCE_KM_THRESHOLD
        else:
            dist_wup, dist_km = '', ''
            dist_ok = False

        both_ok = name_ok and dist_ok
        agreed  = both_ok and (name_wup == dist_wup)

        if agreed:
            status, recommended = 'confirmed', name_wup
        elif both_ok:
            status, recommended = 'conflict', ''
        elif name_ok:
            status, recommended = 'name_only', name_wup
        elif dist_ok:
            status, recommended = 'distance_only', dist_wup
        else:
            status, recommended = 'unmatched', ''

        agreed_str = 'yes' if agreed else ('no' if both_ok else '')
        rows.append(_row(nfc, status, agreed_str, recommended,
                         name_wup, name_score, dist_wup, dist_km))

    report = pd.DataFrame(rows)
    report['_sort'] = report['status'].map(STATUS_SORT_ORDER)
    report = (report
              .sort_values(['_sort', 'dist_km'], na_position='last')
              .drop(columns='_sort')
              .reset_index(drop=True))
    return report


def _row(nfc, status, agreed, recommended, name_wup, name_score, dist_wup, dist_km):
    """Assemble one matching-report row dict."""
    return {
        'status':           status,
        'agreed':           agreed,
        'recommended_wup':  recommended,
        'nfc_name_en':      nfc['city_name_en'],
        'nfc_name_zh':      nfc.get('city_name_zh', ''),
        'province_en':      nfc.get('province_en', ''),
        'province_zh':      nfc.get('province_zh', ''),
        'nfc_lat':          nfc['lat'],
        'nfc_lon':          nfc['lon'],
        'designation_year': nfc['designation_year'],
        'name_wup':         name_wup,
        'name_score':       name_score,
        'dist_wup':         dist_wup,
        'dist_km':          dist_km,
    }


# ---------------------------------------------------------------------------
# Assemble city universe (unchanged from v3)
# ---------------------------------------------------------------------------

def build_cities_full(wup_df: pd.DataFrame, match_report: pd.DataFrame) -> pd.DataFrame:
    """
    Include all (already-filtered) WUP Chinese cities.  Matched NFC cities get
    treated=1; conflict and unmatched rows are excluded until resolved.
    """
    excluded = match_report[match_report['recommended_wup'].astype(str) == '']
    if len(excluded):
        print(f"\n  WARNING: {len(excluded)} NFC cities excluded from cities_full.csv:")
        for _, r in excluded.iterrows():
            print(f"    [{r['status']}] {r['nfc_name_en']}")
        print(f"  Resolve via data/build_city_universe/source/manual_overrides.csv")

    nfc_lookup = {}
    for _, mr in match_report[match_report['recommended_wup'].astype(str) != ''].iterrows():
        nfc_lookup[mr['recommended_wup']] = {
            'city_name_nfc':    mr['nfc_name_en'],
            'city_name_zh':     mr['nfc_name_zh'],
            'province_en':      mr['province_en'],
            'province_zh':      mr['province_zh'],
            'designation_year': mr['designation_year'],
            'match_status':     mr['status'],
            'match_dist_km':    mr['dist_km'],
            'match_name_score': mr['name_score'],
        }

    rows = []
    for i, wup_row in wup_df.iterrows():
        nfc = nfc_lookup.get(wup_row['city_name_wup'])
        row = {
            'city_id':          i + 1,
            'city_name':        wup_row['city_name_wup'],
            'city_name_nfc':    nfc['city_name_nfc']    if nfc else '',
            'city_name_zh':     nfc['city_name_zh']     if nfc else '',
            'lat':              wup_row['lat_wup'],
            'lon':              wup_row['lon_wup'],
            'province_en':      nfc['province_en']      if nfc else '',
            'province_zh':      nfc['province_zh']      if nfc else '',
            'treated':          1                        if nfc else 0,
            'designation_year': nfc['designation_year'] if nfc else pd.NA,
            'match_status':     nfc['match_status']     if nfc else '',
            'match_dist_km':    nfc['match_dist_km']    if nfc else pd.NA,
            'match_name_score': nfc['match_name_score'] if nfc else pd.NA,
        }
        for col in [c for c in wup_df.columns if c.startswith('pop_')]:
            row[col] = wup_row[col]
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.sort_values(['treated', 'city_name'], ascending=[False, True]).reset_index(drop=True)
    df['city_id'] = range(1, len(df) + 1)
    return df


# ---------------------------------------------------------------------------
# Validation (unchanged from v3)
# ---------------------------------------------------------------------------

def validate_output(cities_df: pd.DataFrame) -> None:
    errors = []

    no_coords = cities_df[cities_df['lat'].isna() | cities_df['lon'].isna()]
    if len(no_coords):
        errors.append(f"{len(no_coords)} cities missing coordinates")

    west, south, east, north = CHINA_BBOX
    margin = 5.0
    outside = cities_df[
        (cities_df['lat'] < south - margin) | (cities_df['lat'] > north + margin) |
        (cities_df['lon'] < west  - margin) | (cities_df['lon'] > east  + margin)
    ]
    if len(outside):
        errors.append(f"{len(outside)} cities outside China bbox: "
                      + ', '.join(outside['city_name'].tolist()))

    treated_no_year = cities_df[
        (cities_df['treated'] == 1) & cities_df['designation_year'].isna()
    ]
    if len(treated_no_year):
        errors.append(f"{len(treated_no_year)} treated cities missing designation_year: "
                      + ', '.join(treated_no_year['city_name'].tolist()))

    valid_years = cities_df['designation_year'].dropna()
    if len(valid_years) and valid_years.min() < 2004:
        errors.append(f"Designation years before 2004: "
                      + str(sorted(valid_years[valid_years < 2004].tolist())))

    if errors:
        print("\nVALIDATION ERRORS:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"  Total cities (prefecture-level only) : {len(cities_df)}")
    print(f"  Treated (NFC)                        : {int(cities_df['treated'].sum())}")
    print(f"  Control (untreated, prefecture-level): {int((cities_df['treated'] == 0).sum())}")
    years = cities_df['designation_year'].dropna()
    if len(years):
        print(f"  Designation years                    : {int(years.min())} - {int(years.max())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("BUILD CITY UNIVERSE (v4 -- prefecture-level filter)")
    print("=" * 70)
    print()

    # Check required inputs
    missing = []
    if not WUP_FILE.exists():
        missing.append(str(WUP_FILE))
    if not NFC_TREATMENT_LIST_FILE.exists():
        missing.append(str(NFC_TREATMENT_LIST_FILE))
    if not PREFECTURE_LEVEL_CITIES_FILE.exists():
        missing.append(str(PREFECTURE_LEVEL_CITIES_FILE))
    if missing:
        print("ERROR: Missing required input files:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    print("Loading data...")
    wup_df    = load_wup(WUP_FILE)
    nfc_df    = load_nfc_list(NFC_TREATMENT_LIST_FILE)
    overrides = load_manual_overrides(MANUAL_OVERRIDES_FILE)

    # NEW in v4: restrict WUP pool to prefecture-level cities before matching.
    # This ensures controls are at the same administrative level as treated cities.
    print("\nApplying prefecture-level filter...")
    prefectural_names = load_prefecture_filter(PREFECTURE_LEVEL_CITIES_FILE)
    n_before = len(wup_df)
    wup_df = wup_df[wup_df['city_name_wup'].isin(prefectural_names)].reset_index(drop=True)
    n_after = len(wup_df)
    print(f"  WUP cities before filter : {n_before}")
    print(f"  WUP cities after filter  : {n_after}  ({n_before - n_after} county-level removed)")

    # Warn if any NFC city's later-matched WUP name is not in the filter.
    # (All NFC cities are prefecture-level by programme definition, so this
    # should not happen; if it does, the filter file is incomplete.)
    nfc_names_set = set(nfc_df['city_name_en'].str.strip())
    filter_missing_nfc = [n for n in prefectural_names
                          if n not in set(wup_df['city_name_wup'])]
    if filter_missing_nfc:
        print(f"\n  NOTE: {len(filter_missing_nfc)} names in the prefecture filter "
              f"are not in the WUP file (may be NFC-only names or spelling mismatches):")
        for n in sorted(filter_missing_nfc)[:20]:
            print(f"    '{n}'")

    print(f"\nMatching {len(nfc_df)} NFC cities to {len(wup_df)} WUP "
          f"(prefecture-level) cities...")
    print(f"  Name threshold    : score >= {NAME_SCORE_THRESHOLD}")
    print(f"  Distance threshold: <= {DISTANCE_KM_THRESHOLD} km")
    match_report = match_nfc_to_wup(nfc_df, wup_df, overrides)

    print()
    print("Matching summary:")
    for status in STATUS_SORT_ORDER:
        n = (match_report['status'] == status).sum()
        if n:
            flag = '  <-- REVIEW' if status in ('conflict', 'unmatched') else ''
            print(f"  {status:18s}: {n}{flag}")

    MATCHING_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    match_report.to_csv(MATCHING_REPORT_FILE, index=False)
    print(f"\nMatching report: {MATCHING_REPORT_FILE}")

    print("\nBuilding city universe...")
    cities_df = build_cities_full(wup_df, match_report)

    print("\nValidating...")
    validate_output(cities_df)

    CITIES_FULL_FILE.parent.mkdir(parents=True, exist_ok=True)
    cities_df.to_csv(CITIES_FULL_FILE, index=False)
    print(f"\nSaved {len(cities_df)} cities to {CITIES_FULL_FILE}")

    n_todo = (match_report['recommended_wup'].astype(str) == '').sum()
    print()
    print("=" * 70)
    if n_todo:
        print(f"DONE WITH WARNINGS: {n_todo} NFC cities need resolution")
        print("  Steps to resolve:")
        print("  1. Open matching_report.csv -- conflict/unmatched rows at top")
        print("  2. Identify the correct WUP city name for each")
        print("  3. Add lines to data/build_city_universe/source/manual_overrides.csv:")
        print("       nfc_name_en,wup_city_name")
        print("  4. Re-run this script")
    else:
        print("SUCCESS: All NFC cities matched (prefecture-level universe)")
    print("=" * 70)


if __name__ == "__main__":
    main()
