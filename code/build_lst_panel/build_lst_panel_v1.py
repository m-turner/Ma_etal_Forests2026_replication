"""
Build LST Panel

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/generated/city_buffers_5km.geojson (from create_city_buffers)
    - data/create_city_buffers/generated/city_buffers_10km.geojson (from create_city_buffers)
    - data/create_city_buffers/source/cities_sample.csv (from manual input)
    - data/convert_hdf_to_arrays/generated/**/*.npz (from convert_hdf_to_arrays)

Produces (outputs for later stages):
    - data/build_lst_panel/generated/lst_panel.csv (with _5km and _10km suffixed columns)

Description:
    Processes MODIS LST data for all cities and years to create a city-year panel dataset.
    For each city x year combination, extracts summer mean LST from converted numpy arrays
    using zonal statistics within both 5km and 10km city buffer zones for robustness checks.

Updated: 2026-06-24 - Added 10km buffer support

Usage:
    python code/build_lst_panel/build_lst_panel_v1.py

Expected runtime: ~60 minutes for 10 cities x 26 years x 2 buffers = 520 extractions (uses 24 parallel workers)
"""

import sys
import argparse
from pathlib import Path
import pandas as pd
import geopandas as gpd
from tqdm import tqdm
from multiprocessing import Pool

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    CITIES_FILE,
    LST_PANEL_FILE,
    MODIS_LST_ARRAYS_DIR,
    STUDY_START_YEAR,
    STUDY_END_YEAR,
    MAX_WORKERS,
    BUFFER_RADII_KM,
    PREFECTURE_BOUNDARIES_FILE,
    get_buffer_file
)
from utils.process_lst import process_city_year


def check_required_inputs():
    """
    Check that all required input files exist before starting.
    Fail loudly if any are missing (CRITICAL RULE 2).

    Updated: 2026-06-24 - Check for both 5km and 10km buffer files
    """
    required_files = [
        CITIES_FILE,
    ]

    # Check for both buffer sizes
    for radius_km in BUFFER_RADII_KM:
        required_files.append(get_buffer_file(radius_km))

    required_dirs = [
        MODIS_LST_ARRAYS_DIR,
    ]

    missing = []

    for f in required_files:
        if not f.exists():
            missing.append(f)

    for d in required_dirs:
        if not d.exists():
            missing.append(d)

    if missing:
        print("ERROR: Missing required inputs:")
        for item in missing:
            print(f"  - {item}")
        print()
        print("Please run earlier pipeline stages:")
        print("  1. python code/create_city_buffers/create_city_buffers_v1.py")
        print("  2. python code/download_modis_lst/download_modis_lst_v1.py")
        print("  3. python code/convert_hdf_to_arrays/convert_hdf_to_arrays_v1.py")
        sys.exit(1)


def process_city_year_wrapper(args):
    """
    Wrapper function for multiprocessing.Pool.map().

    Parameters
    ----------
    args : tuple
        (city_row_dict, buffers_gdf, year, lst_data_dir)

    Returns
    -------
    dict or None
        Result dictionary from process_city_year
    """
    city_row_dict, buffers_gdf, year, lst_data_dir = args

    # Convert dict back to Series for process_city_year
    city_row = pd.Series(city_row_dict)

    # Process this city-year combination
    return process_city_year(city_row, buffers_gdf, year, lst_data_dir)


def load_data():
    """
    Load city list and buffers for all buffer sizes.

    Updated: 2026-06-24 - Load both 5km and 10km buffers

    Returns
    -------
    tuple
        (cities_df, buffers_dict) where buffers_dict maps radius_km -> GeoDataFrame
    """
    print("Loading city data...")

    # Load city list
    cities_df = pd.read_csv(CITIES_FILE)
    print(f"  Loaded {len(cities_df)} cities")

    # Load city buffers for each radius
    buffers_dict = {}
    for radius_km in BUFFER_RADII_KM:
        buffer_file = get_buffer_file(radius_km)
        buffers_gdf = gpd.read_file(buffer_file)
        buffers_dict[radius_km] = buffers_gdf
        print(f"  Loaded {len(buffers_gdf)} city buffers ({radius_km}km)")

    # Optionally add prefecture boundaries as an additional extraction geometry.
    # Added 2026-10-06. Run create_prefecture_geometries_v1.py (Step 1b) first.
    if PREFECTURE_BOUNDARIES_FILE.exists():
        pref_gdf = gpd.read_file(PREFECTURE_BOUNDARIES_FILE)
        buffers_dict['pref'] = pref_gdf
        print(f"  Loaded {len(pref_gdf)} prefecture boundaries (pref)")
    else:
        print(f"  Prefecture boundaries not found; skipping _pref extraction")
        print(f"    Run: python code/create_prefecture_geometries/create_prefecture_geometries_v1.py")

    return cities_df, buffers_dict


def check_lst_data():
    """
    Check if LST array data directory exists and has data.

    Returns
    -------
    bool
        True if data exists
    """
    print("\nChecking LST data availability...")

    if not MODIS_LST_ARRAYS_DIR.exists():
        print(f"  ERROR: LST arrays directory not found: {MODIS_LST_ARRAYS_DIR}")
        print(f"  Run 'conda run -n hdf_convert python code/convert_hdf_to_arrays/convert_hdf_to_arrays_v1.py'")
        return False

    # Count available years
    year_dirs = [d for d in MODIS_LST_ARRAYS_DIR.iterdir() if d.is_dir() and d.name.isdigit()]
    years = sorted([int(d.name) for d in year_dirs])

    if not years:
        print(f"  ERROR: No LST data found in {MODIS_LST_ARRAYS_DIR}")
        return False

    print(f"  Found LST data for {len(years)} years: {min(years)}-{max(years)}")

    # Check for at least some files (numpy array format)
    total_files = sum(len(list((MODIS_LST_ARRAYS_DIR / str(year)).glob('*.npz'))) for year in years)
    print(f"  Total LST array files: {total_files}")

    return True


def build_panel(cities_df, buffers_dict, start_year=None):
    """
    Build LST panel for all cities, years, and buffer sizes using parallel processing.

    Updated: 2026-06-24 - Process multiple buffer sizes and add suffix to columns
    Updated: 2026-07-02 - Added start_year parameter to support --start-year 1999 for validation

    Parameters
    ----------
    cities_df : pd.DataFrame
        DataFrame with city information
    buffers_dict : dict
        Dictionary mapping radius_km -> GeoDataFrame with buffers
    start_year : int or None
        Override for STUDY_START_YEAR (e.g. 1999 for validation run)

    Returns
    -------
    pd.DataFrame
        Panel data with LST metrics for all buffer sizes
    """
    effective_start = start_year if start_year is not None else STUDY_START_YEAR
    print("\nBuilding LST panel...")
    print(f"  Cities: {len(cities_df)}")
    print(f"  Years: {effective_start}-{STUDY_END_YEAR} ({STUDY_END_YEAR - effective_start + 1} years)")
    print(f"  Buffer sizes: {list(buffers_dict.keys())} km")
    print(f"  Expected observations per buffer: {len(cities_df) * (STUDY_END_YEAR - effective_start + 1)}")
    print(f"  Using {MAX_WORKERS} parallel workers")
    print()

    # Process each buffer size separately
    all_panels = {}

    for radius_km, buffers_gdf in buffers_dict.items():
        # Build display label: '5km', '10km', ..., or 'pref' for prefecture polygons
        # Added 2026-10-06: handle non-integer key 'pref'
        label = f'{radius_km}km' if isinstance(radius_km, int) else str(radius_km)
        print(f"{'-' * 70}")
        print(f"Processing {label} buffers...")
        print(f"{'-' * 70}")

        # Prepare arguments for parallel processing
        processing_args = []
        for _, city_row in cities_df.iterrows():
            for year in range(effective_start, STUDY_END_YEAR + 1):
                city_row_dict = city_row.to_dict()
                processing_args.append((city_row_dict, buffers_gdf, year, MODIS_LST_ARRAYS_DIR))

        # Process all city-year combinations in parallel
        total_iterations = len(processing_args)
        print(f"Processing {total_iterations} city-year combinations...")

        with Pool(MAX_WORKERS) as pool:
            results = list(tqdm(
                pool.imap_unordered(process_city_year_wrapper, processing_args),
                total=total_iterations,
                desc=f"LST {label}",
                unit="city-year"
            ))

        # Filter out None results
        results = [r for r in results if r is not None]

        # Convert to DataFrame
        panel_df = pd.DataFrame(results)

        # Add suffix to LST columns to distinguish buffer sizes / geographies
        lst_columns = [col for col in panel_df.columns if col.startswith('lst_')]
        rename_dict = {col: f"{col}_{label}" for col in lst_columns}
        panel_df = panel_df.rename(columns=rename_dict)

        print(f"Processed {len(panel_df)} observations for {label} buffer")

        all_panels[radius_km] = panel_df

    # Merge all geometry panels (circular buffers + pref if present)
    print(f"\n{'-' * 70}")
    print("Merging panels from all buffer sizes...")
    print(f"{'-' * 70}")

    panel_keys = list(all_panels.keys())
    merged_panel = all_panels[panel_keys[0]].copy()

    for key in panel_keys[1:]:
        panel = all_panels[key]

        # Get only LST columns plus merge keys
        lst_cols = [col for col in panel.columns if col.startswith('lst_')]
        merge_cols = ['city_id', 'city_name', 'year'] + lst_cols

        merged_panel = merged_panel.merge(
            panel[merge_cols],
            on=['city_id', 'city_name', 'year'],
            how='outer'
        )

    print(f"Final merged panel: {len(merged_panel)} observations")
    print(f"Columns: {list(merged_panel.columns)}")

    return merged_panel


def validate_panel(panel_df):
    """
    Validate the panel dataset.

    Updated: 2026-06-24 - Validate all buffer sizes

    Parameters
    ----------
    panel_df : pd.DataFrame
        Panel dataset

    Returns
    -------
    dict
        Validation statistics
    """
    print("\nValidating panel data...")

    # Get all LST columns (should be lst_summer_mean_5km, lst_summer_mean_10km, etc.)
    lst_columns = [col for col in panel_df.columns if col.startswith('lst_')]

    stats = {
        'total_obs': len(panel_df),
        'cities': panel_df['city_id'].nunique(),
        'years': panel_df['year'].nunique()
    }

    print(f"  Total observations: {stats['total_obs']}")
    print(f"  Unique cities: {stats['cities']}")
    print(f"  Years covered: {stats['years']}")
    print()

    # Validate each buffer size separately
    for col in lst_columns:
        print(f"  {col}:")
        complete = panel_df[col].notna().sum()
        missing = panel_df[col].isna().sum()
        print(f"    Complete: {complete}/{stats['total_obs']} ({complete/stats['total_obs']*100:.1f}%)")
        print(f"    Missing: {missing}/{stats['total_obs']} ({missing/stats['total_obs']*100:.1f}%)")

        if complete > 0:
            mean_val = panel_df[col].mean()
            min_val = panel_df[col].min()
            max_val = panel_df[col].max()
            print(f"    Mean: {mean_val:.2f}C")
            print(f"    Min:  {min_val:.2f}C")
            print(f"    Max:  {max_val:.2f}C")

            # Check for unrealistic values
            unrealistic = panel_df[
                (panel_df[col] < -50) |
                (panel_df[col] > 60)
            ]
            if len(unrealistic) > 0:
                print(f"    WARNING: {len(unrealistic)} observations with unrealistic values")
            else:
                print(f"    OK: All values in realistic range")
        print()

        # Store stats for this column
        stats[f'{col}_complete'] = complete
        stats[f'{col}_mean'] = panel_df[col].mean() if complete > 0 else None

    return stats


def save_panel(panel_df, output_file):
    """
    Save panel dataset to CSV.

    Parameters
    ----------
    panel_df : pd.DataFrame
        Panel dataset
    output_file : Path
        Output file path
    """
    print(f"\nSaving panel to: {output_file}")

    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Save to CSV
    panel_df.to_csv(output_file, index=False)

    print(f"Saved {len(panel_df)} observations")
    print(f"  File size: {output_file.stat().st_size / 1024:.1f} KB")


def main():
    """Main execution function - Updated 2026-06-24 for multiple buffer sizes."""
    # Added 2026-07-02: --start-year override so validation run includes 1999
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-year', type=int, default=None,
                        help='Override STUDY_START_YEAR (use 1999 for validation run)')
    args = parser.parse_args()
    start_year = args.start_year  # None means use STUDY_START_YEAR (normal run)

    print("=" * 70)
    print("BUILD LST PANEL PIPELINE")
    print("=" * 70)
    print()

    try:
        # Check required inputs exist
        check_required_inputs()

        # Step 1: Load data
        cities_df, buffers_dict = load_data()

        # Step 2: Check LST data availability
        if not check_lst_data():
            print("\nERROR: LST data not available. Please download and convert data first.")
            sys.exit(1)

        # Step 3: Build panel
        panel_df = build_panel(cities_df, buffers_dict, start_year=start_year)

        # Step 4: Validate
        stats = validate_panel(panel_df)

        # Step 5: Save
        save_panel(panel_df, LST_PANEL_FILE)

        # Success message
        print()
        print("=" * 70)
        print("SUCCESS: LST PANEL CREATED")
        print("=" * 70)
        print()
        print(f"Output file: {LST_PANEL_FILE}")
        print()
        print("Next steps:")
        print("  1. Merge panels: python code/merge_panels/merge_panels_v1.py")
        print()

    except Exception as e:
        print()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
