"""
Convert HDF4 to Numpy Arrays

Environment: environment_data

Requires (inputs from earlier stages):
    - data/download_modis_lst/generated/**/*.hdf (from download_modis_lst)

Produces (outputs for later stages):
    - data/convert_hdf_to_arrays/generated/**/*.npz (for build_lst_panel)

Description:
    Converts MODIS HDF4 files to numpy arrays (.npz format) with embedded metadata.
    This step exists because Windows GDAL often lacks HDF4 driver support. By
    extracting to numpy arrays once, we can use standard tools for all subsequent
    processing without HDF4 dependencies.

Usage:
    python code/convert_hdf_to_arrays/convert_hdf_to_arrays_v1.py

Expected runtime: 5-10 minutes (depends on number of granules, uses 24 parallel workers)

Dependencies:
    - pyhdf: Reads HDF4 files
    - numpy: Array handling and saving

Environment setup:
    conda create -n hdf_convert -c conda-forge python=3.11 pyhdf "numpy<2" -y
"""

import sys
from pathlib import Path
from pyhdf.SD import SD, SDC
import numpy as np
from multiprocessing import Pool, cpu_count

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    MODIS_LST_DIR,
    MODIS_LST_ARRAYS_DIR,
    LST_DAY_BAND,
    MAX_WORKERS
)


def check_required_inputs():
    """
    Check that all required input files exist before starting.
    Fail loudly if any are missing (CRITICAL RULE 2).
    """
    if not MODIS_LST_DIR.exists():
        print("ERROR: Missing required input directory:")
        print(f"  - {MODIS_LST_DIR}")
        print()
        print("Please run: python code/download_modis_lst/download_modis_lst_v1.py")
        sys.exit(1)

    # Check for at least one .hdf file
    hdf_files = list(MODIS_LST_DIR.glob("**/*.hdf"))
    if not hdf_files:
        print("ERROR: No HDF4 files found in:")
        print(f"  - {MODIS_LST_DIR}")
        print()
        print("Please run: python code/download_modis_lst/download_modis_lst_v1.py")
        sys.exit(1)


def convert_hdf_to_array(hdf_path, output_path):
    """
    Convert a single MODIS HDF4 file to numpy archive (.npz) with metadata.

    Extracts the LST data and all necessary geospatial metadata so that
    venv_geo can later use it for zonal statistics without needing HDF4 support.

    Parameters
    ----------
    hdf_path : Path
        Path to input .hdf file
    output_path : Path
        Path to output .npz file

    Returns
    -------
    bool
        True if conversion succeeded, False otherwise
    """
    try:
        # Create output directory if it doesn't exist
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Open the HDF4 file for reading using pyhdf
        hdf = SD(str(hdf_path), SDC.READ)

        # Get the LST_Day_1km dataset (daytime land surface temperature)
        sds = hdf.select(LST_DAY_BAND)

        # Read the data as a numpy array
        data = sds.get()

        # Get dataset attributes
        sds_attrs = sds.attributes()

        # Get global attributes from the HDF file for geospatial metadata
        file_attrs = hdf.attributes()

        # Extract important metadata
        fill_value = sds_attrs.get('_FillValue', 0)
        scale_factor = sds_attrs.get('scale_factor', 0.02)
        add_offset = sds_attrs.get('add_offset', 0.0)
        valid_range = sds_attrs.get('valid_range', [0, 65535])

        # Close the HDF4 file
        hdf.end()

        # Extract tile ID from filename (e.g., MOD11A2.A2000001.h26v05.061.hdf -> h26v05)
        # This is needed to reconstruct the MODIS sinusoidal grid geotransform
        filename = hdf_path.name

        # Save as compressed numpy archive with all metadata
        # This preserves everything we need for zonal stats later
        np.savez_compressed(
            output_path,
            data=data,
            fill_value=fill_value,
            scale_factor=scale_factor,
            add_offset=add_offset,
            valid_range=valid_range,
            shape=data.shape,
            dtype=str(data.dtype),
            filename=filename  # Save original filename to extract tile ID later
        )

        return True

    except Exception as e:
        print(f"  Error converting {hdf_path.name}: {e}", file=sys.stderr)
        return False


def convert_file_wrapper(args):
    """
    Wrapper function for multiprocessing.Pool.map().

    Takes a tuple of (hdf_path, output_path) and returns result tuple.

    Parameters
    ----------
    args : tuple
        (hdf_path, output_path, file_number, total_files)

    Returns
    -------
    tuple
        (status, hdf_filename) where status is 'converted', 'skipped', or 'failed'
    """
    hdf_path, output_path, file_num, total_files = args

    # Skip if already converted
    if output_path.exists():
        return ('skipped', hdf_path.name)

    # Print progress occasionally (every 10th file)
    if file_num % 10 == 0:
        print(f"  Progress: {file_num}/{total_files} files...")

    # Convert the file
    success = convert_hdf_to_array(hdf_path, output_path)

    if success:
        return ('converted', hdf_path.name)
    else:
        return ('failed', hdf_path.name)


def main():
    """
    Convert all MODIS HDF4 files to numpy arrays with metadata.

    This script scans data/download_modis_lst/generated/ for .hdf files and converts
    each to a .npz file in data/convert_hdf_to_arrays/generated/, preserving the
    directory structure.
    """
    print("=" * 80)
    print("MODIS HDF4 to Numpy Array Converter")
    print("=" * 80)
    print()

    try:
        # Check required inputs exist
        check_required_inputs()

        # Find all .hdf files recursively
        hdf_files = list(MODIS_LST_DIR.glob("**/*.hdf"))

        print(f"Found {len(hdf_files)} HDF4 files to convert")
        print(f"Input directory: {MODIS_LST_DIR}")
        print(f"Output directory: {MODIS_LST_ARRAYS_DIR}")
        print(f"Using {MAX_WORKERS} parallel workers")
        print()

        # Prepare arguments for parallel processing
        # Build list of (hdf_path, output_path, file_number, total_files) tuples
        conversion_args = []
        for i, hdf_path in enumerate(hdf_files, 1):
            relative_path = hdf_path.relative_to(MODIS_LST_DIR)
            output_path = MODIS_LST_ARRAYS_DIR / relative_path.with_suffix('.npz')
            conversion_args.append((hdf_path, output_path, i, len(hdf_files)))

        # Convert files in parallel using multiprocessing pool
        print(f"Converting files in parallel with {MAX_WORKERS} workers...")
        with Pool(MAX_WORKERS) as pool:
            results = pool.map(convert_file_wrapper, conversion_args)

        # Tally results
        converted = sum(1 for status, _ in results if status == 'converted')
        skipped = sum(1 for status, _ in results if status == 'skipped')
        failed = sum(1 for status, _ in results if status == 'failed')

        # Print summary
        print()
        print("=" * 80)
        print("Conversion Summary")
        print("=" * 80)
        print(f"  Total files: {len(hdf_files)}")
        print(f"  Already converted (skipped): {skipped}")
        print(f"  Newly converted: {converted}")
        print(f"  Failed: {failed}")
        print()

        if failed > 0:
            print("ERROR: Some files failed to convert (see errors above)")
            sys.exit(1)
        else:
            print("SUCCESS: All HDF4 files converted to numpy arrays")
            print(f"  Output: {MODIS_LST_ARRAYS_DIR}")
            print()
            print("Next steps:")
            print("  1. Build LST panel: python code/build_lst_panel/build_lst_panel_v1.py")

    except Exception as e:
        print()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
