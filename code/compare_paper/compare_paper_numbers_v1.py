# compare_paper_numbers_v1.py
#
# Environment: environment_stats (conda)
#
# Requires (inputs from earlier steps):
#   - analysis/tables/aggregate_atts.csv               (produced by Step 1)
#   - analysis/tables/stata_aggregate_atts.csv          (produced by Step 2)
#   - analysis/tables/station_event_study_bjs_dr_*.csv  (produced by Step 3)
#
# Produces (outputs):
#   (none -- exits 0 on pass, non-zero on fail)
#
# Purpose:
#   Verifies that a fresh estimation run reproduces the numbers in Table 1
#   of the paper.  Paper values are hardcoded below from comment_nfcp.tex.
#   Exits non-zero if any cell exceeds the rounding tolerance for its
#   printed precision, so run_replication.ps1 marks Step C as FAILED.
#
# How column 6 ATT and SE are computed from fresh data:
#   ATT = direct_att column in station_event_study_bjs_dr_<slug>.csv
#         (aggregate ATT from Stage-2 residuals)
#   SE  = mean of period-level SE for post-designation periods (rel_time >= 0)
#         in the same event-study file

import sys
import logging
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPL_ROOT  = Path(__file__).resolve().parent.parent.parent   # replication/
PYTHON_AGG = REPL_ROOT / "analysis" / "tables" / "aggregate_atts.csv"
STATA_AGG  = REPL_ROOT / "analysis" / "tables" / "stata_aggregate_atts.csv"
STN_DIR    = REPL_ROOT / "analysis" / "tables"

# ---------------------------------------------------------------------------
# Table 1 reference values (hardcoded from paper/comment_nfcp.tex)
#
# Columns: (1) BJS w/ctrl 20km  (2) BJS w/ctrl 5km  (3) DR 20km
#          (4) CS 20km           (5) CS pref          (6) BJS w/ctrl stn
#
# Each cell: (att, se, att_decimal_places, se_decimal_places)
# None = '---' (outcome not available for that column)
# ---------------------------------------------------------------------------
PAPER = {
    # row label : list of 6 cells
    "LST":   [( 0.032, 0.106, 3, 3),
              ( 0.016, 0.121, 3, 3),
              ( 0.010, 0.093, 3, 3),
              ( 0.051, 0.095, 3, 3),
              ( 0.055, 0.087, 3, 3),
              ( 0.114, 0.184, 3, 3)],
    "NDVI":  [( 0.002, 0.002, 3, 3),
              (-0.001, 0.003, 3, 3),
              ( 0.000, 0.002, 3, 3),
              ( 0.000, 0.002, 3, 3),
              (-0.001, 0.002, 3, 3),
              (-0.019, 0.018, 3, 3)],
    "Tree":  [( 0.095, 0.287, 3, 3),
              ( 0.047, 0.380, 3, 3),
              (-0.116, 0.248, 3, 3),
              (-0.196, 0.265, 3, 3),
              (-0.146, 0.253, 3, 3),
              (-1.060, 0.998, 3, 3)],
    "CLCD":  [(-0.194, 0.301, 3, 3),
              (-0.585, 0.319, 3, 3),
              (-0.096, 0.201, 3, 3),
              (-0.263, 0.196, 3, 3),
              (-0.188, 0.178, 3, 3),
              (-2.41,  2.07,  2, 2)],
    "Hot":   [None, None, None, None, None,
              ( 1.602, 2.062, 3, 3)],
}

# ---------------------------------------------------------------------------
# Column definitions: how to fetch fresh ATT and SE for each column
#
# (source, estimator, buffer)
#   source 'python'  -> PYTHON_AGG,  key column: outcome_col
#   source 'stata'   -> STATA_AGG,   key column: outcome_buffer
#   source 'station' -> station_event_study_bjs_dr_<slug>.csv
# ---------------------------------------------------------------------------
COLUMNS = [
    ("python",   "bjs_dr", "20km"),
    ("python",   "bjs_dr", "5km"),
    ("stata",    "cs_dr",  "20km"),
    ("stata",    "cs",     "20km"),
    ("stata",    "cs",     "pref"),
    ("station",  None,     None),
]

# Row definitions: (label, city_outcome_base, station_slug)
# city_outcome_base + '_' + buffer = outcome key in aggregate CSVs
# None = not in city-level analysis
ROWS = [
    ("LST",  "lst_summer_mean", "tmax-summer-mean"),
    ("NDVI", "ndvi_mean",       "ndvi-summer-mean-nearest"),
    ("Tree", "tree_cover_mean", "tree-cover-nearest"),
    ("CLCD", "clcd_forest_pct", "clcd-forest-pct-100m"),
    ("Hot",  None,              "hot-days"),
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger()

# ---------------------------------------------------------------------------
# Fresh-value loaders
# ---------------------------------------------------------------------------

def load_python_agg():
    df = pd.read_csv(PYTHON_AGG)
    return df.set_index(["outcome_col", "estimator"])


def load_stata_agg():
    df = pd.read_csv(STATA_AGG)
    return df.set_index(["outcome_buffer", "estimator"])


def load_station_bjs(slug):
    """Return (direct_att, mean_post_se) from the station event-study CSV."""
    path = STN_DIR / f"station_event_study_bjs_dr_{slug}.csv"
    if not path.exists():
        return None, None
    df = pd.read_csv(path)
    att = float(df["direct_att"].iloc[0])
    se  = float(df[df["rel_time"] >= 0]["se"].mean())
    return att, se

# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def tol(ndp):
    """Rounding tolerance for a value printed with ndp decimal places."""
    return 0.5 * 10 ** (-ndp) + 1e-9


def run_comparison():
    for path, label in [(PYTHON_AGG, "aggregate_atts.csv"),
                        (STATA_AGG,  "stata_aggregate_atts.csv")]:
        if not path.exists():
            log.error(f"ERROR: Required file missing: {label}")
            sys.exit(1)

    py_df = load_python_agg()
    st_df = load_stata_agg()

    log.info("")
    log.info("Comparing fresh estimates against hardcoded Table 1 values")
    log.info("")
    log.info(f"  {'':6s}  {'col':>3s}  {'paper ATT':>10s}  {'fresh ATT':>10s}"
             f"  {'diff':>8s}  {'ATT':>4s}"
             f"  {'paper SE':>9s}  {'fresh SE':>9s}  {'SE':>4s}")
    log.info("  " + "-" * 75)

    n_pass = n_fail = n_skip = 0

    for row_label, city_base, stn_slug in ROWS:
        paper_cells = PAPER[row_label]

        for col_idx, (source, estimator, buf) in enumerate(COLUMNS):
            col_num    = col_idx + 1
            paper_cell = paper_cells[col_idx]

            if paper_cell is None:
                log.info(f"  {row_label:6s}  {col_num:>3d}  {'---':>10s}"
                         f"  {'---':>10s}  {'---':>8s}  SKIP"
                         f"  {'---':>9s}  {'---':>9s}  SKIP")
                n_skip += 2
                continue

            paper_att, paper_se, att_ndp, se_ndp = paper_cell

            # Fetch fresh values
            fresh_att = fresh_se = None

            if source == "python":
                key = (f"{city_base}_{buf}", estimator)
                if key in py_df.index:
                    row = py_df.loc[key]
                    fresh_att = float(row["att"])
                    fresh_se  = float(row["se"])

            elif source == "stata":
                key = (f"{city_base}_{buf}", estimator)
                if key in st_df.index:
                    row = st_df.loc[key]
                    fresh_att = float(row["att"])
                    fresh_se  = float(row["se"])

            elif source == "station":
                fresh_att, fresh_se = load_station_bjs(stn_slug)

            if fresh_att is None:
                log.info(f"  {row_label:6s}  {col_num:>3d}  {paper_att:>+10.4f}"
                         f"  {'N/A':>10s}  {'N/A':>8s}  SKIP"
                         f"  {paper_se:>9.4f}  {'N/A':>9s}  SKIP")
                n_skip += 2
                continue

            # ATT comparison
            att_diff = abs(fresh_att - paper_att)
            att_ok   = att_diff <= tol(att_ndp)
            att_flag = "PASS" if att_ok else "FAIL"
            n_pass += att_ok
            n_fail += not att_ok

            # SE comparison
            se_diff = abs(fresh_se - paper_se)
            se_ok   = se_diff <= tol(se_ndp)
            se_flag = "PASS" if se_ok else "FAIL"
            n_pass += se_ok
            n_fail += not se_ok

            log.info(f"  {row_label:6s}  {col_num:>3d}"
                     f"  {paper_att:>+10.4f}  {fresh_att:>+10.4f}"
                     f"  {att_diff:>8.5f}  {att_flag}"
                     f"  {paper_se:>9.4f}  {fresh_se:>9.4f}  {se_flag}")

            if not att_ok:
                log.warning(f"    !! ATT FAIL: paper={paper_att}  fresh={fresh_att:.6f}"
                            f"  diff={att_diff:.6f}  tol={tol(att_ndp):.6f}")
            if not se_ok:
                log.warning(f"    !! SE  FAIL: paper={paper_se}  fresh={fresh_se:.6f}"
                            f"  diff={se_diff:.6f}  tol={tol(se_ndp):.6f}")

    log.info("  " + "-" * 75)
    log.info(f"  Totals: {n_pass} PASS  {n_fail} FAIL  {n_skip} SKIP")
    log.info("")
    return n_fail

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("compare_paper_numbers_v1.py")
    log.info("Checking fresh estimates against paper Table 1")
    log.info("=" * 60)

    n_fail = run_comparison()

    if n_fail > 0:
        log.error(f"FAIL: {n_fail} cell(s) deviate from paper values.")
        sys.exit(1)

    log.info("PASS: all fresh estimates match the paper values.")
    sys.exit(0)


if __name__ == "__main__":
    main()
