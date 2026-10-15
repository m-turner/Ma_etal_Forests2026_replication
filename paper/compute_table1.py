"""
Compute Table 1 for the NFCP comment paper.

Updated 2026-07-09: reads library-computed aggregate ATTs from
analysis/tables/aggregate_atts.csv (written by run_analysis_v4.py).
Standard errors are now from diff_diff.ImputationDiD (BJS) and
csdid.ATTgt (CS DR / CS) -- no post-hoc independence approximation.
Previous version (with ad-hoc SE) saved in _old/ as compute_table1_v1.py.

Updated 2026-10-14: reads BJS-DR estimates from aggregate_atts.csv (Python
pipeline) and CS / CS-DR estimates from stata_aggregate_atts.csv (Stata).
Stata cannot produce standard errors for the BJS nose (imputed pre-period)
option, so Python is the only source for BJS with controls.  CS and CS-DR
use Stata (csdid) as the preferred implementation.  The "BJS 20km" (BJS
without controls) column was dropped from the paper and is removed here.
Previous version saved in _old/ as compute_table1_v1.py.

Outputs
-------
Prints a LaTeX-ready table body to stdout and writes paper/table1_values.csv.

Usage
-----
    python paper/compute_table1.py

Environment: environment_stats (or any Python 3 with pandas/numpy/scipy)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

ROOT   = Path(__file__).parent.parent
TABLES = ROOT / "analysis" / "tables"

# Source files for each estimator group
PYTHON_AGG_FILE = TABLES / "aggregate_atts.csv"        # BJS with controls (nose SE)
STATA_AGG_FILE  = TABLES / "stata_aggregate_atts.csv"  # CS and CS DR (csdid variance)

# ---------------------------------------------------------------------------
# Table 1 structure
# ---------------------------------------------------------------------------
# Each column maps a display label to (estimator key, buffer suffix).
# Estimators sourced from Python vs Stata:
#   PYTHON: bjs_dr -- Stata cannot produce SE for the nose (imputed pre-period) option
#   STATA:  cs_dr, cs -- preferred Stata csdid implementation
PYTHON_ESTIMATORS = {"bjs_dr"}
STATA_ESTIMATORS  = {"cs_dr", "cs"}

COLUMNS = [
    ("BJS-DR 20km", "bjs_dr", "20km"),
    ("BJS-DR 5km",  "bjs_dr", "5km"),
    ("CS DR 20km",  "cs_dr",  "20km"),
    ("CS 20km",     "cs",     "20km"),
]

OUTCOMES = [
    ("lst_summer_mean",  r"LST mean ($^\circ$C)"),
    ("ndvi_mean",        r"NDVI (index)"),
    ("tree_cover_mean",  r"Tree cover (\%)"),
    ("clcd_forest_pct",  r"CLCD forest (\%)"),
]

# Significance thresholds consistent with the table note:
# * p<0.10  ** p<0.05  *** p<0.01
STAR_THRESHOLDS = [(0.01, "***"), (0.05, "**"), (0.10, "*")]


def add_stars(att, se):
    """Return significance stars for a given ATT and SE."""
    if np.isnan(att) or np.isnan(se) or se <= 0:
        return ""
    p = float(2 * scipy_stats.norm.sf(abs(att / se)))
    for thresh, marker in STAR_THRESHOLDS:
        if p < thresh:
            return marker
    return ""


def main():
    # Load Python aggregate ATTs (BJS with controls / nose SE).
    # aggregate_atts.csv uses 'outcome_col' as the key; rename to 'outcome_buffer'
    # to match the Stata file schema and allow a single lookup path below.
    if not PYTHON_AGG_FILE.exists():
        print(f"ERROR: {PYTHON_AGG_FILE} not found.")
        print("Run: python code/run_analysis/run_analysis_v6.py")
        sys.exit(1)
    python_agg = pd.read_csv(PYTHON_AGG_FILE).rename(
        columns={"outcome_col": "outcome_buffer"}
    )

    # Load Stata aggregate ATTs (CS and CS-DR via csdid).
    # Missing = Stata not yet run; those cells will show NaN / [TBD].
    stata_agg = None
    if STATA_AGG_FILE.exists():
        stata_agg = pd.read_csv(STATA_AGG_FILE)
    else:
        print(f"WARNING: {STATA_AGG_FILE} not found.")
        print("Run: python code/run_analysis_stata/run_analysis_stata_v1.py")
        print("CS DR and CS cells will show [TBD] in the table.")

    # Build table rows: one entry per (outcome_base, column)
    table_rows = []
    for outcome_base, outcome_label in OUTCOMES:
        for col_label, estimator_key, buffer in COLUMNS:
            outcome_col = f"{outcome_base}_{buffer}"

            # Choose the right source for this estimator
            if estimator_key in PYTHON_ESTIMATORS:
                source = python_agg
            elif stata_agg is not None:
                source = stata_agg
            else:
                att, se = np.nan, np.nan
                table_rows.append({
                    "outcome":       outcome_base,
                    "outcome_label": outcome_label,
                    "column":        col_label,
                    "estimator":     estimator_key,
                    "buffer":        buffer,
                    "att":           att,
                    "se":            se,
                })
                continue

            match = source[
                (source["outcome_buffer"] == outcome_col) &
                (source["estimator"]      == estimator_key)
            ]
            if match.empty:
                att, se = np.nan, np.nan
            else:
                att = float(match["att"].values[0])
                se  = float(match["se"].values[0])
            table_rows.append({
                "outcome":       outcome_base,
                "outcome_label": outcome_label,
                "column":        col_label,
                "estimator":     estimator_key,
                "buffer":        buffer,
                "att":           att,
                "se":            se,
            })

    results = pd.DataFrame(table_rows)

    # Save CSV for reference
    out_csv = Path(__file__).parent / "table1_values.csv"
    results.to_csv(out_csv, index=False)
    print(f"Saved: {out_csv}")

    # ---------------------------------------------------------------------------
    # LaTeX table body
    # ---------------------------------------------------------------------------
    col_labels = [c[0] for c in COLUMNS]
    header = " & ".join(["Outcome"] + [f"({i+1}) {c}" for i, c in enumerate(col_labels)])

    print("\n% ---- Table 1 LaTeX body ----")
    print(r"\begin{tabular}{l" + "c" * len(COLUMNS) + "}")
    print(r"\toprule")
    print(header + r" \\")
    print(r"\midrule")

    for outcome_base, outcome_label in OUTCOMES:
        sub = results[results["outcome"] == outcome_base]

        coef_cells = []
        se_cells   = []
        for col_label, _, _ in COLUMNS:
            row = sub[sub["column"] == col_label]
            if row.empty or pd.isna(row["att"].values[0]):
                coef_cells.append("[TBD]")
                se_cells.append("")
            else:
                att = row["att"].values[0]
                se  = row["se"].values[0]
                s   = add_stars(att, se)
                # Format: $-0.185^{***}$  or  $-0.185$
                if s:
                    coef_cells.append(f"${att:.3f}^{{{s}}}$")
                else:
                    coef_cells.append(f"${att:.3f}$")
                se_cells.append(f"$({se:.3f})$")

        print(outcome_label + " & " + " & ".join(coef_cells) + r" \\")
        print(" & " + " & ".join(se_cells) + r" \\[4pt]")

    print(r"\bottomrule")
    print(r"\end{tabular}")

    # Warn about any missing cells
    missing = results[results["att"].isna()]
    if not missing.empty:
        print("\nWARNING -- missing estimates:")
        for _, r in missing.iterrows():
            print(f"  {r['column']} x {r['outcome']} ({r['estimator']} {r['buffer']})")


if __name__ == "__main__":
    main()
