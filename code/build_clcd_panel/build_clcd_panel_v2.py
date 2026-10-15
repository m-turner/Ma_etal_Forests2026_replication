"""
Build CLCD Panel

Updated from build_clcd_panel_v1 to build_clcd_panel_v2 on 2026-06-25.
Main changes in this version:
- Replaced rasterio with osgeo.gdal for reading CLCD GeoTIFFs.
  rasterio crashes on Windows with a DLL load error (_base import fails).
  osgeo.gdal is installed via conda in environment_geo and works correctly.
- Raster is now read as a windowed numpy array (just the bbox of each buffer),
  exactly like the LST / NDVI / tree-cover panels do with their .npz arrays.
  This keeps memory use low on the ~800 MB national rasters.
- zonal_stats is called with (vector, array, affine=...) instead of a file path,
  so rasterstats never touches rasterio internally.
Previous version saved in _old/ as build_clcd_panel_v1.py.

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/generated/city_buffers_5km.geojson (from create_city_buffers)
    - data/create_city_buffers/generated/city_buffers_10km.geojson (from create_city_buffers)
    - data/create_city_buffers/source/cities_sample.csv (city metadata)
    - data/download_clcd/source/CLCD_v01_YYYY_albert.tif (from download_clcd, one per year)

Produces (outputs for later stages):
    - data/build_clcd_panel/generated/clcd_panel.csv (with _5km and _10km suffixed columns)

Description:
    Extracts the percentage of forest-class pixels within each city buffer for each
    city-year combination, from the CLCD (Landsat-derived, 30m) annual land cover
    product. This is a second, non-MODIS tree-cover measure, used to cross-check the
    MODIS NDVI/tree-cover event studies.

Usage:
    python code/build_clcd_panel/build_clcd_panel_v2.py

Expected runtime: ~10-20 minutes (10 cities x 26 years x 2 buffers = 520 extractions)
"""

import sys
import re
import argparse
from pathlib import Path
import pandas as pd
import geopandas as gpd
from osgeo import gdal, osr
from affine import Affine
from rasterstats import zonal_stats
from multiprocessing import Pool
from tqdm import tqdm

# Tell GDAL to raise Python exceptions on errors rather than returning None silently
gdal.UseExceptions()

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    CITIES_FILE,
    CLCD_PANEL_FILE,
    DOWNLOAD_CLCD_SOURCE_DIR,
    CLCD_FILENAME_TEMPLATE,
    CLCD_FOREST_CLASS_CODE,
    STUDY_START_YEAR,
    STUDY_END_YEAR,
    MAX_WORKERS,
    BUFFER_RADII_KM,
    PREFECTURE_BOUNDARIES_FILE,
    get_buffer_file
)


def check_required_inputs():
    """
    Check that all required input files exist before starting.
    Fail loudly if any are missing (CRITICAL RULE 2).
    """
    required_files = [CITIES_FILE]
    for radius_km in BUFFER_RADII_KM:
        required_files.append(get_buffer_file(radius_km))

    missing = [str(f) for f in required_files if not f.exists()]
    if missing:
        print("ERROR: Missing required inputs:")
        for item in missing:
            print(f"  - {item}")
        print()
        print("Please run earlier pipeline stages first:")
        print("  1. python code/create_city_buffers/create_city_buffers_v1.py")
        sys.exit(1)


def find_clcd_rasters():
    """
    Find available CLCD national GeoTIFFs in the source directory and map them by year.

    Returns
    -------
    dict
        {year (int): Path to GeoTIFF}
    """
    pattern = re.compile(
        '^' + re.escape(CLCD_FILENAME_TEMPLATE).replace(r'\{year\}', r'(\d{4})') + '$'
    )
    rasters = {}
    for path in DOWNLOAD_CLCD_SOURCE_DIR.glob('*.tif'):
        match = pattern.match(path.name)
        if match:
            rasters[int(match.group(1))] = path
    return rasters


def check_data_availability(rasters):
    """
    Check if CLCD data is available for the study period and report coverage.

    Returns
    -------
    bool
        True if at least one study year is available
    """
    print("\nChecking CLCD data availability...")
    study_years = list(range(STUDY_START_YEAR, STUDY_END_YEAR + 1))
    available_years = sorted(y for y in rasters if y in study_years)
    missing_years = [y for y in study_years if y not in rasters]

    if not available_years:
        print(f"  ERROR: No CLCD rasters found in {DOWNLOAD_CLCD_SOURCE_DIR}")
        print(f"  Run: python code/download_clcd/download_clcd_v1.py")
        return False

    print(f"  Found data for {len(available_years)}/{len(study_years)} study years: "
          f"{available_years[0]}-{available_years[-1]}")
    if missing_years:
        print(f"  Warning: Missing years (will have gaps in panel): {missing_years}")
    return True


def read_raster_window(raster_path, buffer_bounds):
    """
    Read a windowed numpy array from a GeoTIFF using GDAL - no rasterio needed.

    Reads only the rectangular pixel area that overlaps buffer_bounds, keeping
    memory use low on the ~800 MB national CLCD rasters.

    Parameters
    ----------
    raster_path : Path or str
        Path to the GeoTIFF.
    buffer_bounds : tuple
        (minx, miny, maxx, maxy) in the raster's own CRS.

    Returns
    -------
    tuple
        (data_array, affine, nodata) or (None, None, None) if no overlap.
        data_array is a 2-D numpy integer array.
        affine is an Affine transform for the window.
        nodata is the raster's nodata value (int/float) or None if not set.
    """
    ds = gdal.Open(str(raster_path))
    gt = ds.GetGeoTransform()   # (x_origin, x_res, 0, y_origin, 0, y_res)
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()

    x_origin, x_res = gt[0], gt[1]
    y_origin, y_res = gt[3], gt[5]   # y_res is negative (rows go south)

    minx, miny, maxx, maxy = buffer_bounds

    # Convert geographic bounds to pixel indices (floor/ceil so we don't clip the edge)
    col_start = max(0, int((minx - x_origin) / x_res))
    row_start = max(0, int((maxy - y_origin) / y_res))   # maxy -> smallest row index
    col_end = min(ds.RasterXSize, int((maxx - x_origin) / x_res) + 2)
    row_end = min(ds.RasterYSize, int((miny - y_origin) / y_res) + 2)   # miny -> largest row index

    ds = None   # Close before returning (safe with gdal.UseExceptions)

    if col_end <= col_start or row_end <= row_start:
        return None, None, None

    # Re-open to read the window (GDAL datasets are not fork-safe; keep lifetime short)
    ds = gdal.Open(str(raster_path))
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray(col_start, row_start,
                            col_end - col_start, row_end - row_start)
    ds = None

    # Build Affine transform for this window (top-left corner of the window)
    x_min = x_origin + col_start * x_res
    y_max = y_origin + row_start * y_res   # y_res negative -> this is the north edge
    affine = Affine(x_res, 0.0, x_min,
                    0.0, y_res, y_max)

    return data, affine, nodata


def get_raster_crs_and_geotransform(raster_path):
    """
    Read CRS (as proj4 string) and geotransform from a GeoTIFF using GDAL.

    Called once per raster file (not per city) to avoid redundant I/O.

    Returns
    -------
    tuple
        (proj4_str, geotransform_tuple)
    """
    ds = gdal.Open(str(raster_path))
    gt = ds.GetGeoTransform()
    proj_wkt = ds.GetProjection()
    ds = None

    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj_wkt)
    proj4 = srs.ExportToProj4()

    return proj4, gt


def extract_clcd_forest_pct(city_row, buffers_gdf, year, raster_path,
                             forest_class_code, raster_proj4, geotransform):
    """
    Extract the percent of forest-class pixels within one city's buffer for one year.

    Uses GDAL for windowed raster reading and rasterstats (numpy-array mode) for
    pixel counting - no rasterio involved anywhere.

    Parameters
    ----------
    city_row : dict
        City metadata (city_id, city_name, ...).
    buffers_gdf : GeoDataFrame
        City buffer polygons in their original CRS.
    year : int
        Year being processed (used only for the return dict).
    raster_path : Path
        Path to the CLCD national GeoTIFF for this year.
    forest_class_code : int
        CLCD class code for "Forest" (typically 2).
    raster_proj4 : str
        Proj4 CRS string for the raster (read once outside this function).
    geotransform : tuple
        GDAL geotransform (x_origin, x_res, 0, y_origin, 0, y_res).

    Returns
    -------
    dict or None
        {'city_id', 'city_name', 'year', 'clcd_forest_pct', 'n_pixels'}, or None on failure.
    """
    try:
        city_id = city_row['city_id']
        city_name = city_row['city_name']

        city_buffer = buffers_gdf[buffers_gdf['city_id'] == city_id]
        if len(city_buffer) == 0:
            return None

        # Reproject city buffer to match raster CRS
        city_buffer_reproj = city_buffer.to_crs(raster_proj4)
        bounds = city_buffer_reproj.total_bounds   # (minx, miny, maxx, maxy)

        # Read only the window of the raster overlapping the buffer
        data, affine, nodata = read_raster_window(raster_path, bounds)
        if data is None:
            return None

        # Use rasterstats in numpy-array mode (no rasterio needed internally)
        stats = zonal_stats(
            city_buffer_reproj,
            data,
            affine=affine,
            categorical=True,
            nodata=nodata
        )

        if not stats or not stats[0]:
            return None

        # zonal_stats returns {class_code: pixel_count, ...}; None key = nodata pixels
        class_counts = {k: v for k, v in stats[0].items() if k is not None}
        total_pixels = sum(class_counts.values())

        if total_pixels == 0:
            return None

        forest_pixels = class_counts.get(forest_class_code, 0)
        forest_pct = forest_pixels / total_pixels * 100

        return {
            'city_id': city_id,
            'city_name': city_name,
            'year': year,
            'clcd_forest_pct': forest_pct,
            'n_pixels': total_pixels
        }

    except Exception as e:
        print(f"  Warning: Error processing {city_row.get('city_name')} for {year}: {e}")
        return None


def process_city_year_wrapper(args):
    """Wrapper for multiprocessing.Pool.imap."""
    city_row, buffers_gdf, year, raster_path, forest_class_code, proj4, gt = args
    return extract_clcd_forest_pct(city_row, buffers_gdf, year, raster_path,
                                   forest_class_code, proj4, gt)


def main():
    """Main execution function."""
    # Added 2026-07-02: --start-year override for validation runs (includes 1999)
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-year', type=int, default=None,
                        help='Override STUDY_START_YEAR (use 1999 for validation run)')
    args = parser.parse_args()
    start_year = args.start_year

    print("=" * 70)
    print("BUILD CLCD PANEL (v2 - GDAL backend)")
    print("=" * 70)
    print()

    try:
        check_required_inputs()

        rasters = find_clcd_rasters()
        if not check_data_availability(rasters):
            sys.exit(1)

        print("\nLoading city metadata...")
        cities = pd.read_csv(CITIES_FILE)
        print(f"  Loaded {len(cities)} cities")

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

        # Added 2026-07-02: use start_year override for validation runs (includes 1999)
        effective_start = start_year if start_year is not None else STUDY_START_YEAR
        study_years = [y for y in range(effective_start, STUDY_END_YEAR + 1)
                       if y in rasters]

        print(f"\nProcessing CLCD forest cover for {len(BUFFER_RADII_KM)} buffer sizes: "
              f"{BUFFER_RADII_KM} km")
        print(f"  Cities: {len(cities)}")
        print(f"  Years available: {len(study_years)} of {STUDY_END_YEAR - effective_start + 1}")
        print(f"  Using {MAX_WORKERS} parallel workers")
        print()

        # Read CRS and geotransform once per raster year (not once per city-year)
        # to avoid redundant GDAL I/O inside each worker call.
        print("Reading raster metadata (CRS + geotransform) for each year...")
        raster_meta = {}
        for year in study_years:
            proj4, gt = get_raster_crs_and_geotransform(rasters[year])
            raster_meta[year] = (proj4, gt)
        print(f"  Done ({len(raster_meta)} files)")

        all_panels = {}

        for radius_km, buffers_gdf in buffers_dict.items():
            # Build display label: '5km', '10km', ..., or 'pref' for prefecture polygons
            # Added 2026-10-06: handle non-integer key 'pref'
            label = f'{radius_km}km' if isinstance(radius_km, int) else str(radius_km)
            print(f"\n{'-' * 70}")
            print(f"Processing {label} buffers...")
            print(f"{'-' * 70}")

            processing_args = []
            for _, city_row in cities.iterrows():
                for year in study_years:
                    proj4, gt = raster_meta[year]
                    processing_args.append((
                        city_row.to_dict(),
                        buffers_gdf,
                        year,
                        rasters[year],
                        CLCD_FOREST_CLASS_CODE,
                        proj4,
                        gt
                    ))

            total_iterations = len(processing_args)
            print(f"Processing {total_iterations} city-year combinations...")

            with Pool(MAX_WORKERS) as pool:
                results = list(tqdm(
                    pool.imap(process_city_year_wrapper, processing_args),
                    total=total_iterations,
                    desc=f"CLCD {label}",
                    unit="city-year"
                ))

            results = [r for r in results if r is not None]

            if len(results) == 0:
                print(f"\nERROR: No CLCD data extracted for {label} buffer")
                print("Check that CLCD rasters exist and overlap the city buffers")
                sys.exit(1)

            panel_df = pd.DataFrame(results)

            # Rename clcd_ columns to add buffer-size / geography suffix
            clcd_columns = [col for col in panel_df.columns if col.startswith('clcd_')]
            rename_dict = {col: f"{col}_{label}" for col in clcd_columns}
            panel_df = panel_df.rename(columns=rename_dict)

            print(f"Processed {len(panel_df)} observations for {label} buffer")
            all_panels[radius_km] = panel_df

        print(f"\n{'-' * 70}")
        print("Merging panels from all buffer sizes...")
        print(f"{'-' * 70}")

        panel_keys = list(all_panels.keys())
        merged_panel = all_panels[panel_keys[0]].copy()

        for radius_km in panel_keys[1:]:
            panel = all_panels[radius_km]
            clcd_cols = [col for col in panel.columns if col.startswith('clcd_')]
            merge_cols = ['city_id', 'city_name', 'year'] + clcd_cols
            merged_panel = merged_panel.merge(
                panel[merge_cols],
                on=['city_id', 'city_name', 'year'],
                how='outer'
            )

        print(f"Final merged panel: {len(merged_panel)} observations")
        print(f"Columns: {list(merged_panel.columns)}")

        merged_panel = merged_panel.sort_values(['city_id', 'year']).reset_index(drop=True)

        print("\nValidating CLCD data...")
        for radius_km in BUFFER_RADII_KM:
            pct_col = f'clcd_forest_pct_{radius_km}km'
            if pct_col in merged_panel.columns:
                min_val = merged_panel[pct_col].min()
                max_val = merged_panel[pct_col].max()
                avg_val = merged_panel[pct_col].mean()
                print(f"  {pct_col}: {min_val:.1f}% - {max_val:.1f}% (avg: {avg_val:.1f}%)")
                if min_val < 0 or max_val > 100:
                    print(f"    WARNING: Values outside expected range (0-100%)!")
                else:
                    print(f"    OK: All values in valid range")

        print(f"\nSaving CLCD panel to {CLCD_PANEL_FILE}...")
        CLCD_PANEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        merged_panel.to_csv(CLCD_PANEL_FILE, index=False)
        print(f"Saved {len(merged_panel)} observations")
        print(f"  File size: {CLCD_PANEL_FILE.stat().st_size / 1024:.1f} KB")

        print()
        print("=" * 70)
        print("SUCCESS: CLCD PANEL COMPLETE")
        print("=" * 70)
        print()
        print(f"Output file: {CLCD_PANEL_FILE}")
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
