"""
Build Tree Cover Panel

Updated from build_tree_cover_panel_v1 to build_tree_cover_panel_v2 on 2026-06-25.
Main changes in this version:
- CRITICAL BUG FIX: v1 ignored the city buffer geometry entirely. It loaded all
  pixels from every MODIS tile for the whole year and averaged them, so every
  city received the same tile-wide average and the 5km / 10km columns were
  numerically identical. The event study R^2 = 1.000 for 10km and the
  "spike at tau+1" were artifacts of this wrong aggregation.
- Extraction now mirrors the LST and NDVI panel builders:
    1. Parse tile ID from the .npz filename (hXXvYY).
    2. Build an Affine transform at MOD44B's 250m resolution
       (pixel size = MODIS_PIXEL_SIZE/4; tile origin formula unchanged).
    3. Reproject the city buffer to the MODIS Sinusoidal CRS.
    4. Call rasterstats.zonal_stats with the numpy array + affine so only
       pixels inside the buffer contribute to the statistics.
Previous version saved in _old/ as build_tree_cover_panel_v1.py.

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/generated/city_buffers_5km.geojson (from create_city_buffers)
    - data/create_city_buffers/generated/city_buffers_10km.geojson (from create_city_buffers)
    - data/create_city_buffers/source/cities_sample.csv (city metadata)
    - data/convert_tree_cover_to_arrays/generated/**/*.npz (from convert_tree_cover_to_arrays)

Produces (outputs for later stages):
    - data/build_tree_cover_panel/generated/tree_cover_panel.csv (with _5km and _10km columns)

Usage:
    python code/build_tree_cover_panel/build_tree_cover_panel_v2.py

Expected runtime: ~10-20 minutes (10 cities x 26 years x 2 buffers = 520 extractions)
"""

import sys
import argparse
from pathlib import Path
import pandas as pd
import geopandas as gpd
import numpy as np
from affine import Affine
from rasterstats import zonal_stats
from multiprocessing import Pool
from tqdm import tqdm

# Add code root to path for shared utilities
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    CITIES_FILE,
    TREE_COVER_PANEL_FILE,
    MODIS_TREE_COVER_ARRAYS_DIR,
    TREE_COVER_NODATA,
    STUDY_START_YEAR,
    STUDY_END_YEAR,
    MAX_WORKERS,
    BUFFER_RADII_KM,
    PREFECTURE_BOUNDARIES_FILE,
    get_buffer_file
)
# Re-use the MODIS sinusoidal CRS definition and tile-ID parser from the LST module
from utils.process_lst import (
    MODIS_SINUSOIDAL_CRS,
    MODIS_TILE_SIZE,
    MODIS_PIXEL_SIZE,
    get_modis_tile_from_filename,
)

# MOD44B (tree cover) is at 250m resolution: 4800x4800 pixels per tile.
# The tile origin is computed the same way as for 1km products (same physical
# boundaries), but the pixel size is one quarter as large.
TREE_COVER_PIXEL_SIZE = MODIS_PIXEL_SIZE / 4   # ~231.656 m


def get_tree_cover_tile_affine(h, v):
    """
    Affine transform for one MOD44B tile at 250m resolution.

    Tile origin formula is identical to the 1km version in process_lst.py;
    only the pixel size differs.
    """
    x_origin = (h - 18) * MODIS_TILE_SIZE * MODIS_PIXEL_SIZE
    y_origin = (9 - v) * MODIS_TILE_SIZE * MODIS_PIXEL_SIZE
    return Affine(TREE_COVER_PIXEL_SIZE, 0.0, x_origin,
                  0.0, -TREE_COVER_PIXEL_SIZE, y_origin)


def extract_tree_cover_for_city(city_buffer_gdf, npz_path):
    """
    Extract zonal statistics for one MOD44B .npz file within a city buffer.

    Parameters
    ----------
    city_buffer_gdf : GeoDataFrame
        Single-row GeoDataFrame with the city buffer polygon, in any CRS.
    npz_path : Path
        Path to a .npz file produced by convert_tree_cover_to_arrays_v1.py.

    Returns
    -------
    dict or None
        {'mean', 'min', 'max', 'std', 'count'} of tree cover % inside the
        buffer, or None if no valid pixels overlap the buffer.
    """
    try:
        npz = np.load(npz_path)
        data = npz['data'].copy()            # uint8 array, 0-100 = % cover, >200 = nodata
        fill_value = int(npz['fill_value'])

        # 2026-06-28 bug fix: the HDF4 _FillValue attribute is 255 (true fill), but
        # MOD44B also uses 200=water, 253=unclassified, 254=cloud as special values.
        # All of these are invalid for tree-cover analysis.  Normalise every pixel
        # with value >= 200 to fill_value so that zonal_stats excludes them all.
        data[data >= 200] = fill_value

        # Get tile ID from the filename embedded in the archive
        fname = npz['filename']
        if hasattr(fname, 'item'):
            fname = fname.item()
        if isinstance(fname, bytes):
            fname = fname.decode('utf-8')

        h, v = get_modis_tile_from_filename(fname)
        affine = get_tree_cover_tile_affine(h, v)

        # Reproject buffer to MODIS sinusoidal before calling zonal_stats
        city_sin = city_buffer_gdf.to_crs(MODIS_SINUSOIDAL_CRS)

        stats = zonal_stats(
            city_sin,
            data,
            affine=affine,
            stats=['mean', 'min', 'max', 'std', 'count'],
            nodata=fill_value
        )

        if stats and stats[0] and stats[0]['count'] and stats[0]['count'] > 0:
            return {
                'mean': float(stats[0]['mean']),
                'min':  float(stats[0]['min']),
                'max':  float(stats[0]['max']),
                'std':  float(stats[0]['std']),
                'count': int(stats[0]['count']),
            }
        return None

    except Exception as e:
        print(f"  Warning: Error reading {npz_path.name}: {e}")
        return None


def extract_tree_cover_city_year(city_row, buffers_gdf, year, arrays_dir):
    """
    Aggregate tree cover statistics for one city in one year across all MODIS tiles.

    MOD44B is annual, so there is one (or a few) .npz file(s) per year covering
    the study area. We extract from every tile and merge the statistics.

    Parameters
    ----------
    city_row : dict
        City metadata dict with 'city_id' and 'city_name'.
    buffers_gdf : GeoDataFrame
        Buffer polygons for all cities.
    year : int
        Year to extract.
    arrays_dir : Path
        Root directory of converted .npz arrays.

    Returns
    -------
    dict or None
    """
    try:
        city_id = city_row['city_id']
        city_name = city_row['city_name']

        city_buffer = buffers_gdf[buffers_gdf['city_id'] == city_id]
        if len(city_buffer) == 0:
            return None

        year_dir = arrays_dir / str(year)
        if not year_dir.exists():
            return None

        npz_files = list(year_dir.glob('*.npz'))
        if not npz_files:
            return None

        # Collect per-tile stats for any tile that overlaps this buffer
        tile_means = []
        tile_mins  = []  # 2026-06-28 bug fix: track per-tile mins/maxs separately
        tile_maxs  = []  # (previously used np.min/max of tile_means, which gives
        tile_stds  = []  #  min/max of MEANS rather than true pixel min/max, and
        total_pixels = 0  # makes min=mean=max for single-tile buffers)

        for npz_path in npz_files:
            tile_stats = extract_tree_cover_for_city(city_buffer, npz_path)
            if tile_stats:
                tile_means.append(tile_stats['mean'])
                tile_mins.append(tile_stats['min'])
                tile_maxs.append(tile_stats['max'])
                tile_stds.append(tile_stats['std'])
                total_pixels += tile_stats['count']

        if not tile_means:
            return None

        # Combine tiles: weighted mean would be ideal but we don't have pixel weights
        # per tile here; simple average across tiles is consistent with the LST approach
        return {
            'city_id':          city_id,
            'city_name':        city_name,
            'year':             year,
            'tree_cover_mean':  float(np.mean(tile_means)),
            'tree_cover_min':   float(np.min(tile_mins)),   # min of per-tile minimums
            'tree_cover_max':   float(np.max(tile_maxs)),   # max of per-tile maximums
            'tree_cover_std':   float(np.mean(tile_stds)),
            'n_pixels':         total_pixels,
        }

    except Exception as e:
        print(f"  Warning: Error processing {city_row.get('city_name')} for {year}: {e}")
        return None


def process_city_year_wrapper(args):
    """Wrapper for multiprocessing.Pool.imap."""
    city_row, buffers_gdf, year, arrays_dir = args
    return extract_tree_cover_city_year(city_row, buffers_gdf, year, arrays_dir)


def check_required_inputs():
    required_files = [CITIES_FILE]
    for radius_km in BUFFER_RADII_KM:
        required_files.append(get_buffer_file(radius_km))

    missing = [str(f) for f in required_files if not f.exists()]
    if not MODIS_TREE_COVER_ARRAYS_DIR.exists():
        missing.append(str(MODIS_TREE_COVER_ARRAYS_DIR))

    if missing:
        print("ERROR: Missing required inputs:")
        for item in missing:
            print(f"  - {item}")
        sys.exit(1)


def check_data_availability():
    year_dirs = [d for d in MODIS_TREE_COVER_ARRAYS_DIR.iterdir()
                 if d.is_dir() and d.name.isdigit()]
    years = sorted(int(d.name) for d in year_dirs)

    if not years:
        print(f"  ERROR: No tree cover data found in {MODIS_TREE_COVER_ARRAYS_DIR}")
        return False

    total_files = sum(
        len(list((MODIS_TREE_COVER_ARRAYS_DIR / str(y)).glob('*.npz')))
        for y in years
    )
    print(f"  Found data for {len(years)} years: {years[0]}-{years[-1]} "
          f"({total_files} total .npz files)")
    return total_files > 0


def main():
    """Main execution function."""
    # Added 2026-07-02: --start-year override for validation runs (includes 1999)
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-year', type=int, default=None,
                        help='Override STUDY_START_YEAR (use 1999 for validation run)')
    args = parser.parse_args()
    start_year = args.start_year

    print("=" * 70)
    print("BUILD TREE COVER PANEL (v2 - buffer-aware extraction)")
    print("=" * 70)
    print()

    try:
        check_required_inputs()

        print("\nChecking tree cover data availability...")
        if not check_data_availability():
            sys.exit(1)

        print("\nLoading city metadata...")
        cities = pd.read_csv(CITIES_FILE)
        print(f"  Loaded {len(cities)} cities")

        print("\nLoading city buffers...")
        buffers_dict = {}
        for radius_km in BUFFER_RADII_KM:
            buffers_gdf = gpd.read_file(get_buffer_file(radius_km))
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

        # Added 2026-07-02: --start-year lets validation run include 1999
        effective_start = start_year if start_year is not None else STUDY_START_YEAR

        print(f"\nProcessing tree cover for buffer sizes: {BUFFER_RADII_KM} km")
        print(f"  Cities: {len(cities)}, Years: {effective_start}-{STUDY_END_YEAR}")
        print(f"  Workers: {MAX_WORKERS}")
        print()

        all_panels = {}

        for radius_km, buffers_gdf in buffers_dict.items():
            # Build display label: '5km', '10km', ..., or 'pref' for prefecture polygons
            # Added 2026-10-06: handle non-integer key 'pref'
            label = f'{radius_km}km' if isinstance(radius_km, int) else str(radius_km)
            print(f"{'-' * 70}")
            print(f"Processing {label} buffers...")
            print(f"{'-' * 70}")

            processing_args = [
                (city_row.to_dict(), buffers_gdf, year, MODIS_TREE_COVER_ARRAYS_DIR)
                for _, city_row in cities.iterrows()
                for year in range(effective_start, STUDY_END_YEAR + 1)
            ]

            with Pool(MAX_WORKERS) as pool:
                results = list(tqdm(
                    pool.imap(process_city_year_wrapper, processing_args),
                    total=len(processing_args),
                    desc=f"Tree Cover {label}",
                    unit="city-year"
                ))

            results = [r for r in results if r is not None]

            if not results:
                print(f"\nERROR: No tree cover data extracted for {label} buffer")
                sys.exit(1)

            panel_df = pd.DataFrame(results)

            # Add buffer-size / geography suffix to tree_cover_ columns
            tc_cols = [c for c in panel_df.columns if c.startswith('tree_cover_')]
            panel_df = panel_df.rename(columns={c: f"{c}_{label}" for c in tc_cols})

            print(f"  Extracted {len(panel_df)} observations")
            all_panels[radius_km] = panel_df

        # Merge all buffer sizes
        print(f"\n{'-' * 70}")
        print("Merging buffer-size panels...")
        panel_keys = list(all_panels.keys())
        merged = all_panels[panel_keys[0]].copy()
        for radius_km in panel_keys[1:]:
            panel = all_panels[radius_km]
            tc_cols = [c for c in panel.columns if c.startswith('tree_cover_')]
            merged = merged.merge(
                panel[['city_id', 'city_name', 'year'] + tc_cols],
                on=['city_id', 'city_name', 'year'],
                how='outer'
            )

        merged = merged.sort_values(['city_id', 'year']).reset_index(drop=True)

        # Validate
        print("\nValidating tree cover data...")
        for radius_km in BUFFER_RADII_KM:
            col = f'tree_cover_mean_{radius_km}km'
            if col in merged.columns:
                lo, hi, avg = merged[col].min(), merged[col].max(), merged[col].mean()
                print(f"  {col}: {lo:.1f}% - {hi:.1f}% (avg: {avg:.1f}%)")
                if lo < 0 or hi > 100:
                    print(f"    WARNING: Values outside 0-100%!")
                else:
                    print(f"    OK")

        # Check that 5km and 10km are actually different (would have been identical in v1)
        col_5  = 'tree_cover_mean_5km'
        col_10 = 'tree_cover_mean_10km'
        if col_5 in merged.columns and col_10 in merged.columns:
            if merged[col_5].equals(merged[col_10]):
                print("  WARNING: 5km and 10km columns are identical - check extraction logic")
            else:
                n_diff = (merged[col_5] != merged[col_10]).sum()
                print(f"  OK: 5km and 10km differ in {n_diff}/{len(merged)} rows (expected)")

        # Save
        TREE_COVER_PANEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(TREE_COVER_PANEL_FILE, index=False)
        print(f"\nSaved {len(merged)} observations to {TREE_COVER_PANEL_FILE}")
        print(f"  File size: {TREE_COVER_PANEL_FILE.stat().st_size / 1024:.1f} KB")

        print()
        print("=" * 70)
        print("SUCCESS: TREE COVER PANEL COMPLETE")
        print("=" * 70)
        print()
        print("Next steps: run merge_panels (Step 5) then run_analysis (Step 6)")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
