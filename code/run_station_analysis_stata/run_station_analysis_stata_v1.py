"""
Run Station Analysis (Stata parallel) -- station level -- v1

Environment: environment_stats

Requires (inputs from earlier stages):
    - data/merge_panels/generated/station_city_panel.csv   (from merge_station_panels)
    - data/merge_panels/generated/city_covariates.csv      (from build_covariates)
    - code/run_station_analysis_stata/run_one_spec_station.do
    - Stata (see STATA_EXE below)
    - Stata packages csdid and did_imputation

Produces (outputs for later stages):
    - analysis/tables/stata_station_aggregate_atts.csv
      format: outcome_buffer, estimator, att, se
      estimators: cs, cs_dr, bjs, bjs_ctrl
    - analysis/tables/stata/station_{outcome}_{est}_coeffs.csv (per-outcome)
    - analysis/tables/stata/station_{outcome}.log              (per-outcome log)
    - analysis/figures/stata/station_{outcome}_{est}.png       (per-outcome)
    - last_run_stata_station.log                               (project root)

Estimator mapping to compare_python_stata_v1.py:
    Stata "cs"       <-> Python station "cs"     (CS, never-treated)
    Stata "cs_dr"    <-> Python station "dr"     (CS DR, not-yet-treated fallback)
    Stata "bjs"      <-> Python station "bjs"    (BJS, never-treated-only stage-1)
    Stata "bjs_ctrl" <-> Python station "bjs_dr" (BJS with controls, never-treated-only)
"""

import sys
import shutil
import subprocess
import logging
import time
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# CONFIGURE: path to your Stata executable (same as in run_analysis_stata_v1.py)
# ---------------------------------------------------------------------------
STATA_EXE = r"C:\Program Files\Stata18\StataMP-64.exe"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
STATION_PANEL_CSV = PROJECT_ROOT / 'data' / 'merge_panels' / 'generated' / 'station_city_panel.csv'
COV_CSV           = PROJECT_ROOT / 'data' / 'merge_panels' / 'generated' / 'city_covariates.csv'
DO_FILE           = PROJECT_ROOT / 'code' / 'run_station_analysis_stata' / 'run_one_spec_station.do'
TEMP_DIR          = PROJECT_ROOT / 'code' / 'run_station_analysis_stata' / 'temp'
STATA_TABLES      = PROJECT_ROOT / 'analysis' / 'tables' / 'stata'
STATA_FIGURES     = PROJECT_ROOT / 'analysis' / 'figures' / 'stata'
MASTER_OUT        = PROJECT_ROOT / 'analysis' / 'tables' / 'stata_station_aggregate_atts.csv'

# Fixed-name files the do-file reads/writes (Python manages content each iteration)
STATA_INPUT       = TEMP_DIR / 'stata_input.csv'
STATA_OUTPUT_STEM = TEMP_DIR / 'stata_output'
STATA_RUN_LOG     = TEMP_DIR / 'stata_run.log'

LOG_FILE = PROJECT_ROOT / 'last_run_stata_station.log'

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='ascii', errors='replace'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Post-processing: parse Stata raw CSVs into coeffs CSVs + figures
# ---------------------------------------------------------------------------

# (est_key, display_title, color, marker)
ESTIMATOR_META = [
    ('cs',       'CS (no controls)',      'navy',        'o'),
    ('cs_dr',    'CS DR (with controls)', 'steelblue',   'o'),
    ('bjs',      'BJS (no controls)',     'darkorange',  's'),
    ('bjs_ctrl', 'BJS with controls',     'mediumpurple','D'),
]

# Minimum x-axis window for each estimator (event time range).
# Enforces a consistent pre-period span even when pre-period data is absent
# (e.g. bjs_ctrl uses nose, so e(pre) is empty and x-axis would start at 0).
WINDOW = {
    'cs':       (-5, 10),
    'cs_dr':    (-5, 10),
    'bjs':      (-5, 10),
    'bjs_ctrl': (-5, 10),
}


def parse_stata_period(name):
    """Convert a Stata csdid/did_imputation column name to an integer event time.
    Handles:
      tau(=-22), T_m22, T_0, T_22   -- did_imputation with parens / underscore format
      tau0, tau1, tau-1              -- did_imputation without parens
      Tm22, Tp22                     -- csdid format
    """
    name = str(name).strip()
    name = name.replace('tau(=', '').replace('e(=', '').replace(')', '')
    name = name.replace('T_', '')
    if name.startswith('tau'):
        name = name[3:]
    if name.startswith('Tm') or name.startswith('Tp'):
        name = name[1:]
    # preN format: pretrend(K) writes pre1..preK where preN = event time -N
    if name.startswith('pre') and name[3:].isdigit():
        return -int(name[3:])
    if name.startswith('m'):
        name = '-' + name[1:]
    elif name.startswith('p'):
        name = name[1:]
    return int(float(name))


def process_raw_output(tables_stem, figures_stem, label, outcome_col):
    """Read _raw.csv files from Stata, write clean _coeffs.csv, plot figures.

    CSV format from Stata: period,b,se
      - CS estimators: includes ATT sentinel row (from estat simple)
      - BJS estimators: no ATT row; aggregate computed here as mean of non-zero-SE rows
    CIs are computed here as b +/- 1.96*se.
    Returns a list of aggregate dicts (one per estimator), or None if any file missing."""
    agg_rows = []
    any_missing = False
    # csdid estat event includes Pre_avg/Post_avg summary rows -- always skip them
    SKIP_PERIODS = {'ATT', 'Pre_avg', 'Post_avg'}

    for est, title, color, marker in ESTIMATOR_META:
        raw_path = Path(str(tables_stem) + f'_{est}_raw.csv')
        if not raw_path.exists():
            logger.warning(f'    {est}: raw CSV not found -- skipping')
            any_missing = True
            continue

        df = pd.read_csv(raw_path)
        df = df.rename(columns={'b': 'att'})   # Stata writes 'b'; call it 'att'

        # Separate ATT sentinel row and event-study rows
        agg_df = df[df['period'] == 'ATT']
        df_es  = df[~df['period'].isin(SKIP_PERIODS)].copy()
        # Keep only event-study periods (tau*, pre*, Tm*, Tp*); filter out control-var rows
        df_es = df_es[df_es['period'].astype(str).str.match(r'^(tau|pre|Tm|Tp)\d+$')].copy()
        # Drop omitted coefficients (b=0, se=0); keep nose estimates (b!=0, se=0)
        df_es = df_es[(df_es['se'] > 0) | ((df_es['att'] != 0) & (df_es['se'] == 0))].copy()

        # Parse event times, sort, and restrict to analysis window [-5, +10].
        # Must happen before normalization so we operate on the final row set.
        # 2026-07-31: station analysis restricted to 5 pre / 10 post.
        df_es['rel_time'] = df_es['period'].apply(parse_stata_period)
        df_es = df_es.sort_values('rel_time').reset_index(drop=True)
        df_es = df_es[(df_es['rel_time'] >= -5) & (df_es['rel_time'] <= 10)].copy()

        # For CS estimators, normalize so that t=-1 is the reference period (att=0).
        # By CS construction, ATT(g, g-1) = 0 for each cohort, so the aggregated
        # event-study estimate at t=-1 should be exactly zero.  Stata csdid returns
        # a non-zero value due to numerical aggregation across cohorts; subtract it
        # from all other estimates to impose the convention.
        # 2026-08-04: added to match normalization applied in run_analysis_stata_v1.py.
        if est in ('cs', 'cs_dr'):
            ref_mask = df_es['rel_time'] == -1
            if ref_mask.any():
                ref_val = float(df_es.loc[ref_mask, 'att'].iloc[0])
                if ref_val != 0.0:
                    other = ~ref_mask
                    df_es.loc[other, 'att'] -= ref_val
                    df_es.loc[ref_mask, ['att', 'se']] = 0.0

        # Compute CIs from the (normalized) estimates.
        df_es['ci_lower'] = df_es['att'] - 1.96 * df_es['se']
        df_es['ci_upper'] = df_es['att'] + 1.96 * df_es['se']

        # Aggregate ATT as mean of post-period (normalized) estimates, window t=0..10.
        # Consistent across all estimators; for CS, uses the normalized estimates so the
        # reported ATT is consistent with what is shown in the event-study figure.
        post_df = df_es[df_es['rel_time'] >= 0]
        if len(post_df) > 0:
            n       = len(post_df)
            att_val = float(post_df['att'].mean())
            se_val  = float(np.sqrt((post_df['se'] ** 2).sum()) / n)
        else:
            logger.warning(f'    {est}: no post-period rows for aggregate ATT')
            any_missing = True
            att_val = se_val = float('nan')

        agg_rows.append({
            'outcome_buffer': outcome_col,
            'estimator':      est,
            'att':            att_val,
            'se':             se_val,
        })

        # Write coefficients CSV
        coeffs = df_es[['rel_time', 'att', 'se', 'ci_lower', 'ci_upper']]
        coeffs.to_csv(Path(str(tables_stem) + f'_{est}_coeffs.csv'),
                      index=False, float_format='%.10f')

        # Event-study figure -- s1mono style: black markers, white background, open frame
        lo_win, hi_win = WINDOW[est]
        data_lo = int(df_es['rel_time'].min()) if len(df_es) > 0 else 0
        data_hi = int(df_es['rel_time'].max()) if len(df_es) > 0 else 0
        x_lo = min(data_lo, lo_win)
        x_hi = max(data_hi, hi_win)

        fig, ax = plt.subplots(figsize=(4.5, 3.0))
        fig.patch.set_facecolor('white')
        ax.set_facecolor('white')
        ax.errorbar(df_es['rel_time'], df_es['att'],
                    yerr=[df_es['att'] - df_es['ci_lower'],
                          df_es['ci_upper'] - df_es['att']],
                    fmt='o', color='black', markersize=4,
                    elinewidth=0.8, capsize=3, capthick=0.8)
        ax.axhline(0, color='0.55', linestyle='--', linewidth=0.8)
        # Light gray vertical line at t=0 (treatment date) -- 2026-07-31
        ax.axvline(0, color='lightgray', linewidth=0.8, zorder=0)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('black')
        ax.spines['bottom'].set_color('black')
        ax.grid(False)
        all_ticks = list(range(x_lo, x_hi + 1))
        tick_labels = [str(t) if t % 5 == 0 else '' for t in all_ticks]
        ax.set_xticks(all_ticks)
        ax.set_xticklabels(tick_labels)
        ax.set_xlim(x_lo - 0.6, x_hi + 0.6)
        ax.set_xlabel('years to treatment', fontsize=9)
        ax.set_ylabel('ATT', fontsize=9)
        ax.set_title(f'{title} -- {label}', fontsize=9, pad=4)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        fig.savefig(Path(str(figures_stem) + f'_{est}.png'), dpi=300,
                    bbox_inches='tight', facecolor='white')
        plt.close(fig)
        logger.info(f'    {est:12s}: ATT={att_val:+.4f}  SE={se_val:.4f}')

    return None if any_missing else agg_rows


# ---------------------------------------------------------------------------
# Station outcomes -- match OUTCOMES list in run_station_analysis_v5.py
# ---------------------------------------------------------------------------
STATION_OUTCOMES = [
    ('lst_summer_mean_nearest',   'Satellite LST (nearest pixel)'),
    ('ndvi_summer_mean_nearest',  'MODIS NDVI (nearest pixel)'),
    ('tree_cover_nearest',        'MODIS Tree Cover (nearest pixel)'),
    ('clcd_forest_pct_100m',      'CLCD Forest % (100m buffer)'),
    ('tmax_summer_mean',          'Station Tmax (JJA mean)'),
    ('hot_days',                  'Hot Days >= 35 deg C'),
    ('extreme_hot_days',          'Extreme Hot Days >= 38 deg C'),
    ('clcd_forest_pct_500m',      'CLCD Forest % (500m buffer)'),
]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_station_panel():
    if not STATION_PANEL_CSV.exists():
        logger.error(f'FATAL: station panel not found: {STATION_PANEL_CSV}')
        sys.exit(1)
    panel = pd.read_csv(STATION_PANEL_CSV, dtype={'station_id': str, 'city_id': object})
    panel['city_id'] = panel['city_id'].astype(str).str.strip()
    panel['year'] = panel['year'].astype(int)
    logger.info(f'Loaded station panel: {len(panel):,} rows, '
                f'{panel["station_id"].nunique()} stations, '
                f'{panel["city_id"].nunique()} cities')
    return panel


def load_covariates():
    if not COV_CSV.exists():
        logger.error(f'FATAL: covariates not found: {COV_CSV}')
        sys.exit(1)
    cov = pd.read_csv(COV_CSV, dtype={'city_id': str})
    cov['city_id'] = cov['city_id'].astype(str).str.strip()
    logger.info(f'Loaded covariates: {len(cov):,} cities')
    return cov


# ---------------------------------------------------------------------------
# Aggregate station panel to city level (mirrors aggregate_to_city_level
# in run_station_analysis_v5.py)
# ---------------------------------------------------------------------------

def aggregate_to_city_level(panel, outcome_col):
    """
    Average outcome across stations within each city-year.
    Treatment is assigned at city level, so this is the correct analysis unit.
    """
    city_panel = (
        panel[['city_id', 'year', 'treated', 'designation_year', outcome_col]]
        .dropna(subset=[outcome_col])
        .groupby(['city_id', 'year', 'treated', 'designation_year'], dropna=False)
        [outcome_col].mean()
        .reset_index()
    )
    return city_panel


# ---------------------------------------------------------------------------
# Build Stata input CSV
# ---------------------------------------------------------------------------

def build_stata_input(city_panel, cov, outcome_col):
    """
    Prepare city-level aggregated data for Stata.
    Returns DataFrame: unit_id, year, Y, cohort, lat, lon, log_pop_pretx
    """
    df = city_panel[['city_id', 'year', outcome_col, 'designation_year']].copy()
    df = df.rename(columns={outcome_col: 'y', 'designation_year': 'cohort'})

    # Merge covariates
    cov_cols = ['city_id'] + [c for c in ['lat', 'lon', 'log_pop_pretx'] if c in cov.columns]
    df = df.merge(cov[cov_cols], on='city_id', how='left')

    # Convert city_id to integer for Stata xtset
    df['unit_id'] = df['city_id'].astype(int)

    df['cohort'] = df['cohort'].where(df['cohort'].isna(),
                                       df['cohort'].round().astype('Int64'))

    out_cols = ['unit_id', 'year', 'y', 'cohort', 'lat', 'lon', 'log_pop_pretx']
    out_cols = [c for c in out_cols if c in df.columns]
    return df[out_cols]


# ---------------------------------------------------------------------------
# Call Stata
# ---------------------------------------------------------------------------

def to_stata_path(path):
    return str(Path(path).relative_to(PROJECT_ROOT)).replace('\\', '/')


def call_stata():
    """Call Stata in batch mode.  Polls stata_run.log for the completion marker.

    StataMP on Windows spawns a child process; the launcher exits in ~0 s while the
    child keeps running.  Two failure modes detected:
      1. Log never appears after launcher exits -- crash before log open.
      2. Log stops growing after launcher exits -- crash mid-run (stale log).
    """
    LOG_APPEAR_TIMEOUT = 180  # seconds after parent exit to wait for log to appear
    # 2026-07-23: raised from 180 to 900 -- csdid dripw bootstrap takes >180s silently.
    STALE_LOG_TIMEOUT  = 900  # seconds of no log growth after parent exit -> crash

    # 2026-07-23: Kill any lingering StataMP processes before spawning a new one.
    # StataMP spawns a child worker and its launcher exits immediately; Python only
    # holds a handle to the launcher, so proc.kill() never reaches the worker.
    # Across multiple pipeline runs / crashes, workers accumulate as orphans until
    # StataMP refuses to start (licence exhausted).  Killing upfront ensures a clean
    # slate.  taskkill returns non-zero when nothing is running -- that is expected.
    subprocess.run(
        ['taskkill', '/F', '/IM', 'StataMP-64.exe'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)  # let the OS fully release the process before spawning a new one

    if STATA_RUN_LOG.exists():
        STATA_RUN_LOG.unlink()

    cmd = [STATA_EXE, '-b', 'do', to_stata_path(DO_FILE)]
    logger.info(f"  Stata: {cmd[3]}")

    proc = subprocess.Popen(
        cmd, cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline           = time.time() + 7200
    parent_exit_time   = None
    last_log_size      = -1
    last_log_grow_time = None

    while time.time() < deadline:
        time.sleep(10)

        if STATA_RUN_LOG.exists():
            try:
                txt = STATA_RUN_LOG.read_text(encoding='latin-1', errors='replace')
                if 'run_one_spec_station.do  complete' in txt:
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        pass
                    return 0
                curr_size = STATA_RUN_LOG.stat().st_size
                if curr_size != last_log_size:
                    last_log_size = curr_size
                    last_log_grow_time = time.time()
            except OSError:
                pass

        if proc.poll() is not None and parent_exit_time is None:
            parent_exit_time = time.time()
            logger.info('  Stata launcher exited; continuing to poll for child process...')

        # Check for launcher exit with no log appearing
        if parent_exit_time is not None:
            elapsed = time.time() - parent_exit_time
            if elapsed > LOG_APPEAR_TIMEOUT and not STATA_RUN_LOG.exists():
                logger.error('FATAL: Stata exited without opening the log -- do-file aborted')
                return 1

        # Check for stale log: no growth for STALE_LOG_TIMEOUT seconds.
        # Checked unconditionally (not just after launcher exit) because on Windows,
        # a crashed Stata child can leave the launcher in a hung wait state indefinitely.
        if (last_log_grow_time is not None and
                time.time() - last_log_grow_time > STALE_LOG_TIMEOUT):
            proc.kill()
            logger.error(
                f'FATAL: Stata log stale for {STALE_LOG_TIMEOUT}s'
                ' -- Stata likely crashed or hung (check stata_run.log)'
            )
            return 1

    proc.kill()
    logger.error('FATAL: Stata timed out (7200s) -- completion marker not found in log')
    return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info('=' * 60)
    logger.info('run_station_analysis_stata_v1.py  started')
    logger.info(f'Date: {datetime.now():%Y-%m-%d %H:%M:%S}')
    logger.info('=' * 60)

    if not Path(STATA_EXE).exists():
        logger.error(f'FATAL: Stata not found at {STATA_EXE}')
        logger.error('Edit the STATA_EXE variable at the top of this file.')
        sys.exit(1)
    if not DO_FILE.exists():
        logger.error(f'FATAL: do-file not found: {DO_FILE}')
        sys.exit(1)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    STATA_TABLES.mkdir(parents=True, exist_ok=True)
    STATA_FIGURES.mkdir(parents=True, exist_ok=True)

    station_panel = load_station_panel()
    cov           = load_covariates()

    all_results = []
    n_ok = 0
    n_fail = 0

    for outcome_col, label in STATION_OUTCOMES:
        logger.info(f"\n{'='*50}")
        logger.info(f"Outcome: {outcome_col}  ({label})")

        if outcome_col not in station_panel.columns:
            logger.warning(f'  SKIP: {outcome_col} not in station panel')
            n_fail += 1
            continue

        # Aggregate to city level (treatment unit)
        city_df = aggregate_to_city_level(station_panel, outcome_col)
        n_cities = city_df['city_id'].nunique()
        n_obs    = len(city_df)
        if n_obs == 0:
            logger.warning(f'  SKIP: no data for {outcome_col} after aggregation')
            n_fail += 1
            continue
        logger.info(f'  {n_obs:,} city-year obs across {n_cities} cities')

        tables_stem  = STATA_TABLES  / f'station_{outcome_col}'
        figures_stem = STATA_FIGURES / f'station_{outcome_col}'

        # Copy input data to fixed path the do-file reads
        df_stata = build_stata_input(city_df, cov, outcome_col)
        df_stata.to_csv(STATA_INPUT, index=False, float_format='%.10f', na_rep='')
        logger.info(f'  Input CSV: {len(df_stata):,} rows -> {STATA_INPUT.name}')

        # Run Stata (no arguments -- do-file uses fixed paths)
        rc = call_stata()
        if rc != 0:
            logger.error(f'  FATAL: Stata exit {rc} for {outcome_col}')
            logger.error(f'  Check: {tables_stem}.log')
            n_fail += 1
            sys.exit(rc)
        logger.info('  Stata completed (exit 0)')

        # Copy fixed-name outputs to per-outcome destinations
        for est in ['cs', 'cs_dr', 'bjs', 'bjs_ctrl']:
            src = Path(str(STATA_OUTPUT_STEM) + f'_{est}_raw.csv')
            dst = Path(str(tables_stem) + f'_{est}_raw.csv')
            if src.exists():
                shutil.copy2(src, dst)
        if STATA_RUN_LOG.exists():
            shutil.copy2(STATA_RUN_LOG, Path(str(tables_stem) + '.log'))

        # Parse raw CSVs, write coeffs CSVs, plot figures, extract aggregate ATT/SE
        rows = process_raw_output(tables_stem, figures_stem, label, outcome_col)
        if rows is None:
            logger.error(f'  FATAL: some outputs missing for {outcome_col} -- check Stata log')
            sys.exit(1)
        for r in rows:
            logger.info(f"    {r['estimator']:12s}  ATT={r['att']:+.6f}  SE={r['se']:.6f}")
        all_results.extend(rows)
        n_ok += 1

    if not all_results:
        logger.error('FATAL: no results collected')
        sys.exit(1)

    master = pd.DataFrame(all_results)
    master.to_csv(MASTER_OUT, index=False, float_format='%.10f')
    logger.info(f'\nWrote {len(master)} rows to {MASTER_OUT.relative_to(PROJECT_ROOT)}')

    logger.info('\n' + '=' * 60)
    logger.info('run_station_analysis_stata_v1.py  complete')
    logger.info(f'  Succeeded: {n_ok}   Failed/skipped: {n_fail}')
    if n_fail > 0:
        logger.error(f'  {n_fail} outcomes had errors -- check logs above')
        sys.exit(1)


if __name__ == '__main__':
    main()
