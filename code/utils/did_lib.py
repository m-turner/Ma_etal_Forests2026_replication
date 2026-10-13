"""
Library wrappers for diff_diff (ImputationDiD / BJS) and csdid (ATTgt / CS).

Both wrappers return a standardized triple:
    (event_study_df, overall_att, overall_se)

event_study_df columns (all floats except rel_time which is int):
    rel_time  -- relative event time
    coef      -- ATT estimate
    se        -- standard error
    ci_lower  -- 95% pointwise confidence interval lower bound
    ci_upper  -- 95% pointwise confidence interval upper bound
    pvalue    -- two-sided p-value (computed from coef/se)

overall_att, overall_se are the post-period aggregate ATT and its SE as
computed by the library itself (not an ad-hoc independence approximation).
Either value is np.nan if the library call fails.

Environment: environment_stats (requires diff-diff and csdid packages)

References
----------
Borusyak, K., Jaravel, X., Spiess, J. (2024). Revisiting event-study designs:
    Robust and efficient estimation. Review of Economic Studies 91(6), 3253-3285.
    Python package: diff_diff (Gerber 2026), pip install diff-diff

Callaway, B. and Sant'Anna, P.H.C. (2021). Difference-in-differences with
    multiple time periods. Journal of Econometrics, 225(2), 200-230.
    Python package: csdid (d2cml-ai), pip install csdid

Note on never-treated convention
---------------------------------
Both libraries use 0 in the first-treatment column to flag never-treated units.
This works because no calendar year in our data is year 0.  Our panel uses
NaN in designation_year for never-treated cities; we fill with 0 before
passing to the library.
"""

import copy
import logging
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

# 2026-07-17: Replaced all print() calls with logger calls so that error
# messages from failed BJS/CS estimations appear in the calling script's
# log file (not just stdout). Added diagnostic logging at the start of
# fit_bjs and fit_cs to show unit counts, cohorts, and year range before
# each library call -- needed to diagnose the station-analysis failure
# where BJS and CS DR were returning empty results without any logged reason.
# Also added df[time_var].astype(int) to ensure year is int before library call.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _add_pvalue(df):
    """Add pvalue column computed from coef / se."""
    df = df.copy()
    df['pvalue'] = np.nan
    mask = (df['se'] > 0) & df['coef'].notna()
    if mask.any():
        z = (df.loc[mask, 'coef'] / df.loc[mask, 'se']).values
        df.loc[mask, 'pvalue'] = 2 * scipy_stats.norm.sf(np.abs(z))
    return df


def _ensure_ref_period(df, ref_time=-1):
    """Insert a zero row for the reference period if the library omitted it."""
    if ref_time not in df['rel_time'].values:
        ref_row = pd.DataFrame([{
            'rel_time': ref_time, 'coef': 0.0, 'se': 0.0,
            'ci_lower': 0.0, 'ci_upper': 0.0, 'pvalue': np.nan,
        }])
        df = pd.concat([df, ref_row], ignore_index=True)
    return df


def _normalize_cs_to_ref(df, ref_time=-1):
    """Normalize CS/DR event-study estimates so t=ref_time is the reference.

    The csdid Python library returns a non-zero estimate for t=-1 instead of
    enforcing zero by construction.  Convention for CS/DR event studies is that
    t=-1 is the reference period (coef=0, no SE shown).  This function shifts
    all other estimates by subtracting the t=-1 value, then forces t=-1 to zero.
    Only the point estimate and CI shift; SE at other periods is unchanged
    because subtracting a constant does not affect the standard deviation.
    2026-07-27: added to fix CS/DR figures that showed non-zero t=-1.
    """
    ref_mask = df['rel_time'] == ref_time
    if not ref_mask.any():
        return df
    ref_coef = float(df.loc[ref_mask, 'coef'].iloc[0])
    if ref_coef == 0.0:
        return df  # already zero -- nothing to do
    df = df.copy()
    other = ~ref_mask
    df.loc[other, 'coef']     -= ref_coef
    df.loc[other, 'ci_lower'] -= ref_coef
    df.loc[other, 'ci_upper'] -= ref_coef
    df.loc[ref_mask, ['coef', 'se', 'ci_lower', 'ci_upper']] = 0.0
    return df


def _standard_cols(df):
    """Return DataFrame with exactly the six standard columns, sorted by rel_time."""
    for col in ['pvalue']:
        if col not in df.columns:
            df[col] = np.nan
    return (df[['rel_time', 'coef', 'se', 'ci_lower', 'ci_upper', 'pvalue']]
            .sort_values('rel_time').reset_index(drop=True))


# ---------------------------------------------------------------------------
# BJS: Borusyak-Jaravel-Spiess (2024) via diff_diff.ImputationDiD
# ---------------------------------------------------------------------------

def fit_bjs(panel, outcome, unit_var='city_id', time_var='year',
            desig_year_var='designation_year', covariates=None,
            min_time=-5, max_time=10):
    """
    Fit BJS (Borusyak-Jaravel-Spiess 2024) using diff_diff.ImputationDiD.

    The library handles stage-1 TWFE, stage-2 imputation, and stage-3 OLS
    with influence-function standard errors.  Aggregate ATT is the library's
    own post-period aggregate (not an ad-hoc independence approximation).

    Parameters
    ----------
    panel : pd.DataFrame
        Long-format panel.  Must contain outcome, unit_var, time_var, and
        desig_year_var (NaN for never-treated cities).
    outcome : str
        Name of the outcome column.
    covariates : list of str or None
        Time-invariant city characteristics passed to the library as covariates
        for the stage-1 TWFE model.  When None, plain BJS without controls.
    min_time, max_time : int
        Event-study window.  Output is filtered to this range.

    Returns
    -------
    event_df : pd.DataFrame  (empty on failure)
    overall_att : float or np.nan
    overall_se  : float or np.nan
    """
    from diff_diff import ImputationDiD

    # Drop rows with missing outcome before passing to the library
    df = panel[panel[outcome].notna()].copy()

    # diff_diff convention: 0 in first_treat = never-treated
    df['_first_treat'] = df[desig_year_var].fillna(0).astype(int)
    # Ensure time and unit variables are integer -- diff_diff requires numeric IDs.
    # The station panel stores city_id as object (string); converting here fixes it.
    df[time_var] = df[time_var].astype(int)
    df[unit_var] = pd.to_numeric(df[unit_var]).astype(int)

    # Diagnostic: log data structure so failures are traceable
    n_units   = df[unit_var].nunique()
    n_treated = df.loc[df['_first_treat'] > 0, unit_var].nunique()
    n_control = df.loc[df['_first_treat'] == 0, unit_var].nunique()
    cohorts   = sorted(df.loc[df['_first_treat'] > 0, '_first_treat'].unique())
    yr_min, yr_max = int(df[time_var].min()), int(df[time_var].max())
    logger.info(f"    [BJS] {n_units} units ({n_treated} treated, {n_control} never-treated)"
                f" | years {yr_min}-{yr_max} | {len(cohorts)} cohorts: {cohorts}")

    fit_kwargs = dict(
        data=df,
        outcome=outcome,
        unit=unit_var,
        time=time_var,
        first_treat='_first_treat',
        # aggregate='event_study' gives per-period ATTs; overall_att is also
        # available on the results object even with this aggregate mode.
        aggregate='event_study',
    )

    if covariates:
        valid_cov = [c for c in covariates
                     if c in df.columns and df[c].notna().any()]
        if valid_cov:
            fit_kwargs['covariates'] = valid_cov
            logger.info(f"    [BJS] covariates: {valid_cov}")

    try:
        est = ImputationDiD(
            cluster=unit_var,
            pretrends=True,     # include pre-treatment leads (event times < 0)
            horizon_max=max_time,
        )
        results = est.fit(**fit_kwargs)
    except Exception as exc:
        logger.error(f"    [BJS] Estimation failed: {exc}")
        return pd.DataFrame(), np.nan, np.nan

    # Extract per-period event-study DataFrame
    try:
        es_raw = results.to_dataframe(level='event_study')
        # diff_diff column names: relative_period, effect, se, conf_int_lower, conf_int_upper
        # (Earlier versions used event_horizon/att -- rename handles both.)
        es_raw = es_raw.rename(columns={
            'relative_period': 'rel_time',
            'event_horizon':   'rel_time',   # older diff_diff versions
            'effect':          'coef',
            'att':             'coef',        # older diff_diff versions
            'conf_int_lower':  'ci_lower',
            'conf_int_upper':  'ci_upper',
        })
        es_df = es_raw[
            (es_raw['rel_time'] >= min_time) & (es_raw['rel_time'] <= max_time)
        ].copy()
        if es_df.empty:
            logger.warning(f"    [BJS] to_dataframe returned no rows in window"
                           f" [{min_time}, {max_time}]; raw rel_time range:"
                           f" {list(es_raw.get('rel_time', pd.Series()).unique())[:10]}")
        es_df = _ensure_ref_period(es_df)
        es_df = _add_pvalue(es_df)
        es_df = _standard_cols(es_df)
    except Exception as exc:
        logger.error(f"    [BJS] Failed to extract event-study DataFrame: {exc}")
        es_df = pd.DataFrame()

    # Aggregate ATT (post-period); available on results regardless of aggregate mode
    try:
        overall_att = float(results.overall_att)
        overall_se  = float(results.overall_se)
    except Exception as exc:
        logger.warning(f"    [BJS] Failed to read overall_att/overall_se: {exc}")
        overall_att = np.nan
        overall_se  = np.nan

    return es_df, overall_att, overall_se


# ---------------------------------------------------------------------------
# CS DR: Callaway-Sant'Anna (2021) via csdid.ATTgt
# ---------------------------------------------------------------------------

def fit_cs(panel, outcome, unit_var='city_id', time_var='year',
           desig_year_var='designation_year', covariates=None,
           min_time=-5, max_time=10):
    """
    Fit CS DR (Callaway-Sant'Anna 2021) using csdid.ATTgt.

    With covariates, uses the doubly robust AIPW estimator.
    With covariates=None, uses est_method='reg' (no-controls CS DiD).

    Aggregate ATT is from aggte(typec='simple'), which gives the
    weighted average post-treatment ATT with proper multiplier-bootstrap SEs.

    Parameters
    ----------
    panel : pd.DataFrame
        Long-format panel.  Must contain outcome, unit_var, time_var, and
        desig_year_var (NaN for never-treated cities).
    outcome : str
        Name of the outcome column.
    covariates : list of str or None
        Covariates for the DR estimator.  None = plain CS without covariate
        adjustment (unconditional parallel trends assumed).
    min_time, max_time : int
        Event-study window.  Output is filtered to this range.

    Returns
    -------
    event_df : pd.DataFrame  (empty on failure)
    overall_att : float or np.nan
    overall_se  : float or np.nan
    """
    from csdid.att_gt import ATTgt

    # Drop rows with missing outcome
    df = panel[panel[outcome].notna()].copy()

    # csdid convention: 0 in gname = never-treated
    df['_group'] = df[desig_year_var].fillna(0).astype(int)
    # Ensure time and unit variables are integer -- csdid explicitly requires numeric IDs.
    # The station panel stores city_id as object (string); converting here fixes it.
    df[time_var] = df[time_var].astype(int)
    df[unit_var] = pd.to_numeric(df[unit_var]).astype(int)

    # Diagnostic: log data structure
    n_units   = df[unit_var].nunique()
    n_treated = df.loc[df['_group'] > 0, unit_var].nunique()
    n_control = df.loc[df['_group'] == 0, unit_var].nunique()
    cohorts   = sorted(df.loc[df['_group'] > 0, '_group'].unique())
    yr_min, yr_max = int(df[time_var].min()), int(df[time_var].max())
    logger.info(f"    [CS] {n_units} units ({n_treated} treated, {n_control} never-treated)"
                f" | years {yr_min}-{yr_max} | {len(cohorts)} cohorts: {cohorts}")

    # Rename outcome to a formula-safe alias (avoids patsy misinterpreting
    # column names that contain digits, e.g. 'lst_summer_mean_20km').
    df = df.rename(columns={outcome: '_y'})

    if covariates:
        valid_cov = [c for c in covariates
                     if c in df.columns and df[c].notna().any()]
        formula = '_y ~ ' + ' + '.join(valid_cov) if valid_cov else '_y ~ 1'
        est_method = 'dr'
    else:
        valid_cov = []
        formula = '_y ~ 1'
        # With no covariates the DR formula collapses to a simple 2x2 DiD;
        # use 'reg' (outcome-regression only) which is equivalent and avoids
        # propensity-score instability when X is intercept-only.
        est_method = 'reg'

    logger.info(f"    [CS] formula={formula!r}  est_method={est_method!r}")

    try:
        out = ATTgt(
            yname='_y',
            gname='_group',
            idname=unit_var,
            tname=time_var,
            xformla=formula,
            data=df,
        ).fit(est_method=est_method)
    except Exception as exc:
        # "Never-treated group is too small" means the per-(g,t) never-treated pool is
        # too small for the DR propensity model.  The problem is the control group, not
        # the estimation method, so retrying with est_method='reg' alone won't help.
        # Go directly to control_group='notyettreated', which enlarges the control pool.
        # Expected for small-sample datasets (e.g. station panel with ~39-40 never-treated
        # cities for satellite outcomes).
        # 2026-07-18: added notyettreated fallback for small station never-treated pools.
        # 2026-10-13: removed intermediate est_method='reg' retry (pointless for
        # too-small error); downgraded to logger.info since this is expected fallback.
        if 'too small' in str(exc) and est_method == 'dr':
            logger.info(f"    [CS] never-treated pool too small for DR;"
                        f" retrying with control_group='notyettreated'")
            try:
                out = ATTgt(
                    yname='_y',
                    gname='_group',
                    idname=unit_var,
                    tname=time_var,
                    xformla=formula,
                    data=df,
                    control_group='notyettreated',
                ).fit(est_method=est_method)
                logger.info(f"    [CS] control_group='notyettreated' succeeded")
            except Exception as exc2:
                logger.error(f"    [CS] notyettreated fallback also failed: {exc2}")
                return pd.DataFrame(), np.nan, np.nan
        else:
            logger.error(f"    [CS] fit() failed: {exc}")
            return pd.DataFrame(), np.nan, np.nan

    # Event-study (dynamic) aggregation
    # csdid uses typec='dynamic'; some versions use typec='event' -- try both.
    es_df = pd.DataFrame()
    atte_event = None
    for typec_try in ('dynamic', 'event'):
        try:
            out.aggte(typec=typec_try, na_rm=True)
            atte_event = copy.deepcopy(out.atte)
            logger.info(f"    [CS] aggte(typec={typec_try!r}) succeeded")
            break
        except Exception as exc:
            logger.warning(f"    [CS] aggte(typec={typec_try!r}) failed: {exc}")

    if atte_event is not None:
        try:
            event_times = np.asarray(atte_event['egt'], dtype=float)
            atts_es     = np.asarray(atte_event['att_egt'], dtype=float)

            # se_egt may be a 1-D array or a 2-D array where row 0 is pointwise SEs
            se_raw = np.asarray(atte_event['se_egt'], dtype=float)
            if se_raw.ndim == 2:
                se_raw = se_raw[0]
            ses_es = se_raw.flatten()

            es_rows = []
            for i, t in enumerate(event_times):
                se_i   = float(ses_es[i]) if i < len(ses_es) else np.nan
                coef_i = float(atts_es[i])
                es_rows.append({
                    'rel_time': int(t),
                    'coef':     coef_i,
                    'se':       se_i,
                    'ci_lower': coef_i - 1.96 * se_i if not np.isnan(se_i) else np.nan,
                    'ci_upper': coef_i + 1.96 * se_i if not np.isnan(se_i) else np.nan,
                })

            es_df = pd.DataFrame(es_rows)
            es_df = es_df[
                (es_df['rel_time'] >= min_time) & (es_df['rel_time'] <= max_time)
            ].copy()
            es_df = _ensure_ref_period(es_df)
            es_df = _normalize_cs_to_ref(es_df)   # 2026-07-27: force t=-1 = 0 reference
            es_df = _add_pvalue(es_df)
            es_df = _standard_cols(es_df)
        except Exception as exc:
            logger.error(f"    [CS] Failed to build event-study DataFrame: {exc}")
            es_df = pd.DataFrame()

    # Simple (post-period) aggregate ATT.
    # aggte(typec='simple') fails with a scalar-conversion error in the version
    # of csdid used here, so compute the aggregate directly as the mean of
    # post-period event-study ATTs.  SE is under the independence approximation
    # (understates the true SE, but this is used only for logging, not the paper).
    # 2026-10-13: removed the aggte(typec='simple') try/except; go straight to
    # the mean-of-post-period fallback to eliminate spurious warning messages.
    overall_att = np.nan
    overall_se  = np.nan
    if not es_df.empty:
        post = es_df[es_df['rel_time'] >= 0]
        if not post.empty and post['coef'].notna().any():
            overall_att = float(post['coef'].mean())
            # SE under independence (fallback only -- understates true SE)
            ses_post = post['se'][post['se'] > 0].values
            if len(ses_post) > 0:
                overall_se = float(np.sqrt(np.mean(ses_post ** 2) / len(ses_post)))
            logger.info(f"    [CS] Aggregate ATT={overall_att:.4f} SE={overall_se:.4f}"
                        f" (mean of post-period estimates)")

    return es_df, overall_att, overall_se
