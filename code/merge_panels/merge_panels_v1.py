"""
Merge Panels

Environment: environment_stats

Requires (inputs from earlier stages):
    - data/create_city_buffers/source/cities_sample.csv (from manual input)
    - data/build_lst_panel/generated/lst_panel.csv (from build_lst_panel, with _5km and _10km columns)
    - data/build_tree_cover_panel/generated/tree_cover_panel.csv (from build_tree_cover_panel, with _5km and _10km columns)
    - data/build_modis_ndvi_panel/generated/modis_ndvi_panel.csv (from build_modis_ndvi_panel, with _5km and _10km columns)
    - data/build_clcd_panel/generated/clcd_panel.csv (from build_clcd_panel, with _5km and _10km columns, optional)
    - data/merge_panels/source/nightlights_panel.csv (optional)

Produces (outputs for later stages):
    - data/merge_panels/generated/city_year_panel.parquet
    - data/merge_panels/generated/city_year_panel.csv (backup)

Description:
    Merges all processed panel datasets (LST, tree cover, NDVI, nightlights) into a single
    city-year panel dataset with treatment indicators for difference-in-differences analysis.

Updated: 2026-06-24 - Handle multiple buffer sizes (_5km and _10km suffixed columns)
Updated: 2026-06-25 - Merge in CLCD (Landsat-derived) forest cover panel
Updated: 2026-06-27 - Add lat/lon to panel; fix province_en column name;
                      remove event_score (not in current cities_full schema)

Usage:
    python code/merge_panels/merge_panels_v1.py

Expected runtime: < 1 minute
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    CITIES_FILE,
    LST_PANEL_FILE,
    TREE_COVER_PANEL_FILE,
    MODIS_NDVI_PANEL_FILE,
    CLCD_PANEL_FILE,
    NIGHTLIGHTS_PANEL_FILE,
    FINAL_PANEL_FILE,
    STUDY_START_YEAR,
    STUDY_END_YEAR
)


def check_required_inputs():
    """
    Check that all required input files exist before starting.
    Fail loudly if any are missing (CRITICAL RULE 2).
    """
    required_files = [
        CITIES_FILE,
    ]

    missing = [f for f in required_files if not f.exists()]

    if missing:
        print("ERROR: Missing required input files:")
        for f in missing:
            print(f"  - {f}")
        print()
        print("Please run earlier pipeline stages first.")
        sys.exit(1)

    # LST panel is recommended but not strictly required
    if not LST_PANEL_FILE.exists():
        print("Warning: LST panel file not found:")
        print(f"  - {LST_PANEL_FILE}")
        print("  Panel will be created without LST data.")


def load_city_info():
    """
    Load city information including treatment assignment.

    Returns
    -------
    pd.DataFrame
        City information with treatment indicators
    """
    print("Loading city information...")

    cities_df = pd.read_csv(CITIES_FILE)

    print(f"  Loaded {len(cities_df)} cities")
    print(f"    - Treated: {cities_df['treated'].sum()}")
    print(f"    - Control: {(1 - cities_df['treated']).sum()}")

    return cities_df


def load_panel_data(file_path, panel_name):
    """
    Load a panel dataset if it exists.

    Parameters
    ----------
    file_path : Path
        Path to panel file
    panel_name : str
        Name of panel (for logging)

    Returns
    -------
    pd.DataFrame or None
        Panel dataframe or None if file doesn't exist
    """
    if not file_path.exists():
        print(f"  Warning: {panel_name} not found: {file_path}")
        return None

    df = pd.read_csv(file_path)
    print(f"  Loaded {panel_name}: {len(df)} observations")

    return df


def merge_panels(lst_df, tree_cover_df, ndvi_df, clcd_df, nightlights_df):
    """
    Merge all panel datasets on (city_id, year).

    Updated: 2026-06-24 - Dynamically select all buffer size columns
    Updated: 2026-06-25 - Added clcd_df (Landsat-derived forest cover, optional)

    Parameters
    ----------
    lst_df : pd.DataFrame or None
        LST panel (with _5km and _10km columns)
    tree_cover_df : pd.DataFrame or None
        Tree cover panel (with _5km and _10km columns)
    ndvi_df : pd.DataFrame or None
        NDVI panel (with _5km and _10km columns)
    clcd_df : pd.DataFrame or None
        CLCD forest cover panel (with _5km and _10km columns)
    nightlights_df : pd.DataFrame or None
        Nightlights panel

    Returns
    -------
    pd.DataFrame
        Merged panel dataset
    """
    print("\nMerging panels...")

    # Start with LST as base (or create empty panel if LST not available)
    if lst_df is not None:
        panel = lst_df.copy()
        print(f"  Starting with LST panel: {len(panel)} obs")
        lst_cols = [col for col in lst_df.columns if col.startswith('lst_')]
        print(f"    LST columns: {lst_cols}")
    else:
        # Create skeleton panel if no data available yet
        print("  Warning: No panels available - creating skeleton panel")
        cities = load_city_info()
        years = list(range(STUDY_START_YEAR, STUDY_END_YEAR + 1))

        panel = pd.DataFrame([
            {'city_id': city_id, 'city_name': cities[cities['city_id'] == city_id]['city_name'].values[0], 'year': year}
            for city_id in cities['city_id']
            for year in years
        ])
        print(f"  Created skeleton panel: {len(panel)} obs")

    # Merge tree cover (all tree_cover_* columns)
    if tree_cover_df is not None:
        # Select all tree cover columns plus merge keys
        tree_cover_cols = [col for col in tree_cover_df.columns if col.startswith('tree_cover_')]
        merge_cols = ['city_id', 'year'] + tree_cover_cols

        # Add city_name if not already in panel
        if 'city_name' not in panel.columns and 'city_name' in tree_cover_df.columns:
            merge_cols.append('city_name')

        panel = panel.merge(
            tree_cover_df[merge_cols],
            on=['city_id', 'year'],
            how='outer'
        )
        print(f"  Merged tree cover: {len(panel)} obs")
        print(f"    Tree cover columns: {tree_cover_cols}")

    # Merge NDVI (all ndvi_* columns)
    if ndvi_df is not None:
        # Select all NDVI columns plus merge keys
        ndvi_cols = [col for col in ndvi_df.columns if col.startswith('ndvi_')]
        merge_cols = ['city_id', 'year'] + ndvi_cols

        # Add city_name if not already in panel
        if 'city_name' not in panel.columns and 'city_name' in ndvi_df.columns:
            merge_cols.append('city_name')

        panel = panel.merge(
            ndvi_df[merge_cols],
            on=['city_id', 'year'],
            how='outer'
        )
        print(f"  Merged NDVI: {len(panel)} obs")
        print(f"    NDVI columns: {ndvi_cols}")

    # Merge CLCD (all clcd_* columns)
    if clcd_df is not None:
        clcd_cols = [col for col in clcd_df.columns if col.startswith('clcd_')]
        merge_cols = ['city_id', 'year'] + clcd_cols

        if 'city_name' not in panel.columns and 'city_name' in clcd_df.columns:
            merge_cols.append('city_name')

        panel = panel.merge(
            clcd_df[merge_cols],
            on=['city_id', 'year'],
            how='outer'
        )
        print(f"  Merged CLCD: {len(panel)} obs")
        print(f"    CLCD columns: {clcd_cols}")

    # Merge nightlights
    if nightlights_df is not None:
        panel = panel.merge(
            nightlights_df[['city_id', 'year', 'nightlights_annual']],
            on=['city_id', 'year'],
            how='outer'
        )
        print(f"  Merged nightlights: {len(panel)} obs")

    print(f"\nFinal merged panel: {len(panel)} observations")

    return panel


def add_treatment_indicators(panel, cities_df):
    """
    Add treatment indicators and event study variables.

    Parameters
    ----------
    panel : pd.DataFrame
        Merged panel dataset
    cities_df : pd.DataFrame
        City information with treatment assignment

    Returns
    -------
    pd.DataFrame
        Panel with treatment indicators
    """
    print("\nAdding treatment indicators...")

    # Merge city information.
    # Note: cities_full.csv uses 'province_en' (not 'province'); event_score
    # was present in older versions but is no longer in the schema -- omitted.
    # lat/lon are included so spatial covariates are available in the panel.
    city_cols = ['city_id', 'city_name', 'treated', 'designation_year',
                 'lat', 'lon', 'province_en']
    # province_en may be blank for control cities; include it if present
    available = [c for c in city_cols if c in cities_df.columns]
    panel = panel.merge(
        cities_df[available],
        on='city_id',
        how='left'
    )

    # Create treatment dummy (1 if year >= designation_year, 0 otherwise)
    # For control cities (treated=0), designation_year is NaN, so treated_post will be 0
    panel['treated_post'] = 0
    treated_mask = (panel['treated'] == 1) & (panel['year'] >= panel['designation_year'])
    panel.loc[treated_mask, 'treated_post'] = 1

    # Create event study relative time variable
    # rel_time = year - designation_year
    # For control cities, rel_time is NaN
    panel['rel_time'] = panel['year'] - panel['designation_year']

    # For DiD: treated x post interaction
    panel['treated_x_post'] = panel['treated'] * panel['treated_post']

    # Years since treatment (0 for pre-treatment and controls)
    panel['years_since_treatment'] = 0
    treatment_mask = (panel['treated'] == 1) & (panel['year'] >= panel['designation_year'])
    panel.loc[treatment_mask, 'years_since_treatment'] = panel.loc[treatment_mask, 'year'] - panel.loc[treatment_mask, 'designation_year']

    print("Added treatment indicators:")
    print(f"  - treated: Treatment group indicator")
    print(f"  - treated_post: Post-treatment indicator")
    print(f"  - treated_x_post: Interaction term (DiD estimator)")
    print(f"  - rel_time: Event study time")
    print(f"  - years_since_treatment: Years since designation")

    # Summary statistics
    print(f"\nTreatment balance:")
    print(f"  Treated x Post = 1: {panel['treated_x_post'].sum()} obs")
    print(f"  Treated x Pre = 1: {((panel['treated'] == 1) & (panel['treated_post'] == 0)).sum()} obs")
    print(f"  Control: {(panel['treated'] == 0).sum()} obs")

    return panel


def validate_final_panel(panel):
    """
    Validate the final panel dataset.

    Updated: 2026-06-24 - Check all buffer size columns

    Parameters
    ----------
    panel : pd.DataFrame
        Final panel dataset

    Returns
    -------
    dict
        Validation statistics
    """
    print("\nValidating final panel...")

    stats = {
        'total_obs': len(panel),
        'cities': panel['city_id'].nunique(),
        'years': panel['year'].nunique(),
        'treated_cities': panel[panel['treated'] == 1]['city_id'].nunique(),
        'control_cities': panel[panel['treated'] == 0]['city_id'].nunique(),
    }

    print(f"  Total observations: {stats['total_obs']}")
    print(f"  Unique cities: {stats['cities']}")
    print(f"  Years: {stats['years']} ({panel['year'].min()}-{panel['year'].max()})")
    print(f"  Treated cities: {stats['treated_cities']}")
    print(f"  Control cities: {stats['control_cities']}")

    # Check for balance
    expected_obs = stats['cities'] * stats['years']
    if stats['total_obs'] != expected_obs:
        print(f"\n  Warning: Expected {expected_obs} obs, got {stats['total_obs']}")

    # Check data completeness for all outcome variables (all buffer sizes)
    print(f"\nData completeness:")

    # Get all outcome columns (LST, tree cover, NDVI) with buffer size suffixes
    outcome_columns = []
    for prefix in ['lst_', 'tree_cover_', 'ndvi_', 'clcd_']:
        outcome_columns.extend([col for col in panel.columns if col.startswith(prefix)])

    # Add nightlights if present
    if 'nightlights_annual' in panel.columns:
        outcome_columns.append('nightlights_annual')

    for col in sorted(outcome_columns):
        if col in panel.columns:
            complete_pct = panel[col].notna().sum() / len(panel) * 100
            print(f"  {col}: {complete_pct:.1f}% complete")

    return stats


def save_final_panel(panel, output_file):
    """
    Save final panel dataset.

    Parameters
    ----------
    panel : pd.DataFrame
        Final panel dataset
    output_file : Path
        Output file path
    """
    print(f"\nSaving final panel to: {output_file}")

    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Try to save as parquet (efficient storage and preserves dtypes)
    # Fall back to CSV-only if pyarrow is not available
    parquet_saved = False
    try:
        panel.to_parquet(output_file, index=False)
        print(f"Saved {len(panel)} observations (parquet)")
        print(f"  File size: {output_file.stat().st_size / 1024:.1f} KB")
        parquet_saved = True
    except ImportError:
        print("  Warning: pyarrow not available, skipping parquet format")

    # Save CSV copy (always, for easy inspection)
    csv_file = output_file.with_suffix('.csv')
    panel.to_csv(csv_file, index=False)
    if parquet_saved:
        print(f"  CSV copy: {csv_file}")
    else:
        print(f"Saved {len(panel)} observations (CSV)")
        print(f"  File: {csv_file}")
        print(f"  File size: {csv_file.stat().st_size / 1024:.1f} KB")


def main():
    """Main execution function."""
    print("=" * 70)
    print("MERGE PANELS PIPELINE")
    print("=" * 70)
    print()

    try:
        # Check required inputs exist
        check_required_inputs()

        # Step 1: Load city information
        cities_df = load_city_info()

        # Step 2: Load all panel datasets
        print("\nLoading panel datasets...")
        lst_df = load_panel_data(LST_PANEL_FILE, "LST panel")
        tree_cover_df = load_panel_data(TREE_COVER_PANEL_FILE, "Tree cover panel")
        ndvi_df = load_panel_data(MODIS_NDVI_PANEL_FILE, "MODIS NDVI panel")
        clcd_df = load_panel_data(CLCD_PANEL_FILE, "CLCD panel")
        nightlights_df = load_panel_data(NIGHTLIGHTS_PANEL_FILE, "Nightlights panel")

        # Step 3: Merge all panels
        panel = merge_panels(lst_df, tree_cover_df, ndvi_df, clcd_df, nightlights_df)

        # Step 4: Add treatment indicators
        panel = add_treatment_indicators(panel, cities_df)

        # Step 5: Validate
        stats = validate_final_panel(panel)

        # Step 6: Save
        save_final_panel(panel, FINAL_PANEL_FILE)

        # Success message
        print()
        print("=" * 70)
        print("SUCCESS: FINAL PANEL CREATED")
        print("=" * 70)
        print()
        print(f"Output file: {FINAL_PANEL_FILE}")
        print()
        print("Panel structure:")
        print(f"  - {stats['total_obs']} observations")
        print(f"  - {stats['cities']} cities ({stats['treated_cities']} treated + {stats['control_cities']} control)")
        print(f"  - {stats['years']} years ({panel['year'].min()}-{panel['year'].max()})")
        print()
        print("Next steps:")
        print("  1. Run analysis: python code/run_analysis/run_analysis_v1.py")
        print()

    except Exception as e:
        print()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
