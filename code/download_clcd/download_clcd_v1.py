"""
Download CLCD (China Land Cover Dataset)

Environment: environment_geo

Requires (inputs from earlier stages):
    - None (downloads directly from Zenodo)

Produces (outputs for later stages):
    - data/download_clcd/source/CLCD_v01_YYYY_albert.tif (one national GeoTIFF per year)

Description:
    Downloads the CLCD annual land cover product (Yang & Huang, ESSD 2021) from its
    Zenodo archive. CLCD is Landsat-derived (30m), so it gives a tree-cover measure
    from a different sensor platform than the existing MODIS NDVI/tree-cover layers -
    useful for cross-checking the odd MODIS event-study pattern (spike at the
    treatment year, near-zero elsewhere).

    Unlike the MODIS downloads, this is a SINGLE BULK DOWNLOAD: CLCD is published as
    one static dataset version on Zenodo (not a live feed), so this step only needs to
    run ONCE per pinned record (see CLCD_ZENODO_RECORD_ID in config.py), not on every
    pipeline run. Re-run only if deliberately bumping to a newer CLCD release.

    Includes resume capability (skips files already downloaded and verified) and
    verifies each downloaded file's MD5 checksum against Zenodo's published metadata -
    fails loudly on a checksum mismatch rather than silently keeping a corrupt file.

Usage:
    python code/download_clcd/download_clcd_v1.py

Expected runtime: ~1-3 hours for 26 years x ~800 MB (network dependent). Run once.

Requirements:
    - requests library
    - ~21 GB free disk space (26 years x ~800 MB)
"""

import sys
import re
import hashlib
from pathlib import Path
import requests

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    DOWNLOAD_CLCD_SOURCE_DIR,
    CLCD_ZENODO_API_URL,
    CLCD_FILENAME_TEMPLATE,
    STUDY_START_YEAR,
    STUDY_END_YEAR
)

CHUNK_SIZE = 1024 * 1024  # 1 MB read chunks for streaming download


def check_required_inputs():
    """
    Check that the output directory is writable before starting.
    Fail loudly if not (CRITICAL RULE 2).
    """
    try:
        DOWNLOAD_CLCD_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("ERROR: Cannot access output directory:")
        print(f"  - {DOWNLOAD_CLCD_SOURCE_DIR}")
        print(f"  Error: {e}")
        sys.exit(1)


def fetch_zenodo_file_index():
    """
    Query the Zenodo record API and build a lookup of year -> file metadata.

    Returns
    -------
    dict
        {year (int): {'filename': str, 'size': int, 'md5': str, 'download_url': str}}
    """
    print(f"Querying Zenodo record metadata: {CLCD_ZENODO_API_URL}")

    try:
        response = requests.get(CLCD_ZENODO_API_URL, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"ERROR: Could not reach Zenodo API: {e}")
        sys.exit(1)

    record = response.json()
    files = record.get('files', [])

    if not files:
        print("ERROR: Zenodo record returned no files - record ID may be wrong")
        print(f"  Record ID: {CLCD_ZENODO_API_URL}")
        sys.exit(1)

    # CLCD_FILENAME_TEMPLATE is e.g. 'CLCD_v01_{year}_albert.tif' - build a regex
    # from it so we only pick up the national (non-tiled, non-zipped) GeoTIFFs.
    pattern = re.compile('^' + re.escape(CLCD_FILENAME_TEMPLATE).replace(r'\{year\}', r'(\d{4})') + '$')

    file_index = {}
    for f in files:
        filename = f.get('key', '')
        match = pattern.match(filename)
        if not match:
            continue

        year = int(match.group(1))
        checksum = f.get('checksum', '')  # format: "md5:<hash>"
        md5_hash = checksum.split(':', 1)[1] if ':' in checksum else checksum

        file_index[year] = {
            'filename': filename,
            'size': f.get('size', 0),
            'md5': md5_hash,
            'download_url': f.get('links', {}).get('self', '')
        }

    print(f"  Found {len(file_index)} national CLCD GeoTIFFs in record")
    return file_index


def compute_md5(file_path):
    """
    Compute the MD5 checksum of a local file (streamed, so it works on large files).

    Parameters
    ----------
    file_path : Path

    Returns
    -------
    str
        Hex digest of the MD5 checksum
    """
    md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b''):
            md5.update(chunk)
    return md5.hexdigest()


def is_already_downloaded(local_path, expected_size, expected_md5):
    """
    Check whether a previously downloaded file is complete and uncorrupted.

    Enables resume capability: skip re-downloading files that already match
    the expected size and checksum.

    Parameters
    ----------
    local_path : Path
    expected_size : int
    expected_md5 : str

    Returns
    -------
    bool
    """
    if not local_path.exists():
        return False

    if local_path.stat().st_size != expected_size:
        print(f"    Existing file has wrong size, will re-download")
        return False

    print(f"    Verifying checksum of existing file...")
    actual_md5 = compute_md5(local_path)
    if actual_md5 != expected_md5:
        print(f"    Existing file failed checksum verification, will re-download")
        return False

    return True


def download_file(download_url, local_path, expected_size):
    """
    Stream-download one file to local_path.

    Parameters
    ----------
    download_url : str
    local_path : Path
    expected_size : int

    Returns
    -------
    bool
        True on success
    """
    try:
        with requests.get(download_url, stream=True, timeout=60) as response:
            response.raise_for_status()

            downloaded = 0
            tmp_path = local_path.with_suffix(local_path.suffix + '.partial')

            with open(tmp_path, 'wb') as out_file:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        out_file.write(chunk)
                        downloaded += len(chunk)
                        pct = (downloaded / expected_size * 100) if expected_size else 0
                        print(f"\r    Downloaded {downloaded / 1e6:.0f} MB / {expected_size / 1e6:.0f} MB ({pct:.1f}%)", end='')

            print()  # newline after progress
            tmp_path.rename(local_path)
            return True

    except Exception as e:
        print(f"\n    ERROR: Download failed: {e}")
        return False


def main():
    """Main execution function."""
    print("=" * 70)
    print("CLCD (CHINA LAND COVER DATASET) DOWNLOAD")
    print("=" * 70)
    print()
    print("NOTE: This is a one-time bulk download of a static Zenodo dataset.")
    print("It does not need to be re-run on every pipeline execution - only")
    print("re-run if deliberately bumping to a newer CLCD release.")
    print()

    try:
        check_required_inputs()

        file_index = fetch_zenodo_file_index()

        study_years = list(range(STUDY_START_YEAR, STUDY_END_YEAR + 1))
        missing_years = [y for y in study_years if y not in file_index]
        if missing_years:
            print(f"Warning: CLCD record has no file for these study years: {missing_years}")
            print("  Continuing with the years that are available.")

        downloaded_count = 0
        skipped_count = 0
        failed_years = []

        for year in study_years:
            if year not in file_index:
                continue

            info = file_index[year]
            local_path = DOWNLOAD_CLCD_SOURCE_DIR / info['filename']

            print(f"\n{'-' * 70}")
            print(f"Year {year}: {info['filename']} ({info['size'] / 1e6:.0f} MB)")
            print(f"{'-' * 70}")

            if is_already_downloaded(local_path, info['size'], info['md5']):
                print(f"    Already downloaded and verified, skipping")
                skipped_count += 1
                continue

            success = download_file(info['download_url'], local_path, info['size'])

            if not success:
                failed_years.append(year)
                continue

            print(f"    Verifying checksum...")
            actual_md5 = compute_md5(local_path)
            if actual_md5 != info['md5']:
                print(f"    ERROR: Checksum mismatch for {info['filename']}!")
                print(f"      Expected: {info['md5']}")
                print(f"      Actual:   {actual_md5}")
                print(f"    Deleting corrupted download.")
                local_path.unlink()
                failed_years.append(year)
                continue

            print(f"    Checksum verified OK")
            downloaded_count += 1

        # Summary
        print()
        print("=" * 70)
        if failed_years:
            print("CLCD DOWNLOAD COMPLETED WITH ERRORS")
        else:
            print("SUCCESS: CLCD DOWNLOAD COMPLETE")
        print("=" * 70)
        print()
        print(f"Newly downloaded: {downloaded_count}")
        print(f"Already had (verified): {skipped_count}")
        print(f"Failed: {len(failed_years)}")
        if failed_years:
            print(f"  Failed years: {failed_years}")
        print(f"\nOutput directory: {DOWNLOAD_CLCD_SOURCE_DIR}")

        # Fail loudly if any year failed to download - a partial download set
        # must not be treated as a clean run (CRITICAL RULE 2 and 3)
        if failed_years:
            sys.exit(1)

        print()
        print("Next steps:")
        print("  1. Build CLCD panel: python code/build_clcd_panel/build_clcd_panel_v1.py")
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
