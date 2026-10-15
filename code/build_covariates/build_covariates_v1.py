"""
Build City Covariates

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/source/WUP2018-F22-Cities_Over_300K_Annual_V7.xls
      (WUP 2018 file with city population at 5-year intervals 1950-2035)
    - data/build_city_universe/generated/cities_full.csv (from build_city_universe)

Produces (outputs for later stages):
    - data/merge_panels/generated/city_covariates.csv
      (Updated 2026-07-20: redirected to merge_panels/generated/ for replication
      transparency -- all analysis inputs are now co-located there.)

Description:
    Extracts static city-level pre-treatment covariates for use in the
    Callaway-Sant'Anna (2021) doubly robust DiD estimator:

        - lat, lon   : city centroid from WUP (already in cities_full.csv,
                       included here so the covariate file is self-contained)
        - pop_pretx  : city population in the year before NFC designation
                       (designation_year - 1 for treated cities; 2003 for
                       never-treated controls, i.e. one year before the first
                       NFC wave in 2004). Interpolated linearly from WUP 5-year
                       snapshots (pop_2000, pop_2005, pop_2010, pop_2015, pop_2020).
        - log_pop_pretx : natural log of pop_pretx (used as regressor)

    For the propensity-score and outcome-regression models in the DR estimator,
    covariates must be measured BEFORE treatment. Using city-specific pre-treatment
    population (rather than a fixed year) is cleanest for treated cities; using 2003
    for controls mirrors the first cohort's pre-treatment year.

Usage:
    python code/build_covariates/build_covariates_v1.py

Expected runtime: < 1 minute
"""

import sys
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    WUP_FILE,
    CITIES_FULL_FILE,
    CITY_COVARIATES_FILE,
)

# Year to use as "t=-1" for never-treated controls.
# 2003 is one year before the first NFC cohort (2004), matching the earliest
# treated city's pre-treatment window.
CONTROL_BASE_YEAR = 2003

# Years we need population for.  WUP File 22 has ANNUAL estimates (POP1950,
# POP1951, ..., POP2020+) so we include every year from 1999 to 2026 to
# cover any designation_year - 1 target we might encounter.
POP_YEARS = list(range(1999, 2027))


def check_required_inputs():
    missing = []
    for f in [WUP_FILE, CITIES_FULL_FILE]:
        if not f.exists():
            missing.append(str(f))
    if missing:
        print("ERROR: Missing required inputs:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


def normalise_name(name: str) -> str:
    """Lower-case, strip diacritics and punctuation, drop admin suffixes."""
    if not isinstance(name, str):
        return ''
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_only = ''.join(c for c in nfkd if not unicodedata.combining(c))
    clean = re.sub(r"['\-\s\.]+", '', ascii_only.lower())
    clean = re.sub(r'(city|shi)$', '', clean)
    return clean


def load_wup_population(wup_path: Path) -> pd.DataFrame:
    """
    Read WUP file and return a DataFrame with city_name_wup and pop_YYYY columns.

    Uses the same flexible header-scan logic as build_city_universe_v3.py.
    Returns only China rows with valid coordinates.
    """
    print(f"  Reading WUP file: {wup_path.name}")
    engine = 'xlrd' if wup_path.suffix.lower() == '.xls' else 'openpyxl'

    df = None
    country_col = city_col = None
    for header_row in range(25):
        try:
            candidate = pd.read_excel(wup_path, header=header_row, engine=engine)
            cols = [str(c).strip() for c in candidate.columns]
            c_country = next((c for c in cols if 'country' in c.lower()), None)
            c_city    = next((c for c in cols if 'city' in c.lower() or 'agglomeration' in c.lower()), None)
            if c_country and c_city:
                df = candidate
                country_col = c_country
                city_col    = c_city
                break
        except Exception:
            continue

    if df is None:
        print("ERROR: Could not find header row in WUP file.")
        sys.exit(1)

    # China rows only
    china = df[df[country_col].astype(str).str.strip() == 'China'].copy()
    print(f"  Found {len(china)} Chinese cities in WUP")

    lat_col = next((c for c in df.columns if 'lat' in str(c).lower()), None)
    lon_col = next((c for c in df.columns if 'lon' in str(c).lower()), None)
    if not lat_col or not lon_col:
        print("ERROR: Latitude/Longitude columns not found in WUP file.")
        sys.exit(1)

    # Build output DataFrame
    out = pd.DataFrame()
    out['city_name_wup'] = china[city_col].astype(str).str.strip()
    out['name_key']      = out['city_name_wup'].apply(normalise_name)
    out['lat_wup']       = pd.to_numeric(china[lat_col], errors='coerce')
    out['lon_wup']       = pd.to_numeric(china[lon_col], errors='coerce')

    # Population columns: WUP File 22 uses the format 'POP1950', 'POP1951', ...
    # (uppercase prefix + 4-digit year, all stored as strings).  Also handle the
    # less-common variants: bare integer/float year (1950, 1950.0) or lower-case
    # prefix (pop1950), just in case the file version ever changes.
    import re as _re
    pop_year_map = {}   # {int_year: original_column_label}
    for c in df.columns:
        cs = str(c).strip()
        # Format 1: 'POP2000' / 'pop2000' (WUP File 22 format confirmed)
        m = _re.match(r'^[Pp][Oo][Pp](\d{4})$', cs)
        if m:
            yr = int(m.group(1))
            if 1950 <= yr <= 2035:
                pop_year_map[yr] = c
            continue
        # Format 2: bare number (1950, 1950.0) — other WUP file variants
        try:
            yr = int(float(cs))
            if 1950 <= yr <= 2035:
                pop_year_map[yr] = c
        except (ValueError, TypeError):
            pass

    found_years = []
    for yr in POP_YEARS:
        col = pop_year_map.get(yr)
        if col is not None:
            # WUP reports population in thousands; multiply by 1000
            out[f'pop_{yr}'] = (pd.to_numeric(china[col], errors='coerce') * 1000).round(0)
            found_years.append(yr)

    print(f"  Population years available: {min(found_years) if found_years else 'none'}"
          f"–{max(found_years) if found_years else 'none'} ({len(found_years)} years)")
    out = out.dropna(subset=['lat_wup', 'lon_wup']).reset_index(drop=True)
    return out


def interpolate_population(pop_row: pd.Series, target_year: int,
                           available_years: list) -> float:
    """
    Linearly interpolate WUP 5-year population snapshots to an arbitrary year.

    If target_year is exactly a snapshot year, return that value directly.
    If target_year is between two snapshots, interpolate.
    If target_year is outside the range, extrapolate from the nearest pair.

    Parameters
    ----------
    pop_row : Series
        Row with pop_YYYY columns for each year in available_years.
    target_year : int
        Year to interpolate to.
    available_years : list of int
        Sorted list of years with valid population data.

    Returns
    -------
    float or NaN if no data available.
    """
    years = sorted(available_years)

    # Direct hit
    if target_year in years and not pd.isna(pop_row.get(f'pop_{target_year}')):
        return float(pop_row[f'pop_{target_year}'])

    # Find bracketing years
    lower = [y for y in years if y <= target_year]
    upper = [y for y in years if y >= target_year]

    if not lower and not upper:
        return np.nan

    # Extrapolate below range: use lowest two
    if not lower:
        y0, y1 = years[0], years[1]
    # Extrapolate above range: use highest two
    elif not upper:
        y0, y1 = years[-2], years[-1]
    # Interpolate between brackets
    else:
        y0, y1 = lower[-1], upper[0]

    p0 = pop_row.get(f'pop_{y0}', np.nan)
    p1 = pop_row.get(f'pop_{y1}', np.nan)

    if pd.isna(p0) or pd.isna(p1):
        return np.nan

    # Linear interpolation (or extrapolation if target_year outside [y0, y1])
    if y0 == y1:
        return float(p0)

    return float(p0 + (target_year - y0) * (p1 - p0) / (y1 - y0))


def main():
    print("=" * 70)
    print("BUILD CITY COVARIATES (v1)")
    print("=" * 70)
    print()

    try:
        check_required_inputs()

        # --- Load data ---
        print("Loading WUP population data...")
        wup_df = load_wup_population(WUP_FILE)

        print("\nLoading city universe...")
        cities = pd.read_csv(CITIES_FULL_FILE)
        print(f"  Loaded {len(cities)} cities "
              f"({int(cities['treated'].sum())} treated, "
              f"{int((~cities['treated'].astype(bool)).sum())} controls)")

        # --- Match cities_full to WUP by city_name ---
        # city_name in cities_full.csv equals city_name_wup (set by build_city_universe)
        wup_lookup = wup_df.set_index('city_name_wup')

        # Determine which population years are actually available
        available_years = sorted([yr for yr in POP_YEARS if f'pop_{yr}' in wup_df.columns])
        if available_years:
            print(f"\nAvailable WUP population years: {available_years[0]}–{available_years[-1]}"
                  f" ({len(available_years)} years)")
        else:
            print("\nERROR: No population years found in WUP file. Cannot build covariates.")
            sys.exit(1)

        # --- Compute pre-treatment population for each city ---
        # WUP File 22 has annual data, so this is a direct lookup for most cities.
        print("\nLooking up pre-treatment population...")

        rows = []
        n_missing_pop = 0

        for _, city in cities.iterrows():
            city_id   = int(city['city_id'])
            city_name = str(city['city_name'])
            treated   = int(city['treated'])
            lat       = float(city['lat'])
            lon       = float(city['lon'])

            # Target year: designation_year - 1 for treated; CONTROL_BASE_YEAR for controls
            if treated == 1 and not pd.isna(city['designation_year']):
                target_year = int(city['designation_year']) - 1
            else:
                target_year = CONTROL_BASE_YEAR

            # Look up WUP row
            if city_name in wup_lookup.index:
                wup_row = wup_lookup.loc[city_name]
                # If multiple rows match (shouldn't happen), take first
                if isinstance(wup_row, pd.DataFrame):
                    wup_row = wup_row.iloc[0]
                pop = interpolate_population(wup_row, target_year, available_years)
            else:
                pop = np.nan
                n_missing_pop += 1

            if pd.isna(pop) or pop <= 0:
                pop = np.nan
                n_missing_pop += 1

            log_pop = np.log(pop) if not pd.isna(pop) else np.nan

            rows.append({
                'city_id':         city_id,
                'city_name':       city_name,
                'treated':         treated,
                'designation_year': city['designation_year'],
                'pretx_year':      target_year,
                'lat':             lat,
                'lon':             lon,
                'pop_pretx':       pop,
                'log_pop_pretx':   log_pop,
            })

        covariates = pd.DataFrame(rows)

        # Report
        n_valid = covariates['pop_pretx'].notna().sum()
        print(f"  Cities with valid pre-treatment population: {n_valid}/{len(covariates)}")
        if n_missing_pop > 0:
            print(f"  WARNING: {n_missing_pop} cities could not be matched to WUP population")
            missing = covariates[covariates['pop_pretx'].isna()][['city_id', 'city_name', 'treated']]
            for _, r in missing.iterrows():
                print(f"    [{r['treated']}] {r['city_name']}")

        # Summary stats
        print(f"\nPre-treatment population summary:")
        pop_k = covariates['pop_pretx'].dropna() / 1000
        print(f"  Min:    {pop_k.min():,.0f}k")
        print(f"  Median: {pop_k.median():,.0f}k")
        print(f"  Max:    {pop_k.max():,.0f}k")

        # --- Validate completeness ---
        # Warn if more than 5% missing — the DR estimator requires covariates
        missing_pct = covariates['pop_pretx'].isna().mean() * 100
        if missing_pct > 5:
            print(f"\nWARNING: {missing_pct:.1f}% of cities missing population data. "
                  "DR estimator will drop them from estimation.")

        # --- Save ---
        CITY_COVARIATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        covariates.to_csv(CITY_COVARIATES_FILE, index=False)
        print(f"\nSaved {len(covariates)} rows to {CITY_COVARIATES_FILE}")

        print()
        print("=" * 70)
        print("SUCCESS: CITY COVARIATES COMPLETE")
        print("=" * 70)
        print()
        print("Columns: city_id, city_name, treated, designation_year, pretx_year,")
        print("         lat, lon, pop_pretx, log_pop_pretx")
        print()
        print("Next: run merge_panels (Step 5) then run_analysis (Step 6)")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
