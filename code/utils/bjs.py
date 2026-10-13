"""
Borusyak-Jaravel-Spiess (2024) Imputation Estimator

Implements the BJS staggered DiD event-study estimator.  Used to produce
event-study plots directly comparable to Ma et al. (2026, Forests), which
applies BJS as a robustness check alongside their main TWFE estimates.

Algorithm (BJS 2024, Section 3)
---------------------------------
Stage 1 -- Two-way FE on "clean" observations only.
    "Clean" = never-treated cities in ALL years
              + eventually-treated cities in ALL pre-treatment years.
    Fit:  Y_{it} = alpha_i + gamma_t + eps_{it}   (clean obs only)
    This is solved via linearmodels PanelOLS (entity_effects=True + explicit
    year dummies), which uses within-group demeaning -- the same algorithm
    as Stata's absorb.  The prior statsmodels OLS-with-dummies approach was
    ill-conditioned on unbalanced panels (~300 cities x 25 years).

Stage 2 -- Impute counterfactuals for treated observations.
    Y_hat_{it} = alpha_hat_i + gamma_hat_t   (from stage-1 model)
    tau_hat_{it} = Y_{it} - Y_hat_{it}
    For pre-treatment treated obs (in the training set), tau_hat is the
    in-sample residual (small but not zero).  For post-treatment treated obs
    (out of sample for stage 1), tau_hat is the genuine treatment effect.

Stage 3 -- Aggregate into event-study coefficients.
    OLS of tau_hat on event-time dummies, including observations at the
    reference period (ref_time) but omitting its dummy.  The intercept
    then equals the mean tau_hat at ref_time, and each coefficient measures
    the deviation of mean tau_hat at that period from the reference.
    Standard errors are clustered at the unit level.

Key differences from Callaway-Sant'Anna (CS DR):
    - BJS does not restrict comparisons by cohort.  All never-treated cities
      pool into a single "donor" set for all treated cohorts.
    - BJS is more efficient but relies on a uniform parallel-trends assumption
      (trends must be parallel for ALL cohorts simultaneously, not just
      cohort by cohort).
    - Pre-treatment BJS coefficients represent in-sample residuals from the
      stage-1 model (close to zero by construction); they are a less
      powerful pre-trend test than OLS event-study pre-trends.

Reference
---------
Borusyak, K., Jaravel, X., Spiess, J. (2024). Revisiting event-study
designs: Robust and efficient estimation. Review of Economic Studies,
91(6), 3253-3285.
"""

import numpy as np
import pandas as pd
from statsmodels.formula.api import ols   # still used for Stage-3 event-study OLS


def bjs_event_study(
    panel,
    outcome,
    unit_var='city_id',
    time_var='year',
    treatment_var='treated',
    rel_time_var='rel_time',
    desig_year_var='designation_year',
    min_time=-5,
    max_time=10,
    ref_time=-1,
    cluster_var='city_id',
    covariates=None,
    never_treated_only=False,
):
    """
    BJS (2024) imputation estimator event-study.

    Parameters
    ----------
    panel : pd.DataFrame
        Long-format panel.  Required columns: outcome, unit_var, time_var,
        treatment_var (1=ever-treated, 0=never-treated), rel_time_var
        (years relative to designation; NaN for never-treated).
    outcome : str
        Outcome column name.
    unit_var : str
        Unit (city) identifier column.
    time_var : str
        Calendar time (year) column.
    treatment_var : str
        Binary indicator: 1 if ever treated, 0 if never treated.
    rel_time_var : str
        Years relative to designation year.  NaN for never-treated units.
    desig_year_var : str
        Designation year column (used to count cohorts per period).
    min_time, max_time : int
        Event-study window included in stage-3 aggregation.
    ref_time : int
        Reference period.  Its coefficient is normalized to 0.
    cluster_var : str
        Column on which to cluster standard errors in stage 3.
    covariates : list of str or None
        Optional covariate columns to include in the stage-1 two-way FE
        regression (e.g. ['lat', 'lon', 'log_pop_pretx']).  When provided,
        the stage-1 model becomes:
            Y_it = alpha_i + gamma_t + X_i * beta_t + eps_it
        (covariate x year interactions, since time-invariant covariates are
        collinear with city FEs in levels).
    never_treated_only : bool, default False
        If True, stage-1 TWFE is fit on never-treated units only (treatment_var==0).
        If False (default), stage-1 also includes pre-treatment obs of treated units
        (never-treated + pre-treatment treated = standard BJS 2024 definition).
        # 2026-07-18: Added to allow direct comparison with diff_diff.ImputationDiD,
        # which appears to use never-treated-only stage-1 controls.

    Returns
    -------
    pd.DataFrame  (empty if estimation fails)
        Columns: rel_time, coef, se, ci_lower, ci_upper, pvalue,
                 n_cities, n_cohorts.
        One row per integer period in [min_time, max_time].
    """
    # Drop rows with missing outcome
    df = panel[panel[outcome].notna()].copy()

    # ------------------------------------------------------------------
    # Stage 1: Two-way FE on clean observations via linearmodels PanelOLS
    # ------------------------------------------------------------------
    # 2026-07-24: Replaced statsmodels OLS with dummies with linearmodels
    # PanelOLS (entity_effects=True + explicit year dummies as X).
    # PanelOLS uses within-group demeaning -- the same algorithm as Stata's
    # absorb -- and is numerically stable on unbalanced panels.  The prior
    # OLS-with-dummies approach had an ill-conditioned ~325-column design
    # matrix that caused Stage-2 counterfactuals to diverge from Stata
    # did_imputation by up to 0.8 units for some outcomes (tree_cover,
    # lst_summer_max_20km etc.).  Previous version saved in _old/.

    # never_treated_only=False (default, BJS 2024): clean = never-treated
    #   in all years + eventually-treated in pre-treatment years only.
    # never_treated_only=True: clean = never-treated only.  Pre-period
    #   estimates become out-of-sample pre-trend tests.
    if never_treated_only:
        is_clean = (df[treatment_var] == 0)
    else:
        is_clean = (df[treatment_var] == 0) | (
            (df[treatment_var] == 1) & (df[rel_time_var] < 0)
        )
    clean = df[is_clean].copy()

    if clean[unit_var].nunique() < 5 or len(clean) < 30:
        return pd.DataFrame()

    # Rename outcome to a formula-safe name (avoids hyphens in Stage-3 OLS)
    clean = clean.rename(columns={outcome: '_y'})

    # Determine available covariates up-front (needed in both Stage-1 and Stage-2)
    available_cov = []
    if covariates:
        available_cov = [c for c in covariates
                         if c in clean.columns and clean[c].notna().any()]
        if available_cov:
            print(f'    [BJS] Stage-1 covariate x time interactions: {available_cov}')

    # Import linearmodels (required for numerically stable TWFE on unbalanced panels)
    try:
        from linearmodels.panel import PanelOLS as _PanelOLS
    except ImportError:
        print('    [BJS] linearmodels is not installed. '
              'Install with: conda install -c conda-forge linearmodels')
        return pd.DataFrame()

    # Build X for PanelOLS: explicit year dummies + optional covariate x year interactions.
    # Entity FEs (alpha_hat_i) are absorbed by entity_effects=True.
    # The first year (ref_year_s1) is dropped; its gamma is 0 by convention.
    years_in_clean = sorted(clean[time_var].unique())
    ref_year_s1 = years_in_clean[0]

    clean_lm = clean.copy()
    x_cols = []
    for yr in years_in_clean:
        if yr == ref_year_s1:
            continue
        t_col = f'_yr_{yr}'
        clean_lm[t_col] = (clean_lm[time_var] == yr).astype(float)
        x_cols.append(t_col)
        for cov in available_cov:
            xt_col = f'_{cov}_x_{yr}'
            clean_lm[xt_col] = clean_lm[cov] * clean_lm[t_col]
            x_cols.append(xt_col)

    # PanelOLS requires at least one regressor.  If only one year exists (no
    # time dummies generated), use a constant and absorb time effects internally.
    if not x_cols:
        clean_lm['_const'] = 1.0
        x_cols = ['_const']
        time_absorbed = True
    else:
        time_absorbed = False

    # Set MultiIndex (entity, time) required by linearmodels
    clean_lm = clean_lm.set_index([unit_var, time_var])

    try:
        model_lm = _PanelOLS(
            clean_lm['_y'],
            clean_lm[x_cols],
            entity_effects=True,
            time_effects=time_absorbed,   # True only if no explicit time dummies
            drop_absorbed=True,
        )
        results_lm = model_lm.fit()
    except Exception as exc:
        print(f'    [BJS] Stage-1 PanelOLS failed: {exc}')
        return pd.DataFrame()

    # Extract entity FEs: alpha_hat_i per unit (identical across all time periods
    # within an entity; take mean to collapse the MultiIndex to one row per entity)
    entity_fes = (
        results_lm.estimated_effects.iloc[:, 0]
        .groupby(level=0)   # level 0 = entity (unit_var)
        .mean()
    )
    lm_params = dict(results_lm.params)   # gamma_hat_t and cov x year coefs

    # ------------------------------------------------------------------
    # Stage 2: Impute counterfactuals for all treated obs in the window
    # ------------------------------------------------------------------
    # 2026-07-18: Split into two paths depending on never_treated_only.
    #
    # Standard path (never_treated_only=False):
    #   Treated city FEs are in Stage-1 (pre-treatment obs were clean).
    #   entity_fes[i] = alpha_hat_i directly.
    #   y_hat_it = entity_fes[i] + lm_params['_yr_{t}'] + cov contributions
    #
    # Never-treated-only path (never_treated_only=True):
    #   Treated city FEs are NOT in Stage-1.  Two-step approach:
    #   (a) baseline_t = lm_params['_yr_{t}'] + covariate contributions for city i
    #   (b) alpha_hat_i = mean pre-treatment residual (y_is - baseline_s)
    #   (c) y_hat_it = alpha_hat_i + baseline_t

    treated = df[df[treatment_var] == 1].copy()
    in_window = (
        treated[rel_time_var].notna()
        & (treated[rel_time_var] >= min_time)
        & (treated[rel_time_var] <= max_time)
    )
    treated = treated[in_window].copy()

    if len(treated) == 0:
        return pd.DataFrame()

    trained_years = set(clean[time_var].unique())
    # Drop event-window years not covered by the clean panel
    missing_years = set(treated[time_var].unique()) - trained_years
    if missing_years:
        treated = treated[treated[time_var].isin(trained_years)]
    if len(treated) == 0:
        return pd.DataFrame()

    def _gamma_t(yr):
        """Time FE for year yr (0 for the reference year dropped from X)."""
        return lm_params.get(f'_yr_{yr}', 0.0)

    def _cov_contribution(row):
        """Sum of covariate x time interaction terms for one observation."""
        total = 0.0
        yr = row[time_var]
        for cov in available_cov:
            coef = lm_params.get(f'_{cov}_x_{yr}', 0.0)
            total += coef * row.get(cov, 0.0)
        return total

    if never_treated_only:
        # Baseline: gamma_hat_t + covariate x time contributions for city i.
        # Treated city FE is estimated from pre-treatment residuals below.
        treated['_baseline'] = treated[time_var].apply(_gamma_t)
        if available_cov:
            treated['_baseline'] += treated.apply(_cov_contribution, axis=1)

        # Estimate each treated city's FE from pre-treatment residuals
        pre_mask = treated[rel_time_var] < 0
        pre_data = treated[pre_mask].copy()
        pre_data['_pretx_resid'] = pre_data[outcome] - pre_data['_baseline']
        alpha_hat = pre_data.groupby(unit_var)['_pretx_resid'].mean()

        treated['_alpha_hat'] = treated[unit_var].map(alpha_hat)
        treated = treated.dropna(subset=['_alpha_hat'])
        if len(treated) == 0:
            print('    [BJS] No treated cities with pre-treatment data '
                  '(never_treated_only path).')
            return pd.DataFrame()

        treated['_y_hat'] = treated['_alpha_hat'] + treated['_baseline']

    else:
        # Standard path: entity_fes[i] = alpha_hat_i (estimated from pre-treatment clean obs)
        trained_units = set(entity_fes.index)
        missing_units = set(treated[unit_var].unique()) - trained_units
        if missing_units:
            treated = treated[treated[unit_var].isin(trained_units)]
        if len(treated) == 0:
            return pd.DataFrame()

        # Vectorized prediction: y_hat = alpha_hat_i + gamma_hat_t + cov contributions
        treated['_alpha_hat'] = treated[unit_var].map(entity_fes)
        treated['_gamma_hat'] = treated[time_var].apply(_gamma_t)
        treated['_y_hat'] = treated['_alpha_hat'] + treated['_gamma_hat']
        if available_cov:
            treated['_y_hat'] += treated.apply(_cov_contribution, axis=1)

    treated['_tau_hat'] = treated[outcome] - treated['_y_hat']

    # 2026-07-23: Compute the ATT directly from Stage-2 residuals so it does
    # not depend on the Stage-3 reference-period normalization.  The Stage-3
    # OLS gives coef_k = tau_k - tau_{-1}, and adding the intercept back
    # (tau_{-1}) should recover mean(tau_k).  However, if some treated cities
    # have MISSING data at k=-1 their tau_hat at that period is an out-of-
    # sample prediction rather than an in-sample residual, making the
    # intercept large and biased (e.g. +0.33 for tree_cover, -0.24 for
    # lst_20km), which flips the sign of the ATT.  The direct Stage-2 mean
    # bypasses this entirely and matches Stata did_imputation directly.
    # Period-weighted (each period has equal weight regardless of n_obs per
    # period) to match Stata's convention.
    _post_mask = treated[rel_time_var].between(0, max_time)
    if _post_mask.sum() > 0:
        _direct_att = float(
            treated[_post_mask].groupby(rel_time_var)['_tau_hat'].mean().mean()
        )
    else:
        _direct_att = float('nan')

    # ------------------------------------------------------------------
    # Stage 3: OLS of tau_hat on event-time dummies (clustered SEs)
    # ------------------------------------------------------------------
    # Include all treated obs in the window; omit the dummy for ref_time
    # but keep those observations so the intercept = mean tau_hat at ref_time.
    # Coefficient at t = deviation of mean tau_hat at t from the ref period.

    # BJS does not normalize to a reference period.  Include all event-time
    # dummies (no intercept) so each coefficient = mean tau_hat at that period,
    # reported in absolute terms relative to the panel baseline.  This differs
    # from OLS TWFE and CS/DR which conventionally set t=-1 to zero.
    # 2026-07-27: removed ref_time exclusion (was wrong for BJS -- it is only
    # appropriate for OLS TWFE and CS/DR estimators).
    event_times = list(range(min_time, max_time + 1))
    dummy_cols = []
    for t in event_times:
        col = f'_et_{t}' if t >= 0 else f'_et_m{abs(t)}'
        treated[col] = (treated[rel_time_var] == t).astype(float)
        dummy_cols.append(col)

    # '~ 0 + dummies' suppresses the intercept so each coefficient is the
    # mean tau_hat for that event time (absolute, not relative to any reference).
    formula_s3 = '_tau_hat ~ 0 + ' + ' + '.join(dummy_cols)
    try:
        model_s3 = ols(formula_s3, data=treated).fit(
            cov_type='cluster',
            cov_kwds={'groups': treated[cluster_var]}
        )
    except Exception as exc:
        print(f'    [BJS] Stage-3 estimation failed: {exc}')
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # Extract coefficients and build output table
    # ------------------------------------------------------------------
    # 2026-07-27: No intercept in Stage-3, so stage3_intercept = 0.  Each
    # coefficient is the mean tau_hat at that event time (absolute, not relative
    # to any reference period -- BJS convention).
    stage3_intercept = 0.0

    ci = model_s3.conf_int()

    rows = []
    for t in range(min_time, max_time + 1):
        col = f'_et_{t}' if t >= 0 else f'_et_m{abs(t)}'
        if col not in model_s3.params.index:
            # Period not present in data (e.g. sparse cohorts)
            continue

        period_obs = treated[treated[rel_time_var] == t]
        n_cities = period_obs[unit_var].nunique()
        n_cohorts = (period_obs[desig_year_var].dropna().nunique()
                     if desig_year_var in period_obs.columns else np.nan)

        rows.append({
            'rel_time':  t,
            'coef':      model_s3.params[col],
            'se':        model_s3.bse[col],
            'ci_lower':  ci.loc[col, 0],
            'ci_upper':  ci.loc[col, 1],
            'pvalue':    model_s3.pvalues[col],
            'n_cities':  n_cities,
            'n_cohorts': n_cohorts,
        })

    result = pd.DataFrame(rows)
    # Store Stage-3 intercept as DataFrame metadata so aggregate-ATT callers
    # can recover absolute tau_k values (coef + intercept) without changing
    # the event-study coefficients used for plotting.
    result.attrs['stage3_intercept'] = stage3_intercept
    result.attrs['direct_att'] = _direct_att   # 2026-07-23: use this for ATT; see note above
    return result
