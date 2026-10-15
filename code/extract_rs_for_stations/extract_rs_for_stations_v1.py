"""
Extract Remote Sensing Variables for Weather Stations

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/station_buffers/generated/station_buffers_100m.geojson  (from create_station_buffers)
    - data/station_buffers/generated/station_buffers_500m.geojson  (from create_station_buffers)
    - data/station_temps/generated/station_inventory.csv            (from build_station_panel)
    - data/convert_hdf_to_arrays/generated/{year}/MOD11A2.*.npz    (LST; from convert_hdf_to_arrays)
    - data/convert_modis_ndvi_to_arrays/generated/{year}/MOD13A2.*.npz (NDVI; from convert_modis_ndvi_to_arrays)
    - data/convert_tree_cover_to_arrays/generated/{year}/MOD44B.*.npz  (tree cover; from convert_tree_cover_to_arrays)
    - data/download_clcd/source/CLCD_v01_YYYY_albert.tif            (CLCD; from download_clcd)

Produces (outputs for later stages):
    - data/station_rs/generated/station_rs_panel.csv

Description:
    Extracts satellite and land-cover variables at each weather station for every
    study year. Two extraction strategies are used:

    CLCD (30m Landsat — fine enough for small buffers):
        zonal_stats within the 100m and 500m station disk buffers.
        Reuses the same GDAL windowed-read approach as build_clcd_panel_v2.py.
        Columns: clcd_forest_pct_100m, clcd_forest_pct_500m.

    MODIS (500m-1km pixels — coarser than the station buffers):
        Nearest-pixel lookup at the station's exact WGS84 coordinates.
        The station's MODIS tile (h, v) is precomputed once in main() using a
        pyproj WGS84→sinusoidal transform. Each worker then globs only the file
        for that tile, loads it, and reads the pixel at the station location.
        Columns:
            lst_summer_mean_nearest   — MOD11A2, JJA mean, Celsius
            ndvi_summer_mean_nearest  — MOD13A2, JJA mean, scaled -1..1
            tree_cover_nearest        — MOD44B, annual % tree cover

    Processing uses multiprocessing.Pool at the station×year level, matching the
    parallelism pattern used by the city panel builders.

Usage:
    conda activate C:\\py_envs\\environment_geo
    python code\\extract_rs_for_stations\\extract_rs_for_stations_v1.py

Expected runtime: 15-40 minutes (500 stations × 25 years with 24 workers).
"""

import sys
import re
import math
import logging
from pathlib import Path
from datetime import datetime
from multiprocessing import Pool

import numpy as np
import pandas as pd
import geopandas as gpd
from affine import Affine
from osgeo import gdal, osr
from rasterstats import zonal_stats
from pyproj import CRS, Transformer

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT / 'code'))

from utils.config import (
    STATION_INVENTORY_FILE,
    STATION_BUFFERS_100M_FILE,
    STATION_BUFFERS_500M_FILE,
    STATION_RS_PANEL_FILE,
    MODIS_LST_ARRAYS_DIR,
    MODIS_NDVI_ARRAYS_DIR,
    MODIS_TREE_COVER_ARRAYS_DIR,
    DOWNLOAD_CLCD_SOURCE_DIR,
    CLCD_FILENAME_TEMPLATE,
    CLCD_FOREST_CLASS_CODE,
    LST_SCALE_FACTOR,
    LST_KELVIN_OFFSET,
    STUDY_START_YEAR,
    STUDY_END_YEAR,
    MAX_WORKERS,
)
from utils.process_lst import (
    MODIS_TILE_SIZE,
    MODIS_PIXEL_SIZE,
    get_modis_tile_from_filename,
    is_summer_period,
)

gdal.UseExceptions()

LOG_FILE = PROJECT_ROOT / 'last_run_extract_rs_for_stations.log'

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

# PROJ4 string for MODIS sinusoidal — sphere radius matches the WKT in process_lst.py
MODIS_SINU_PROJ4 = (
    "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 "
    "+a=6371007.181 +b=6371007.181 +units=m +no_defs"
)

# Module-level transformer cache: initialized once per worker process on first call
_TRANSFORMER = None


def _get_transformer():
    """Return a cached WGS84 → MODIS sinusoidal Transformer."""
    global _TRANSFORMER
    if _TRANSFORMER is None:
        _TRANSFORMER = Transformer.from_crs(
            "EPSG:4326", CRS.from_proj4(MODIS_SINU_PROJ4), always_xy=True
        )
    return _TRANSFORMER


# ---------------------------------------------------------------------------
# MODIS nearest-pixel helpers
# ---------------------------------------------------------------------------

def compute_modis_tile(lat, lon):
    """
    Return the MODIS (h, v) tile indices containing (lat, lon).
    Called once per station in main() and stored in the station dict
    so workers don't repeat the projection.
    """
    transformer = _get_transformer()
    x_sin, y_sin = transformer.transform(lon, lat)
    tile_width = MODIS_TILE_SIZE * MODIS_PIXEL_SIZE
    h = math.floor(x_sin / tile_width) + 18
    v = math.floor(-y_sin / tile_width) + 9
    return int(h), int(v)


def _extract_filename_str(npz):
    """Pull the original HDF4 filename string out of an npz metadata field."""
    fn = npz['filename']
    if hasattr(fn, 'item'):
        fn = fn.item()
    if isinstance(fn, bytes):
        fn = fn.decode('utf-8')
    return str(fn)


def load_and_lookup(lat, lon, npz_path):
    """
    Load a MODIS npz file and return the pixel value at (lat, lon).

    Auto-detects pixel size from the array shape:
        (1200, 1200) → 1 km product (LST, NDVI)
        (2400, 2400) → 500 m product (tree cover MOD44B)

    Returns
    -------
    (raw_value, npz_data) if the station falls within this tile, else (None, npz_data).
    raw_value is None if the pixel is fill/nodata.
    npz_data allows the caller to access product-specific fields (scale_factor etc.)
    without a second file read. npz_data is None if the file could not be opened.
    """
    try:
        npz = np.load(npz_path, allow_pickle=False)
        data = npz['data']
        fill_value = int(npz['fill_value'])
        filename = _extract_filename_str(npz)

        h, v = get_modis_tile_from_filename(filename)
        n_rows, n_cols = data.shape
        # Pixel size: full tile width divided by number of pixels along one axis
        psize = (MODIS_TILE_SIZE * MODIS_PIXEL_SIZE) / n_rows

        tile_width = MODIS_TILE_SIZE * MODIS_PIXEL_SIZE
        x_origin = (h - 18) * tile_width   # west edge of tile in sinusoidal metres
        y_origin = (9 - v) * tile_width    # north (top) edge of tile

        transformer = _get_transformer()
        x_sin, y_sin = transformer.transform(lon, lat)

        # Row/col of the station's containing pixel (rows increase downward)
        col = int((x_sin - x_origin) / psize)
        row = int((y_origin - y_sin) / psize)

        if not (0 <= row < n_rows and 0 <= col < n_cols):
            # Station is not inside this tile (should not happen if tile precomputed)
            return None, npz

        raw = data[row, col]
        if int(raw) == fill_value:
            return None, npz

        return raw, npz

    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# CLCD extraction helpers (mirrors build_clcd_panel_v2.py)
# ---------------------------------------------------------------------------

def get_raster_meta(raster_path):
    """Read CRS (proj4 string) and nodata value from a GeoTIFF. Called once per year."""
    ds = gdal.Open(str(raster_path))
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    proj_wkt = ds.GetProjection()
    ds = None
    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj_wkt)
    return srs.ExportToProj4(), nodata


def read_raster_window(raster_path, bounds):
    """
    Read a small windowed numpy array from a GeoTIFF using GDAL.
    bounds = (minx, miny, maxx, maxy) in the raster's own CRS.
    Returns (data, affine, nodata) or (None, None, nodata) if no pixel overlap.
    """
    ds = gdal.Open(str(raster_path))
    gt = ds.GetGeoTransform()   # (x_origin, x_res, 0, y_origin, 0, y_res)
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    raster_x_size = ds.RasterXSize
    raster_y_size = ds.RasterYSize
    ds = None

    x_origin, x_res = gt[0], gt[1]
    y_origin, y_res = gt[3], gt[5]   # y_res is negative
    minx, miny, maxx, maxy = bounds

    col_start = max(0, int((minx - x_origin) / x_res))
    row_start = max(0, int((maxy - y_origin) / y_res))
    col_end   = min(raster_x_size, int((maxx - x_origin) / x_res) + 2)
    row_end   = min(raster_y_size, int((miny - y_origin) / y_res) + 2)

    if col_end <= col_start or row_end <= row_start:
        return None, None, nodata

    ds = gdal.Open(str(raster_path))
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray(col_start, row_start,
                            col_end - col_start, row_end - row_start)
    ds = None

    x_min_win = x_origin + col_start * x_res
    y_max_win = y_origin + row_start * y_res
    affine = Affine(x_res, 0.0, x_min_win, 0.0, y_res, y_max_win)
    return data, affine, nodata


def extract_clcd_pct(station_buf_gdf, raster_path, nodata):
    """
    Extract CLCD forest-class percentage within one station buffer polygon.

    Parameters
    ----------
    station_buf_gdf : GeoDataFrame
        Single-row GeoDataFrame already reprojected to the CLCD raster's CRS.
    raster_path : Path
    nodata : float or None

    Returns
    -------
    float or None
        Forest percentage 0-100, or None if no valid pixels.
    """
    try:
        bounds = station_buf_gdf.total_bounds   # (minx, miny, maxx, maxy)
        data, affine, nd = read_raster_window(raster_path, bounds)
        if data is None:
            return None

        stats = zonal_stats(
            station_buf_gdf,
            data,
            affine=affine,
            categorical=True,
            nodata=nd if nd is not None else nodata,
        )
        if not stats or not stats[0]:
            return None

        class_counts = {k: v for k, v in stats[0].items() if k is not None}
        total_pixels = sum(class_counts.values())
        if total_pixels == 0:
            return None

        forest_pixels = class_counts.get(CLCD_FOREST_CLASS_CODE, 0)
        return forest_pixels / total_pixels * 100.0

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-station-year worker function
# ---------------------------------------------------------------------------

def process_station_year(args):
    """
    Extract all RS variables for one station × year combination.

    Tuple layout:
        station_id, lat, lon, modis_h, modis_v  — station identity + precomputed tile
        year
        clcd_raster        — Path to CLCD GeoTIFF for this year, or None
        clcd_nodata        — nodata value from CLCD raster
        buf_100m           — one-row GeoDataFrame in CLCD CRS for 100m buffer, or None
        buf_500m           — one-row GeoDataFrame in CLCD CRS for 500m buffer, or None
        lst_year_dir       — Path to LST npz directory for this year
        ndvi_year_dir      — Path to NDVI npz directory for this year
        tc_year_dir        — Path to tree-cover npz directory for this year
    """
    (station_id, lat, lon, modis_h, modis_v,
     year,
     clcd_raster, clcd_nodata,
     buf_100m, buf_500m,
     lst_year_dir, ndvi_year_dir, tc_year_dir) = args

    result = {'station_id': station_id, 'year': year}

    # ---- CLCD (30m): zonal_stats at 100m and 500m ----
    if clcd_raster is not None:
        for radius_m, buf in [(100, buf_100m), (500, buf_500m)]:
            col = f'clcd_forest_pct_{radius_m}m'
            if buf is not None:
                result[col] = extract_clcd_pct(buf, clcd_raster, clcd_nodata)
            else:
                result[col] = None

    # Tile glob pattern: only open files for this station's MODIS tile
    tile_pat = f'*h{modis_h:02d}v{modis_v:02d}*.npz'

    # ---- MODIS LST (1km): nearest pixel, summer (JJA) mean ----
    lst_vals = []
    if lst_year_dir.exists():
        for npz_path in sorted(lst_year_dir.glob(tile_pat)):
            # Filter to summer composites only
            if not is_summer_period(npz_path.name):
                continue
            raw, npz = load_and_lookup(lat, lon, npz_path)
            # fill_value for LST is 0; any raw > 0 is a valid DN
            if raw is not None and int(raw) > 0:
                lst_c = int(raw) * LST_SCALE_FACTOR - LST_KELVIN_OFFSET
                lst_vals.append(lst_c)
    result['lst_summer_mean_nearest'] = (
        float(np.mean(lst_vals)) if lst_vals else None
    )

    # ---- MODIS NDVI (1km): nearest pixel, summer (JJA) mean ----
    ndvi_vals = []
    if ndvi_year_dir.exists():
        for npz_path in sorted(ndvi_year_dir.glob(tile_pat)):
            if not is_summer_period(npz_path.name):
                continue
            raw, npz = load_and_lookup(lat, lon, npz_path)
            if raw is None or npz is None:
                continue
            try:
                scale_factor = float(npz['scale_factor'])
                ndvi_val = int(raw) * scale_factor
                if -1.0 <= ndvi_val <= 1.0:
                    ndvi_vals.append(ndvi_val)
            except Exception:
                pass
    result['ndvi_summer_mean_nearest'] = (
        float(np.mean(ndvi_vals)) if ndvi_vals else None
    )

    # ---- MODIS Tree Cover MOD44B (500m): nearest pixel, one annual value ----
    tc_val = None
    if tc_year_dir.exists():
        for npz_path in sorted(tc_year_dir.glob(tile_pat)):
            raw, _ = load_and_lookup(lat, lon, npz_path)
            # Values 0-100 are valid tree cover %; >=200 are nodata
            if raw is not None and int(raw) < 200:
                tc_val = float(int(raw))
                break   # one annual value per station; stop after first valid hit
    result['tree_cover_nearest'] = tc_val

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def find_clcd_rasters():
    """Return {year: Path} for CLCD GeoTIFFs found in the source directory."""
    pattern = re.compile(
        '^' + re.escape(CLCD_FILENAME_TEMPLATE).replace(r'\{year\}', r'(\d{4})') + '$'
    )
    rasters = {}
    for path in DOWNLOAD_CLCD_SOURCE_DIR.glob('*.tif'):
        match = pattern.match(path.name)
        if match:
            rasters[int(match.group(1))] = path
    return rasters


def check_required_inputs(clcd_rasters):
    """Fail loudly if any required input is missing (CRITICAL RULE 2)."""
    missing = []
    for path in [STATION_INVENTORY_FILE, STATION_BUFFERS_100M_FILE,
                 STATION_BUFFERS_500M_FILE]:
        if not path.exists():
            missing.append(str(path))
    for d in [MODIS_LST_ARRAYS_DIR, MODIS_NDVI_ARRAYS_DIR,
              MODIS_TREE_COVER_ARRAYS_DIR]:
        if not d.exists():
            missing.append(str(d))
    if not clcd_rasters:
        missing.append(f"{DOWNLOAD_CLCD_SOURCE_DIR} (no CLCD GeoTIFFs found)")
    if missing:
        logger.error("FATAL: Required inputs missing:")
        for m in missing:
            logger.error(f"  - {m}")
        sys.exit(1)


def main():
    logger.info("=" * 70)
    logger.info("Extract Remote Sensing Variables for Weather Stations")
    logger.info(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)

    # ---- Find CLCD rasters ----
    clcd_rasters = find_clcd_rasters()
    logger.info(f"Found {len(clcd_rasters)} CLCD rasters "
                f"({min(clcd_rasters) if clcd_rasters else 'none'}"
                f"–{max(clcd_rasters) if clcd_rasters else 'none'})")

    check_required_inputs(clcd_rasters)

    # ---- Load station inventory (within_20km stations) ----
    stations = pd.read_csv(STATION_INVENTORY_FILE, dtype={'station_id': str})
    stations = stations[stations['within_20km'] == True].dropna(subset=['lat', 'lon'])
    logger.info(f"Loaded {len(stations):,} stations (within_20km filter applied).")

    # ---- Read CLCD raster CRS and nodata once (same for all years) ----
    first_clcd = clcd_rasters[sorted(clcd_rasters.keys())[0]]
    clcd_proj4, clcd_nodata = get_raster_meta(first_clcd)
    logger.info(f"CLCD CRS (proj4): {clcd_proj4[:60]}...")

    # ---- Load station buffer GeoDataFrames and reproject to CLCD CRS ----
    logger.info("Loading and reprojecting station buffers to CLCD CRS...")
    buf_100m_gdf = gpd.read_file(STATION_BUFFERS_100M_FILE).to_crs(clcd_proj4)
    buf_500m_gdf = gpd.read_file(STATION_BUFFERS_500M_FILE).to_crs(clcd_proj4)
    logger.info(f"  100m buffers: {len(buf_100m_gdf):,} rows")
    logger.info(f"  500m buffers: {len(buf_500m_gdf):,} rows")

    # Build per-station dicts: station_id → single-row GeoDataFrame in CLCD CRS
    # These are passed per-task so each worker has its own geometry without
    # searching the full GeoDataFrame inside the worker.
    def make_buf_dict(gdf):
        return {
            row['station_id']: gdf.iloc[[i]]
            for i, (_, row) in enumerate(gdf.iterrows())
        }
    buf_100m_dict = make_buf_dict(buf_100m_gdf)
    buf_500m_dict = make_buf_dict(buf_500m_gdf)

    # ---- Precompute MODIS tile (h, v) for each station ----
    logger.info("Precomputing MODIS tile membership for each station...")
    transformer = _get_transformer()
    station_tiles = {}
    for _, row in stations.iterrows():
        h, v = compute_modis_tile(row['lat'], row['lon'])
        station_tiles[row['station_id']] = (h, v)
    tile_counts = {}
    for h, v in station_tiles.values():
        tile_counts[(h, v)] = tile_counts.get((h, v), 0) + 1
    logger.info(f"  Stations span {len(tile_counts)} unique MODIS tiles.")

    # ---- Study years: intersection of MODIS availability and CLCD coverage ----
    # Use all study years for MODIS (CLCD is None for missing years)
    study_years = list(range(STUDY_START_YEAR, STUDY_END_YEAR + 1))
    logger.info(f"Study years: {STUDY_START_YEAR}–{STUDY_END_YEAR} "
                f"({len(study_years)} years)")

    # ---- Build task list ----
    logger.info("Building task list (one task per station × year)...")
    tasks = []
    for _, stn in stations.iterrows():
        sid = stn['station_id']
        lat = float(stn['lat'])
        lon = float(stn['lon'])
        modis_h, modis_v = station_tiles[sid]
        b100 = buf_100m_dict.get(sid)
        b500 = buf_500m_dict.get(sid)

        for year in study_years:
            tasks.append((
                sid, lat, lon, modis_h, modis_v,
                year,
                clcd_rasters.get(year),   # None if CLCD not available for this year
                clcd_nodata,
                b100, b500,
                MODIS_LST_ARRAYS_DIR       / str(year),
                MODIS_NDVI_ARRAYS_DIR      / str(year),
                MODIS_TREE_COVER_ARRAYS_DIR / str(year),
            ))

    n_tasks = len(tasks)
    logger.info(f"Total tasks: {n_tasks:,} "
                f"({len(stations):,} stations × {len(study_years)} years)")

    # ---- Process in parallel ----
    logger.info(f"Processing with {MAX_WORKERS} parallel workers...")
    from tqdm import tqdm

    with Pool(MAX_WORKERS) as pool:
        results = list(tqdm(
            pool.imap(process_station_year, tasks),
            total=n_tasks,
            desc="Station RS extraction",
            unit="station-year",
        ))

    results = [r for r in results if r is not None]
    logger.info(f"Collected {len(results):,} station-year records.")

    if not results:
        logger.error("FATAL: No results produced. Check MODIS arrays and CLCD files.")
        sys.exit(1)

    # ---- Save output ----
    panel = pd.DataFrame(results).sort_values(['station_id', 'year']).reset_index(drop=True)

    logger.info("\n--- Validation summary ---")
    for col in ['lst_summer_mean_nearest', 'ndvi_summer_mean_nearest',
                'tree_cover_nearest', 'clcd_forest_pct_100m', 'clcd_forest_pct_500m']:
        if col in panel.columns:
            n_valid = panel[col].notna().sum()
            pct = 100 * n_valid / len(panel)
            logger.info(f"  {col}: {n_valid:,}/{len(panel):,} valid ({pct:.1f}%)")

    STATION_RS_PANEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(STATION_RS_PANEL_FILE, index=False)
    logger.info(f"\nSaved: {STATION_RS_PANEL_FILE} ({len(panel):,} rows)")
    logger.info("Next: run merge_station_panels_v1.py")
    logger.info("=" * 70)


if __name__ == '__main__':
    main()
