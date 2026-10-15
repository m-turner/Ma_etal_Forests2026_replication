* =============================================================================
* run_one_spec.do  (CITY version)
* Rewritten 2026-07-22: replaced Mata with Stata file write + foreach + locals.
*   No Mata matrix manipulation.  Each coefficient is extracted by column index
*   inside a foreach loop and written directly with file write.
*   BJS aggregate ATT computed with cond() instead of nested if.
* Previous (Mata-based) version saved in _old/run_one_spec_mata.do
* 2026-10-15: reverted from failed cond/string/tempname refactor (it hung in foreach ;
*   loops -- stata_output_cs_raw.csv stayed 0 bytes). Python-side fix is sufficient: ;
*   skipinitialspace=True + na_values=['.'] in run_analysis_stata_v1.py handles ;
*   %18.10f leading spaces and Stata missing-value dots without any Stata change. ;
*   Failed refactor backed up in _old/run_one_spec_pre_20261014.do (confusingly named ;
*   -- that file is the refactored broken version; THIS is the restore). ;
* =============================================================================
* Stata worker for the Python/Stata parallel analysis pipeline.
* Called once per outcome by run_analysis_stata_v1.py.
*
* Fixed paths (defined as local macros below):
*   input_csv   = code/run_analysis_stata/temp/stata_input.csv
*   tables_stem = code/run_analysis_stata/temp/stata_output
*
* Outputs (all relative to project root):
*   {tables_stem}_cs_raw.csv       period,b,se  (+ ATT row from estat simple)
*   {tables_stem}_cs_dr_raw.csv    same
*   {tables_stem}_bjs_raw.csv      period,b,se  (+ ATT row if any valid tau)
*   {tables_stem}_bjs_ctrl_raw.csv same
* =============================================================================

#delimit ;

local input_csv   "code/run_analysis_stata/temp/stata_input.csv" ;
local tables_stem "code/run_analysis_stata/temp/stata_output" ;

set more off ;
* 2026-07-24: s1mono scheme applied globally -- all graph exports use it ;
set scheme s1mono ;
capture log close _all ;
log using "code/run_analysis_stata/temp/stata_run.log", replace text ;

display "" ;
display "==============================================================" ;
display "run_one_spec.do  started" ;
display "  input_csv   = `input_csv'" ;
display "  tables_stem = `tables_stem'" ;
display c(current_date) " " c(current_time) ;
display "==============================================================" ;


* --------------------------------------------------------------------------
* Load data
* --------------------------------------------------------------------------

display "" ;
import delimited "`input_csv'", clear varnames(1) ;
display "Loaded " _N " rows" ;

recast long unit_id year, force ;
xtset unit_id year ;

* 2026-07-23: cohort is missing for never-treated. csdid needs gvar=0 for them ;
gen gvar = cond(missing(cohort), 0, cohort) ;


* ==========================================================================
* ESTIMATOR 1: CS (Callaway-Sant'Anna, no controls, never-treated)
* ==========================================================================

display "" ;
display "--- CS (no controls, never-treated) ---" ;

csdid y, ivar(unit_id) time(year) gvar(gvar) method(reg) ;

estat simple ;
local cs_att = r(table)[1,1] ;
local cs_se  = r(table)[2,1] ;
display "  ATT = " %10.6f (`cs_att') "   SE = " %10.6f (`cs_se') ;

estat event ;
local cs_cols : colnames r(table) ;

file open outfile using "`tables_stem'_cs_raw.csv", write replace ;
file write outfile "period,b,se" _n ;
local j = 0 ;
foreach cname of local cs_cols {;
    local j = `j' + 1 ;
    file write outfile "`cname'," %18.10f (r(table)[1,`j']) "," %18.10f (r(table)[2,`j']) _n ;
} ;
file write outfile "ATT," %18.10f (`cs_att') "," %18.10f (`cs_se') _n ;
file close outfile ;

* 2026-07-25: Figure generation moved to Python (csdid_plot hung in batch mode).
* Python reads the _cs_coeffs.csv written above and generates the PNG.

* 2026-07-27: display forces a log flush so Python stale-log timer resets ;
display "--- CS complete ---" ;

* ==========================================================================
* ESTIMATOR 2: CS DR (Callaway-Sant'Anna, doubly robust, with controls)
* ==========================================================================

display "" ;
display "--- CS DR (dripw, with controls, never-treated) ---" ;

csdid y lat lon log_pop_pretx,
    ivar(unit_id) time(year) gvar(gvar) method(dripw) ;

estat simple ;
local csdr_att = r(table)[1,1] ;
local csdr_se  = r(table)[2,1] ;
display "  ATT = " %10.6f (`csdr_att') "   SE = " %10.6f (`csdr_se') ;

estat event ;
local csdr_cols : colnames r(table) ;

file open outfile using "`tables_stem'_cs_dr_raw.csv", write replace ;
file write outfile "period,b,se" _n ;
local j = 0 ;
foreach cname of local csdr_cols {;
    local j = `j' + 1 ;
    file write outfile "`cname'," %18.10f (r(table)[1,`j']) "," %18.10f (r(table)[2,`j']) _n ;
} ;
file write outfile "ATT," %18.10f (`csdr_att') "," %18.10f (`csdr_se') _n ;
file close outfile ;

* 2026-07-25: Figure generation moved to Python (csdid_plot hung in batch mode).
* Python reads the _cs_dr_coeffs.csv written above and generates the PNG.

* 2026-07-27: display forces a log flush so Python stale-log timer resets ;
display "--- CS DR complete ---" ;

* ==========================================================================
* ESTIMATOR 3: BJS imputation, no controls, autosample
* ==========================================================================

display "" ;
display "--- BJS (no controls, autosample) ---" ;

* 2026-07-27: added pretrend(5) to show 5 pre-treatment periods in the figure ;
did_imputation y unit_id year cohort, autosample allhorizons pretrend(5) ;

local bjs_cols : colnames e(b) ;

file open outfile using "`tables_stem'_bjs_raw.csv", write replace ;
file write outfile "period,b,se" _n ;

* Write post-period coefficients from e(b) and compute aggregate ATT
local nv = 0 ;
local sb = 0 ;
local sv = 0 ;
local j  = 0 ;
foreach cname of local bjs_cols {;
    local j  = `j' + 1 ;
    local ok = (sqrt(e(V)[`j',`j']) > 0) ;
    local nv = `nv' + `ok' ;
    local sb = `sb' + `ok' * e(b)[1,`j'] ;
    local sv = `sv' + `ok' * e(V)[`j',`j'] ;
    file write outfile "`cname'," %18.10f (e(b)[1,`j']) "," %18.10f (sqrt(e(V)[`j',`j'])) _n ;
} ;
local bjs_att = cond(`nv'>0, `sb'/`nv', .) ;
local bjs_se  = cond(`nv'>0, sqrt(`sv')/`nv', .) ;
display "  ATT = " %10.6f (`bjs_att') "   SE = " %10.6f (`bjs_se') ;
if (`nv' > 0) {;
    file write outfile "ATT," %18.10f (`bjs_att') "," %18.10f (`bjs_se') _n ;
} ;

* 2026-07-27: write pre-trend estimates from e(pre)/e(V_pre) if present ;
* Use confirm matrix + if -- capture+block does not catch macro errors in #delimit mode ;
* e(pre) may not exist in this Stata installation -- skipped silently if absent ;
capture confirm matrix e(pre) ;
if (_rc == 0) {;
    local bjs_pre_cols : colnames e(pre) ;
    local jp = 0 ;
    foreach cname of local bjs_pre_cols {;
        local jp = `jp' + 1 ;
        file write outfile "`cname'," %18.10f (e(pre)[1,`jp']) "," %18.10f (sqrt(e(V_pre)[`jp',`jp'])) _n ;
    } ;
} ;

file close outfile ;

* 2026-07-25: Figure generation moved to Python (event_plot also hung in batch mode).
* Python reads the _bjs_coeffs.csv written above and generates the PNG.

* 2026-07-27: display forces a log flush so Python stale-log timer resets ;
display "--- BJS complete ---" ;

* ==========================================================================
* ESTIMATOR 4: BJS with controls, autosample
* ==========================================================================

display "" ;
display "--- BJS ctrl (lat/lon/log_pop_pretx x year, autosample) ---" ;

gen _ctl_lat_b2  = lat           * (year >= 2005 & year <= 2009) ;
gen _ctl_lat_b3  = lat           * (year >= 2010 & year <= 2014) ;
gen _ctl_lat_b4  = lat           * (year >= 2015 & year <= 2019) ;
gen _ctl_lat_b5  = lat           * (year >= 2020) ;
gen _ctl_lon_b2  = lon           * (year >= 2005 & year <= 2009) ;
gen _ctl_lon_b3  = lon           * (year >= 2010 & year <= 2014) ;
gen _ctl_lon_b4  = lon           * (year >= 2015 & year <= 2019) ;
gen _ctl_lon_b5  = lon           * (year >= 2020) ;
gen _ctl_pop_b2  = log_pop_pretx * (year >= 2005 & year <= 2009) ;
gen _ctl_pop_b3  = log_pop_pretx * (year >= 2010 & year <= 2014) ;
gen _ctl_pop_b4  = log_pop_pretx * (year >= 2015 & year <= 2019) ;
gen _ctl_pop_b5  = log_pop_pretx * (year >= 2020) ;
* 2026-07-27: added pretrend(5) to show 5 pre-treatment periods in the figure ;
did_imputation y unit_id year cohort,
    autosample allhorizons nose pretrend(5)
    controls(_ctl_lat_b2 _ctl_lat_b3 _ctl_lat_b4 _ctl_lat_b5
             _ctl_lon_b2 _ctl_lon_b3 _ctl_lon_b4 _ctl_lon_b5
             _ctl_pop_b2 _ctl_pop_b3 _ctl_pop_b4 _ctl_pop_b5) ;
drop _ctl_lat_b* _ctl_lon_b* _ctl_pop_b* ;

local bjsc_cols : colnames e(b) ;
local bjsc_tau_cols "" ;
foreach cname of local bjsc_cols {;
    local istau = (substr("`cname'", 1, 3) == "tau") ;
    local bjsc_tau_cols = cond(`istau', "`bjsc_tau_cols' `cname'", "`bjsc_tau_cols'") ;
} ;

file open outfile using "`tables_stem'_bjs_ctrl_raw.csv", write replace ;
file write outfile "period,b,se" _n ;

* Write post-period tau coefficients (nose: se=0)
local nb = 0 ;
local sb = 0 ;
local j  = 0 ;
foreach cname of local bjsc_tau_cols {;
    local j  = `j' + 1 ;
    local ok = (e(b)[1,`j'] != 0) ;
    local nb = `nb' + `ok' ;
    local sb = `sb' + `ok' * e(b)[1,`j'] ;
    file write outfile "`cname'," %18.10f (e(b)[1,`j']) ",0" _n ;
} ;
local bjsc_att = cond(`nb'>0, `sb'/`nb', .) ;
display "  ATT = " %10.6f (`bjsc_att') "   SE = (nose, not computed)" ;
if (`nb' > 0) {;
    file write outfile "ATT," %18.10f (`bjsc_att') ",0" _n ;
} ;

* 2026-07-27: write pre-trend estimates from e(pre) if present ;
* Same approach as BJS no-ctrl -- confirm matrix + if ;
capture confirm matrix e(pre) ;
if (_rc == 0) {;
    local bjsc_pre_cols : colnames e(pre) ;
    local jp = 0 ;
    foreach cname of local bjsc_pre_cols {;
        local jp = `jp' + 1 ;
        file write outfile "`cname'," %18.10f (e(pre)[1,`jp']) ",0" _n ;
    } ;
} ;

file close outfile ;


display "" ;
display "==============================================================" ;
display "run_one_spec.do  complete" ;
display c(current_date) " " c(current_time) ;
display "==============================================================" ;
log close ;
exit 0 ;
