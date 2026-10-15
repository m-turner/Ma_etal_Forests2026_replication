"""
Download GHCN-Daily Temperature Data for Chinese Stations

Environment: environment_geo

Requires (inputs from earlier stages):
    - None (downloads directly from NOAA public servers)

Produces (outputs for later stages):
    - data/station_temps/source/ghcn_china_stations.csv   (station inventory: id, lat, lon, name)
    - data/station_temps/source/dly/{STATION_ID}.dly      (one raw file per station, ~500-800 files)

Description:
    Downloads GHCN-Daily (Global Historical Climatology Network) data for all
    Chinese weather stations. GHCN-Daily is NOAA's public dataset of daily
    temperature records from land stations worldwide.

    Step 1: Downloads the global station inventory (ghcnd-stations.txt),
            filters for stations in China (IDs starting with "CH"), and
            saves a CSV with station metadata including coordinates.

    Step 2: For each Chinese station, downloads its daily record file (.dly)
            from NOAA's public server. Implements resume capability so
            already-downloaded files are skipped (safe to re-run).

    The .dly files are raw and must be parsed by build_station_panel_v1.py
    to extract usable temperature variables.

Usage:
    conda activate C:\\py_envs\\environment_geo
    python code\\download_station_temps\\download_ghcn_china_v1.py

Expected runtime: 20-60 minutes depending on network speed (~500-800 stations).
"""

import sys
import csv
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# PATH SETUP: add project root to sys.path so we can import config.py
# ---------------------------------------------------------------------------
# __file__ is code/download_station_temps/download_ghcn_china_v1.py
# .parent = code/download_station_temps/
# .parent.parent = code/
# .parent.parent.parent = project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT / 'code'))

from utils.config import (
    STATION_TEMPS_SOURCE_DIR,
    STATION_TEMPS_DLY_DIR,
    GHCN_STATIONS_FILE,
)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

# NOAA public server — no authentication needed
GHCN_BASE_URL     = 'https://www.ncei.noaa.gov/pub/data/ghcn/daily'
STATIONS_URL      = f'{GHCN_BASE_URL}/ghcnd-stations.txt'
BY_STATION_URL    = f'{GHCN_BASE_URL}/all'

# How long to wait between HTTP requests (seconds) — be polite to NOAA servers
REQUEST_DELAY_SEC = 0.5

# Country prefix for China in GHCN station IDs (FIPS-10 code)
CHINA_PREFIX = 'CH'

# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------

LOG_FILE = PROJECT_ROOT / f'last_run_download_station_temps.log'


def log(msg: str, file=None):
    """Print msg with timestamp, and mirror to the log file."""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    if file:
        file.write(line + '\n')
        file.flush()


# ---------------------------------------------------------------------------
# STEP 1: Download the global station inventory and filter for China
# ---------------------------------------------------------------------------

def download_station_inventory(logfile) -> list[dict]:
    """
    Download ghcnd-stations.txt from NOAA, parse it, and return a list
    of dicts for Chinese stations only.

    The file is fixed-width with this layout (from the GHCN readme):
        Chars  1-11: Station ID
        Chars 13-20: Latitude (decimal degrees)
        Chars 22-30: Longitude (decimal degrees)
        Chars 32-37: Elevation (meters; -999.9 = missing)
        Chars 42-71: Station name
        Chars 81-85: WMO ID (if station has one)
    """
    log("Step 1: Downloading global station inventory...", logfile)
    log(f"  Source: {STATIONS_URL}", logfile)

    try:
        # Stream the file content — it is ~10 MB
        with urllib.request.urlopen(STATIONS_URL, timeout=120) as response:
            raw_bytes = response.read()
    except urllib.error.URLError as e:
        log(f"FATAL: Could not download station inventory: {e}", logfile)
        sys.exit(1)

    content = raw_bytes.decode('ascii', errors='replace')
    lines   = content.splitlines()
    log(f"  Downloaded {len(lines):,} total station records.", logfile)

    # Parse only Chinese stations (ID starts with "CH")
    china_stations = []
    for line in lines:
        if len(line) < 21:
            continue  # skip malformed short lines

        station_id = line[0:11].strip()
        if not station_id.startswith(CHINA_PREFIX):
            continue

        # Parse fixed-width fields
        try:
            lat    = float(line[12:20].strip())
            lon    = float(line[21:30].strip())
            elev   = float(line[31:37].strip()) if line[31:37].strip() else -999.9
            name   = line[41:71].strip() if len(line) > 41 else ''
            wmo_id = line[80:85].strip() if len(line) > 80 else ''
        except ValueError:
            continue  # skip if lat/lon can't be parsed

        china_stations.append({
            'station_id': station_id,
            'lat':        lat,
            'lon':        lon,
            'elev_m':     elev,
            'name':       name,
            'wmo_id':     wmo_id,
        })

    log(f"  Found {len(china_stations):,} Chinese stations (prefix '{CHINA_PREFIX}').", logfile)

    # Save the filtered inventory as a CSV for later use by build_station_panel
    GHCN_STATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GHCN_STATIONS_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['station_id','lat','lon','elev_m','name','wmo_id'])
        writer.writeheader()
        writer.writerows(china_stations)

    log(f"  Station inventory saved: {GHCN_STATIONS_FILE}", logfile)
    return china_stations


# ---------------------------------------------------------------------------
# STEP 2: Download one .dly file per Chinese station
# ---------------------------------------------------------------------------

def download_dly_files(stations: list[dict], logfile) -> dict:
    """
    For each station in the list, download its daily record file (.dly)
    from NOAA's by_station folder.

    Implements resume: if the local .dly file already exists, it is skipped.
    Logs failures but does not crash — a missing station file is non-fatal
    because some stations may not be in the by_station archive.

    Returns a summary dict: {downloaded, skipped, failed}.
    """
    log(f"\nStep 2: Downloading .dly files for {len(stations):,} Chinese stations...", logfile)
    log(f"  Output directory: {STATION_TEMPS_DLY_DIR}", logfile)
    log(f"  Request delay: {REQUEST_DELAY_SEC}s between downloads\n", logfile)

    STATION_TEMPS_DLY_DIR.mkdir(parents=True, exist_ok=True)

    n_downloaded = 0
    n_skipped    = 0
    n_failed     = 0
    failed_ids   = []

    for i, stn in enumerate(stations, start=1):
        sid       = stn['station_id']
        out_path  = STATION_TEMPS_DLY_DIR / f'{sid}.dly'

        # Resume: skip if already downloaded
        if out_path.exists() and out_path.stat().st_size > 0:
            n_skipped += 1
            if i % 50 == 0:
                log(f"  [{i}/{len(stations)}] Progress: {n_downloaded} downloaded, "
                    f"{n_skipped} skipped, {n_failed} failed", logfile)
            continue

        url = f'{BY_STATION_URL}/{sid}.dly'
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
            out_path.write_bytes(data)
            n_downloaded += 1

            if i % 25 == 0 or i <= 5:
                log(f"  [{i}/{len(stations)}] Downloaded {sid} ({len(data):,} bytes)", logfile)

        except urllib.error.HTTPError as e:
            # Log every HTTP error so systematic problems (wrong URL path, auth) are visible
            log(f"  WARNING [{i}/{len(stations)}] HTTP {e.code} for {sid}", logfile)
            n_failed += 1
            failed_ids.append(sid)

        except urllib.error.URLError as e:
            log(f"  WARNING [{i}/{len(stations)}] Network error for {sid}: {e}", logfile)
            n_failed += 1
            failed_ids.append(sid)

        # Polite delay between requests
        time.sleep(REQUEST_DELAY_SEC)

    log(f"\n  Done. Downloaded: {n_downloaded} | Skipped (already had): {n_skipped} | "
        f"Failed/missing: {n_failed}", logfile)

    if failed_ids:
        # Save the list of failed IDs so they can be investigated later
        fail_path = STATION_TEMPS_SOURCE_DIR / 'download_failures.txt'
        fail_path.write_text('\n'.join(failed_ids), encoding='utf-8')
        log(f"  Failed station IDs saved to: {fail_path}", logfile)

    return {'downloaded': n_downloaded, 'skipped': n_skipped, 'failed': n_failed}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    with open(LOG_FILE, 'w', encoding='utf-8') as logfile:
        log("=" * 70, logfile)
        log("Download GHCN-Daily China Station Data", logfile)
        log(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", logfile)
        log("=" * 70, logfile)

        # Step 1: get station list
        stations = download_station_inventory(logfile)

        if not stations:
            log("FATAL: No Chinese stations found in inventory. Check country prefix.", logfile)
            sys.exit(1)

        # Step 2: download .dly files
        summary = download_dly_files(stations, logfile)

        # Final status
        log("\n" + "=" * 70, logfile)
        total = summary['downloaded'] + summary['skipped']
        log(f"Run complete. {total} stations have local .dly files "
            f"({summary['downloaded']} newly downloaded, "
            f"{summary['skipped']} already present, "
            f"{summary['failed']} unavailable on server).", logfile)
        log("Next step: run build_station_panel_v1.py to parse temperature data.", logfile)
        log("=" * 70, logfile)


if __name__ == '__main__':
    main()
