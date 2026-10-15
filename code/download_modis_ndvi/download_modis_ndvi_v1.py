"""
Download MODIS NDVI Data

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/generated/city_buffers_5km.geojson (for reference only)

Produces (outputs for later stages):
    - data/download_modis_ndvi/source/YYYY/*.hdf (HDF4 files organized by year)

Description:
    Downloads MOD13A2 v061 (16-day NDVI composite, 1km resolution) from NASA Earthdata
    for Chinese MODIS tiles covering the study period (2000-2025). Includes resume
    capability - only downloads missing granules.

Usage:
    python code/download_modis_ndvi/download_modis_ndvi_v1.py

Expected runtime: Variable (depends on network and number of missing granules)

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
    MODIS_NDVI_DIR,
    MODIS_NDVI_PRODUCT,
    MODIS_NDVI_VERSION,
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
        MODIS_NDVI_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("ERROR: Cannot access output directory:")
        print(f"  - {MODIS_NDVI_DIR}")
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


def search_modis_ndvi(start_date, end_date, tiles):
    """
    Search for MODIS NDVI data in date range and tiles.

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
    print(f"\nSearching for {MODIS_NDVI_PRODUCT} v{MODIS_NDVI_VERSION}...")
    print(f"  Date range: {start_date} to {end_date}")
    print(f"  Tiles: {', '.join(tiles)}")

    # China bounding box to narrow query and reduce CMR errors
    # Coordinates: (west, south, east, north) covering China
    china_bbox = (73.0, 18.0, 135.0, 54.0)

    # Added retry logic for transient NASA CMR errors
    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            results = earthaccess.search_data(
                short_name=MODIS_NDVI_PRODUCT,
                version=MODIS_NDVI_VERSION,
                temporal=(start_date, end_date),
                bounding_box=china_bbox
            )

            print(f"Found {len(results)} granules")
            return results

        except Exception as e:
            if attempt < max_attempts - 1:
                # Retry once after brief pause for transient errors
                print(f"Warning: Search error (attempt {attempt + 1}/{max_attempts}): {e}")
                print(f"  Retrying after 5 seconds...")
                time.sleep(5)
            else:
                # Final attempt failed
                print(f"ERROR: Search error after {max_attempts} attempts: {e}")
                return []


def filter_by_tiles(results, tiles):
    """
    Filter search results by MODIS tile IDs.

    MODIS granule names include tile ID (e.g., MOD13A2.A2020001.h27v05.061...)

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
        # GranuleUR format: MOD13A2.A2020001.h27v05.061.2021005123456
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
    Download MODIS NDVI data files.

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

    try:
        # Download files
        files = earthaccess.download(
            results,
            str(output_dir)
        )

        print(f"Downloaded {len(files)} files")
        return files

    except Exception as e:
        print(f"ERROR: Download error: {e}")
        return []


def organize_by_year(downloaded_files, base_dir):
    """
    Organize downloaded files into year subdirectories.

    MODIS NDVI files are named: MOD13A2.AYYYYDOY.*.hdf
    where YYYY is year and DOY is day of year.

    Parameters
    ----------
    downloaded_files : list
        List of downloaded file paths
    base_dir : Path
        Base directory for organization
    """
    print("\nOrganizing files by year...")

    for file_path in downloaded_files:
        file_path = Path(file_path)

        try:
            # Extract year from filename (AYYYYDOY)
            # Example: MOD13A2.A2020001.h27v05.061.2021005123456.hdf
            filename = file_path.name
            parts = filename.split('.')

            # Find the part starting with 'A' (date code)
            date_code = [p for p in parts if p.startswith('A')][0]
            year = date_code[1:5]  # Extract YYYY

            # Create year subdirectory
            year_dir = base_dir / year
            year_dir.mkdir(exist_ok=True)

            # Move file
            new_path = year_dir / filename
            if not new_path.exists():
                file_path.rename(new_path)
                print(f"  -> {year}/{filename}")

        except Exception as e:
            print(f"  Warning: Could not process {file_path.name}: {e}")

    print("Files organized by year")


def main():
    """Main execution function."""
    print("=" * 70)
    print("MODIS NDVI DATA DOWNLOAD")
    print("=" * 70)
    print()
    print(f"Product: {MODIS_NDVI_PRODUCT} v{MODIS_NDVI_VERSION}")
    print(f"Period: {STUDY_START_YEAR}-{STUDY_END_YEAR}")
    print(f"Tiles: {', '.join(MODIS_TILES_CHINA)}")
    print(f"Output: {MODIS_NDVI_DIR}")
    print()

    try:
        # Check required inputs
        check_required_inputs()

        # Step 1: Authenticate
        if not authenticate_earthdata():
            sys.exit(1)

        # Step 2: Download data by year (to avoid overwhelming the API)
        all_files = []

        for year in range(STUDY_START_YEAR, STUDY_END_YEAR + 1):
            print(f"\n{'=' * 70}")
            print(f"Processing year: {year}")
            print('=' * 70)

            # Clamp start date to MOD13A2 launch date (2000-02-18)
            # Prevents CMR "Internal Error" when requesting data before product exists
            start_date = f"{year}-01-01"
            if year == 2000:
                start_date = "2000-02-18"  # MOD13A2 first available date
                print(f"  Note: Clamping start date to {start_date} (MOD13A2 launch)")

            end_date = f"{year}-12-31"

            # Search
            results = search_modis_ndvi(start_date, end_date, MODIS_TILES_CHINA)

            if not results:
                print(f"Warning: No data found for {year}")
                continue

            # Filter by tiles
            results = filter_by_tiles(results, MODIS_TILES_CHINA)

            if not results:
                print(f"Warning: No data for specified tiles in {year}")
                continue

            # Filter out already-downloaded granules (resume capability)
            results = filter_already_downloaded(results, MODIS_NDVI_DIR / str(year))

            if not results:
                print(f"All granules for {year} already downloaded (skipping)")
                continue

            # Download only the missing granules
            files = download_data(results, MODIS_NDVI_DIR / str(year))

            if files:
                all_files.extend(files)

            # Brief pause between years to be nice to the server
            time.sleep(2)

        # Step 3: Organize files
        if all_files:
            organize_by_year(all_files, MODIS_NDVI_DIR)

        # Summary
        print()
        print("=" * 70)
        print("DOWNLOAD COMPLETE")
        print("=" * 70)
        print(f"\nTotal files downloaded: {len(all_files)}")
        print(f"Output directory: {MODIS_NDVI_DIR}")
        print()
        print("Next steps:")
        print("  1. Convert HDF to arrays: (TBD - will create converter script)")
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
