"""
Create City Buffers

Environment: environment_geo

Requires (inputs from earlier stages):
    - data/create_city_buffers/source/cities_sample.csv  [POC, 10 cities]
    OR
    - data/build_city_universe/generated/cities_full.csv  [national expansion]
    To switch from POC to full analysis: change CITIES_FILE -> CITIES_FILE
    in the import below (both are defined in config.py).

Produces (outputs for later stages):
    - data/create_city_buffers/generated/city_buffers_5km.geojson
    - data/create_city_buffers/generated/city_buffers_10km.geojson
    - data/create_city_buffers/generated/city_buffers_20km.geojson

Description:
    Reads the city list CSV, creates Point geometries for city centers,
    projects to an equal-area CRS (Asia North Albers), and creates buffer disks
    at all radii in BUFFER_RADII_KM (currently 5, 10, 20 km).

Updated: 2026-06-24 - Added 10km buffers
Updated: 2026-06-25 - Added 20km buffer; added CITIES_FILE option for national expansion

Usage:
    python code/create_city_buffers/create_city_buffers_v1.py

Expected runtime: < 1 minute (POC); ~1 min (200+ cities)
"""

import sys
from pathlib import Path
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# Add code to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    CITIES_FILE,
    CITY_BUFFERS_5KM_FILE,
    CITY_BUFFERS_10KM_FILE,
    BUFFER_RADII_KM,
    CRS_GEOGRAPHIC,
    CRS_PROJECTED,
    FIGURES_DIR,
    get_buffer_file
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
        print("Run Step 0b first: python code/build_city_universe/build_city_universe_v3.py")
        sys.exit(1)


def create_city_centroids(city_list_file):
    """
    Read city list CSV and create GeoDataFrame with Point geometries.

    Parameters
    ----------
    city_list_file : Path
        Path to cities_sample.csv file

    Returns
    -------
    geopandas.GeoDataFrame
        GeoDataFrame with city points in WGS84 (EPSG:4326)
    """
    print(f"Reading city list from: {city_list_file}")

    # Read CSV
    df = pd.read_csv(city_list_file)

    print(f"Loaded {len(df)} cities:")
    print(f"  - Treated cities: {df['treated'].sum()}")
    print(f"  - Control cities: {(1 - df['treated']).sum()}")

    # Create Point geometries from lat/lon
    geometry = [Point(lon, lat) for lon, lat in zip(df['lon'], df['lat'])]

    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame(
        df,
        geometry=geometry,
        crs=CRS_GEOGRAPHIC  # WGS84
    )

    print(f"Created GeoDataFrame with {len(gdf)} city points")

    return gdf


def create_city_buffers(centroids_gdf, radius_km=5):
    """
    Create equal-area buffers around city centroids.

    Parameters
    ----------
    centroids_gdf : geopandas.GeoDataFrame
        GeoDataFrame with city points in geographic CRS
    radius_km : float
        Buffer radius in kilometers (default: 5)

    Returns
    -------
    geopandas.GeoDataFrame
        GeoDataFrame with buffer polygons in projected CRS
    """
    print(f"\nCreating {radius_km}km buffers around city centers...")

    # Project to equal-area coordinate system (Asia North Albers)
    # This ensures buffers are accurate circles
    print(f"  - Projecting to {CRS_PROJECTED}")
    gdf_proj = centroids_gdf.to_crs(CRS_PROJECTED)

    # Create buffers (radius in meters)
    radius_m = radius_km * 1000
    print(f"  - Creating buffers with radius = {radius_m}m")
    gdf_proj['geometry'] = gdf_proj.buffer(radius_m)

    # Calculate buffer area for verification
    gdf_proj['area_km2'] = gdf_proj.geometry.area / 1_000_000  # Convert m2 to km2
    expected_area = 3.14159 * (radius_km ** 2)
    print(f"  - Expected buffer area: ~{expected_area:.2f} km2")
    print(f"  - Actual mean buffer area: {gdf_proj['area_km2'].mean():.2f} km2")

    print(f"Created {len(gdf_proj)} buffer polygons")

    return gdf_proj


def save_buffers(buffers_gdf, output_file):
    """
    Save buffer GeoDataFrame to GeoJSON file.

    Parameters
    ----------
    buffers_gdf : geopandas.GeoDataFrame
        GeoDataFrame with buffer polygons
    output_file : Path
        Output file path (GeoJSON format)
    """
    print(f"\nSaving buffers to: {output_file}")

    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Save to GeoJSON (portable format)
    buffers_gdf.to_file(output_file, driver='GeoJSON')

    print(f"Saved {len(buffers_gdf)} buffers to GeoJSON")
    print(f"  File size: {output_file.stat().st_size / 1024:.1f} KB")


def visualize_buffers(buffers_gdf, radius_km):
    """
    Create a simple visualization of the city buffers.

    Parameters
    ----------
    buffers_gdf : geopandas.GeoDataFrame
        GeoDataFrame with buffer polygons
    radius_km : int
        Buffer radius in km, used only for the plot title.
        2026-06-25 fix: title previously referenced the undefined
        global BUFFER_RADIUS_KM, which crashed visualization.
    """
    try:
        import matplotlib.pyplot as plt

        print("\nCreating visualization...")

        # Reproject to geographic for plotting
        buffers_geo = buffers_gdf.to_crs(CRS_GEOGRAPHIC)

        fig, ax = plt.subplots(figsize=(12, 10))

        # Plot buffers colored by treatment status
        buffers_geo.plot(
            ax=ax,
            column='treated',
            cmap='RdYlGn',
            alpha=0.5,
            edgecolor='black',
            linewidth=0.5,
            legend=True,
            legend_kwds={'label': 'Treatment Status', 'orientation': 'horizontal'}
        )

        # Add city labels
        for idx, row in buffers_geo.iterrows():
            ax.annotate(
                text=row['city_name'],
                xy=(row.geometry.centroid.x, row.geometry.centroid.y),
                xytext=(3, 3),
                textcoords='offset points',
                fontsize=8,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.7)
            )

        ax.set_xlabel('Longitude')
        ax.set_ylabel('Latitude')
        ax.set_title(f'Study Cities - {radius_km}km Buffers\n'
                     f'(Green = Treated, Red = Control)', fontsize=14)
        ax.grid(True, alpha=0.3)

        # Save figure
        fig_path = FIGURES_DIR / 'city_buffers_map.png'
        fig_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(fig_path, dpi=150, bbox_inches='tight')
        print(f"Saved visualization to: {fig_path}")

    except ImportError:
        print("Warning: Matplotlib not available - skipping visualization")


def main():
    """Main execution function - Updated 2026-06-24 for multiple buffer sizes."""
    print("=" * 70)
    print("CITY CENTROIDS AND BUFFERS CREATION")
    print("=" * 70)
    print()

    try:
        # Check required inputs exist
        check_required_inputs()

        # Step 1: Create city centroids (shared for all buffer radii)
        centroids = create_city_centroids(CITIES_FILE)

        # Step 2: Create and save buffers for each radius
        print(f"\nCreating buffers for {len(BUFFER_RADII_KM)} radii: {BUFFER_RADII_KM} km")
        all_buffers = {}

        for radius_km in BUFFER_RADII_KM:
            print(f"\n{'-' * 70}")
            print(f"Processing {radius_km}km buffer...")
            print(f"{'-' * 70}")

            # Create buffers
            buffers = create_city_buffers(centroids, radius_km=radius_km)

            # Save to appropriate file
            output_file = get_buffer_file(radius_km)
            save_buffers(buffers, output_file)

            # Store for visualization
            all_buffers[radius_km] = buffers

        # Step 3: Visualize (using 5km buffers for the main map)
        if 5 in all_buffers:
            visualize_buffers(all_buffers[5], radius_km=5)

        print()
        print("=" * 70)
        print("SUCCESS: City buffers created successfully")
        print("=" * 70)
        print()
        print("Output files created:")
        for radius_km in BUFFER_RADII_KM:
            print(f"  - {get_buffer_file(radius_km)}")
        print()
        print("Summary:")
        print(f"  - Total cities: {len(centroids)}")
        print(f"  - Treated cities: {centroids['treated'].sum()}")
        print(f"  - Control cities: {(1 - centroids['treated']).sum()}")
        print(f"  - Buffer radii: {BUFFER_RADII_KM} km")
        print(f"  - Coordinate system: {CRS_PROJECTED}")
        print()
        print("Next steps:")
        print("  1. Download satellite data (python code/download_modis_lst/download_modis_lst_v1.py)")
        print("  2. Convert HDF4 to arrays (python code/convert_hdf_to_arrays/convert_hdf_to_arrays_v1.py)")
        print()

    except Exception as e:
        print()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
