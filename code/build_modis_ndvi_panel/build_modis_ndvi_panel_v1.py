"""
Build MODIS NDVI Panel

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/generated/city_buffers_5km.geojson (from create_city_buffers)
    - data/create_city_buffers/generated/city_buffers_10km.geojson (from create_city_buffers)
    - data/create_city_buffers/source/cities_sample.csv (city metadata)
    - data/convert_modis_ndvi_to_arrays/generated/**/*.npz (from convert_modis_ndvi_to_arrays)

Produces (outputs for later stages):
    - data/build_modis_ndvi_panel/generated/modis_ndvi_panel.csv (with _5km and _10km suffixed columns)

Description:
    Extracts zonal statistics for MODIS NDVI (vegetation greenness) within city buffers
    for each city-year combination. MOD13A2 provides 16-day composites, so we aggregate
    all composites within each year to get annual NDVI statistics.

    Key differences from Landsat NDVI:
    - MODIS: 16-day composites, 1km resolution, global coverage
    - Landsat: Monthly, 30m resolution, requires GEE project

    This uses MODIS for simplicity and global coverage without GEE dependencies.

Updated: 2026-06-24 - Added 10km buffer support

Usage:
    python code/build_modis_ndvi_panel/build_modis_ndvi_panel_v1.py

Expected runtime: ~20-30 minutes (10 cities x 26 years x 2 buffers = 520 extractions)
"""

import sys
import argparse
from pathlib import Path
import pandas as pd
import geopandas as gpd
import numpy as np
from multiprocessing import Pool
from tqdm import tqdm

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    CITIES_FILE,
    MODIS_NDVI_PANEL_FILE,
    MODIS_NDVI_ARRAYS_DIR,
    STUDY_START_YEAR,
    STUDY_END_YEAR,
    MAX_WORKERS,
    BUFFER_RADII_KM,
    PREFECTURE_BOUNDARIES_FILE,
    get_buffer_file
)
from utils.process_modis_ndvi import extract_ndvi_for_city_year


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
        MODIS_NDVI_ARRAYS_DIR,
    ]

    missing = []

    for file_path in required_files:
        if not file_path.exists():
            missing.append(str(file_path))

    for dir_path in required_dirs:
        if not dir_path.exists():
            missing.append(str(dir_path))

    if missing:
        print("ERROR: Missing required inputs:")
        for item in missing:
            print(f"  - {item}")
        print()
        print("Please run earlier pipeline stages first:")
        print("  1. python code/create_city_buffers/create_city_buffers_v1.py")
        print("  2. python code/download_modis_ndvi/download_modis_ndvi_v1.py")
        print("  3. python code/convert_modis_ndvi_to_arrays/convert_modis_ndvi_to_arrays_v1.py")
        sys.exit(1)


def extract_ndvi_for_city(city_row, buffers_gdf, year, arrays_dir):
    """
    Extract MODIS NDVI statistics for one city in one year.

    Uses zonal statistics with MODIS sinusoidal grid to aggregate all 16-day
    NDVI composites within the city buffer for the year.

    Parameters
    ----------
    city_row : dict
        City metadata (id, name, lat, lon)
    buffers_gdf : GeoDataFrame
        City buffer polygons
    year : int
        Year to extract
    arrays_dir : Path
        Directory containing .npz files

    Returns
    -------
    dict or None
        Dictionary with NDVI statistics, or None if no data
    """
    try:
        city_id = city_row['city_id']
        city_name = city_row['city_name']

        # Get the buffer polygon for this city
        city_buffer = buffers_gdf[buffers_gdf['city_id'] == city_id]
        if len(city_buffer) == 0:
            return None

        # Year directory path
        year_dir = arrays_dir / str(year)

        # Use utility function to extract zonal statistics
        # This handles MODIS grid reprojection and aggregation across 16-day periods
        stats = extract_ndvi_for_city_year(city_buffer, year_dir)

        if stats:
            return {
                'city_id': city_id,
                'city_name': city_name,
                'year': year,
                'ndvi_mean': stats['mean'],
                'ndvi_min': stats['min'],
                'ndvi_max': stats['max'],
                'ndvi_std': stats['std'],
                'n_pixels': stats['count']
            }
        else:
            return None

    except Exception as e:
        print(f"  Warning: Error processing {city_name} for {year}: {e}")
        return None


def process_city_year_wrapper(args):
    """
    Wrapper for multiprocessing.

    Parameters
    ----------
    args : tuple
        (city_row_dict, buffers_gdf, year, arrays_dir)

    Returns
    -------
    dict or None
    """
    city_row, buffers_gdf, year, arrays_dir = args
    return extract_ndvi_for_city(city_row, buffers_gdf, year, arrays_dir)


def check_data_availability():
    """
    Check if MODIS NDVI data is available and report coverage.

    Returns
    -------
    bool
        True if data is available, False otherwise
    """
    print("\nChecking MODIS NDVI data availability...")

    if not MODIS_NDVI_ARRAYS_DIR.exists():
        print(f"  ERROR: MODIS NDVI arrays directory not found: {MODIS_NDVI_ARRAYS_DIR}")
        print(f"  Run: python code/convert_modis_ndvi_to_arrays/convert_modis_ndvi_to_arrays_v1.py")
        return False

    # Count available years
    year_dirs = [d for d in MODIS_NDVI_ARRAYS_DIR.iterdir() if d.is_dir() and d.name.isdigit()]
    years = sorted([int(d.name) for d in year_dirs])

    if not years:
        print(f"  ERROR: No MODIS NDVI data found in {MODIS_NDVI_ARRAYS_DIR}")
        return False

    print(f"  Found data for {len(years)} years: {years[0]}-{years[-1]}")

    # Check for at least some files (numpy array format)
    total_files = sum(len(list((MODIS_NDVI_ARRAYS_DIR / str(year)).glob('*.npz'))) for year in years)
    print(f"  Total MODIS NDVI array files: {total_files}")

    if total_files == 0:
        print(f"  ERROR: No .npz files found")
        return False

    return True


def main():
    """Main execution function - Updated 2026-06-24 for multiple buffer sizes."""
    # Added 2026-07-02: --start-year override for validation runs (includes 1999)
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-year', type=int, default=None,
                        help='Override STUDY_START_YEAR (use 1999 for validation run)')
    args = parser.parse_args()
    start_year = args.start_year

    print("=" * 70)
    print("BUILD MODIS NDVI PANEL")
    print("=" * 70)
    print()

    try:
        # Check required inputs exist
        check_required_inputs()

        # Check data availability
        if not check_data_availability():
            sys.exit(1)

        # Load city data
        print("\nLoading city metadata...")
        cities = pd.read_csv(CITIES_FILE)
        print(f"  Loaded {len(cities)} cities")

        # Load city buffers for each radius
        print("\nLoading city buffers...")
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

        # Process each buffer size separately
        # Added 2026-07-02: use start_year override when provided (validation run)
        effective_start = start_year if start_year is not None else STUDY_START_YEAR

        print(f"\nProcessing MODIS NDVI for {len(BUFFER_RADII_KM)} buffer sizes: {BUFFER_RADII_KM} km")
        print(f"  Cities: {len(cities)}")
        print(f"  Years: {effective_start}-{STUDY_END_YEAR} ({STUDY_END_YEAR - effective_start + 1} years)")
        print(f"  Expected observations per buffer: {len(cities) * (STUDY_END_YEAR - effective_start + 1)}")
        print(f"  Using {MAX_WORKERS} parallel workers")
        print()

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
            for _, city_row in cities.iterrows():
                for year in range(effective_start, STUDY_END_YEAR + 1):
                    city_row_dict = city_row.to_dict()
                    processing_args.append((city_row_dict, buffers_gdf, year, MODIS_NDVI_ARRAYS_DIR))

            # Process all city-year combinations in parallel
            total_iterations = len(processing_args)
            print(f"Processing {total_iterations} city-year combinations...")

            with Pool(MAX_WORKERS) as pool:
                results = list(tqdm(
                    pool.imap(process_city_year_wrapper, processing_args),
                    total=total_iterations,
                    desc=f"MODIS NDVI {label}",
                    unit="city-year"
                ))

            # Filter out None results
            results = [r for r in results if r is not None]

            if len(results) == 0:
                print(f"\nERROR: No MODIS NDVI data extracted for {label} buffer")
                print("Check that MODIS NDVI array files exist for the study period")
                sys.exit(1)

            # Create panel DataFrame
            panel_df = pd.DataFrame(results)

            # Add suffix to NDVI columns to distinguish buffer sizes / geographies
            ndvi_columns = [col for col in panel_df.columns if col.startswith('ndvi_')]
            rename_dict = {col: f"{col}_{label}" for col in ndvi_columns}
            panel_df = panel_df.rename(columns=rename_dict)

            print(f"Processed {len(panel_df)} observations for {label} buffer")

            all_panels[radius_km] = panel_df

        # Merge all buffer size panels on city_id, city_name, year
        print(f"\n{'-' * 70}")
        print("Merging panels from all buffer sizes...")
        print(f"{'-' * 70}")

        # Start with the first panel (includes city_id, city_name, year)
        panel_keys = list(all_panels.keys())
        merged_panel = all_panels[panel_keys[0]].copy()

        # Merge in additional buffer sizes / geographies (only their NDVI columns)
        for radius_km in panel_keys[1:]:
            panel = all_panels[radius_km]

            # Get only NDVI columns plus merge keys
            ndvi_cols = [col for col in panel.columns if col.startswith('ndvi_')]
            merge_cols = ['city_id', 'city_name', 'year'] + ndvi_cols

            merged_panel = merged_panel.merge(
                panel[merge_cols],
                on=['city_id', 'city_name', 'year'],
                how='outer'
            )

        print(f"Final merged panel: {len(merged_panel)} observations")
        print(f"Columns: {list(merged_panel.columns)}")

        # Sort by city and year
        merged_panel = merged_panel.sort_values(['city_id', 'year']).reset_index(drop=True)

        # Validate data for each buffer size
        print("\nValidating MODIS NDVI data...")
        for radius_km in BUFFER_RADII_KM:
            mean_col = f'ndvi_mean_{radius_km}km'
            if mean_col in merged_panel.columns:
                min_val = merged_panel[mean_col].min()
                max_val = merged_panel[mean_col].max()
                avg_val = merged_panel[mean_col].mean()
                print(f"  {mean_col}: {min_val:.3f} - {max_val:.3f} (avg: {avg_val:.3f})")

                if min_val < -1.0 or max_val > 1.0:
                    print(f"    WARNING: Values outside expected range (-1.0 to 1.0)!")
                else:
                    print(f"    OK: All values in valid range")

        # Save panel
        print(f"\nSaving MODIS NDVI panel to {MODIS_NDVI_PANEL_FILE}...")
        MODIS_NDVI_PANEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        merged_panel.to_csv(MODIS_NDVI_PANEL_FILE, index=False)
        print(f"Saved {len(merged_panel)} observations")
        print(f"  File size: {MODIS_NDVI_PANEL_FILE.stat().st_size / 1024:.1f} KB")

        # Summary
        print()
        print("=" * 70)
        print("SUCCESS: MODIS NDVI PANEL COMPLETE")
        print("=" * 70)
        print()
        print(f"Output file: {MODIS_NDVI_PANEL_FILE}")
        print()
        print("Panel summary:")
        print(f"  Cities: {merged_panel['city_id'].nunique()}")
        print(f"  Years: {merged_panel['year'].min()}-{merged_panel['year'].max()}")
        print(f"  Observations: {len(merged_panel)}")
        print(f"  Buffer sizes: {BUFFER_RADII_KM} km")
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
