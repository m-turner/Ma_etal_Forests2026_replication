"""
Create Prefecture Geometries -- Step 1b

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/source/pf05.shp
      (China prefecture boundaries, 2005, China Albers Conic CRS, one polygon per
      prefecture-level administrative unit, attribute CITY_CODE = NBS 6-digit GB code)
    - data/build_city_universe/generated/cities_full.csv
      (from build_city_universe; must have lat, lon columns)

Produces (outputs for later stages):
    - data/create_city_buffers/generated/prefecture_boundaries.geojson
      (one polygon per matched city, city_id + nbs_code attributes, WGS84 CRS)
      Used by build_lst_panel, build_tree_cover_panel, build_modis_ndvi_panel,
      build_clcd_panel as an additional extraction geometry (suffix _pref).

Description:
    Matches each city in cities_full.csv to its 2005 prefecture polygon using
    point-in-polygon spatial containment: the WUP lat/lon (administrative seat)
    almost always falls inside its prefecture's boundary.

    Pass 1 -- spatial containment:
        Convert each city lat/lon to a point; run gpd.sjoin with predicate='within'
        against the prefecture polygons. Most cities (~95%+) are matched here.

    Pass 2 -- nearest centroid fallback:
        For any cities whose point missed containment (coordinate slightly off, or
        2005 boundary does not include modern city seat), find the nearest polygon
        centroid using gpd.sjoin_nearest. Logs the match distance so borderline
        cases can be reviewed.

    Output GeoJSON has the same format as city_buffers_*.geojson: one feature per
    matched city, carrying city_id and city_name. Downstream scripts filter on
    city_id, so no NBS code awareness is required elsewhere.

    Run ONCE after receiving pf05.shp. Re-run only if cities_full.csv changes.
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import geopandas as gpd

sys.path.append(str(Path(__file__).resolve().parent.parent))
from utils.config import (
    CITIES_FILE,
    CITY_BUFFERS_SOURCE_DIR,
    PREFECTURE_BOUNDARIES_FILE,
)

# ---------------------------------------------------------------------------
# Logging: write to a dated file in this script's directory + stdout
# ---------------------------------------------------------------------------
_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_FILE = Path(__file__).parent / f'create_prefecture_geometries_{_ts}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='ascii', errors='replace'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

SHAPEFILE = CITY_BUFFERS_SOURCE_DIR / 'pf05.shp'


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def check_required_inputs():
    """Fail loudly if required inputs are missing (CRITICAL RULE 2)."""
    required = [SHAPEFILE, CITIES_FILE]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        log.error("Missing required inputs:")
        for m in missing:
            log.error("  - %s", m)
        log.error("Cannot continue.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def load_and_match():
    """
    Match WUP cities to 2005 prefecture polygons.

    Returns a GeoDataFrame with one polygon per matched city:
      city_id, city_name, nbs_code (int), geometry (polygon, WGS84)
    """
    # --- Load shapefile and reproject to WGS84 ---
    log.info("Loading shapefile: %s", SHAPEFILE)
    pref_raw = gpd.read_file(SHAPEFILE)
    log.info("  %d raw polygons, source CRS: %s", len(pref_raw), pref_raw.crs)

    # Remove the dummy record (CITY_CODE = 0)
    pref_raw = pref_raw[pref_raw['CITY_CODE'] > 0].copy()
    log.info("  %d prefecture polygons after removing code-0 record", len(pref_raw))
    pref_raw['CITY_CODE'] = pref_raw['CITY_CODE'].astype(int)

    # Keep the original projected CRS (China Albers Equal Area) for Pass 2.
    # Centroid computation and sjoin_nearest must use a projected CRS so that
    # distances are in metres. WGS84 centroids are geometrically wrong and
    # sjoin_nearest distances in degrees cannot be compared to a "200 km" threshold.
    # Fixed 2026-10-06: was computing centroids in WGS84, causing all 17 fallback
    # cities to be assigned to wrong prefectures (e.g. Hohhot -> Shanxi, Urumqi -> Gansu).
    pref_projected = pref_raw.copy()   # China Albers Equal Area Conic (metres)
    pref_gdf = pref_raw.to_crs('EPSG:4326')
    log.info("  Reprojected to WGS84 (EPSG:4326)")

    # Build lookup: NBS code -> polygon geometry (used after matching)
    code_to_geom = dict(zip(pref_gdf['CITY_CODE'], pref_gdf['geometry']))

    # --- Load WUP cities and create point geometries ---
    log.info("Loading city list: %s", CITIES_FILE)
    cities_df = pd.read_csv(CITIES_FILE)
    log.info("  %d cities", len(cities_df))

    city_pts = gpd.GeoDataFrame(
        cities_df[['city_id', 'city_name', 'lat', 'lon']].copy(),
        geometry=gpd.points_from_xy(cities_df['lon'], cities_df['lat']),
        crs='EPSG:4326'
    )

    # --- Pass 1: point-in-polygon ---
    log.info("Pass 1: point-in-polygon spatial join...")
    joined = gpd.sjoin(
        city_pts[['city_id', 'city_name', 'geometry']],
        pref_gdf[['CITY_CODE', 'geometry']],
        how='left',
        predicate='within'
    )
    # Drop duplicates if a point straddles two polygons (rare at boundaries)
    joined = joined.drop_duplicates(subset='city_id', keep='first')

    matched_mask = joined['CITY_CODE'].notna()
    matched_p1 = joined[matched_mask][['city_id', 'city_name', 'CITY_CODE']].copy()
    matched_p1['CITY_CODE'] = matched_p1['CITY_CODE'].astype(int)

    unmatched_ids = joined[~matched_mask]['city_id'].tolist()
    log.info("  Matched %d / %d cities via containment", len(matched_p1), len(city_pts))

    # --- Pass 2: nearest centroid for unmatched cities ---
    all_matched = matched_p1.copy()

    if unmatched_ids:
        log.info("Pass 2: nearest-centroid fallback for %d cities...", len(unmatched_ids))

        unmatched_pts = city_pts[city_pts['city_id'].isin(unmatched_ids)].copy()

        # Reproject to China Albers so distances are in metres.
        unmatched_proj = unmatched_pts.to_crs(pref_projected.crs)

        # Use nearest POLYGON BOUNDARY (not centroid).  A city point that is 1 km
        # outside its own prefecture boundary gets distance=1 km; a wrong prefecture
        # whose boundary is 100 km away gets distance >= 100 km.  Nearest-centroid was
        # wrong because large irregular prefectures have centroids far from their admin
        # seat, so a neighbouring prefecture's centroid could win.  Fixed 2026-10-06.
        # 10 km: if the coordinate is merely slightly outside its own polygon (precision
        # issue), the boundary will be < a few km.  24-34 km distances mean the city's
        # own polygon is simply not in the shapefile and the match is a wrong neighbour.
        FALLBACK_MAX_KM = 10   # accept match only if polygon boundary is within 10 km
        nearest = gpd.sjoin_nearest(
            unmatched_proj[['city_id', 'city_name', 'geometry']],
            pref_projected[['CITY_CODE', 'geometry']].reset_index(drop=True),
            how='left',
            distance_col='dist_m'
        )
        nearest = nearest.drop_duplicates(subset='city_id', keep='first')

        accepted = nearest[nearest['dist_m'] <= FALLBACK_MAX_KM * 1000].copy()
        rejected = nearest[nearest['dist_m'] >  FALLBACK_MAX_KM * 1000].copy()

        for _, row in accepted.iterrows():
            log.info("  %-30s -> NBS %d  (%.1f km from boundary)",
                     row['city_name'], int(row['CITY_CODE']), row['dist_m'] / 1000)

        for _, row in rejected.iterrows():
            log.warning("  %-30s: nearest boundary %.1f km away -- "
                        "no matching polygon in shapefile; excluded from _pref outcomes",
                        row['city_name'], row['dist_m'] / 1000)

        if len(accepted) > 0:
            nearest_clean = accepted[['city_id', 'city_name', 'CITY_CODE']].copy()
            nearest_clean['CITY_CODE'] = nearest_clean['CITY_CODE'].astype(int)
            all_matched = pd.concat([all_matched, nearest_clean], ignore_index=True)

        log.info("  Pass 2: %d accepted (boundary < %d km), %d excluded (no polygon in shapefile)",
                 len(accepted), FALLBACK_MAX_KM, len(rejected))
        log.info("  Total matched: %d", len(all_matched))

    else:
        log.info("No unmatched cities; Pass 2 not needed")

    # --- Build output GeoDataFrame with polygon geometries ---
    log.info("Building output GeoDataFrame...")
    rows = []
    for _, row in all_matched.iterrows():
        code = int(row['CITY_CODE'])
        if code not in code_to_geom:
            log.warning("  NBS code %d not found in shapefile; skipping city %s",
                        code, row['city_id'])
            continue
        rows.append({
            'city_id':   row['city_id'],
            'city_name': row['city_name'],
            'nbs_code':  code,
            'geometry':  code_to_geom[code]
        })

    result_gdf = gpd.GeoDataFrame(rows, crs='EPSG:4326')
    log.info("  %d city-polygon pairs in output", len(result_gdf))
    return result_gdf


# ---------------------------------------------------------------------------
# Validation and output
# ---------------------------------------------------------------------------

def validate_and_save(result_gdf):
    """Validate output and write GeoJSON. Exits non-zero on failure."""
    if len(result_gdf) == 0:
        log.error("No cities matched. Check that pf05.shp and cities_full.csv overlap.")
        sys.exit(1)

    dups = result_gdf['city_id'].duplicated().sum()
    if dups > 0:
        log.error("Duplicate city_id in output (%d duplicates). "
                  "Check spatial join logic.", dups)
        sys.exit(1)

    log.info("Validation OK: %d unique city-polygon pairs", len(result_gdf))

    PREFECTURE_BOUNDARIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    result_gdf[['city_id', 'city_name', 'nbs_code', 'geometry']].to_file(
        PREFECTURE_BOUNDARIES_FILE, driver='GeoJSON'
    )
    log.info("Wrote: %s", PREFECTURE_BOUNDARIES_FILE)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("Create Prefecture Geometries -- Step 1b")
    log.info("=" * 60)

    check_required_inputs()
    result_gdf = load_and_match()
    validate_and_save(result_gdf)

    log.info("")
    log.info("SUCCESS")
    log.info("Output : %s", PREFECTURE_BOUNDARIES_FILE)
    log.info("Log    : %s", LOG_FILE)
    log.info("")
    log.info("Next: re-run panel building steps (4, 4b, 4d, 4e) to add _pref columns,")
    log.info("      then re-run merge_panels (step 5).")


if __name__ == "__main__":
    main()
