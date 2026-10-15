"""
Create Station Buffers

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/station_temps/generated/station_inventory.csv  (from build_station_panel_v1.py)

Produces (outputs for later stages):
    - data/station_buffers/generated/station_buffers_100m.geojson
    - data/station_buffers/generated/station_buffers_500m.geojson

Description:
    Creates disk polygons at 100m and 500m radius around each matched weather
    station. Only stations within 20km of their nearest study city centroid are
    kept (within_20km == True in the inventory produced by build_station_panel_v1.py).

    Approach mirrors create_city_buffers_v1.py: projects to Asia North Albers
    Equal Area (ESRI:102025) before buffering so each disk is an accurate circle
    regardless of latitude. Output files are saved in that projected CRS.

    These buffers are consumed by extract_rs_for_stations_v1.py for CLCD (30m)
    zonal_stats. MODIS variables (500m-1km pixels) are extracted via nearest-pixel
    lookup at the station coordinates and do not use these files.

Usage:
    conda activate C:\\py_envs\\environment_geo
    python code\\create_station_buffers\\create_station_buffers_v1.py
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT / 'code'))

from utils.config import (
    STATION_INVENTORY_FILE,
    STATION_BUFFERS_100M_FILE,
    STATION_BUFFERS_500M_FILE,
    CRS_GEOGRAPHIC,
    CRS_PROJECTED,
)

LOG_FILE = PROJECT_ROOT / 'last_run_create_station_buffers.log'

# Buffer radii in metres (not km — stations need much finer rings than cities)
BUFFER_RADII_M = [100, 500]

BUFFER_OUTPUT_FILES = {
    100: STATION_BUFFERS_100M_FILE,
    500: STATION_BUFFERS_500M_FILE,
}

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
    """Fail loudly if the station inventory is missing (CRITICAL RULE 2)."""
    if not STATION_INVENTORY_FILE.exists():
        logger.error(f"FATAL: Station inventory not found: {STATION_INVENTORY_FILE}")
        logger.error("Run build_station_panel_v1.py first.")
        sys.exit(1)


def load_stations() -> gpd.GeoDataFrame:
    """
    Load station inventory, apply the 20km distance filter, and return a
    GeoDataFrame with Point geometries in WGS84.
    The 20km cut-off mirrors the instructions in notes/instructions_8.txt:
    drop stations more than 20km from the nearest city centroid.
    """
    df = pd.read_csv(STATION_INVENTORY_FILE, dtype={'station_id': str})
    logger.info(f"Loaded {len(df):,} station records from inventory.")

    # Keep only stations within 20km of a study city centroid
    before = len(df)
    df = df[df['within_20km'] == True].copy()
    logger.info(f"After 20km filter: {len(df):,} kept, {before - len(df):,} dropped.")

    # Drop any rows with missing coordinates
    df = df.dropna(subset=['lat', 'lon'])

    geometry = [Point(row['lon'], row['lat']) for _, row in df.iterrows()]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=CRS_GEOGRAPHIC)
    logger.info(f"GeoDataFrame created: {len(gdf):,} station points (WGS84).")
    return gdf


def create_and_save_buffers(stations_gdf: gpd.GeoDataFrame, radius_m: int,
                             out_file: Path) -> None:
    """
    Project stations to equal-area CRS, buffer by radius_m metres, and save
    the result as a GeoJSON file in that projected CRS.
    """
    logger.info(f"Creating {radius_m}m buffers...")

    # Project to Asia North Albers Equal Area for accurate circle geometry
    gdf_proj = stations_gdf.to_crs(CRS_PROJECTED).copy()
    gdf_proj['geometry'] = gdf_proj.geometry.buffer(radius_m)
    gdf_proj['buffer_radius_m'] = radius_m
    gdf_proj['area_m2'] = gdf_proj.geometry.area

    import math
    expected_area = math.pi * radius_m ** 2
    logger.info(f"  Expected area: {expected_area:,.0f} m², "
                f"actual mean: {gdf_proj['area_m2'].mean():,.0f} m²")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    gdf_proj.to_file(out_file, driver='GeoJSON')
    logger.info(f"  Saved {len(gdf_proj):,} buffers → {out_file}")


def main():
    logger.info("=" * 70)
    logger.info("Create Station Buffers (100m and 500m)")
    logger.info(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)

    check_required_inputs()

    stations = load_stations()

    for radius_m in BUFFER_RADII_M:
        out_file = BUFFER_OUTPUT_FILES[radius_m]
        create_and_save_buffers(stations, radius_m, out_file)

    logger.info("=" * 70)
    logger.info("Done. Output files:")
    for radius_m, out_file in BUFFER_OUTPUT_FILES.items():
        logger.info(f"  {radius_m}m: {out_file}")
    logger.info("Next: run extract_rs_for_stations_v1.py")
    logger.info("=" * 70)


if __name__ == '__main__':
    main()
