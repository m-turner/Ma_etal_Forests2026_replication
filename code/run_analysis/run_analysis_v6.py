"""
Run Analysis -- v6

Updated from run_analysis_v5 to run_analysis_v6 on 2026-07-19.
Main changes in this version:
- Added file logging (LogTee): every run now writes output to
  last_run_analysis.log in the project root, in addition to stdout.
  This replaces piping stdout into error_log.txt via the pipeline.
- Added analyze_outcome_bjs_allclean(): runs BJS with controls using
  never_treated_only=False (standard BJS 2024 definition, which includes
  pre-treatment observations of eventually-treated cities in stage 1).
  This is the specification closest to Ma et al and produces the appendix
  comparison figure. Figures saved with _allclean suffix.
Previous version saved in _old/ as run_analysis_v5.

(Prior v5 changes below for reference)
Updated from run_analysis_v4 to run_analysis_v5 on 2026-07-18.
Main changes in that version:
- Reverted BJS and BJS-DR from diff_diff.ImputationDiD (library) back to
  the custom bjs.py implementation (bjs_event_study with never_treated_only=True).
  The library produced impossible estimates (+47 to +113 C for BJS-DR LST) and
  disagrees with custom bjs.py on the no-controls case (-0.286 vs -0.045).
  Synthetic validation confirms custom bjs.py recovers the true ATT correctly;
  the library crashes when covariates are passed.
- never_treated_only=True: stage-1 TWFE fit on never-treated only; treated city
  FEs estimated from pre-treatment residuals via a reference city (2026-07-18 fix).
- Fixed figure label and title: "BJS-DR" -> "BJS with controls".
- CS DR and CS (no controls) remain via csdid (fit_cs) -- unaffected by BJS bug.
Previous version of that revision saved in _old/ as run_analysis_v4.

Environment: environment_stats

Requires (inputs from earlier stages):
    - data/merge_panels/generated/city_year_panel.csv  (from merge_panels)
    - data/merge_panels/generated/city_covariates.csv (from build_covariates)

Produces (outputs for later stages):
    - analysis/figures/event_study_*.png           (OLS TWFE)
    - analysis/figures/event_study_dr_*.png        (CS DR)
    - analysis/figures/event_study_bjs_*.png       (BJS, no controls)
    - analysis/figures/event_study_bjs_dr_*.png    (BJS with controls)
    - analysis/figures/event_study_cs_*.png        (CS no controls)
    - analysis/tables/event_study_coeffs_*.csv
    - analysis/tables/event_study_dr_coeffs_*.csv
    - analysis/tables/event_study_bjs_coeffs_*.csv
    - analysis/tables/event_study_bjs_dr_coeffs_*.csv
    - analysis/tables/event_study_cs_coeffs_*.csv
    - analysis/tables/aggregate_atts.csv
    - analysis/tables/summary_statistics.csv
    - last_run_analysis.log       (project root, one timestamped file per run)

Usage:
    python code/run_analysis/run_analysis_v6.py
"""

import sys
import numpy as np
from pathlib import Path
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt

# Project root is three levels up from this file:
# code/run_analysis/run_analysis_v6.py  ->  code/run_analysis  ->  code  ->  project root
PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Logging: tee stdout to a log file in the project root.
# ---------------------------------------------------------------------------

class LogTee:
    """
    Redirect sys.stdout so every print() goes to both the real terminal and
    a log file.  Non-ASCII characters are replaced with '?' to keep the log
    file clean (ASCII-only, per project convention).
    """
    def __init__(self, log_path: Path):
        self._terminal  = sys.stdout
        self._log_file  = open(log_path, 'w', encoding='ascii', errors='replace')

    def write(self, msg: str):
        # Write to terminal as-is; write ASCII-safe version to log file.
        self._terminal.write(msg)
        self._log_file.write(msg)

    def flush(self):
        self._terminal.flush()
        self._log_file.flush()

    def close(self):
        """Restore sys.stdout and close the log file."""
        sys.stdout = self._terminal
        self._log_file.close()

sys.path.append(str(Path(__file__).parent.parent))
from utils.config import (
    FINAL_PANEL_FILE,
    CITY_COVARIATES_FILE,
    FIGURES_DIR,
    TABLES_DIR
)
from utils.event_study import run_event_study_regression
from utils.bjs import bjs_event_study
from utils.did_lib import fit_cs

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
FIGSIZE_PANEL = (5, 4)

STYLE = {
    'ols':    dict(color='forestgreen',  marker='^', label='OLS TWFE estimate'),
    'cs':     dict(color='royalblue',    marker='v', label='CS estimate'),
    'dr':     dict(color='steelblue',    marker='o', label='CS DR estimate'),
    'bjs':    dict(color='darkorange',   marker='s', label='BJS estimate'),
    'bjs_dr': dict(color='mediumpurple', marker='D', label='BJS with controls estimate'),
}


# ---------------------------------------------------------------------------
# Label helper and shared plot helper
# ---------------------------------------------------------------------------

def _outcome_labels(outcome):
    """Return (y_label, title, x_label) for a city-level outcome column name."""
    x = 'years to treatment'   # 2026-07-27: standardised label for all estimators
    if 'ndvi' in outcome:
        return 'NDVI', 'MODIS NDVI', x
    if 'tree_cover' in outcome:
        return '% Tree Cover', 'MODIS Tree Cover', x
    if 'clcd' in outcome:
        return '% Forest Cover', 'CLCD/Landsat Forest Cover', x
    if 'lst_summer_max' in outcome:
        return 'deg C', 'Peak Summer LST', x
    if 'lst' in outcome:
        return 'deg C', 'Summer Mean LST', x
    return '', outcome.replace('_', ' '), x


def _plot_panel(coeffs_df, title, y_label, save_path, estimator_key,
                x_label='years to treatment'):
    """Save one event-study panel in s1mono style.

    2026-07-26: switched to whiskers (errorbar) for CIs; removed connecting
    line; embedded title, y-label, x-label in figure.

    coeffs_df must have: rel_time, coef, ci_lower, ci_upper.
    Style: white background, black axes and markers, no grid.
    """
    fig, ax = plt.subplots(figsize=FIGSIZE_PANEL)
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    # 95% CI as vertical whiskers with caps; circle markers, no connecting line
    ax.errorbar(
        coeffs_df['rel_time'], coeffs_df['coef'],
        yerr=[coeffs_df['coef'] - coeffs_df['ci_lower'],
              coeffs_df['ci_upper'] - coeffs_df['coef']],
        fmt='o',
        color='black',
        markersize=4,
        elinewidth=0.8,
        capsize=3,
        capthick=0.8,
    )

    # Zero reference line (grey dashed)
    ax.axhline(0, color='0.55', linestyle='--', linewidth=0.8)
    # Light gray vertical line at t=0 (treatment date) -- 2026-07-31
    ax.axvline(0, color='lightgray', linewidth=0.8, zorder=0)

    # Open frame: left + bottom only (no top or right border)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('black')
    ax.spines['bottom'].set_color('black')
    ax.grid(False)

    # Axis labels and title embedded; explicit fontsizes override rcParams so
    # that labels fit within the 5x4in panel without clipping.
    ax.set_title(title,   fontsize=11, pad=4)
    ax.set_ylabel(y_label, fontsize=10)
    ax.set_xlabel(x_label, fontsize=10)
    ax.tick_params(labelsize=9)

    # Tick mark at every year; label only at multiples of 5 (including 0).
    # 2026-07-27: was step-based ticks; changed per user requirement.
    lo = int(coeffs_df['rel_time'].min())
    hi = int(coeffs_df['rel_time'].max())
    all_ticks = list(range(lo, hi + 1))
    tick_labels = [str(t) if t % 5 == 0 else '' for t in all_ticks]
    ax.set_xticks(all_ticks)
    ax.set_xticklabels(tick_labels)
    ax.set_xlim(lo - 0.6, hi + 0.6)

    plt.tight_layout()
    plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def check_required_inputs():
    csv_file = FINAL_PANEL_FILE.with_suffix('.csv')
    if not FINAL_PANEL_FILE.exists() and not csv_file.exists():
        print("ERROR: Missing required input file:")
        print(f"  {FINAL_PANEL_FILE}")
        print("Run: python code/merge_panels/merge_panels_v1.py")
        sys.exit(1)


def load_panel():
    print("Loading final panel...")
    csv_file = FINAL_PANEL_FILE.with_suffix('.csv')
    if csv_file.exists():
        panel = pd.read_csv(csv_file)
    elif FINAL_PANEL_FILE.exists():
        panel = pd.read_parquet(FINAL_PANEL_FILE)
    else:
        raise FileNotFoundError(f"Panel not found: {FINAL_PANEL_FILE}")
    # Year 1999 is synthetic test data inserted during pipeline development --
    # not real observations.  Drop it here so it never enters any estimator.
    panel = panel[panel['year'] != 1999]
    print(f"  {len(panel):,} obs | cities {panel['city_id'].nunique()} | "
          f"years {panel['year'].min()}-{panel['year'].max()}")
    return panel


def load_covariates():
    if not CITY_COVARIATES_FILE.exists():
        print(f"  Warning: {CITY_COVARIATES_FILE.name} not found.")
        print("  Run Step 0c (build_covariates_v1.py) to enable DR / BJS with controls.")
        return None
    cov = pd.read_csv(CITY_COVARIATES_FILE)
    n_valid = cov['log_pop_pretx'].notna().sum()
    print(f"  Loaded covariates: {len(cov)} cities ({n_valid} with valid population)")
    return cov


# ---------------------------------------------------------------------------
# OLS TWFE (unchanged)
# ---------------------------------------------------------------------------

def analyze_outcome(panel, outcome, outcome_label, min_time=-5, max_time=10):
    """Run OLS TWFE event study for one outcome."""
    print(f"\nAnalyzing {outcome_label}...")

    if outcome not in panel.columns:
        print(f"  Warning: {outcome} not in panel -- skipping")
        return None, None

    n_complete = panel[outcome].notna().sum()
    if n_complete == 0:
        print(f"  Warning: no data for {outcome} -- skipping")
        return None, None

    print(f"  {n_complete:,} obs -- running OLS TWFE...")
    try:
        model, coeffs_df = run_event_study_regression(
            panel, outcome=outcome, min_time=min_time, max_time=max_time
        )
        print(f"  R^2 = {model.rsquared:.3f}")
    except Exception as e:
        print(f"  Regression failed: {e}")
        return None, None

    y_label, fig_title, x_label = _outcome_labels(outcome)
    save_path = FIGURES_DIR / f"event_study_{outcome}.png"
    _plot_panel(
        coeffs_df,
        title=fig_title,
        y_label=y_label,
        save_path=save_path,
        estimator_key='ols',
        x_label=x_label,
    )
    print(f"  Saved: {save_path.name}")
    return model, coeffs_df


# ---------------------------------------------------------------------------
# CS Doubly Robust (unchanged -- csdid library is correct)
# ---------------------------------------------------------------------------

def analyze_outcome_dr(panel, covariates_df, outcome, outcome_label,
                       min_time=-5, max_time=10):
    """Run CS DR event study for one outcome; returns (coeffs, overall_att, overall_se)."""
    print(f"\n  [CS DR] {outcome_label}...")

    if outcome not in panel.columns or panel[outcome].notna().sum() == 0:
        print(f"    Skipped: no data for {outcome}")
        return None, np.nan, np.nan

    panel_cov = panel.merge(
        covariates_df[['city_id', 'log_pop_pretx']], on='city_id', how='inner'
    )

    preferred = ['lat', 'lon', 'log_pop_pretx']
    cov_features = [f for f in preferred
                    if f in panel_cov.columns and panel_cov[f].notna().any()]
    if not cov_features:
        print("    ERROR: no usable covariates -- skipping")
        return None, np.nan, np.nan
    panel_cov = panel_cov.dropna(subset=cov_features)

    n_treated = panel_cov.loc[panel_cov['treated'] == 1, 'city_id'].nunique()
    if n_treated < 5:
        print("    Skipped: too few treated cities with valid covariates")
        return None, np.nan, np.nan
    print(f"    {panel_cov['city_id'].nunique()} cities ({n_treated} treated), "
          f"covariates: {cov_features}")

    coeffs, overall_att, overall_se = fit_cs(
        panel_cov, outcome=outcome, covariates=cov_features,
        min_time=min_time, max_time=max_time,
    )

    if coeffs is None or coeffs.empty:
        print("    No CS DR coefficients returned.")
        return None, overall_att, overall_se

    y_label, fig_title, x_label = _outcome_labels(outcome)
    save_path = FIGURES_DIR / f"event_study_dr_{outcome}.png"
    _plot_panel(
        coeffs,
        title=fig_title,
        y_label=y_label,
        save_path=save_path,
        estimator_key='dr',
        x_label=x_label,
    )
    print(f"    Saved: {save_path.name}")
    if not np.isnan(overall_att):
        print(f"    Aggregate ATT={overall_att:.4f}  SE={overall_se:.4f}")
    return coeffs, overall_att, overall_se


# ---------------------------------------------------------------------------
# CS (no controls) -- unchanged
# ---------------------------------------------------------------------------

def analyze_outcome_cs(panel, outcome, outcome_label, min_time=-5, max_time=10):
    """
    Run plain CS event study (no covariates).
    Returns (coeffs, overall_att, overall_se).
    """
    print(f"\n  [CS] {outcome_label}...")

    if outcome not in panel.columns or panel[outcome].notna().sum() == 0:
        print(f"    Skipped: no data for {outcome}")
        return None, np.nan, np.nan

    n_treated = panel.loc[(panel['treated'] == 1) & panel[outcome].notna(),
                          'city_id'].nunique()
    print(f"    {panel[outcome].notna().sum():,} obs, {n_treated} treated cities")

    coeffs, overall_att, overall_se = fit_cs(
        panel, outcome=outcome, covariates=None,
        min_time=min_time, max_time=max_time,
    )

    if coeffs is None or coeffs.empty:
        print("    No CS coefficients returned.")
        return None, overall_att, overall_se

    y_label, fig_title, x_label = _outcome_labels(outcome)
    save_path = FIGURES_DIR / f"event_study_cs_{outcome}.png"
    _plot_panel(
        coeffs,
        title=fig_title,
        y_label=y_label,
        save_path=save_path,
        estimator_key='cs',
        x_label=x_label,
    )
    print(f"    Saved: {save_path.name}")
    if not np.isnan(overall_att):
        print(f"    Aggregate ATT={overall_att:.4f}  SE={overall_se:.4f}")
    return coeffs, overall_att, overall_se


# ---------------------------------------------------------------------------
# BJS imputation (no covariates) -- custom bjs.py, never_treated_only=True
# ---------------------------------------------------------------------------

def analyze_outcome_bjs(panel, outcome, outcome_label, min_time=-5, max_time=10):
    """
    Run BJS imputation event study (no covariates) using custom bjs.py.
    never_treated_only=False: stage-1 TWFE fit on all clean obs (never-treated +
    not-yet-treated), matching Stata did_imputation autosample behaviour.
    Returns (coeffs, post_mean_att, post_mean_se).
    """
    print(f"\n  [BJS] {outcome_label}...")

    if outcome not in panel.columns or panel[outcome].notna().sum() == 0:
        print(f"    Skipped: no data for {outcome}")
        return None, np.nan, np.nan

    n_treated = panel.loc[(panel['treated'] == 1) & panel[outcome].notna(),
                          'city_id'].nunique()
    print(f"    {panel[outcome].notna().sum():,} obs, {n_treated} treated cities")

    coeffs = bjs_event_study(
        panel,
        outcome=outcome,
        unit_var='city_id',
        time_var='year',
        treatment_var='treated',
        rel_time_var='rel_time',
        desig_year_var='designation_year',
        min_time=min_time,
        max_time=max_time,
        ref_time=-1,
        cluster_var='city_id',
        covariates=None,
        never_treated_only=False,  # 2026-07-22: changed from True; use not-yet-treated to match Stata autosample
    )

    if coeffs is None or coeffs.empty:
        print("    No BJS coefficients returned.")
        return None, np.nan, np.nan

    # Aggregate ATT from Stage-2 direct mean (2026-07-23).
    # The Stage-3 intercept correction (coef_mean + tau_{-1}) was found to be
    # unreliable when some treated cities lack data at k=-1: their tau_hat at
    # that period is an out-of-sample prediction, biasing tau_{-1} and flipping
    # the sign of the ATT (e.g. tree_cover, lst_20km).  Direct Stage-2 mean
    # matches Stata did_imputation without reference-period bias.
    post = coeffs[coeffs['rel_time'] >= 0]
    intercept = coeffs.attrs.get('stage3_intercept', 0.0)
    direct_att = coeffs.attrs.get('direct_att', None)
    overall_att = direct_att if (direct_att is not None and not np.isnan(direct_att)) \
                  else post['coef'].mean() + intercept
    overall_se = post['se'].mean()
    print(f"    Aggregate ATT={overall_att:.4f}  SE(approx)={overall_se:.4f}"
          f"  (stage3_intercept={intercept:+.4f})")

    y_label, fig_title, x_label = _outcome_labels(outcome)
    save_path = FIGURES_DIR / f"event_study_bjs_{outcome}.png"
    _plot_panel(
        coeffs,
        title=fig_title,
        y_label=y_label,
        save_path=save_path,
        estimator_key='bjs',
        x_label=x_label,
    )
    print(f"    Saved: {save_path.name}")
    (TABLES_DIR / f"event_study_bjs_coeffs_{outcome}.csv").write_text(
        coeffs.to_csv(index=False), encoding='ascii'
    )
    return coeffs, overall_att, overall_se


# ---------------------------------------------------------------------------
# BJS with controls -- custom bjs.py with lat/lon/log_pop_pretx interactions
# ---------------------------------------------------------------------------

def analyze_outcome_bjs_dr(panel, covariates_df, outcome, outcome_label,
                            min_time=-5, max_time=10):
    """
    Run BJS with controls (lat/lon/log_pop_pretx x year interactions in stage 1).
    never_treated_only=False: stage-1 TWFE fit on all clean obs (never-treated +
    not-yet-treated), matching Stata did_imputation autosample behaviour.
    Returns (coeffs, post_mean_att, post_mean_se).
    """
    print(f"\n  [BJS with controls] {outcome_label}...")

    if outcome not in panel.columns or panel[outcome].notna().sum() == 0:
        print(f"    Skipped: no data for {outcome}")
        return None, np.nan, np.nan

    panel_cov = panel.merge(
        covariates_df[['city_id', 'log_pop_pretx']], on='city_id', how='inner'
    )

    preferred = ['lat', 'lon', 'log_pop_pretx']
    cov_features = [f for f in preferred
                    if f in panel_cov.columns and panel_cov[f].notna().any()]
    if not cov_features:
        print("    ERROR: no usable covariates -- skipping BJS with controls")
        return None, np.nan, np.nan
    panel_cov = panel_cov.dropna(subset=cov_features)

    n_treated = panel_cov.loc[(panel_cov['treated'] == 1) & panel_cov[outcome].notna(),
                              'city_id'].nunique()
    if n_treated < 5:
        print("    Skipped: too few treated cities with valid covariates")
        return None, np.nan, np.nan
    print(f"    {panel_cov[outcome].notna().sum():,} obs, {n_treated} treated cities, "
          f"covariates: {cov_features}")

    coeffs = bjs_event_study(
        panel_cov,
        outcome=outcome,
        unit_var='city_id',
        time_var='year',
        treatment_var='treated',
        rel_time_var='rel_time',
        desig_year_var='designation_year',
        min_time=min_time,
        max_time=max_time,
        ref_time=-1,
        cluster_var='city_id',
        covariates=cov_features,
        never_treated_only=False,  # 2026-07-22: changed from True; match Stata autosample
    )

    if coeffs is None or coeffs.empty:
        print("    No BJS with controls coefficients returned.")
        return None, np.nan, np.nan

    # Aggregate ATT from Stage-2 direct mean (2026-07-23, same fix as plain BJS above).
    post = coeffs[coeffs['rel_time'] >= 0]
    intercept = coeffs.attrs.get('stage3_intercept', 0.0)
    direct_att = coeffs.attrs.get('direct_att', None)
    overall_att = direct_att if (direct_att is not None and not np.isnan(direct_att)) \
                  else post['coef'].mean() + intercept
    overall_se = post['se'].mean()
    print(f"    Aggregate ATT={overall_att:.4f}  SE(approx)={overall_se:.4f}"
          f"  (stage3_intercept={intercept:+.4f})")

    y_label, fig_title, x_label = _outcome_labels(outcome)
    save_path = FIGURES_DIR / f"event_study_bjs_dr_{outcome}.png"
    _plot_panel(
        coeffs,
        title=fig_title,
        y_label=y_label,
        save_path=save_path,
        estimator_key='bjs_dr',
        x_label=x_label,
    )
    print(f"    Saved: {save_path.name}")
    (TABLES_DIR / f"event_study_bjs_dr_coeffs_{outcome}.csv").write_text(
        coeffs.to_csv(index=False), encoding='ascii'
    )
    return coeffs, overall_att, overall_se


# ---------------------------------------------------------------------------
# BJS with controls, ALL-CLEAN stage 1 (never_treated_only=False)
# This is the standard BJS 2024 definition: stage-1 TWFE fitted on never-
# treated cities PLUS pre-treatment observations of eventually-treated cities.
# Including pre-treatment treated obs means the stage-1 fit can pick up
# systematic differences in pre-trends between treated and never-treated cities,
# which tends to produce larger (more negative) covariate-adjusted estimates.
# Produces comparison figures for Appendix A3.
# ---------------------------------------------------------------------------

def analyze_outcome_bjs_allclean(panel, covariates_df, outcome, outcome_label,
                                  min_time=-5, max_time=10):
    """
    BJS with controls using all clean observations in stage 1
    (never_treated_only=False: includes pre-treatment obs of treated cities).
    Standard BJS 2024. Figures saved with _allclean suffix for appendix.
    Returns (coeffs, post_mean_att, post_mean_se).
    """
    print(f"\n  [BJS all-clean] {outcome_label}...")

    if outcome not in panel.columns or panel[outcome].notna().sum() == 0:
        print(f"    Skipped: no data for {outcome}")
        return None, np.nan, np.nan

    panel_cov = panel.merge(
        covariates_df[['city_id', 'log_pop_pretx']], on='city_id', how='inner'
    )

    preferred = ['lat', 'lon', 'log_pop_pretx']
    cov_features = [f for f in preferred
                    if f in panel_cov.columns and panel_cov[f].notna().any()]
    if not cov_features:
        print("    ERROR: no usable covariates -- skipping BJS all-clean")
        return None, np.nan, np.nan
    panel_cov = panel_cov.dropna(subset=cov_features)

    n_treated = panel_cov.loc[(panel_cov['treated'] == 1) & panel_cov[outcome].notna(),
                              'city_id'].nunique()
    if n_treated < 5:
        print("    Skipped: too few treated cities with valid covariates")
        return None, np.nan, np.nan
    print(f"    {panel_cov[outcome].notna().sum():,} obs, {n_treated} treated cities "
          f"(stage-1 includes pre-treatment treated obs)")

    # never_treated_only=False: standard BJS 2024 (all clean observations in stage 1)
    coeffs = bjs_event_study(
        panel_cov,
        outcome=outcome,
        unit_var='city_id',
        time_var='year',
        treatment_var='treated',
        rel_time_var='rel_time',
        desig_year_var='designation_year',
        min_time=min_time,
        max_time=max_time,
        ref_time=-1,
        cluster_var='city_id',
        covariates=cov_features,
        never_treated_only=False,
    )

    if coeffs is None or coeffs.empty:
        print("    No BJS all-clean coefficients returned.")
        return None, np.nan, np.nan

    # Aggregate ATT = mean of post-period coefficients
    post = coeffs[coeffs['rel_time'] >= 0]
    overall_att = post['coef'].mean()
    overall_se  = post['se'].mean()
    print(f"    Aggregate ATT={overall_att:.4f}  SE(approx)={overall_se:.4f}")

    y_label, fig_title, x_label = _outcome_labels(outcome)
    save_path = FIGURES_DIR / f"event_study_bjs_dr_allclean_{outcome}.png"
    _plot_panel(
        coeffs,
        title=fig_title,
        y_label=y_label,
        save_path=save_path,
        estimator_key='bjs_dr',
        x_label=x_label,
    )
    print(f"    Saved: {save_path.name}")
    (TABLES_DIR / f"event_study_bjs_dr_allclean_coeffs_{outcome}.csv").write_text(
        coeffs.to_csv(index=False), encoding='ascii'
    )
    return coeffs, overall_att, overall_se


# ---------------------------------------------------------------------------
# Summary statistics (unchanged)
# ---------------------------------------------------------------------------

def create_summary_table(panel):
    print("\nCreating summary statistics...")
    prefixes = ['lst_', 'tree_cover_', 'ndvi_', 'clcd_', 'nightlights_']
    vars_to_summarize = [
        col for col in panel.columns
        if any(col.startswith(p) for p in prefixes) and panel[col].notna().any()
    ]
    if not vars_to_summarize:
        print("  Warning: no outcome variables available")
        return None

    rows = []
    for var in vars_to_summarize:
        for group_filter, group_name in [
            (slice(None), 'Overall'),
            (panel['treated'] == 1, 'Treated'),
            (panel['treated'] == 0, 'Control'),
        ]:
            data = panel.loc[group_filter, var].dropna() if group_name != 'Overall' \
                   else panel[var].dropna()
            rows.append({'variable': var, 'group': group_name,
                         'n': len(data), 'mean': data.mean(), 'std': data.std(),
                         'min': data.min(), 'max': data.max()})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # ---- Set up file logging: tee all print() output to last_run_analysis.log
    log_path = PROJECT_ROOT / 'last_run_analysis.log'
    tee = LogTee(log_path)
    sys.stdout = tee

    try:
        _main_inner(tee, log_path)
    except Exception:
        import traceback
        traceback.print_exc()      # goes to LogTee -> log file
        raise
    finally:
        # Always restore stdout and close the log file, even on error.
        tee.close()


def _main_inner(tee, log_path):
    """Inner main body; called from main() inside a try/finally."""
    run_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print("=" * 70)
    print(f"ANALYSIS -- OLS TWFE + CS DR + BJS + BJS with controls (v6, custom bjs.py)")
    print(f"Run started: {run_ts}")
    print("=" * 70)

    check_required_inputs()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    TABLES_DIR.mkdir(parents=True, exist_ok=True)

    panel = load_panel()

    # Accumulate aggregate ATTs for Table 1
    agg_rows = []

    # ------------------------------------------------------------------ OLS
    ols_outcomes = [
        ('lst_summer_mean_5km',    'Summer LST (C) -- 5km'),
        ('lst_summer_mean_10km',   'Summer LST (C) -- 10km'),
        ('lst_summer_mean_20km',   'Summer LST (C) -- 20km'),
        ('lst_summer_max_5km',     'Peak LST (C) -- 5km'),
        ('lst_summer_max_10km',    'Peak LST (C) -- 10km'),
        ('lst_summer_max_20km',    'Peak LST (C) -- 20km'),
        ('tree_cover_mean_5km',    'Tree Cover (%) -- 5km'),
        ('tree_cover_mean_10km',   'Tree Cover (%) -- 10km'),
        ('tree_cover_mean_20km',   'Tree Cover (%) -- 20km'),
        ('ndvi_mean_5km',          'NDVI -- 5km'),
        ('ndvi_mean_10km',         'NDVI -- 10km'),
        ('ndvi_mean_20km',         'NDVI -- 20km'),
        ('clcd_forest_pct_5km',    'CLCD Forest (%) -- 5km'),
        ('clcd_forest_pct_10km',   'CLCD Forest (%) -- 10km'),
        ('clcd_forest_pct_20km',   'CLCD Forest (%) -- 20km'),
        # Prefecture boundary outcomes (added 2026-10-06: pf05.shp polygon extraction)
        ('lst_summer_mean_pref',   'Summer LST (C) -- pref'),
        ('lst_summer_max_pref',    'Peak LST (C) -- pref'),
        ('tree_cover_mean_pref',   'Tree Cover (%) -- pref'),
        ('ndvi_mean_pref',         'NDVI -- pref'),
        ('clcd_forest_pct_pref',   'CLCD Forest (%) -- pref'),
        ('nightlights_annual',     'Nighttime Lights'),
    ]
    print(f"\n{'='*70}")
    print("OLS TWFE EVENT STUDY")
    print('='*70)
    ols_results = {}
    for outcome, label in ols_outcomes:
        model, coeffs = analyze_outcome(panel, outcome, label)
        ols_results[outcome] = (model, coeffs)
        if coeffs is not None:
            (TABLES_DIR / f"event_study_coeffs_{outcome}.csv").write_text(
                coeffs.to_csv(index=False), encoding='ascii'
            )

    # ------------------------------------------------------------------ DR
    print(f"\n{'='*70}")
    print("CS DOUBLY ROBUST EVENT STUDY (csdid.ATTgt, est_method='dr')")
    print('='*70)
    covariates_df = load_covariates()

    covariate_outcomes = [
        ('lst_summer_mean_5km',  'Summer LST (C) -- 5km'),
        ('lst_summer_mean_10km', 'Summer LST (C) -- 10km'),
        ('lst_summer_mean_20km', 'Summer LST (C) -- 20km'),
        ('lst_summer_max_5km',   'Peak LST (C) -- 5km'),
        ('lst_summer_max_10km',  'Peak LST (C) -- 10km'),
        ('lst_summer_max_20km',  'Peak LST (C) -- 20km'),
        ('clcd_forest_pct_5km',  'CLCD Forest (%) -- 5km'),
        ('clcd_forest_pct_10km', 'CLCD Forest (%) -- 10km'),
        ('clcd_forest_pct_20km', 'CLCD Forest (%) -- 20km'),
        ('tree_cover_mean_5km',  'Tree Cover (%) -- 5km'),
        ('tree_cover_mean_10km', 'Tree Cover (%) -- 10km'),
        ('tree_cover_mean_20km', 'Tree Cover (%) -- 20km'),
        ('ndvi_mean_5km',        'NDVI -- 5km'),
        ('ndvi_mean_10km',       'NDVI -- 10km'),
        ('ndvi_mean_20km',       'NDVI -- 20km'),
        # Prefecture boundary outcomes (added 2026-10-06: pf05.shp polygon extraction)
        ('lst_summer_mean_pref', 'Summer LST (C) -- pref'),
        ('lst_summer_max_pref',  'Peak LST (C) -- pref'),
        ('tree_cover_mean_pref', 'Tree Cover (%) -- pref'),
        ('ndvi_mean_pref',       'NDVI -- pref'),
        ('clcd_forest_pct_pref', 'CLCD Forest (%) -- pref'),
    ]

    dr_results = {}
    if covariates_df is not None:
        for outcome, label in covariate_outcomes:
            coeffs, att, se = analyze_outcome_dr(panel, covariates_df, outcome, label)
            if coeffs is not None:
                dr_results[outcome] = coeffs
                (TABLES_DIR / f"event_study_dr_coeffs_{outcome}.csv").write_text(
                    coeffs.to_csv(index=False), encoding='ascii'
                )
                agg_rows.append({
                    'outcome_col': outcome,
                    'estimator':   'cs_dr',
                    'att':         att,
                    'se':          se,
                    'ci_lower':    att - 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                    'ci_upper':    att + 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                })
    else:
        print("  Skipping CS DR analysis (covariates not available).")

    # ------------------------------------------------------------------ CS
    print(f"\n{'='*70}")
    print("CS EVENT STUDY -- NO CONTROLS (csdid.ATTgt, plain DiD)")
    print('='*70)
    cs_results = {}
    for outcome, label in covariate_outcomes:
        coeffs, att, se = analyze_outcome_cs(panel, outcome, label)
        if coeffs is not None:
            cs_results[outcome] = coeffs
            (TABLES_DIR / f"event_study_cs_coeffs_{outcome}.csv").write_text(
                coeffs.to_csv(index=False), encoding='ascii'
            )
            agg_rows.append({
                'outcome_col': outcome,
                'estimator':   'cs',
                'att':         att,
                'se':          se,
                'ci_lower':    att - 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                'ci_upper':    att + 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
            })

    # ------------------------------------------------------------------ BJS
    print(f"\n{'='*70}")
    print("BJS IMPUTATION EVENT STUDY (custom bjs.py, never_treated_only=False)")
    print('='*70)
    bjs_results = {}
    for outcome, label in covariate_outcomes:
        coeffs, att, se = analyze_outcome_bjs(panel, outcome, label)
        if coeffs is not None:
            bjs_results[outcome] = coeffs
            agg_rows.append({
                'outcome_col': outcome,
                'estimator':   'bjs',
                'att':         att,
                'se':          se,
                'ci_lower':    att - 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                'ci_upper':    att + 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
            })

    # --------------------------------------------------------- BJS with controls
    print(f"\n{'='*70}")
    print("BJS WITH CONTROLS (custom bjs.py, lat/lon/log_pop_pretx, never_treated_only=True)")
    print('='*70)
    bjs_dr_results = {}
    if covariates_df is not None:
        for outcome, label in covariate_outcomes:
            coeffs, att, se = analyze_outcome_bjs_dr(panel, covariates_df, outcome, label)
            if coeffs is not None:
                bjs_dr_results[outcome] = coeffs
                agg_rows.append({
                    'outcome_col': outcome,
                    'estimator':   'bjs_dr',
                    'att':         att,
                    'se':          se,
                    'ci_lower':    att - 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                    'ci_upper':    att + 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                })
    else:
        print("  Skipping BJS with controls (covariates not available).")

    # -------------------------------- BJS with controls (all-clean, never_treated_only=False)
    # Standard BJS 2024: stage-1 fitted on all clean obs (never-treated + pre-treatment
    # observations of eventually-treated cities).  This is the specification closest to
    # the Ma et al (2026) replication and shows what changes when we expand the stage-1
    # control pool beyond never-treated-only.  Figures saved with _allclean suffix.
    print(f"\n{'='*70}")
    print("BJS WITH CONTROLS -- ALL-CLEAN stage 1 (never_treated_only=False, standard BJS 2024)")
    print('='*70)
    bjs_allclean_results = {}
    if covariates_df is not None:
        for outcome, label in covariate_outcomes:
            coeffs, att, se = analyze_outcome_bjs_allclean(panel, covariates_df, outcome, label)
            if coeffs is not None:
                bjs_allclean_results[outcome] = coeffs
                agg_rows.append({
                    'outcome_col': outcome,
                    'estimator':   'bjs_allclean',
                    'att':         att,
                    'se':          se,
                    'ci_lower':    att - 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                    'ci_upper':    att + 1.96 * se if not (np.isnan(att) or np.isnan(se)) else np.nan,
                })
    else:
        print("  Skipping BJS all-clean (covariates not available).")

    # ----------------------------------------- Save aggregate ATTs for Table 1
    if agg_rows:
        agg_df = pd.DataFrame(agg_rows)
        agg_path = TABLES_DIR / "aggregate_atts.csv"
        agg_df.to_csv(agg_path, index=False)
        print(f"\nAggregate ATTs saved: {agg_path}")
        print(f"  {len(agg_df)} cells ({agg_df['estimator'].nunique()} estimators x "
              f"{agg_df['outcome_col'].nunique()} outcomes)")
    else:
        print("\nWARNING: No aggregate ATTs collected -- aggregate_atts.csv not written.")

    # -------------------------------------------------------- Summary stats
    summary = create_summary_table(panel)
    if summary is not None:
        (TABLES_DIR / "summary_statistics.csv").write_text(
            summary.to_csv(index=False), encoding='ascii'
        )
        print(f"\nSummary statistics saved: {TABLES_DIR / 'summary_statistics.csv'}")

    # -------------------------------------------------------- Final report
    print()
    print("=" * 70)
    print("SUCCESS: ANALYSIS COMPLETE")
    print("=" * 70)
    print(f"  OLS TWFE           : {sum(1 for m, c in ols_results.values() if c is not None)} outcomes")
    print(f"  CS (no ctrl)       : {len(cs_results)} outcomes")
    print(f"  CS DR              : {len(dr_results)} outcomes")
    print(f"  BJS                : {len(bjs_results)} outcomes")
    print(f"  BJS with controls  : {len(bjs_dr_results)} outcomes")
    print(f"  BJS all-clean      : {len(bjs_allclean_results)} outcomes")
    print(f"  Figures  : {FIGURES_DIR}")
    print(f"  Tables   : {TABLES_DIR}")
    print(f"  Log file : {log_path}")


if __name__ == "__main__":
    main()
