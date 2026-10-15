"""
Download MODIS Tree Cover Data (MOD44B)

Environment: environment_geo

Requires (inputs from earlier stages):
    - None (downloads from NASA Earthdata)

Produces (outputs for later stages):
    - data/download_modis_tree_cover/source/YYYY/*.hdf (HDF4 files organized by year)

Description:
    Downloads MOD44B v061 (annual Vegetation Continuous Fields, 250m resolution)
    from NASA Earthdata for Chinese MODIS tiles covering the study period (2000-2025).
    Includes resume capability - only downloads missing granules.

    MOD44B provides annual tree cover percentage estimates. Unlike MOD11A2 (8-day LST),
    MOD44B is an annual product with one file per tile per year, making downloads
    much faster (~130 files vs ~6,000 for LST).

Usage:
    python code/download_modis_tree_cover/download_modis_tree_cover_v1.py

Expected runtime: 10-30 minutes (depends on network and number of missing files)

Requirements:
    - NASA Earthdata account (https://urs.earthdata.nasa.gov/)
    - earthaccess library
"""

import sys
from pathlib import Path
from datetime import datetime
import time
import earthaccess

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    MODIS_TREE_COVER_DIR,
    TREE_COVER_PRODUCT,
    TREE_COVER_VERSION,
    MODIS_TILES_CHINA,
    STUDY_START_YEAR,
    STUDY_END_YEAR
)


def check_required_inputs():
    """
    Check that all required input files exist before starting.
    Fail loudly if any are missing (CRITICAL RULE 2).
    """
    # This task doesn't have strict file dependencies, but we verify
    # that the output directory is accessible
    try:
        MODIS_TREE_COVER_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("ERROR: Cannot access output directory:")
        print(f"  - {MODIS_TREE_COVER_DIR}")
        print(f"  Error: {e}")
        sys.exit(1)


def authenticate_earthdata():
    """
    Authenticate with NASA Earthdata.

    Uses stored credentials from .netrc or environment variables.
    If not found, prints instructions for manual setup.
    """
    print("Authenticating with NASA Earthdata...")
    try:
        # Try to use stored credentials first (non-interactive)
        auth = earthaccess.login(strategy="netrc")
        if auth.authenticated:
            print("Authentication successful (using stored credentials)")
            return True
        else:
            # If netrc fails, try environment variables
            auth = earthaccess.login(strategy="environment")
            if auth.authenticated:
                print("Authentication successful (using environment variables)")
                return True
            else:
                print("ERROR: No stored credentials found")
                print("\nTo authenticate, choose one of these options:")
                print("\n1. Create a .netrc file (recommended):")
                print("   Run this once in Python:")
                print("   >>> import earthaccess")
                print("   >>> earthaccess.login(persist=True)")
                print("   Enter your NASA Earthdata username and password when prompted.")
                print("\n2. Use environment variables:")
                print("   Set EARTHDATA_USERNAME and EARTHDATA_PASSWORD")
                print("\n3. Register for a free account:")
                print("   https://urs.earthdata.nasa.gov/")
                return False
    except Exception as e:
        print(f"ERROR: Authentication error: {e}")
        print("\nPlease set up NASA Earthdata credentials:")
        print("  1. Register at: https://urs.earthdata.nasa.gov/")
        print("  2. Run: earthaccess.login(persist=True) in Python")
        return False


def search_modis_tree_cover(start_date, end_date, tiles):
    """
    Search for MODIS Tree Cover data in date range and tiles.

    Parameters
    ----------
    start_date : str
        Start date (YYYY-MM-DD)
    end_date : str
        End date (YYYY-MM-DD)
    tiles : list
        List of MODIS tile IDs (e.g., ['h27v05', 'h27v06'])

    Returns
    -------
    list
        List of search results
    """
    print(f"\nSearching for {TREE_COVER_PRODUCT} v{TREE_COVER_VERSION}...")
    print(f"  Date range: {start_date} to {end_date}")
    print(f"  Tiles: {', '.join(tiles)}")

    # China bounding box to narrow query and reduce CMR errors
    # Coordinates: (west, south, east, north) covering China
    china_bbox = (73.0, 18.0, 135.0, 54.0)

    # Added retry logic for transient NASA CMR errors (SSL failures, connection resets)
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            results = earthaccess.search_data(
                short_name=TREE_COVER_PRODUCT,
                version=TREE_COVER_VERSION,
                temporal=(start_date, end_date),
                bounding_box=china_bbox
            )

            print(f"Found {len(results)} granules")
            return results

        except Exception as e:
            if attempt < max_attempts - 1:
                # Exponential backoff: wait longer each time (10s, 20s, 40s, 80s)
                wait_time = 10 * (2 ** attempt)
                print(f"Warning: Search error (attempt {attempt + 1}/{max_attempts}): {e}")
                print(f"  Retrying after {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                # Final attempt failed
                print(f"ERROR: Search error after {max_attempts} attempts: {e}")
                print(f"  This is likely a temporary NASA server issue.")
                print(f"  Use the retry prompt to try again.")
                return []


def filter_by_tiles(results, tiles):
    """
    Filter search results by MODIS tile IDs.

    MODIS granule names include tile ID (e.g., MOD44B.A2020065.h27v05.061...)

    Parameters
    ----------
    results : list
        Search results from earthaccess
    tiles : list
        List of desired tile IDs

    Returns
    -------
    list
        Filtered results
    """
    filtered = []
    for result in results:
        # Get granule name
        granule_name = result['umm']['GranuleUR']
        # Check if any of our tiles is in the name
        if any(tile in granule_name for tile in tiles):
            filtered.append(result)

    print(f"  After tile filtering: {len(filtered)} granules")
    return filtered


def filter_by_year(results, target_year):
    """
    Filter search results by product year extracted from granule name.

    MOD44B granule names have format: MOD44B.AYYYYDDD.tile.version.timestamp
    where YYYY is the product year. This filter ensures we only get granules
    for the target year, since temporal search can return multiple years.

    Parameters
    ----------
    results : list
        Search results from earthaccess
    target_year : int
        Desired year (e.g., 2020)

    Returns
    -------
    list
        Filtered results containing only target year granules
    """
    filtered = []
    for result in results:
        # Get granule name (e.g., MOD44B.A2020065.h27v05.061.2021044211859)
        granule_name = result['umm']['GranuleUR']

        # Extract year from AYYYYDDD pattern
        # Granule format: MOD44B.AYYYYDDD.tile.version.timestamp
        parts = granule_name.split('.')
        if len(parts) >= 2 and parts[1].startswith('A') and len(parts[1]) >= 5:
            # Extract YYYY from AYYYYDDD
            granule_year = int(parts[1][1:5])
            if granule_year == target_year:
                filtered.append(result)
        else:
            # If we can't parse the year, include it anyway (be conservative)
            filtered.append(result)

    print(f"  After year filtering: {len(filtered)} granules for {target_year}")
    return filtered


def filter_already_downloaded(results, output_dir):
    """
    Filter out granules that are already downloaded.

    Checks if the HDF file for each granule already exists locally.
    This enables resume capability - only downloads missing granules.

    Parameters
    ----------
    results : list
        Search results from earthaccess
    output_dir : Path
        Output directory where files would be downloaded

    Returns
    -------
    list
        Filtered results containing only granules not yet downloaded
    """
    if not results:
        return []

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get list of existing HDF files in the directory
    existing_files = set(f.name for f in output_dir.glob("*.hdf"))

    # Filter results - only keep granules not yet downloaded
    needed = []
    for result in results:
        # Extract the HDF filename from the granule
        # GranuleUR format: MOD44B.A2020065.h27v05.061.2021044211859
        granule_ur = result['umm']['GranuleUR']
        hdf_filename = f"{granule_ur}.hdf"

        # Check if this file already exists
        if hdf_filename not in existing_files:
            needed.append(result)

    # Report what we found
    already_downloaded = len(results) - len(needed)
    if already_downloaded > 0:
        print(f"  Already have {already_downloaded} granules (skipping)")
    if needed:
        print(f"  Need to download {len(needed)} new granules")

    return needed


def download_data(results, output_dir):
    """
    Download MODIS Tree Cover data files.

    Parameters
    ----------
    results : list
        Search results from earthaccess
    output_dir : Path
        Output directory for downloaded files

    Returns
    -------
    list
        List of downloaded file paths
    """
    if not results:
        print("Warning: No data to download")
        return []

    print(f"\nDownloading {len(results)} granules to {output_dir}...")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Retry logic for download (SSL errors can happen during file transfer too)
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            # Download files
            files = earthaccess.download(
                results,
                str(output_dir)
            )

            print(f"Downloaded {len(files)} files")
            return files

        except Exception as e:
            if attempt < max_attempts - 1:
                wait_time = 15 * (attempt + 1)  # 15s, 30s
                print(f"Warning: Download error (attempt {attempt + 1}/{max_attempts}): {e}")
                print(f"  Retrying after {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"ERROR: Download error after {max_attempts} attempts: {e}")
                print(f"  Partial downloads may exist. Use retry prompt to continue.")
                return []


def main():
    """Main execution function."""
    print("=" * 70)
    print("MODIS TREE COVER DATA DOWNLOAD (MOD44B)")
    print("=" * 70)
    print()
    print(f"Product: {TREE_COVER_PRODUCT} v{TREE_COVER_VERSION}")
    print(f"Period: {STUDY_START_YEAR}-{STUDY_END_YEAR}")
    print(f"Tiles: {', '.join(MODIS_TILES_CHINA)}")
    print(f"Output: {MODIS_TREE_COVER_DIR}")
    print()

    try:
        # Check required inputs
        check_required_inputs()

        # Step 1: Authenticate
        if not authenticate_earthdata():
            sys.exit(1)

        # Step 2: Download data by year
        # MOD44B is annual (not 8-day), so much simpler than LST
        all_files = []

        for year in range(STUDY_START_YEAR, STUDY_END_YEAR + 1):
            print(f"\n{'=' * 70}")
            print(f"Processing year: {year}")
            print('=' * 70)

            # MOD44B starts in 2000 (same as MOD11A2)
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"

            # Search
            results = search_modis_tree_cover(start_date, end_date, MODIS_TILES_CHINA)

            if not results:
                print(f"Warning: No data found for {year}")
                continue

            # Filter by tiles
            results = filter_by_tiles(results, MODIS_TILES_CHINA)

            if not results:
                print(f"Warning: No data for specified tiles in {year}")
                continue

            # Filter by product year (NASA search can return multiple years)
            results = filter_by_year(results, year)

            if not results:
                print(f"Warning: No data for year {year} after filtering")
                continue

            # Create year subdirectory
            year_dir = MODIS_TREE_COVER_DIR / str(year)

            # Filter out already-downloaded granules (resume capability)
            results = filter_already_downloaded(results, year_dir)

            if not results:
                print(f"All granules for {year} already downloaded (skipping)")
                continue

            # Download only the missing granules
            files = download_data(results, year_dir)

            if files:
                all_files.extend(files)

            # Brief pause between years to be nice to the server
            time.sleep(2)

        # Summary
        print()
        print("=" * 70)
        print("DOWNLOAD COMPLETE")
        print("=" * 70)
        print(f"\nTotal files downloaded: {len(all_files)}")
        print(f"Output directory: {MODIS_TREE_COVER_DIR}")
        print()
        print("Expected file count: ~130 files (26 years x 5 tiles)")
        print()
        print("Next steps:")
        print("  1. Convert HDF to arrays: python code/convert_tree_cover_to_arrays/convert_tree_cover_to_arrays_v1.py")
        print()

    except Exception as e:
        print()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nWarning: Download interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
