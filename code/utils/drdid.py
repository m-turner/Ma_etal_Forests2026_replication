"""
Doubly Robust Difference-in-Differences (CS 2021)

Implements the Callaway-Sant'Anna (2021) doubly robust ATT(g,t) estimator
for staggered adoption with a never-treated comparison group, following the
2x2 panel DRDID formula of Sant'Anna and Zhao (2020).

Core idea
---------
For each cohort g (cities designated in year g) and calendar year t:

    1. Outcome change: Δy_i = y_{i,t} - y_{i,g-1}
       The base period is always g-1 (the year before that cohort's treatment).
       This means pre-trend estimates at l = t-g < -1 are all relative to
       l = -1 (i.e. the fixed-base normalization the user asked for).

    2. Propensity score: p_g(X) = Pr(D_g=1 | D_g+C=1, X) via logistic regression,
       where D_g=1 for cohort-g cities, C=1 for never-treated cities.

    3. Outcome regression: m_0(X) = E[Δy | C=1, X] via OLS on control units.

    4. AIPW combination (doubly robust ATT):
       ATT(g,t) = E[w1 * (Δy - m0)] - E[w0 * (Δy - m0)]
       where w1 = D_g / p̄_g and w0 = (1-D_g)*p_g(X)/(1-p_g(X)) / E[...].

    5. Aggregate across cohorts at each relative time l = t-g:
       es(l) = Σ_g n_g * ATT(g, g+l) / Σ_g n_g

Standard errors
---------------
Uses the influence-function variance estimator (sandwich SE) rather than
bootstrap. This is consistent for large n but ignores estimation error from
the propensity score and outcome regression models. For our sample sizes
(400+ cities) this is an adequate approximation.

Across-cohort SE at each relative time uses the independence approximation:
Var(es(l)) ≈ Σ_g w_g^2 * Var(ATT(g,g+l))

References
----------
Callaway, B. and Sant'Anna, P.H.C. (2021). Difference-in-differences with
    multiple time periods. Journal of Econometrics, 225(2), 200-230.
Sant'Anna, P.H.C. and Zhao, J. (2020). Doubly robust difference-in-differences
    estimators. Journal of Econometrics, 219(1), 101-122.
"""

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


def _drdid_2x2(delta_y, D, X):
    """
    Doubly robust ATT for a single 2x2 panel DiD (one cohort, two periods).

    Parameters
    ----------
    delta_y : ndarray, shape (n,)
        Outcome change: y_t - y_{base} for each unit.
    D : ndarray, shape (n,)
        1 for cohort-g treated cities, 0 for never-treated controls.
    X : ndarray, shape (n, k)
        Covariate matrix INCLUDING an intercept column.

    Returns
    -------
    att : float
        Doubly robust ATT estimate.
    se : float
        Standard error (influence-function sandwich).
    psi : ndarray, shape (n,)
        Influence function contributions (for potential meta-SE computation).
    """
    n  = len(D)
    n1 = D.sum()
    n0 = n - n1

    if n1 < 3 or n0 < 3:
        return np.nan, np.nan, np.full(n, np.nan)

    # --- Step 1: propensity score via logistic regression ---
    try:
        from statsmodels.discrete.discrete_model import Logit
        logit_result = Logit(D, X).fit(disp=False, maxiter=500,
                                       method='bfgs', warn_convergence=False)
        pscore = logit_result.predict()
    except Exception:
        # Fallback: constant propensity score (reduces to standard DiD)
        pscore = np.full(n, n1 / n)

    # Trim pscores to avoid extreme weights (Crump et al. 2009 trimming)
    pscore = np.clip(pscore, 0.01, 0.99)

    # --- Step 2: outcome regression on controls ---
    X0   = X[D == 0]
    dy0  = delta_y[D == 0]
    try:
        coef = np.linalg.lstsq(X0, dy0, rcond=None)[0]
    except np.linalg.LinAlgError:
        coef = np.zeros(X.shape[1])
        coef[0] = dy0.mean()
    mu0 = X @ coef      # predicted outcome change under control for all units

    # --- Step 3: AIPW weights ---
    p_bar = n1 / n                               # unconditional Pr(D=1)
    # IPW ratio for control observations
    ipw   = (1 - D) * pscore / (1 - pscore)
    lam   = ipw.mean()                           # E[(1-D)*p/(1-p)]

    w1 = D / p_bar                               # treated weight, sums to n/n1
    # Control weight: reweighted so E[w0] = 1 over the sample
    w0 = ipw / lam if lam > 0 else np.zeros(n)

    # --- Step 4: doubly robust ATT ---
    resid = delta_y - mu0
    att   = np.mean(w1 * resid) - np.mean(w0 * resid)

    # --- Step 5: influence function (ignoring nuisance estimation noise) ---
    # Each unit's contribution: psi_i = (w1_i - w0_i)*resid_i - att
    psi     = (w1 - w0) * resid - att
    var_att = np.mean(psi ** 2) / n
    se      = np.sqrt(max(var_att, 0.0))

    return att, se, psi


def cs_event_study_dr(panel_df, outcome, covariates=None,
                      min_time=-5, max_time=10, ref_time=-1):
    """
    Callaway-Sant'Anna (2021) doubly robust event study.

    Aggregates cohort-specific ATT(g, g+l) across all cohorts g at each
    relative time l = min_time, ..., max_time.  The reference period is
    ref_time (default -1), which is set to zero by definition; all other
    periods are estimated relative to it.

    Parameters
    ----------
    panel_df : DataFrame
        Long-format panel with columns:
            city_id, year, treated, designation_year, <outcome>, <covariates>
        Never-treated cities should have treated=0, designation_year=NaN.
    outcome : str
        Name of the outcome column.
    covariates : list of str or None
        Names of covariate columns for propensity score and outcome regression.
        Must be non-missing for all cities.  If None or empty, the estimator
        reduces to plain Callaway-Sant'Anna without covariate adjustment:
        the intercept-only propensity score and outcome regression collapse
        the AIPW formula to a simple 2x2 DiD within each cohort-time cell.
    min_time : int
        Minimum relative time (e.g. -5 means 5 years before treatment).
    max_time : int
        Maximum relative time.
    ref_time : int
        Reference period (excluded; coefficient set to 0). Default -1.

    Returns
    -------
    DataFrame with columns:
        rel_time, coef, se, ci_lower, ci_upper, pvalue, n_cohorts, n_cities
    """
    # Normalise: None means no covariate adjustment (plain CS DiD)
    if covariates is None:
        covariates = []

    # Drop rows where outcome or any covariate is missing
    needed_cols = [outcome] + covariates
    df = panel_df.dropna(subset=needed_cols).copy()

    # City-level cross-section: one row per city with covariates + treatment info
    city_cols = ['city_id', 'treated', 'designation_year'] + covariates
    city_meta = (df[city_cols]
                 .drop_duplicates('city_id')
                 .set_index('city_id'))

    # Identify cohorts: unique designation years among treated cities
    cohorts = sorted(
        city_meta.loc[city_meta['treated'] == 1, 'designation_year']
        .dropna().unique().astype(int)
    )
    if not cohorts:
        print("  ERROR: No treated cohorts found in panel.")
        return pd.DataFrame()

    # Pivot outcome to wide format: city_id x year
    y_wide = (df[['city_id', 'year', outcome]]
              .pivot(index='city_id', columns='year', values=outcome))

    results = []

    for l in range(min_time, max_time + 1):

        # Reference period: coefficient is zero by construction
        if l == ref_time:
            results.append({
                'rel_time': l, 'coef': 0.0, 'se': 0.0,
                'ci_lower': 0.0, 'ci_upper': 0.0,
                'pvalue': np.nan, 'n_cohorts': 0, 'n_cities': 0
            })
            continue

        cohort_atts  = []
        cohort_ses   = []
        cohort_ns    = []

        for g in cohorts:
            t    = g + l          # calendar year corresponding to relative time l
            base = g - 1          # always g-1, giving fixed-base normalization

            # Skip if either calendar year falls outside the study panel
            if t not in y_wide.columns or base not in y_wide.columns:
                continue

            # Cohort-g treated cities + never-treated controls
            treated_ids = city_meta[city_meta['designation_year'] == g].index
            control_ids = city_meta[city_meta['treated'] == 0].index
            all_ids     = treated_ids.union(control_ids)

            sub_meta = city_meta.loc[city_meta.index.isin(all_ids)]
            delta_y  = (y_wide.loc[sub_meta.index, t]
                        - y_wide.loc[sub_meta.index, base])

            # Drop units with missing outcomes in either period
            valid    = delta_y.notna()
            sub_meta = sub_meta[valid]
            delta_y  = delta_y[valid].values

            n_g = (sub_meta['designation_year'] == g).sum()  # actual treated count
            if n_g < 3 or (len(sub_meta) - n_g) < 3:
                continue

            D = (sub_meta['designation_year'] == g).astype(float).values

            # Build covariate matrix with intercept.
            # When no covariates are requested X is intercept-only, which
            # reduces the AIPW formula to a plain 2x2 DiD.
            n_obs = len(sub_meta)
            if covariates:
                cov_mat = sub_meta[covariates].values.astype(float)
                # Standardize for better logit convergence (OLS absorbs scale)
                col_std = cov_mat.std(axis=0)
                col_std[col_std == 0] = 1.0
                cov_std = (cov_mat - cov_mat.mean(axis=0)) / col_std
                X = np.column_stack([np.ones(n_obs), cov_std])
            else:
                X = np.ones((n_obs, 1))

            att, se, _ = _drdid_2x2(delta_y, D, X)

            if not np.isnan(att):
                cohort_atts.append(att)
                cohort_ses.append(se)
                cohort_ns.append(n_g)

        if not cohort_atts:
            # No eligible cohort-time pairs for this relative time
            continue

        # Weighted average of cohort-specific ATTs (weights = cohort size)
        ns     = np.array(cohort_ns, dtype=float)
        W      = ns / ns.sum()
        att_l  = float(np.dot(W, cohort_atts))

        # SE under cross-cohort independence approximation
        ses    = np.array(cohort_ses)
        se_l   = float(np.sqrt(np.dot(W ** 2, ses ** 2)))

        z      = att_l / se_l if se_l > 0 else np.nan
        pvalue = float(2 * scipy_stats.norm.sf(abs(z))) if not np.isnan(z) else np.nan

        results.append({
            'rel_time':  l,
            'coef':      att_l,
            'se':        se_l,
            'ci_lower':  att_l - 1.96 * se_l,
            'ci_upper':  att_l + 1.96 * se_l,
            'pvalue':    pvalue,
            'n_cohorts': len(cohort_atts),
            'n_cities':  int(ns.sum()),
        })

    coeffs_df = pd.DataFrame(results).sort_values('rel_time').reset_index(drop=True)
    return coeffs_df
