"""
Configuration file for urban tree planting impact analysis.

This module contains all project-wide configuration parameters including paths,
study parameters, and data processing constants. All paths use pathlib.Path for
cross-platform compatibility.

UPDATED: 2026-06-15
Refactored to use task-based directory structure per CLAUDE.md guidelines.
"""

from pathlib import Path
import os

# ============================================================================
# PROJECT PATHS (Portable - works on Windows, Linux, Mac)
# ============================================================================

# Automatic project root detection (now in code/utils/ instead of src/utils/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Main directories
DATA_DIR = PROJECT_ROOT / 'data'
CODE_DIR = PROJECT_ROOT / 'code'
ANALYSIS_DIR = PROJECT_ROOT / 'analysis'

# Task-specific data directories (following CLAUDE.md structure)
# Each task has source/ (inputs) and generated/ (outputs)

# Task 1: Create city buffers
CITY_BUFFERS_DATA_DIR = DATA_DIR / 'create_city_buffers'
CITY_BUFFERS_SOURCE_DIR = CITY_BUFFERS_DATA_DIR / 'source'
CITY_BUFFERS_GENERATED_DIR = CITY_BUFFERS_DATA_DIR / 'generated'

# Task 2: Download MODIS LST
DOWNLOAD_MODIS_DATA_DIR = DATA_DIR / 'download_modis_lst'
DOWNLOAD_MODIS_SOURCE_DIR = DOWNLOAD_MODIS_DATA_DIR / 'source'
DOWNLOAD_MODIS_GENERATED_DIR = DOWNLOAD_MODIS_DATA_DIR / 'generated'

# Task 3: Convert HDF to arrays
CONVERT_HDF_DATA_DIR = DATA_DIR / 'convert_hdf_to_arrays'
CONVERT_HDF_SOURCE_DIR = CONVERT_HDF_DATA_DIR / 'source'
CONVERT_HDF_GENERATED_DIR = CONVERT_HDF_DATA_DIR / 'generated'

# Task 2b: Download MODIS Tree Cover
DOWNLOAD_TREE_COVER_DATA_DIR = DATA_DIR / 'download_modis_tree_cover'
DOWNLOAD_TREE_COVER_SOURCE_DIR = DOWNLOAD_TREE_COVER_DATA_DIR / 'source'
DOWNLOAD_TREE_COVER_GENERATED_DIR = DOWNLOAD_TREE_COVER_DATA_DIR / 'generated'

# Task 3b: Convert Tree Cover to Arrays
CONVERT_TREE_COVER_DATA_DIR = DATA_DIR / 'convert_tree_cover_to_arrays'
CONVERT_TREE_COVER_SOURCE_DIR = CONVERT_TREE_COVER_DATA_DIR / 'source'
CONVERT_TREE_COVER_GENERATED_DIR = CONVERT_TREE_COVER_DATA_DIR / 'generated'

# Task 4b: Build Tree Cover Panel
BUILD_TREE_COVER_DATA_DIR = DATA_DIR / 'build_tree_cover_panel'
BUILD_TREE_COVER_SOURCE_DIR = BUILD_TREE_COVER_DATA_DIR / 'source'
BUILD_TREE_COVER_GENERATED_DIR = BUILD_TREE_COVER_DATA_DIR / 'generated'

# Task 2d: Download MODIS NDVI
DOWNLOAD_MODIS_NDVI_DATA_DIR = DATA_DIR / 'download_modis_ndvi'
DOWNLOAD_MODIS_NDVI_SOURCE_DIR = DOWNLOAD_MODIS_NDVI_DATA_DIR / 'source'
DOWNLOAD_MODIS_NDVI_GENERATED_DIR = DOWNLOAD_MODIS_NDVI_DATA_DIR / 'generated'

# Task 3d: Convert MODIS NDVI to Arrays
CONVERT_MODIS_NDVI_DATA_DIR = DATA_DIR / 'convert_modis_ndvi_to_arrays'
CONVERT_MODIS_NDVI_SOURCE_DIR = CONVERT_MODIS_NDVI_DATA_DIR / 'source'
CONVERT_MODIS_NDVI_GENERATED_DIR = CONVERT_MODIS_NDVI_DATA_DIR / 'generated'

# Task 4d: Build MODIS NDVI Panel
BUILD_MODIS_NDVI_DATA_DIR = DATA_DIR / 'build_modis_ndvi_panel'
BUILD_MODIS_NDVI_SOURCE_DIR = BUILD_MODIS_NDVI_DATA_DIR / 'source'
BUILD_MODIS_NDVI_GENERATED_DIR = BUILD_MODIS_NDVI_DATA_DIR / 'generated'

# ============================================================================
# DEPRECATED: Landsat/GEE NDVI task chain (2c -> 3c -> 4c)
# Replaced by MODIS NDVI (2d -> 3d -> 4d) to remove Google Earth Engine
# project requirement. Scripts archived in code/download_ndvi/_old/,
# code/build_ndvi_panel/_old/. Constants retained so archived scripts
# remain interpretable but directories are NOT auto-created.
# ============================================================================
DOWNLOAD_NDVI_DATA_DIR = DATA_DIR / 'download_ndvi'
DOWNLOAD_NDVI_SOURCE_DIR = DOWNLOAD_NDVI_DATA_DIR / 'source'
DOWNLOAD_NDVI_GENERATED_DIR = DOWNLOAD_NDVI_DATA_DIR / 'generated'
CONVERT_NDVI_DATA_DIR = DATA_DIR / 'convert_ndvi_to_arrays'
CONVERT_NDVI_SOURCE_DIR = CONVERT_NDVI_DATA_DIR / 'source'
CONVERT_NDVI_GENERATED_DIR = CONVERT_NDVI_DATA_DIR / 'generated'
BUILD_NDVI_DATA_DIR = DATA_DIR / 'build_ndvi_panel'
BUILD_NDVI_SOURCE_DIR = BUILD_NDVI_DATA_DIR / 'source'
BUILD_NDVI_GENERATED_DIR = BUILD_NDVI_DATA_DIR / 'generated'

# Task 4: Build LST panel
BUILD_LST_DATA_DIR = DATA_DIR / 'build_lst_panel'
BUILD_LST_SOURCE_DIR = BUILD_LST_DATA_DIR / 'source'
BUILD_LST_GENERATED_DIR = BUILD_LST_DATA_DIR / 'generated'

# Task 5: Merge panels
MERGE_PANELS_DATA_DIR = DATA_DIR / 'merge_panels'
MERGE_PANELS_SOURCE_DIR = MERGE_PANELS_DATA_DIR / 'source'
MERGE_PANELS_GENERATED_DIR = MERGE_PANELS_DATA_DIR / 'generated'

# Task 6: Run analysis
RUN_ANALYSIS_DATA_DIR = DATA_DIR / 'run_analysis'
RUN_ANALYSIS_SOURCE_DIR = RUN_ANALYSIS_DATA_DIR / 'source'
RUN_ANALYSIS_GENERATED_DIR = RUN_ANALYSIS_DATA_DIR / 'generated'

# ============================================================================
# DEPRECATED: UN WUP download step (formerly Step 0a)
# The WUP file is now placed manually. Script archived in
# code/download_un_cities/_old/download_un_cities_v1.py.
# ============================================================================
DOWNLOAD_UN_CITIES_DATA_DIR      = DATA_DIR / 'download_un_cities'
DOWNLOAD_UN_CITIES_SOURCE_DIR    = DOWNLOAD_UN_CITIES_DATA_DIR / 'source'
DOWNLOAD_UN_CITIES_GENERATED_DIR = DOWNLOAD_UN_CITIES_DATA_DIR / 'generated'

# Task 0b: Build city universe
# Added 2026-06-25: Merges UN WUP data with NFC treatment list to produce
# the full city panel (all Chinese prefecture-level cities, treated + control).
BUILD_CITY_UNIVERSE_DATA_DIR = DATA_DIR / 'build_city_universe'
BUILD_CITY_UNIVERSE_SOURCE_DIR = BUILD_CITY_UNIVERSE_DATA_DIR / 'source'
BUILD_CITY_UNIVERSE_GENERATED_DIR = BUILD_CITY_UNIVERSE_DATA_DIR / 'generated'

# Task 0c: Build city covariates (lat, lon, pre-treatment population)
# Added 2026-06-27: Extracts static city-level covariates for the doubly robust
# CS estimator. Population interpolated from WUP 5-year snapshots to each
# city's pre-treatment year (designation_year - 1); controls use 2003.
BUILD_COVARIATES_DATA_DIR = DATA_DIR / 'build_covariates'
BUILD_COVARIATES_SOURCE_DIR = BUILD_COVARIATES_DATA_DIR / 'source'
BUILD_COVARIATES_GENERATED_DIR = BUILD_COVARIATES_DATA_DIR / 'generated'

# Task 2e: Download CLCD (China Land Cover Dataset, Landsat-derived, from Zenodo)
# Added 2026-06-25: Second, non-MODIS tree-cover measure (forest-class %) to
# cross-check the MODIS NDVI/tree-cover event studies, which showed an
# unexplained spike at the treatment year and near-zero effects elsewhere.
DOWNLOAD_CLCD_DATA_DIR = DATA_DIR / 'download_clcd'
DOWNLOAD_CLCD_SOURCE_DIR = DOWNLOAD_CLCD_DATA_DIR / 'source'
DOWNLOAD_CLCD_GENERATED_DIR = DOWNLOAD_CLCD_DATA_DIR / 'generated'

# Task 4e: Build CLCD Panel
BUILD_CLCD_DATA_DIR = DATA_DIR / 'build_clcd_panel'
BUILD_CLCD_SOURCE_DIR = BUILD_CLCD_DATA_DIR / 'source'
BUILD_CLCD_GENERATED_DIR = BUILD_CLCD_DATA_DIR / 'generated'

# Task 8a/8b: GHCN Ground Station Temperatures (download + panel build share one data folder)
# Added 2026-06-29: Ground station Tmax/hot-day panel for gaming test and air-temp outcomes.
STATION_TEMPS_DATA_DIR      = DATA_DIR / 'station_temps'
STATION_TEMPS_SOURCE_DIR    = STATION_TEMPS_DATA_DIR / 'source'
STATION_TEMPS_DLY_DIR       = STATION_TEMPS_SOURCE_DIR / 'dly'   # raw per-station .dly files
STATION_TEMPS_GENERATED_DIR = STATION_TEMPS_DATA_DIR / 'generated'

# Key file paths for 8a/8b
GHCN_STATIONS_FILE     = STATION_TEMPS_SOURCE_DIR / 'ghcn_china_stations.csv'
STATION_PANEL_FILE     = STATION_TEMPS_GENERATED_DIR / 'station_panel.csv'
STATION_INVENTORY_FILE = STATION_TEMPS_GENERATED_DIR / 'station_inventory.csv'

# Task 8c: Station buffers (100m and 500m disks around each matched station)
# Added 2026-06-30: Used by extract_rs_for_stations for CLCD zonal_stats.
STATION_BUFFERS_DATA_DIR      = DATA_DIR / 'station_buffers'
STATION_BUFFERS_GENERATED_DIR = STATION_BUFFERS_DATA_DIR / 'generated'
STATION_BUFFERS_100M_FILE     = STATION_BUFFERS_GENERATED_DIR / 'station_buffers_100m.geojson'
STATION_BUFFERS_500M_FILE     = STATION_BUFFERS_GENERATED_DIR / 'station_buffers_500m.geojson'

# Task 8d: Station remote sensing extraction (CLCD + MODIS nearest pixel at station locations)
# Added 2026-06-30: Satellite variables at station location for gaming test and mechanism check.
STATION_RS_DATA_DIR      = DATA_DIR / 'station_rs'
STATION_RS_GENERATED_DIR = STATION_RS_DATA_DIR / 'generated'
STATION_RS_PANEL_FILE    = STATION_RS_GENERATED_DIR / 'station_rs_panel.csv'

# Task 8e: Merged station panel (temperature outcomes + RS variables, one row per station-year)
# Added 2026-06-30: Final analysis panel for run_station_analysis_v1.py.
# Updated 2026-07-20: Redirected STATION_CITY_PANEL_FILE to merge_panels/generated/ so
# all analysis inputs are co-located for replication transparency.
STATION_PANELS_DATA_DIR      = DATA_DIR / 'station_panels'
STATION_PANELS_GENERATED_DIR = STATION_PANELS_DATA_DIR / 'generated'
STATION_CITY_PANEL_FILE      = MERGE_PANELS_GENERATED_DIR / 'station_city_panel.csv'

# Analysis outputs (unstructured per CLAUDE.md)
FIGURES_DIR = ANALYSIS_DIR / 'figures'
TABLES_DIR = ANALYSIS_DIR / 'tables'

# Legacy directories (for backward compatibility with old raw data)
RAW_DIR = DATA_DIR / 'raw'
MODIS_VEG_DIR = RAW_DIR / 'modis_veg'
VIIRS_DIR = RAW_DIR / 'viirs'
LANDSAT_DIR = RAW_DIR / 'landsat'

# Create directories if they don't exist
for directory in [
    DATA_DIR, CODE_DIR, ANALYSIS_DIR,
    BUILD_CITY_UNIVERSE_SOURCE_DIR, BUILD_CITY_UNIVERSE_GENERATED_DIR,
    BUILD_COVARIATES_SOURCE_DIR, BUILD_COVARIATES_GENERATED_DIR,
    CITY_BUFFERS_SOURCE_DIR, CITY_BUFFERS_GENERATED_DIR,
    DOWNLOAD_MODIS_SOURCE_DIR, DOWNLOAD_MODIS_GENERATED_DIR,
    CONVERT_HDF_SOURCE_DIR, CONVERT_HDF_GENERATED_DIR,
    DOWNLOAD_TREE_COVER_SOURCE_DIR, DOWNLOAD_TREE_COVER_GENERATED_DIR,
    CONVERT_TREE_COVER_SOURCE_DIR, CONVERT_TREE_COVER_GENERATED_DIR,
    BUILD_TREE_COVER_SOURCE_DIR, BUILD_TREE_COVER_GENERATED_DIR,
    # DEPRECATED dirs omitted: download_ndvi, convert_ndvi_to_arrays, build_ndvi_panel
    BUILD_LST_SOURCE_DIR, BUILD_LST_GENERATED_DIR,
    MERGE_PANELS_SOURCE_DIR, MERGE_PANELS_GENERATED_DIR,
    RUN_ANALYSIS_SOURCE_DIR, RUN_ANALYSIS_GENERATED_DIR,
    DOWNLOAD_CLCD_SOURCE_DIR, DOWNLOAD_CLCD_GENERATED_DIR,
    BUILD_CLCD_SOURCE_DIR, BUILD_CLCD_GENERATED_DIR,
    STATION_TEMPS_SOURCE_DIR, STATION_TEMPS_DLY_DIR, STATION_TEMPS_GENERATED_DIR,
    STATION_BUFFERS_GENERATED_DIR,
    STATION_RS_GENERATED_DIR,
    STATION_PANELS_GENERATED_DIR,
    FIGURES_DIR, TABLES_DIR
]:
    directory.mkdir(parents=True, exist_ok=True)

# ============================================================================
# STUDY PARAMETERS
# ============================================================================

# Spatial parameters
# Updated 2026-06-24: Support multiple buffer sizes for robustness checks
# Updated 2026-06-25: Added 20km buffer for peri-urban reforestation capture
BUFFER_RADII_KM = [5, 10, 20, 50]  # Buffer radii around city center for analysis; 50km added 2026-06-28
BUFFER_RADIUS_KM = 5  # Default buffer radius (for backward compatibility)
CRS_GEOGRAPHIC = 'EPSG:4326'  # WGS84 (lat/lon)
CRS_PROJECTED = 'ESRI:102025'  # Asia North Albers Equal Area

# Temporal parameters
STUDY_START_YEAR = 2000
STUDY_END_YEAR = 2025
SUMMER_MONTHS = [6, 7, 8]  # June, July, August

# ============================================================================
# MODIS LST (Land Surface Temperature) PARAMETERS
# ============================================================================

# MOD11A2 v061 specifications
LST_PRODUCT = 'MOD11A2'
LST_VERSION = '061'
LST_SCALE_FACTOR = 0.02  # Convert DN to Kelvin
LST_KELVIN_OFFSET = 273.15  # Convert Kelvin to Celsius
LST_NODATA = -999  # Nodata value in DN

# MODIS tile coverage for China
# Updated 2026-06-25: Expanded from 5 tiles (east-central only) to 17 tiles
# covering all mainland Chinese prefecture-level cities.
# Tile grid (each ~10° latitude x ~10° longitude in MODIS sinusoidal projection):
#   h: 23=far west (Xinjiang), 24=Xinjiang/Tibet, 25=Sichuan/Yunnan,
#      26=Hubei/Hunan, 27=Coastal, 28=Liaoning/Shandong
#   v: 04=Northeast (~40-53°N), 05=Central (~25-40°N), 06=South (~10-25°N)
MODIS_TILES_CHINA = [
    # Northeast (Heilongjiang, Jilin, Liaoning)
    'h26v04', 'h27v04', 'h28v04',
    # North-central (Beijing, Tianjin, Hebei, Shanxi, Shandong, Inner Mongolia E)
    'h25v04', 'h26v04',
    # Northwest (Shaanxi, Gansu, Ningxia, Qinghai E)
    'h24v04', 'h25v04',
    # Central (Henan, Hubei, Anhui, Jiangsu, Shanghai; current coverage)
    'h26v05', 'h27v05',
    # Central-west (Sichuan, Chongqing)
    'h25v05',
    # Far west (Xinjiang, Tibet)
    'h23v04', 'h23v05', 'h24v05',
    # East coast (Zhejiang, Fujian)
    'h28v05',
    # South (Guangdong, Guangxi, Fujian S, Hainan)
    'h25v06', 'h26v06', 'h27v06',
    # South coast (Guangdong coast, Hainan; current coverage)
    'h28v06',
]
# De-duplicate while preserving order (some tiles appear in multiple region comments)
_seen = set()
MODIS_TILES_CHINA = [t for t in MODIS_TILES_CHINA
                     if not (t in _seen or _seen.add(t))]

# LST bands
LST_DAY_BAND = 'LST_Day_1km'
LST_NIGHT_BAND = 'LST_Night_1km'
LST_QC_BAND = 'QC_Day'

# ============================================================================
# MODIS MOD44B (Vegetation Continuous Fields) PARAMETERS
# ============================================================================

# MOD44B v061 specifications
VEG_PRODUCT = 'MOD44B'
VEG_VERSION = '061'
TREE_COVER_BAND = 'Percent_Tree_Cover'
TREE_COVER_NODATA = 200  # Values >= 200 are nodata/water

# Aliases for tree cover scripts
TREE_COVER_PRODUCT = VEG_PRODUCT  # 'MOD44B'
TREE_COVER_VERSION = VEG_VERSION  # '061'

# ============================================================================
# MODIS MOD13A2 (Vegetation Indices - NDVI) PARAMETERS
# ============================================================================

# MOD13A2 v061 specifications
MODIS_NDVI_PRODUCT = 'MOD13A2'
MODIS_NDVI_VERSION = '061'
MODIS_NDVI_BAND = '1 km 16 days NDVI'  # Primary NDVI band
MODIS_NDVI_SCALE_FACTOR = 0.0001  # Convert DN to NDVI (-1 to +1)
MODIS_NDVI_NODATA = -3000  # Nodata value in DN
MODIS_NDVI_VALID_RANGE = (-2000, 10000)  # Valid DN range (becomes -0.2 to 1.0 after scaling)

# ============================================================================
# DEPRECATED: Landsat/GEE NDVI parameters
# Used by the archived download_ndvi and build_ndvi_panel scripts.
# Retained for interpretability of archived code; not used by active pipeline.
# ============================================================================
LANDSAT_COLLECTION = 'LANDSAT/LC08/C02/T1_L2'
LANDSAT_RED_BAND = 'SR_B4'
LANDSAT_NIR_BAND = 'SR_B5'
LANDSAT_QA_BAND = 'QA_PIXEL'
NDVI_NODATA = -9999
NDVI_VALID_RANGE = (-1.0, 1.0)
NDVI_TARGET_MONTH = 7
NDVI_CLOUD_THRESHOLD = 20
GEE_LANDSAT_COLLECTIONS = {
    'L5': 'LANDSAT/LT05/C02/T1_L2',
    'L7': 'LANDSAT/LE07/C02/T1_L2',
    'L8': 'LANDSAT/LC08/C02/T1_L2',
    'L9': 'LANDSAT/LC09/C02/T1_L2',
}
LANDSAT_BAND_MAPPING = {
    'L5': {'red': 'SR_B3', 'nir': 'SR_B4', 'qa': 'QA_PIXEL'},
    'L7': {'red': 'SR_B3', 'nir': 'SR_B4', 'qa': 'QA_PIXEL'},
    'L8': {'red': 'SR_B4', 'nir': 'SR_B5', 'qa': 'QA_PIXEL'},
    'L9': {'red': 'SR_B4', 'nir': 'SR_B5', 'qa': 'QA_PIXEL'},
}
CHINA_BBOX = [73.0, 18.0, 135.0, 54.0]  # [west, south, east, north]

# ============================================================================
# CLCD (China Land Cover Dataset) PARAMETERS
# ============================================================================
# Added 2026-06-25: Landsat-derived (30m) annual forest-class layer, used as a
# second, non-MODIS tree-cover measure. Source: Yang & Huang, ESSD 2021,
# distributed via Zenodo (not GEE). Pinned to a specific Zenodo record so the
# dataset version used in this project is reproducible; bump CLCD_ZENODO_RECORD_ID
# deliberately if a newer CLCD release should be adopted.
CLCD_ZENODO_RECORD_ID = '18180184'  # "30 m annual land cover datasets ... 1985 to 2025"
CLCD_ZENODO_API_URL = f'https://zenodo.org/api/records/{CLCD_ZENODO_RECORD_ID}'
CLCD_FILENAME_TEMPLATE = 'CLCD_v01_{year}_albert.tif'  # National (non-tiled) GeoTIFF per year

# CLCD 9-class legend (from CLCD_classificationsystem.xlsx):
# 1=Cropland, 2=Forest, 3=Shrub, 4=Grassland, 5=Water,
# 6=Snow/Ice, 7=Barren, 8=Impervious, 9=Wetland
CLCD_FOREST_CLASS_CODE = 2

# ============================================================================
# VIIRS (Nighttime Lights) PARAMETERS
# ============================================================================

# VIIRS DNB (Day/Night Band) specifications
VIIRS_PRODUCT = 'VNP46A2'  # Monthly composite
VIIRS_BAND = 'NearNadir_Composite_Snow_Free'
VIIRS_NODATA = 65535

# ============================================================================
# TEMPERATURE DATA SOURCES (Extensibility)
# ============================================================================

TEMPERATURE_SOURCES = {
    'modis_lst': {
        'enabled': True,
        'product': 'MOD11A2',
        'resolution': '1km',
        'scale_factor': 0.02,
        'offset': -273.15,
        'nodata': -999
    },
    'landsat_lst': {
        'enabled': False,  # Set to True to enable
        'product': 'LANDSAT_C2_ST',
        'resolution': '30m',
        'scale_factor': 0.00341802,
        'offset': 149.0,
        'nodata': 0
    },
    'ecostress': {
        'enabled': False,
        'product': 'ECO_L2T_LSTE',
        'resolution': '70m',
        'scale_factor': 0.02,
        'offset': -273.15,
        'nodata': 0
    }
}

# ============================================================================
# HELPER FUNCTIONS (Platform-independent file handling)
# ============================================================================

def get_figure_path(filename):
    """Construct platform-independent figure path."""
    return FIGURES_DIR / filename

def get_table_path(filename):
    """Construct platform-independent table path."""
    return TABLES_DIR / filename

def get_buffer_file(radius_km):
    """
    Get city buffer file path for specified radius.

    Parameters
    ----------
    radius_km : int
        Buffer radius in kilometers (5, 10, or 20)

    Returns
    -------
    Path
        Path to buffer GeoJSON file

    Updated: 2026-06-25 - Added 20km
    """
    _buffer_files = {
        5:  CITY_BUFFERS_5KM_FILE,
        10: CITY_BUFFERS_10KM_FILE,
        20: CITY_BUFFERS_20KM_FILE,
        50: CITY_BUFFERS_50KM_FILE,  # Added 2026-06-28
    }
    if radius_km not in _buffer_files:
        raise ValueError(f"Unsupported buffer radius: {radius_km}km. Use 5, 10, 20, or 50.")
    return _buffer_files[radius_km]

# ============================================================================
# INPUT FILES (Task-based organization)
# ============================================================================

# Task 0b: UN WUP source file and city universe
# WUP file is placed manually in data/create_city_buffers/source/ (alongside
# cities_sample.csv). Keep the original UN filename so the revision is visible.
WUP_FILE                = CITY_BUFFERS_SOURCE_DIR / 'WUP2018-F22-Cities_Over_300K_Annual_V7.xls'
NFC_TREATMENT_LIST_FILE       = BUILD_CITY_UNIVERSE_SOURCE_DIR / 'nfc_treatment_list.csv'
MANUAL_OVERRIDES_FILE         = BUILD_CITY_UNIVERSE_SOURCE_DIR / 'manual_overrides.csv'
PREFECTURE_LEVEL_CITIES_FILE  = BUILD_CITY_UNIVERSE_SOURCE_DIR / 'prefecture_level_cities.csv'  # Added 2026-06-28
CITIES_FULL_FILE        = BUILD_CITY_UNIVERSE_GENERATED_DIR / 'cities_full.csv'
MATCHING_REPORT_FILE    = BUILD_CITY_UNIVERSE_GENERATED_DIR / 'matching_report.csv'

# Task 0c: City-level pre-treatment covariates for doubly robust estimator
# Updated 2026-07-20: Redirected to merge_panels/generated/ so all analysis
# inputs are co-located for replication transparency. build_covariates_v1.py
# now writes there; the old path (build_covariates/generated/) is no longer used.
CITY_COVARIATES_FILE = MERGE_PANELS_GENERATED_DIR / 'city_covariates.csv'

# Task 1: Create city buffers
CITIES_SAMPLE_FILE = CITY_BUFFERS_SOURCE_DIR / 'cities_sample.csv'
# Full city universe (output of build_city_universe). Used by Step 1 onward
# once the national expansion is ready; switch CITIES_SAMPLE_FILE -> CITIES_FILE
# in each panel-building script when moving from POC to full analysis.
CITIES_FILE = CITIES_FULL_FILE

# ============================================================================
# OUTPUT FILES (Task-based organization)
# ============================================================================

# Task 1: Create city buffers
# Updated 2026-06-24: Support multiple buffer sizes
# Updated 2026-06-25: Added 20km buffer
CITY_BUFFERS_5KM_FILE  = CITY_BUFFERS_GENERATED_DIR / 'city_buffers_5km.geojson'
CITY_BUFFERS_10KM_FILE = CITY_BUFFERS_GENERATED_DIR / 'city_buffers_10km.geojson'
CITY_BUFFERS_20KM_FILE = CITY_BUFFERS_GENERATED_DIR / 'city_buffers_20km.geojson'
CITY_BUFFERS_50KM_FILE = CITY_BUFFERS_GENERATED_DIR / 'city_buffers_50km.geojson'  # Added 2026-06-28
CITY_BUFFERS_FILE = CITY_BUFFERS_5KM_FILE  # For backward compatibility

# Task 2: Download MODIS LST
MODIS_LST_DIR = DOWNLOAD_MODIS_SOURCE_DIR  # HDF4 files from NASA (external source data)

# Task 3: Convert HDF to arrays
MODIS_LST_ARRAYS_DIR = CONVERT_HDF_GENERATED_DIR  # .npz files with metadata

# Task 2b: Download MODIS Tree Cover
MODIS_TREE_COVER_DIR = DOWNLOAD_TREE_COVER_SOURCE_DIR  # HDF4 files from NASA (external source)

# Task 2d: Download MODIS NDVI
MODIS_NDVI_DIR = DOWNLOAD_MODIS_NDVI_SOURCE_DIR  # HDF4 files from NASA (external source data)

# Task 3d: Convert MODIS NDVI to Arrays
MODIS_NDVI_ARRAYS_DIR = CONVERT_MODIS_NDVI_GENERATED_DIR  # .npz files

# Task 3b: Convert Tree Cover to Arrays
MODIS_TREE_COVER_ARRAYS_DIR = CONVERT_TREE_COVER_GENERATED_DIR  # .npz files

# Task 4: Build LST panel
LST_PANEL_FILE = BUILD_LST_GENERATED_DIR / 'lst_panel.csv'

# Task 4b: Build Tree Cover panel
TREE_COVER_PANEL_FILE = BUILD_TREE_COVER_GENERATED_DIR / 'tree_cover_panel.csv'

# DEPRECATED: Landsat/GEE NDVI output paths (archived scripts only)
LANDSAT_NDVI_DIR = DOWNLOAD_NDVI_SOURCE_DIR
NDVI_ARRAYS_DIR = CONVERT_NDVI_GENERATED_DIR
NDVI_PANEL_FILE = BUILD_NDVI_GENERATED_DIR / 'ndvi_panel.csv'

# Task 4d: Build MODIS NDVI panel
MODIS_NDVI_PANEL_FILE = BUILD_MODIS_NDVI_GENERATED_DIR / 'modis_ndvi_panel.csv'

# Task 4e: Build CLCD panel
CLCD_PANEL_FILE = BUILD_CLCD_GENERATED_DIR / 'clcd_panel.csv'

# Task 5: Merge panels
# Input panels (may be generated by other tasks or provided manually)
NIGHTLIGHTS_PANEL_FILE = MERGE_PANELS_SOURCE_DIR / 'nightlights_panel.csv'

# Output: Final merged panel
FINAL_PANEL_FILE = MERGE_PANELS_GENERATED_DIR / 'city_year_panel.parquet'

# ============================================================================
# PROCESSING PARAMETERS
# ============================================================================

# Multiprocessing
MAX_WORKERS = 24  # Number of parallel workers for processing

# Logging
LOG_LEVEL = 'INFO'
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# ============================================================================
# VALIDATION THRESHOLDS
# ============================================================================

# Expected value ranges for validation
LST_MIN = -50  # Minimum realistic LST (Celsius)
LST_MAX = 60   # Maximum realistic LST (Celsius)
TREE_COVER_MIN = 0
TREE_COVER_MAX = 100
NDVI_MIN = -1.0
NDVI_MAX = 1.0
CLCD_FOREST_PCT_MIN = 0
CLCD_FOREST_PCT_MAX = 100
