"""
Run Analysis (Stata parallel) -- city level -- v1

Environment: environment_stats

Requires (inputs from earlier stages):
    - data/merge_panels/generated/city_year_panel.parquet  (from merge_panels)
      OR data/merge_panels/generated/city_year_panel.csv
    - data/merge_panels/generated/city_covariates.csv       (from build_covariates)
    - code/run_analysis_stata/run_one_spec.do
    - Stata (see STATA_EXE below; adjust path and edition to match your install)
    - Stata packages csdid and did_imputation (ssc install each)

Produces (outputs for later stages):
    - analysis/tables/stata_aggregate_atts.csv
      format: outcome_buffer, estimator, att, se
      estimators: cs, cs_dr, bjs, bjs_ctrl
    - analysis/tables/stata/city_{outcome}_{est}_coeffs.csv (per-outcome)
    - analysis/tables/stata/city_{outcome}.log              (per-outcome Stata log)
    - analysis/figures/stata/city_{outcome}_{est}.png       (per-outcome)
    - last_run_stata_city.log                               (project root)

Estimator mapping to compare_python_stata_v1.py:
    Stata "cs"       <-> Python "cs"
    Stata "cs_dr"    <-> Python "cs_dr"
    Stata "bjs"      <-> Python "bjs_allclean"
    Stata "bjs_ctrl" <-> Python "bjs_dr_allclean"
"""

import sys
import shutil
import subprocess
import logging
import time
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
# 2026-07-25: figures generated in Python (Stata graph commands hung in batch mode)
import matplotlib
matplotlib.use('Agg')   # non-interactive backend -- no display required
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# CONFIGURE: path to your Stata executable.
# Typical Windows paths:
#   StataMP:  C:\Program Files\Stata18\StataMP-64.exe
#   StataSE:  C:\Program Files\Stata18\StataSE-64.exe
#   Stata/IC: C:\Program Files\Stata18\Stata-64.exe
# Adjust version number (18) and edition (MP/SE/IC) to match your install.
# ---------------------------------------------------------------------------
STATA_EXE = r"C:\Program Files\Stata18\StataMP-64.exe"

# ---------------------------------------------------------------------------
# Paths (relative to project root)
# ---------------------------------------------------------------------------
PANEL_PARQ  = PROJECT_ROOT / 'data' / 'merge_panels' / 'generated' / 'city_year_panel.parquet'
PANEL_CSV   = PROJECT_ROOT / 'data' / 'merge_panels' / 'generated' / 'city_year_panel.csv'
COV_CSV     = PROJECT_ROOT / 'data' / 'merge_panels' / 'generated' / 'city_covariates.csv'
DO_FILE     = PROJECT_ROOT / 'code' / 'run_analysis_stata' / 'run_one_spec.do'
TEMP_DIR    = PROJECT_ROOT / 'code' / 'run_analysis_stata' / 'temp'
STATA_TABLES  = PROJECT_ROOT / 'analysis' / 'tables' / 'stata'
STATA_FIGURES = PROJECT_ROOT / 'analysis' / 'figures' / 'stata'
MASTER_OUT    = PROJECT_ROOT / 'analysis' / 'tables' / 'stata_aggregate_atts.csv'

# Fixed-name files the do-file reads/writes (Python manages content each iteration)
STATA_INPUT       = TEMP_DIR / 'stata_input.csv'
STATA_OUTPUT_STEM = TEMP_DIR / 'stata_output'
STATA_RUN_LOG     = TEMP_DIR / 'stata_run.log'

LOG_FILE = PROJECT_ROOT / 'last_run_stata_city.log'

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
# Outcomes: 15 city-level outcomes (nightlights excluded -- not in panel)
# ---------------------------------------------------------------------------
CITY_OUTCOMES = [
    ('lst_summer_mean_5km',    'Summer LST (C) 5km'),
    ('lst_summer_mean_10km',   'Summer LST (C) 10km'),
    ('lst_summer_mean_20km',   'Summer LST (C) 20km'),
    ('lst_summer_max_5km',     'Peak LST (C) 5km'),
    ('lst_summer_max_10km',    'Peak LST (C) 10km'),
    ('lst_summer_max_20km',    'Peak LST (C) 20km'),
    ('tree_cover_mean_5km',    'Tree Cover (%) 5km'),
    ('tree_cover_mean_10km',   'Tree Cover (%) 10km'),
    ('tree_cover_mean_20km',   'Tree Cover (%) 20km'),
    ('ndvi_mean_5km',          'NDVI 5km'),
    ('ndvi_mean_10km',         'NDVI 10km'),
    ('ndvi_mean_20km',         'NDVI 20km'),
    ('clcd_forest_pct_5km',    'CLCD Forest (%) 5km'),
    ('clcd_forest_pct_10km',   'CLCD Forest (%) 10km'),
    ('clcd_forest_pct_20km',   'CLCD Forest (%) 20km'),
]


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

# Figure size (inches) and x-axis window for each estimator.
# 2026-07-27: BJS windows changed from (0, 10) to (-5, 10) now that
# did_imputation is called with pretrend(5) to produce pre-treatment estimates.
FIGSIZE_PANEL = (4.5, 3.0)
WINDOW = {
    'cs':       (-5, 10),
    'cs_dr':    (-5, 10),
    'bjs':      (-5, 10),
    'bjs_ctrl': (-5, 10),
}


def _outcome_meta(outcome_col, est):
    """Return (y_label, title, x_label) for a city-level outcome.

    y_label  -- vertical axis label (unit or index name)
    title    -- short panel title describing the data source/variable
    x_label  -- horizontal axis label (post-only vs full pre/post range)
    """
    if 'ndvi' in outcome_col:
        y_label = 'NDVI'
        title   = 'MODIS NDVI'
    elif 'tree_cover' in outcome_col:
        y_label = '% Tree Cover'
        title   = 'MODIS Tree Cover'
    elif 'clcd' in outcome_col:
        y_label = '% Forest Cover'
        title   = 'CLCD/Landsat Forest Cover'
    elif 'lst_summer_max' in outcome_col:
        y_label, title = 'deg C', 'Peak Summer LST'
    elif 'lst' in outcome_col:
        y_label, title = 'deg C', 'Summer Mean LST'
    else:
        y_label, title = '', ''
    x_label = 'years to treatment'   # 2026-07-27: standardised for all estimators
    return y_label, title, x_label


def _plot_coeffs(df_es, save_path, outcome_col, est, xlim_lo=None, xlim_hi=None):
    """Save one event-study figure in s1mono style.

    2026-07-26: switched to whiskers (errorbar) for CIs; removed connecting
    line so only circle markers appear; embedded title, y-label, x-label.
    2026-07-28: added xlim_lo/xlim_hi so x-axis always spans the intended
    window even when pre-period data is absent (e.g. BJS ctrl with nose).

    df_es must have columns: rel_time, att, ci_lower, ci_upper.
    Style: white background, black axes, black whisker error bars, no grid.
    """
    y_label, title, x_label = _outcome_meta(outcome_col, est)

    fig, ax = plt.subplots(figsize=FIGSIZE_PANEL)
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    # 95% CI as vertical whiskers with caps; dots only (no connecting line)
    ax.errorbar(
        df_es['rel_time'], df_es['att'],
        yerr=[df_es['att'] - df_es['ci_lower'],
              df_es['ci_upper'] - df_es['att']],
        fmt='o',          # circle markers, no line between points
        color='black',
        markersize=4,
        elinewidth=0.8,   # whisker stem width
        capsize=3,        # horizontal cap width
        capthick=0.8,
    )

    # Dashed grey zero line
    ax.axhline(0, color='0.55', linestyle='--', linewidth=0.8)
    # Light gray vertical line at t=0 (treatment date) -- 2026-07-31
    ax.axvline(0, color='lightgray', linewidth=0.8, zorder=0)

    # Open frame: left + bottom only (no top or right border)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('black')
    ax.spines['bottom'].set_color('black')
    ax.grid(False)

    # Axis labels and title embedded in figure
    ax.set_title(title,   fontsize=10, pad=4)
    ax.set_ylabel(y_label, fontsize=9)
    ax.set_xlabel(x_label, fontsize=9)
    ax.tick_params(labelsize=8)

    # Tick mark at every year; label only at multiples of 5 (including 0).
    # x-axis spans at least the full intended window (xlim_lo..xlim_hi) even
    # when some pre-period data is absent -- e.g. BJS ctrl with nose has no
    # e(pre) rows, so without this the axis would start at t=0.
    data_lo = int(df_es['rel_time'].min())
    data_hi = int(df_es['rel_time'].max())
    lo = min(data_lo, xlim_lo) if xlim_lo is not None else data_lo
    hi = max(data_hi, xlim_hi) if xlim_hi is not None else data_hi
    all_ticks = list(range(lo, hi + 1))
    tick_labels = [str(t) if t % 5 == 0 else '' for t in all_ticks]
    ax.set_xticks(all_ticks)
    ax.set_xticklabels(tick_labels)
    ax.set_xlim(lo - 0.6, hi + 0.6)

    plt.tight_layout()
    plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def parse_stata_period(name):
    """Convert a Stata csdid/did_imputation column name to an integer event time.
    Handles:
      tau(=-22), T_m22, T_0, T_22   -- did_imputation with parens / underscore format
      tau0, tau1, tau-1              -- did_imputation without parens
      Tm22, Tp22                     -- csdid format
      pre1, pre2, pre5               -- did_imputation pretrend(K): preN means N periods
                                        before treatment, i.e. event time -N
    """
    name = str(name).strip()
    # 2026-07-28: did_imputation pretrend(K) stores e(pre) with column names pre1..preK
    # where preN = N years before treatment = event time -N.
    # Must check before the generic 'p' prefix strip (which would turn 'pre1' -> 're1').
    if name.startswith('pre') and name[3:].isdigit():
        return -int(name[3:])
    # tau(=N) format: tau(=-22) -> -22, tau(=0) -> 0
    name = name.replace('tau(=', '').replace('e(=', '').replace(')', '')
    # T_N format: T_m22 -> m22, T_0 -> 0, T_22 -> 22
    name = name.replace('T_', '')
    # tau without parens: tau0 -> 0, tau-1 -> -1
    if name.startswith('tau'):
        name = name[3:]
    # csdid: Tm22 -> m22, Tp22 -> p22
    if name.startswith('Tm') or name.startswith('Tp'):
        name = name[1:]
    # negative prefix: m22 -> -22
    if name.startswith('m'):
        name = '-' + name[1:]
    # positive prefix (csdid): p22 -> 22
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

        # Keep only parseable event-study periods; try parsing each period and
        # discard any that fail (covariate rows, summary labels, etc.).
        # 2026-07-27: replaced the old regex filter r'^(tau|Tm|Tp)\d+$' which
        # only matched post-period tau columns (e.g. tau0, tau1) and dropped
        # pre-period BJS columns produced by pretrend() (e.g. tau-5, tau(=-5)).
        def _try_parse(name):
            try:
                return parse_stata_period(str(name))
            except Exception:
                return None

        df_es['rel_time'] = df_es['period'].apply(_try_parse)
        df_es = df_es[df_es['rel_time'].notna()].copy()
        df_es['rel_time'] = df_es['rel_time'].astype(int)

        # Drop omitted coefficients (b=0, se=0); keep nose estimates (b!=0, se=0)
        df_es = df_es[(df_es['se'] > 0) | ((df_es['att'] != 0) & (df_es['se'] == 0))].copy()
        df_es = df_es.sort_values('rel_time').reset_index(drop=True)

        # For CS estimators, normalize to t=-1 as the reference period.
        # Stata csdid does not enforce t=-1 = 0 by construction; subtract its
        # value from all other periods and zero it out for the plot.
        # 2026-07-27: added to fix non-zero t=-1 in Stata CS/DR figures.
        if est in ('cs', 'cs_dr'):
            ref_mask = df_es['rel_time'] == -1
            if ref_mask.any():
                ref_val = float(df_es.loc[ref_mask, 'att'].iloc[0])
                if ref_val != 0.0:
                    other = ~ref_mask
                    df_es.loc[other, 'att'] -= ref_val
                    df_es.loc[ref_mask, ['att', 'se']] = 0.0

        # Compute CIs
        df_es['ci_lower'] = df_es['att'] - 1.96 * df_es['se']
        df_es['ci_upper'] = df_es['att'] + 1.96 * df_es['se']

        # Aggregate ATT: from sentinel row (CS) or mean of post-period horizons (BJS)
        if not agg_df.empty:
            att_val = float(agg_df['att'].iloc[0])
            se_val  = float(agg_df['se'].iloc[0])
        elif len(df_es) > 0:
            post_es = df_es[df_es['rel_time'] >= 0]
            if not post_es.empty:
                n       = len(post_es)
                att_val = float(post_es['att'].mean())
                se_val  = float(np.sqrt((post_es['se'] ** 2).sum()) / n)
            else:
                att_val = se_val = float('nan')
        else:
            logger.warning(f'    {est}: no rows for aggregate ATT')
            any_missing = True
            att_val = se_val = float('nan')

        agg_rows.append({
            'outcome_buffer': outcome_col,
            'estimator':      est,
            'att':            att_val,
            'se':             se_val,
        })

        # Write coefficients CSV (rel_time already parsed and sorted above)
        coeffs = df_es[['rel_time', 'att', 'se', 'ci_lower', 'ci_upper']]
        coeffs.to_csv(Path(str(tables_stem) + f'_{est}_coeffs.csv'),
                      index=False, float_format='%.10f')

        # Generate event-study figure from coefficients CSV.
        # 2026-07-25: Stata graph commands (csdid_plot, event_plot) hung in batch
        # mode, so all figures are now generated here in Python using the same
        # s1mono-compatible monochrome style as run_analysis_v6.py.
        # 2026-07-28: pass window bounds so x-axis always spans lo..hi even
        # when pre-period data is absent (e.g. BJS ctrl with nose).
        lo, hi = WINDOW.get(est, (-5, 10))
        df_plot = df_es[(df_es['rel_time'] >= lo) & (df_es['rel_time'] <= hi)].copy()
        dest_fig = Path(str(figures_stem) + f'_{est}.png')
        if not df_plot.empty:
            _plot_coeffs(df_plot, dest_fig, outcome_col, est, xlim_lo=lo, xlim_hi=hi)
        else:
            logger.warning(f'    {est}: no coefficients in window ({lo},{hi}) -- figure skipped')
        logger.info(f'    {est:12s}: ATT={att_val:+.4f}  SE={se_val:.4f}')

    return None if any_missing else agg_rows


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_panel():
    if PANEL_PARQ.exists():
        panel = pd.read_parquet(PANEL_PARQ)
        logger.info(f"Loaded panel from parquet: {len(panel):,} rows")
    elif PANEL_CSV.exists():
        panel = pd.read_csv(PANEL_CSV, dtype={'city_id': str})
        logger.info(f"Loaded panel from CSV: {len(panel):,} rows")
    else:
        logger.error(f"FATAL: panel not found at {PANEL_PARQ} or {PANEL_CSV}")
        sys.exit(1)
    # Year 1999 is synthetic test data inserted during pipeline development --
    # not real observations.  Drop it here so it never enters any estimator.
    panel = panel[panel['year'] != 1999]
    logger.info(f"Dropped year 1999 (synthetic); {len(panel):,} rows remain")
    # Normalise city_id to stripped string for consistent merge
    panel['city_id'] = panel['city_id'].astype(str).str.strip()
    panel['year'] = panel['year'].astype(int)
    return panel


def load_covariates():
    if not COV_CSV.exists():
        logger.error(f"FATAL: covariates not found at {COV_CSV}")
        sys.exit(1)
    cov = pd.read_csv(COV_CSV, dtype={'city_id': str})
    cov['city_id'] = cov['city_id'].astype(str).str.strip()
    logger.info(f"Loaded covariates: {len(cov):,} cities")
    return cov


# ---------------------------------------------------------------------------
# Build Stata input CSV
# ---------------------------------------------------------------------------

def build_stata_input(panel, cov, outcome_col):
    """
    Select and reshape one outcome for Stata.
    Returns a DataFrame with: unit_id, year, y, cohort, lat, lon, log_pop_pretx
    unit_id is an integer (city_id converted to int).
    cohort is designation_year (int) for treated, empty for never-treated.
    """
    df = panel[['city_id', 'year', outcome_col, 'designation_year']].copy()
    df = df.rename(columns={outcome_col: 'y', 'designation_year': 'cohort'})
    df = df.dropna(subset=['y'])

    # Merge covariates (lat, lon, log_pop_pretx)
    cov_cols = ['city_id'] + [c for c in ['lat', 'lon', 'log_pop_pretx'] if c in cov.columns]
    df = df.merge(cov[cov_cols], on='city_id', how='left')

    # Convert city_id to integer for Stata xtset (6-digit prefecture codes)
    df['unit_id'] = df['city_id'].astype(int)

    # Round cohort to int (stays as float NaN for never-treated -- CSV writes as empty)
    df['cohort'] = df['cohort'].where(df['cohort'].isna(),
                                       df['cohort'].round().astype('Int64'))

    out_cols = ['unit_id', 'year', 'y', 'cohort', 'lat', 'lon', 'log_pop_pretx']
    out_cols = [c for c in out_cols if c in df.columns]
    return df[out_cols]


# ---------------------------------------------------------------------------
# Call Stata
# ---------------------------------------------------------------------------

def to_stata_path(path):
    """Convert absolute path to forward-slash path relative to project root."""
    return str(Path(path).relative_to(PROJECT_ROOT)).replace('\\', '/')


def call_stata():
    """Call Stata in batch mode.  Polls stata_run.log for the completion marker.

    StataMP on Windows spawns a child process; the launcher exits in ~0 s while the
    child keeps running.  This function handles two failure modes:
      1. Log never appears after launcher exits -- genuine crash before log open.
      2. Log exists but stops growing -- Stata crashed mid-run (stale log).
    """
    LOG_APPEAR_TIMEOUT = 180  # seconds after parent exit to wait for log to appear
    # 2026-07-23: raised from 180 to 900 -- csdid dripw bootstrap on 7150 obs takes
    # >180s of silent computation, triggering false-positive stale detection.
    # 2026-07-27: raised from 900 to 3600 -- csdid variance computation after dot
    # output phase can take >15 min silently, causing false stale detection.
    STALE_LOG_TIMEOUT  = 3600  # seconds of no log growth -> crash

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

    deadline          = time.time() + 7200
    parent_exit_time  = None
    last_log_size     = -1
    last_log_grow_time = None

    while time.time() < deadline:
        time.sleep(10)

        if STATA_RUN_LOG.exists():
            try:
                txt = STATA_RUN_LOG.read_text(encoding='latin-1', errors='replace')
                if 'run_one_spec.do  complete' in txt:
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
                pass  # log may be locked while Stata is writing

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
    logger.info('run_analysis_stata_v1.py  started')
    logger.info(f'Date: {datetime.now():%Y-%m-%d %H:%M:%S}')
    logger.info('=' * 60)

    # Verify Stata and do-file exist before any heavy work
    if not Path(STATA_EXE).exists():
        logger.error(f'FATAL: Stata not found at {STATA_EXE}')
        logger.error('Edit the STATA_EXE variable at the top of this file.')
        sys.exit(1)
    if not DO_FILE.exists():
        logger.error(f'FATAL: do-file not found: {DO_FILE}')
        sys.exit(1)

    # Create output directories
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    STATA_TABLES.mkdir(parents=True, exist_ok=True)
    STATA_FIGURES.mkdir(parents=True, exist_ok=True)

    panel = load_panel()
    cov   = load_covariates()

    all_results = []
    n_ok = 0
    n_fail = 0

    for outcome_col, label in CITY_OUTCOMES:
        logger.info(f"\n{'='*50}")
        logger.info(f"Outcome: {outcome_col}  ({label})")

        if outcome_col not in panel.columns:
            logger.warning(f"  SKIP: {outcome_col} not in panel")
            n_fail += 1
            continue
        n_obs = int(panel[outcome_col].notna().sum())
        if n_obs == 0:
            logger.warning(f"  SKIP: no data for {outcome_col}")
            n_fail += 1
            continue
        logger.info(f"  {n_obs:,} non-missing obs")

        tables_stem  = STATA_TABLES  / f'city_{outcome_col}'
        figures_stem = STATA_FIGURES / f'city_{outcome_col}'

        # Copy input data to fixed path the do-file reads
        df_stata = build_stata_input(panel, cov, outcome_col)
        df_stata.to_csv(STATA_INPUT, index=False, float_format='%.10f', na_rep='')
        logger.info(f"  Input CSV: {len(df_stata):,} rows -> {STATA_INPUT.name}")

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
    logger.info('run_analysis_stata_v1.py  complete')
    logger.info(f'  Succeeded: {n_ok}   Failed/skipped: {n_fail}')
    if n_fail > 0:
        logger.error(f'  {n_fail} outcomes had errors -- check logs above')
        sys.exit(1)


if __name__ == '__main__':
    main()
