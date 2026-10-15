"""
Run Station-Level Event Study Analysis -- v5

Updated from run_station_analysis_v4 to run_station_analysis_v5 on 2026-07-18.
Main changes in this version:
- Reverted BJS and BJS with controls from diff_diff.ImputationDiD (library) back to
  custom bjs.py (bjs_event_study with never_treated_only=True).
  Library produced impossible estimates and failed synthetic validation.
  Custom bjs.py recovers true ATT correctly in both no-controls and with-controls
  synthetic tests (errors < 0.05 on 600-obs panel with true ATT = -1.0).
- never_treated_only=True: stage-1 TWFE fit on never-treated only; treated city
  FEs estimated from pre-treatment residuals (2026-07-18 fix to bjs.py).
- Fixed figure label and title: "BJS-DR" -> "BJS with controls".
- CS DR and CS (no controls) remain via csdid (fit_cs) -- unaffected.
Previous version saved in _old/ as run_station_analysis_v4.

Environment: environment_stats

Requires:
    - data/merge_panels/generated/station_city_panel.csv
    - data/build_covariates/generated/city_covariates.csv

Produces:
    - analysis/figures/station_event_study_{cs,dr,ols,bjs,bjs_dr}_*.png
    - analysis/tables/station_event_study_{cs,dr,ols,bjs,bjs_dr}_*.csv
    - analysis/figures/station_gaming_test.png  (CS DR overlay, Tmax vs LST)

Note: CS DR (with controls) fails for satellite outcomes at station level because
the never-treated pool (~47 cities) is too small for the per-(g,t) DR propensity
model. BJS with controls is similarly unreliable at this sample size. Both are
noted in the paper. CS (no controls) and BJS (no controls) work for all 8 outcomes.
The paper's station figure shows BJS (top row) and CS (bottom row) only.

Usage:
    conda activate C:\\py_envs\\environment_stats
    python code\\run_station_analysis\\run_station_analysis_v5.py
"""

import sys
import logging
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Suppress Python library warnings (numpy RuntimeWarning, scipy ConvergenceWarning,
# etc.) that appear on stderr during csdid DR propensity-score estimation on small
# samples.  These are expected for our station sample size and do not affect results;
# all failures are handled by the fallback logic in did_lib.py.
# 2026-10-13: added to keep console output clean.
warnings.filterwarnings('ignore')

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT / 'code'))

from utils.config import (
    STATION_CITY_PANEL_FILE,
    CITY_COVARIATES_FILE,
    FIGURES_DIR,
    TABLES_DIR,
)
from utils.event_study import run_event_study_regression
from utils.bjs import bjs_event_study
from utils.did_lib import fit_cs

LOG_FILE = PROJECT_ROOT / 'last_run_station_analysis.log'

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Figure style
# ---------------------------------------------------------------------------
plt.rcParams.update({
    'font.size':         16,
    'axes.labelsize':    20,
    'axes.titlesize':    18,
    'xtick.labelsize':   15,
    'ytick.labelsize':   15,
    'legend.fontsize':   14,
    'axes.linewidth':    1.2,
    'lines.linewidth':   2.0,
    'lines.markersize':  8,
})
FIGSIZE_PANEL  = (5, 4)
FIGSIZE_GAMING = (10, 4.5)

STYLE = {
    'ols':    dict(color='forestgreen',  marker='^', label='OLS TWFE estimate'),
    'cs':     dict(color='royalblue',    marker='v', label='CS estimate'),
    'dr':     dict(color='steelblue',    marker='o', label='CS DR estimate'),
    'bjs':    dict(color='darkorange',   marker='s', label='BJS estimate'),
    'bjs_dr': dict(color='mediumpurple', marker='D', label='BJS with controls estimate'),
}

MIN_TIME = -5
MAX_TIME = 10
REF_TIME = -1

OUTCOMES = [
    # Satellite RS outcomes (grid position matches city-level figures)
    # y_label updated 2026-07-26: standardised with city-level figures
    # 2026-10-13: parentheticals removed from outcome_label to match city figure style
    ('lst_summer_mean_nearest',  'Satellite LST',         'LST (deg C)'),
    ('ndvi_summer_mean_nearest', 'MODIS NDVI',            'NDVI'),
    ('tree_cover_nearest',       'MODIS Tree Cover',      '% Tree Cover'),
    ('clcd_forest_pct_100m',     'CLCD Forest Cover',     '% Forest Cover'),
    # Temperature outcomes
    ('tmax_summer_mean',         'Station Tmax',          'Tmax (deg C)'),
    ('hot_days',                 'Hot Days >= 35 deg C',  'Days per summer'),
    ('extreme_hot_days',         'Extreme Hot Days >= 38 deg C', 'Days per summer'),
    ('clcd_forest_pct_500m',     'CLCD Forest Cover',    '% Forest Cover'),
]


# ---------------------------------------------------------------------------
# Data loading and preparation
# ---------------------------------------------------------------------------

def load_panel():
    panel = pd.read_csv(STATION_CITY_PANEL_FILE,
                        dtype={'station_id': str, 'city_id': object})
    logger.info(f"Loaded station panel: {len(panel):,} rows, "
                f"{panel['station_id'].nunique():,} stations, "
                f"{panel['city_id'].nunique():,} cities")
    return panel


def load_covariates():
    if not CITY_COVARIATES_FILE.exists():
        logger.warning(f"City covariates not found: {CITY_COVARIATES_FILE}")
        logger.warning("BJS with controls and CS DR will use lat+lon only.")
        return None
    cov = pd.read_csv(CITY_COVARIATES_FILE, dtype={'city_id': object})
    logger.info(f"Loaded city covariates: {len(cov):,} cities")
    return cov


def aggregate_to_city_level(panel, outcome):
    """
    Average station outcomes to one observation per city x year.
    Treatment is at the city level, so we collapse before estimating.
    """
    city_panel = (
        panel[['city_id', 'year', 'treated', 'designation_year', outcome]]
        .dropna(subset=[outcome])
        .groupby(['city_id', 'year', 'treated', 'designation_year'], dropna=False)
        [outcome].mean().reset_index()
    )
    city_panel['rel_time'] = city_panel['year'] - city_panel['designation_year']
    return city_panel


def merge_covariates(city_panel, cov_df):
    """Merge lat/lon/log_pop_pretx into the city panel for DR and BJS with controls."""
    if cov_df is None:
        return city_panel, ['lat', 'lon'] if 'lat' in city_panel.columns else []

    merge_cols = ['city_id'] + [c for c in ['lat', 'lon', 'log_pop_pretx']
                                 if c in cov_df.columns]
    city_panel = city_panel.merge(cov_df[merge_cols], on='city_id', how='left')

    covariate_cols = [col for col in ['lat', 'lon', 'log_pop_pretx']
                      if col in city_panel.columns
                      and city_panel[col].notna().mean() > 0.5]
    if not covariate_cols:
        logger.warning("No usable covariates after merge -- "
                       "DR will use intercept only.")
    return city_panel, covariate_cols


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def plot_station_event_study(coeffs_df, y_label, title, save_path, estimator_key):
    """Plot one event-study panel (5x4 in, 300 dpi) in s1mono style.

    2026-07-27: converted to s1mono (black markers, white background, no grid,
    no coloured background spans, no red vertical line) to match city figures.
    Also fixed x-axis to tick every year with labels at multiples of 5.
    """
    fig, ax = plt.subplots(figsize=FIGSIZE_PANEL)
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    # 95% CI as vertical whiskers; circle markers only, no connecting line.
    # s1mono: all black (no per-estimator colour).
    ax.errorbar(
        coeffs_df['rel_time'], coeffs_df['coef'],
        yerr=[coeffs_df['coef'] - coeffs_df['ci_lower'],
              coeffs_df['ci_upper'] - coeffs_df['coef']],
        fmt='o',
        color='black',
        ecolor='black',
        markersize=4,
        elinewidth=0.8,
        capsize=3,
        capthick=0.8,
    )

    # Grey dashed zero line only (no red vertical line, no coloured spans)
    ax.axhline(0, color='0.55', linestyle='--', linewidth=0.8)

    # Open frame: left + bottom only (no top or right border)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('black')
    ax.spines['bottom'].set_color('black')
    ax.grid(False)

    # Explicit fontsizes override rcParams so labels fit in the 5x4in panel.
    ax.set_ylabel(y_label, fontsize=10)
    ax.set_xlabel('years to treatment', fontsize=10)   # 2026-07-27
    ax.set_title(title, fontsize=11, fontweight='normal')
    ax.tick_params(labelsize=9)

    # Tick mark at every year; label only at multiples of 5 (including 0).
    lo = int(coeffs_df['rel_time'].min())
    hi = int(coeffs_df['rel_time'].max())
    all_ticks = list(range(lo, hi + 1))
    tick_labels = [str(t) if t % 5 == 0 else '' for t in all_ticks]
    ax.set_xticks(all_ticks)
    ax.set_xticklabels(tick_labels)
    ax.set_xlim(lo - 0.6, hi + 0.6)

    plt.tight_layout()
    plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()
    logger.info(f"  Saved: {save_path}")


def plot_gaming_test(tmax_coeffs, lst_coeffs, save_path):
    """
    Overlay CS DR estimates for station Tmax and satellite LST.
    Gaming signal = Tmax showing more cooling than satellite LST.
    """
    fig, axes = plt.subplots(1, 2, figsize=FIGSIZE_GAMING, sharey=False)

    pairs = [
        (axes[0], tmax_coeffs, 'steelblue',
         'Station Tmax (deg C)', 'Station Air Temperature\n(Tmax summer mean, CS DR)'),
        (axes[1], lst_coeffs,  'darkorange',
         'Satellite LST (deg C)', 'Satellite LST at Station\n(nearest pixel, CS DR)'),
    ]
    for ax, coeffs, color, ylabel, title in pairs:
        if coeffs is None or coeffs.empty:
            ax.text(0.5, 0.5, 'Insufficient data', ha='center', va='center',
                    transform=ax.transAxes)
            ax.set_title(title, fontweight='bold')
            continue
        ax.plot(coeffs['rel_time'], coeffs['coef'],
                marker='o', color=color, linewidth=2, label='CS DR estimate')
        ax.fill_between(coeffs['rel_time'],
                        coeffs['ci_lower'], coeffs['ci_upper'],
                        alpha=0.20, color=color, label='95% CI')
        ax.axhline(0, color='gray', linestyle='--', linewidth=1, alpha=0.7)
        ax.axvline(0, color='red', linestyle='--', linewidth=1.5, alpha=0.7,
                   label='NFC designation')
        ax.axvspan(coeffs['rel_time'].min(), -0.5, alpha=0.05, color='blue')
        ax.axvspan(0.5, coeffs['rel_time'].max(),  alpha=0.05, color='green')
        # x-axis label and legend omitted; content moved to figure captions in paper
        ax.set_ylabel(ylabel)
        ax.set_title(title, fontweight='bold')
        ax.grid(True, alpha=0.3, linestyle=':')

    fig.suptitle(
        'Gaming Test: Station Air Temperature vs Satellite LST (CS DR)\n'
        'Gaming signal = station Tmax shows more cooling than satellite LST',
        fontweight='bold', y=1.01
    )
    plt.tight_layout()
    plt.savefig(save_path, dpi=300, bbox_inches='tight')
    plt.close()
    logger.info(f"  Saved gaming test: {save_path}")


# ---------------------------------------------------------------------------
# Per-outcome helpers
# ---------------------------------------------------------------------------

def log_coeffs(coeffs_df, label):
    for _, row in coeffs_df.iterrows():
        pval = row.get('pvalue', float('nan'))
        sig = '*' if not np.isnan(pval) and pval < 0.1 else ''
        pval_str = f"{pval:.3f}" if not np.isnan(pval) else "  ---"
        logger.info(f"    [{label}] t={int(row['rel_time']):+d}  "
                    f"coef={row['coef']:+.4f}  se={row['se']:.4f}  "
                    f"p={pval_str}{sig}")


def run_dr_for_outcome(panel, outcome, outcome_label, y_label, cov_df, gaming_coeffs):
    """Run CS DR event study for one outcome; save plot + CSV."""
    logger.info("  Running CS DR (csdid.ATTgt)...")
    city_panel = aggregate_to_city_level(panel, outcome)
    city_panel, covariate_cols = merge_covariates(city_panel, cov_df)
    if not covariate_cols:
        city_panel['_const'] = 1.0
        covariate_cols = ['_const']
    logger.info(f"  {len(city_panel):,} city-year obs | "
                f"{city_panel['city_id'].nunique()} cities | covs: {covariate_cols}")

    coeffs, _, _ = fit_cs(
        city_panel, outcome=outcome, covariates=covariate_cols,
        min_time=MIN_TIME, max_time=MAX_TIME,
    )
    if coeffs is None or coeffs.empty:
        logger.warning(f"  CS DR returned no estimates for {outcome}")
        return None

    log_coeffs(coeffs, 'DR')
    slug = outcome.replace('_', '-')
    coeffs.to_csv(TABLES_DIR / f'station_event_study_dr_{slug}.csv', index=False)
    plot_station_event_study(
        coeffs, y_label=y_label,
        title=f'CS DR: {outcome_label}',
        save_path=FIGURES_DIR / f'station_event_study_dr_{slug}.png',
        estimator_key='dr',
    )
    if outcome in ('tmax_summer_mean', 'lst_summer_mean_nearest'):
        gaming_coeffs[outcome] = coeffs
    return coeffs


def run_ols_for_outcome(panel, outcome, outcome_label, y_label):
    """Run OLS TWFE event study for one outcome; save plot + CSV."""
    logger.info("  Running OLS TWFE...")
    city_panel = aggregate_to_city_level(panel, outcome)
    try:
        model, coeffs = run_event_study_regression(
            city_panel, outcome=outcome,
            min_time=MIN_TIME, max_time=MAX_TIME, ref_time=REF_TIME,
            cluster_var='city_id',
        )
    except Exception as e:
        logger.error(f"  OLS TWFE failed: {e}")
        return None
    if coeffs is None or coeffs.empty:
        return None

    log_coeffs(coeffs, 'OLS')
    slug = outcome.replace('_', '-')
    coeffs.to_csv(TABLES_DIR / f'station_event_study_ols_{slug}.csv', index=False)
    plot_station_event_study(
        coeffs, y_label=y_label,
        title=f'OLS TWFE: {outcome_label}',
        save_path=FIGURES_DIR / f'station_event_study_ols_{slug}.png',
        estimator_key='ols',
    )
    return coeffs


def run_bjs_for_outcome(panel, outcome, outcome_label, y_label):
    """Run BJS imputation event study (no covariates) using custom bjs.py."""
    # 2026-07-22: changed never_treated_only from True to False to match
    # Stata did_imputation (which uses all clean obs: never-treated + not-yet-treated)
    logger.info("  Running BJS (custom bjs.py, never_treated_only=False)...")
    city_panel = aggregate_to_city_level(panel, outcome)

    coeffs = bjs_event_study(
        city_panel,
        outcome=outcome,
        unit_var='city_id',
        time_var='year',
        treatment_var='treated',
        rel_time_var='rel_time',
        desig_year_var='designation_year',
        min_time=MIN_TIME,
        max_time=MAX_TIME,
        ref_time=REF_TIME,
        cluster_var='city_id',
        covariates=None,
        never_treated_only=False,
    )
    if coeffs is None or coeffs.empty:
        logger.warning(f"  BJS returned no estimates for {outcome}")
        return None

    log_coeffs(coeffs, 'BJS')
    slug = outcome.replace('_', '-')
    # 2026-07-22: save intercept as a column so compare_python_stata_v1.py can
    # correct for Stage-3 normalization (coef_k = tau_k - tau_{-1}).
    # 2026-07-23: also save direct_att (Stage-2 period-weighted mean) which
    # avoids the reference-period bias that can flip the ATT sign.
    coeffs_save = coeffs.copy()
    coeffs_save['stage3_intercept'] = coeffs.attrs.get('stage3_intercept', 0.0)
    coeffs_save['direct_att'] = coeffs.attrs.get('direct_att', float('nan'))
    coeffs_save.to_csv(TABLES_DIR / f'station_event_study_bjs_{slug}.csv', index=False)
    plot_station_event_study(
        coeffs, y_label=y_label,
        title=f'BJS: {outcome_label}',
        save_path=FIGURES_DIR / f'station_event_study_bjs_{slug}.png',
        estimator_key='bjs',
    )
    return coeffs


def run_cs_for_outcome(panel, outcome, outcome_label, y_label):
    """Run plain CS event study (no covariates) for one outcome; save plot + CSV."""
    logger.info("  Running CS (csdid.ATTgt, no covariates)...")
    city_panel = aggregate_to_city_level(panel, outcome)

    coeffs, _, _ = fit_cs(
        city_panel, outcome=outcome, covariates=None,
        min_time=MIN_TIME, max_time=MAX_TIME,
    )
    if coeffs is None or coeffs.empty:
        logger.warning(f"  CS returned no estimates for {outcome}")
        return None

    log_coeffs(coeffs, 'CS')
    slug = outcome.replace('_', '-')
    coeffs.to_csv(TABLES_DIR / f'station_event_study_cs_{slug}.csv', index=False)
    plot_station_event_study(
        coeffs, y_label=y_label,
        title=f'CS: {outcome_label}',
        save_path=FIGURES_DIR / f'station_event_study_cs_{slug}.png',
        estimator_key='cs',
    )
    return coeffs


def run_bjs_dr_for_outcome(panel, outcome, outcome_label, y_label, cov_df):
    """Run BJS with controls (lat/lon/log_pop_pretx) using custom bjs.py."""
    # 2026-07-22: changed never_treated_only from True to False to match Stata autosample
    logger.info("  Running BJS with controls (custom bjs.py, never_treated_only=False)...")
    city_panel = aggregate_to_city_level(panel, outcome)
    city_panel, covariate_cols = merge_covariates(city_panel, cov_df)
    if not covariate_cols:
        logger.warning("  No covariates available -- skipping BJS with controls")
        return None
    logger.info(f"  {len(city_panel):,} city-year obs | "
                f"{city_panel['city_id'].nunique()} cities | covs: {covariate_cols}")

    coeffs = bjs_event_study(
        city_panel,
        outcome=outcome,
        unit_var='city_id',
        time_var='year',
        treatment_var='treated',
        rel_time_var='rel_time',
        desig_year_var='designation_year',
        min_time=MIN_TIME,
        max_time=MAX_TIME,
        ref_time=REF_TIME,
        cluster_var='city_id',
        covariates=covariate_cols,
        never_treated_only=False,
    )
    if coeffs is None or coeffs.empty:
        logger.warning(f"  BJS with controls returned no estimates for {outcome}")
        return None

    log_coeffs(coeffs, 'BJS with controls')
    slug = outcome.replace('_', '-')
    # 2026-07-22: save intercept for comparison script.
    # 2026-07-23: also save direct_att (same fix as plain BJS above).
    coeffs_save = coeffs.copy()
    coeffs_save['stage3_intercept'] = coeffs.attrs.get('stage3_intercept', 0.0)
    coeffs_save['direct_att'] = coeffs.attrs.get('direct_att', float('nan'))
    coeffs_save.to_csv(TABLES_DIR / f'station_event_study_bjs_dr_{slug}.csv', index=False)
    plot_station_event_study(
        coeffs, y_label=y_label,
        title=outcome_label,
        save_path=FIGURES_DIR / f'station_event_study_bjs_dr_{slug}.png',
        estimator_key='bjs_dr',
    )
    return coeffs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 70)
    logger.info("Station-Level Event Study Analysis -- v5  (custom bjs.py)")
    logger.info("Estimators: CS + CS DR (csdid) + OLS TWFE + BJS + BJS with controls")
    logger.info(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)

    if not STATION_CITY_PANEL_FILE.exists():
        logger.error(f"FATAL: Station panel not found: {STATION_CITY_PANEL_FILE}")
        logger.error("Run merge_station_panels_v1.py first.")
        sys.exit(1)

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    TABLES_DIR.mkdir(parents=True, exist_ok=True)

    panel  = load_panel()
    cov_df = load_covariates()

    cs_count = dr_count = ols_count = bjs_count = bjs_dr_count = 0
    gaming_coeffs = {}

    for outcome, outcome_label, y_label in OUTCOMES:
        if outcome not in panel.columns:
            logger.info(f"Skipping {outcome} (not in panel).")
            continue
        n_valid = panel[outcome].notna().sum()
        if n_valid < 100:
            logger.info(f"Skipping {outcome}: only {n_valid} non-null obs.")
            continue

        logger.info(f"\n{'='*60}")
        logger.info(f"Outcome: {outcome}  ({n_valid:,} obs)")

        logger.info("")
        coeffs_cs = run_cs_for_outcome(panel, outcome, outcome_label, y_label)
        if coeffs_cs is not None:
            cs_count += 1

        logger.info("")
        coeffs_dr = run_dr_for_outcome(
            panel, outcome, outcome_label, y_label, cov_df, gaming_coeffs
        )
        if coeffs_dr is not None:
            dr_count += 1

        logger.info("")
        coeffs_ols = run_ols_for_outcome(panel, outcome, outcome_label, y_label)
        if coeffs_ols is not None:
            ols_count += 1

        logger.info("")
        coeffs_bjs = run_bjs_for_outcome(panel, outcome, outcome_label, y_label)
        if coeffs_bjs is not None:
            bjs_count += 1

        logger.info("")
        coeffs_bjs_dr = run_bjs_dr_for_outcome(
            panel, outcome, outcome_label, y_label, cov_df
        )
        if coeffs_bjs_dr is not None:
            bjs_dr_count += 1

    # Gaming test (CS DR overlay)
    logger.info("\n--- Gaming Test Plot ---")
    plot_gaming_test(
        tmax_coeffs=gaming_coeffs.get('tmax_summer_mean'),
        lst_coeffs=gaming_coeffs.get('lst_summer_mean_nearest'),
        save_path=FIGURES_DIR / 'station_gaming_test.png',
    )

    logger.info("\n" + "=" * 70)
    logger.info("Station analysis complete.")
    logger.info(f"  CS                  : {cs_count} outcomes")
    logger.info(f"  CS DR               : {dr_count} outcomes")
    logger.info(f"  OLS TWFE            : {ols_count} outcomes")
    logger.info(f"  BJS                 : {bjs_count} outcomes")
    logger.info(f"  BJS with controls   : {bjs_dr_count} outcomes")
    logger.info(f"  Figures  : {FIGURES_DIR}")
    logger.info(f"  Tables   : {TABLES_DIR}")
    logger.info("=" * 70)

    if cs_count + dr_count + ols_count + bjs_count + bjs_dr_count == 0:
        logger.error("FATAL: No estimates produced for any outcome or estimator.")
        sys.exit(1)


if __name__ == '__main__':
    main()
