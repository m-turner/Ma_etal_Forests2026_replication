"""
Convert MOD44B HDF4 to Numpy Arrays

Environment: environment_data

Requires (inputs from earlier stages):
    - data/download_modis_tree_cover/source/**/*.hdf (from download_modis_tree_cover)

Produces (outputs for later stages):
    - data/convert_tree_cover_to_arrays/generated/**/*.npz (for build_tree_cover_panel)

Description:
    Converts MODIS MOD44B HDF4 files to numpy arrays (.npz format) with embedded metadata.
    Extracts the Percent_Tree_Cover band which provides annual tree canopy cover estimates
    at 250m resolution. Like the LST conversion, this step exists to avoid HDF4 dependency
    issues on Windows.

Usage:
    python code/convert_tree_cover_to_arrays/convert_tree_cover_to_arrays_v1.py

Expected runtime: 2-5 minutes (fewer files than LST: ~130 vs ~6,000)

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
    MODIS_TREE_COVER_DIR,
    MODIS_TREE_COVER_ARRAYS_DIR,
    TREE_COVER_BAND,
    TREE_COVER_NODATA,
    MAX_WORKERS
)


def check_required_inputs():
    """
    Check that all required input files exist before starting.
    Fail loudly if any are missing (CRITICAL RULE 2).
    """
    if not MODIS_TREE_COVER_DIR.exists():
        print("ERROR: Missing required input directory:")
        print(f"  - {MODIS_TREE_COVER_DIR}")
        print()
        print("Please run: python code/download_modis_tree_cover/download_modis_tree_cover_v1.py")
        sys.exit(1)

    # Check for at least one .hdf file
    hdf_files = list(MODIS_TREE_COVER_DIR.glob("**/*.hdf"))
    if not hdf_files:
        print("ERROR: No HDF4 files found in:")
        print(f"  - {MODIS_TREE_COVER_DIR}")
        print()
        print("Please run: python code/download_modis_tree_cover/download_modis_tree_cover_v1.py")
        sys.exit(1)


def convert_hdf_to_array(hdf_path, output_path):
    """
    Convert a single MOD44B HDF4 file to numpy archive (.npz) with metadata.

    Extracts the Percent_Tree_Cover band and all necessary geospatial metadata
    so that venv_geo can later use it for zonal statistics without needing
    HDF4 support.

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

        # Get the Percent_Tree_Cover dataset (tree canopy percentage 0-100)
        sds = hdf.select(TREE_COVER_BAND)

        # Read the data as a numpy array
        data = sds.get()

        # Get dataset attributes
        sds_attrs = sds.attributes()

        # Get global attributes from the HDF file for geospatial metadata
        file_attrs = hdf.attributes()

        # Extract important metadata
        # Tree cover has different metadata than LST:
        # - Values are already 0-100% (no scaling needed)
        # - Nodata/water is indicated by values >= 200
        fill_value = sds_attrs.get('_FillValue', TREE_COVER_NODATA)
        scale_factor = 1.0  # No scaling - already in percent
        add_offset = 0.0    # No offset
        valid_range = sds_attrs.get('valid_range', [0, 100])

        # Close the HDF4 file
        hdf.end()

        # Extract tile ID from filename (e.g., MOD44B.A2020065.h26v05.061.hdf -> h26v05)
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
        (status, hdf_path) where status is 'converted', 'skipped', or 'failed'
    """
    hdf_path, output_path, file_number, total_files = args

    # Skip if output already exists
    if output_path.exists():
        print(f"  [{file_number}/{total_files}] Skipping {hdf_path.name} (already converted)")
        return ('skipped', hdf_path)

    # Convert the file
    print(f"  [{file_number}/{total_files}] Converting {hdf_path.name}...")
    success = convert_hdf_to_array(hdf_path, output_path)

    if success:
        return ('converted', hdf_path)
    else:
        return ('failed', hdf_path)


def main():
    """Main execution function."""
    print("=" * 70)
    print("CONVERT MOD44B HDF4 TO NUMPY ARRAYS")
    print("=" * 70)
    print()

    try:
        # Check required inputs exist
        check_required_inputs()

        # Find all .hdf files recursively
        hdf_files = list(MODIS_TREE_COVER_DIR.glob("**/*.hdf"))

        print(f"Found {len(hdf_files)} HDF4 files to convert")
        print(f"Input directory: {MODIS_TREE_COVER_DIR}")
        print(f"Output directory: {MODIS_TREE_COVER_ARRAYS_DIR}")
        print(f"Using {MAX_WORKERS} parallel workers")
        print()

        # Prepare arguments for parallel processing
        # Build list of (hdf_path, output_path, file_number, total_files) tuples
        conversion_args = []
        for i, hdf_path in enumerate(hdf_files, 1):
            relative_path = hdf_path.relative_to(MODIS_TREE_COVER_DIR)
            output_path = MODIS_TREE_COVER_ARRAYS_DIR / relative_path.with_suffix('.npz')
            conversion_args.append((hdf_path, output_path, i, len(hdf_files)))

        # Convert files in parallel using multiprocessing pool
        print(f"Converting files in parallel with {MAX_WORKERS} workers...")
        with Pool(MAX_WORKERS) as pool:
            results = pool.map(convert_file_wrapper, conversion_args)

        # Tally results
        converted = sum(1 for status, _ in results if status == 'converted')
        skipped = sum(1 for status, _ in results if status == 'skipped')
        failed = sum(1 for status, _ in results if status == 'failed')

        # Report summary
        print()
        print("=" * 70)
        print("CONVERSION COMPLETE")
        print("=" * 70)
        print()
        print(f"Results:")
        print(f"  Converted: {converted}")
        print(f"  Skipped (already existed): {skipped}")
        print(f"  Failed: {failed}")
        print(f"  Total: {len(hdf_files)}")
        print()

        if failed > 0:
            print("WARNING: Some files failed to convert.")
            print("Check error messages above for details.")
            print()

        print("Output directory:", MODIS_TREE_COVER_ARRAYS_DIR)
        print()
        print("Next steps:")
        print("  1. Build tree cover panel: python code/build_tree_cover_panel/build_tree_cover_panel_v1.py")
        print()

        # Exit with error if any conversions failed
        if failed > 0:
            sys.exit(1)

    except Exception as e:
        print()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
