"""
Event Study Analysis Module

Functions for running difference-in-differences event study regressions
and creating visualizations.

Main functions:
    - create_event_time_dummies: Create relative time dummy variables
    - run_event_study_regression: Run DiD regression with event time dummies
    - plot_event_study: Create event study plot with confidence intervals
    - run_simple_did: Run simple 2x2 DiD regression

Usage:
    from src.analysis.event_study import run_event_study_regression, plot_event_study
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from statsmodels.formula.api import ols
import statsmodels.api as sm


def create_event_time_dummies(panel_df, min_time=-5, max_time=10, ref_time=-1):
    """
    Create event time dummy variables for event study regression.

    Parameters
    ----------
    panel_df : pd.DataFrame
        Panel dataset with 'rel_time' column
    min_time : int
        Minimum relative time to include (default: -5)
    max_time : int
        Maximum relative time to include (default: 10)
    ref_time : int
        Reference period to omit (default: -1)

    Returns
    -------
    pd.DataFrame
        DataFrame with added dummy columns
    """
    df = panel_df.copy()

    # Create dummies for each relative time period (except reference).
    # Endpoint binning: observations outside [min_time, max_time] are assigned to
    # the nearest endpoint dummy rather than left as implicit reference observations.
    # Without this, late-treated cities (e.g. designation year 2016 or 2018) have
    # many pre-treatment years outside the window that silently look like tau=-1 to
    # the regression, inflating the baseline and biasing all post-treatment coefficients.
    # Fixed 2026-06-25.
    for t in range(min_time, max_time + 1):
        if t == ref_time:
            continue
        dummy_name = f"rel_time_{t}" if t >= 0 else f"rel_time_m{abs(t)}"
        if t == min_time:
            # Left endpoint: all observations at or before min_time map here
            df[dummy_name] = (df['rel_time'] <= t).astype(int)
        elif t == max_time:
            # Right endpoint: all observations at or beyond max_time map here
            df[dummy_name] = (df['rel_time'] >= t).astype(int)
        else:
            df[dummy_name] = (df['rel_time'] == t).astype(int)

    return df


def run_event_study_regression(panel_df, outcome='lst_summer_mean',
                                min_time=-5, max_time=10, ref_time=-1,
                                cluster_var='city_id'):
    """
    Run event study regression with relative time dummies.

    Specification:
        outcome_it = a_i + g_t + Sum_tau b_tau D_{i,tau} + e_it

    where D_{i,tau} = 1 if city i is tau periods from treatment, 0 otherwise

    Parameters
    ----------
    panel_df : pd.DataFrame
        Panel dataset with outcome and treatment variables
    outcome : str
        Name of outcome variable (default: 'lst_summer_mean')
    min_time : int
        Minimum relative time (default: -5)
    max_time : int
        Maximum relative time (default: 10)
    ref_time : int
        Reference period (default: -1, omitted from regression)
    cluster_var : str
        Variable to cluster standard errors (default: 'city_id')

    Returns
    -------
    tuple
        (model, event_time_coefficients_df)
    """
    # Filter to observations with non-missing outcome
    df = panel_df[panel_df[outcome].notna()].copy()

    # Create event time dummies
    df = create_event_time_dummies(df, min_time, max_time, ref_time)

    # Build formula
    # Only include dummies for treated cities (control cities have rel_time = NaN)
    dummy_names = []
    for t in range(min_time, max_time + 1):
        if t != ref_time:
            dummy_name = f"rel_time_{t}" if t >= 0 else f"rel_time_m{abs(t)}"
            dummy_names.append(dummy_name)

    formula = f"{outcome} ~ " + " + ".join(dummy_names) + " + C(city_id) + C(year)"

    # Run regression with clustered standard errors
    model = ols(formula, data=df).fit(
        cov_type='cluster',
        cov_kwds={'groups': df[cluster_var]}
    )

    # Extract event time coefficients
    coeffs = []
    for t in range(min_time, max_time + 1):
        if t == ref_time:
            # Reference period has coefficient = 0
            coeffs.append({
                'rel_time': t,
                'coef': 0.0,
                'se': 0.0,
                'ci_lower': 0.0,
                'ci_upper': 0.0,
                'pvalue': np.nan
            })
        else:
            dummy_name = f"rel_time_{t}" if t >= 0 else f"rel_time_m{abs(t)}"
            if dummy_name in model.params.index:
                coeffs.append({
                    'rel_time': t,
                    'coef': model.params[dummy_name],
                    'se': model.bse[dummy_name],
                    'ci_lower': model.conf_int().loc[dummy_name, 0],
                    'ci_upper': model.conf_int().loc[dummy_name, 1],
                    'pvalue': model.pvalues[dummy_name]
                })

    coeffs_df = pd.DataFrame(coeffs)

    return model, coeffs_df


def plot_event_study(coeffs_df, outcome_label="Outcome",
                    title="Event Study: Treatment Effect Over Time",
                    figsize=(12, 7), save_path=None):
    """
    Create event study plot.

    Parameters
    ----------
    coeffs_df : pd.DataFrame
        DataFrame with columns: rel_time, coef, ci_lower, ci_upper
    outcome_label : str
        Label for outcome variable (default: "Outcome")
    title : str
        Plot title
    figsize : tuple
        Figure size (default: (12, 7))
    save_path : Path or None
        Path to save figure (default: None, don't save)

    Returns
    -------
    matplotlib.figure.Figure
        Figure object
    """
    fig, ax = plt.subplots(figsize=figsize)

    # Plot point estimates
    ax.plot(coeffs_df['rel_time'], coeffs_df['coef'],
            marker='o', markersize=8, color='steelblue',
            linewidth=2, label='Point Estimate')

    # Plot confidence interval
    ax.fill_between(
        coeffs_df['rel_time'],
        coeffs_df['ci_lower'],
        coeffs_df['ci_upper'],
        alpha=0.3,
        color='steelblue',
        label='95% CI'
    )

    # Add reference lines
    ax.axhline(y=0, color='gray', linestyle='--', linewidth=1, alpha=0.7)
    ax.axvline(x=0, color='red', linestyle='--', linewidth=1.5, alpha=0.7,
              label='Treatment')

    # Formatting
    ax.set_xlabel('Years Relative to National Forest City Designation',
                 fontsize=12)
    ax.set_ylabel(f'Effect on {outcome_label}', fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.legend(loc='best', frameon=True, shadow=True)
    ax.grid(True, alpha=0.3, linestyle=':')

    # Add shading for pre/post periods
    ax.axvspan(coeffs_df['rel_time'].min(), -0.5, alpha=0.05, color='blue',
              label='Pre-treatment')
    ax.axvspan(0.5, coeffs_df['rel_time'].max(), alpha=0.05, color='green',
              label='Post-treatment')

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"  [OK] Saved plot to: {save_path}")

    return fig


def run_simple_did(panel_df, outcome='lst_summer_mean', cluster_var='city_id'):
    """
    Run simple 2x2 difference-in-differences regression.

    Specification:
        outcome_it = a + b1*treated_i + b2*post_t + b3*(treated_i x post_t) + e_it

    Parameters
    ----------
    panel_df : pd.DataFrame
        Panel dataset
    outcome : str
        Outcome variable name
    cluster_var : str
        Variable for clustering standard errors

    Returns
    -------
    statsmodels.regression.linear_model.RegressionResultsWrapper
        Regression results
    """
    # Filter to non-missing outcomes
    df = panel_df[panel_df[outcome].notna()].copy()

    # Run regression
    formula = f"{outcome} ~ treated + treated_post + treated_x_post"
    model = ols(formula, data=df).fit(
        cov_type='cluster',
        cov_kwds={'groups': df[cluster_var]}
    )

    return model


def test_parallel_trends(panel_df, outcome='lst_summer_mean', pre_periods=(-5, -1)):
    """
    Test for parallel trends in pre-treatment period.

    Tests if pre-treatment event time coefficients are jointly zero.

    Parameters
    ----------
    panel_df : pd.DataFrame
        Panel dataset
    outcome : str
        Outcome variable
    pre_periods : tuple
        Range of pre-treatment periods to test (default: (-5, -1))

    Returns
    -------
    dict
        Dictionary with test results
    """
    # Run event study
    model, coeffs_df = run_event_study_regression(
        panel_df,
        outcome=outcome,
        min_time=pre_periods[0],
        max_time=0
    )

    # Get pre-treatment coefficients
    pre_coeffs = coeffs_df[
        (coeffs_df['rel_time'] >= pre_periods[0]) &
        (coeffs_df['rel_time'] < 0)
    ]

    # Joint F-test that all pre-treatment coefficients = 0
    # (Not implemented in detail here, but would use model.f_test())

    results = {
        'pre_coeffs': pre_coeffs,
        'max_pre_coef': pre_coeffs['coef'].abs().max(),
        'mean_pre_coef': pre_coeffs['coef'].mean()
    }

    return results


# Example usage
if __name__ == "__main__":
    print("Event Study Analysis Module")
    print("=" * 70)
    print()
    print("This module provides functions for difference-in-differences analysis.")
    print()
    print("Main functions:")
    print("  - run_event_study_regression()")
    print("  - plot_event_study()")
    print("  - run_simple_did()")
    print("  - test_parallel_trends()")
    print()
    print("Use src/analysis/run_poc_analysis.py to run full analysis pipeline.")
