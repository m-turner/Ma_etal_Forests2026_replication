* =============================================================================
* run_one_spec_station.do  (STATION version)
* Rewritten 2026-07-22: replaced Mata with Stata file write + foreach + locals.
*   No Mata matrix manipulation.  Each coefficient is extracted by column index
*   inside a foreach loop and written directly with file write.
*   BJS aggregate ATT computed with cond() instead of nested if.
* Previous (Mata-based) version saved in _old/run_one_spec_station_mata.do
* 2026-10-15: reverted from failed cond/string/tempname refactor (it hung in foreach ;
*   loops -- stata_output_cs_raw.csv stayed 0 bytes). Python-side fix is sufficient: ;
*   na_values=['.'] + pd.to_numeric in run_station_analysis_stata_v1.py handles ;
*   %18.10f leading spaces and Stata missing-value dots without any Stata change. ;
*   dropmissing on estat simple/event (added 2026-10-14) is retained below. ;
* =============================================================================
* Stata worker for the station-level Python/Stata parallel analysis pipeline.
*
* Differences from city version (run_one_spec.do):
*   1. CS DR uses notyet (not-yet-treated controls) -- never-treated pool too small.
*   2. BJS does NOT use autosample -- stage-1 is never-treated only.
*   3. estat simple and estat event use dropmissing option (2026-10-14).
*
* Fixed paths (defined as local macros below):
*   input_csv   = code/run_station_analysis_stata/temp/stata_input.csv
*   tables_stem = code/run_station_analysis_stata/temp/stata_output
* =============================================================================

#delimit ;

local input_csv   "code/run_station_analysis_stata/temp/stata_input.csv" ;
local tables_stem "code/run_station_analysis_stata/temp/stata_output" ;

set more off ;
* 2026-07-29: s1mono scheme applied globally -- all graph exports use it ;
set scheme s1mono ;
capture log close _all ;
log using "code/run_station_analysis_stata/temp/stata_run.log", replace text ;

display "" ;
display "==============================================================" ;
display "run_one_spec_station.do  started" ;
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
* ESTIMATOR 1: CS (no controls, never-treated)
* ==========================================================================

display "" ;
display "--- CS (no controls, never-treated) ---" ;

csdid y, ivar(unit_id) time(year) gvar(gvar) method(reg) ;

* 2026-10-14: dropmissing added -- newer csdid fails estat simple when some ATT(g,t)
*   cells are missing (small station sample produces many missing cells). dropmissing
*   aggregates over the estimated cells only. ;
estat simple, dropmissing ;
local cs_att = r(table)[1,1] ;
local cs_se  = r(table)[2,1] ;
display "  ATT = " %10.6f (`cs_att') "   SE = " %10.6f (`cs_se') ;

estat event, dropmissing ;
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


* ==========================================================================
* ESTIMATOR 2: CS DR (doubly robust, not-yet-treated controls)
* ==========================================================================

display "" ;
display "--- CS DR (dripw, controls, not-yet-treated) ---" ;

csdid y lat lon log_pop_pretx,
    ivar(unit_id) time(year) gvar(gvar) method(dripw) notyet ;

estat simple, dropmissing ;
local csdr_att = r(table)[1,1] ;
local csdr_se  = r(table)[2,1] ;
display "  ATT = " %10.6f (`csdr_att') "   SE = " %10.6f (`csdr_se') ;

estat event, dropmissing ;
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


* ==========================================================================
* ESTIMATOR 3: BJS, no controls, never-treated-only stage-1 (no autosample)
* ==========================================================================

display "" ;
display "--- BJS (no controls, never-treated-only stage-1) ---" ;

* 2026-07-29: added pretrend(5) to show 5 pre-treatment periods in the figure ;
* 2026-07-31: changed allhorizons to horizon(0 10) -- BUT horizon(0 10) in Stata
*   numlist means {0,10} only, not the range 0..10.  Reverted to allhorizons on
*   2026-08-03.  Window restriction to t=0..10 for ATT and plot is handled by the
*   Python post-processing code (run_station_analysis_stata_v1.py). ;
did_imputation y unit_id year cohort, allhorizons pretrend(5) minn(0) ;
/* 2026-07-22: added minn(0) -- station panel has ~100 cities, so many tau
   periods have fewer than the default minn(30) treated obs. Without minn(0),
   all taus suppressed as "insufficient effective sample size". */

local bjs_cols : colnames e(b) ;

file open outfile using "`tables_stem'_bjs_raw.csv", write replace ;
file write outfile "period,b,se" _n ;
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

* 2026-07-29: write pre-trend estimates from e(pre)/e(V_pre) if present ;
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


* ==========================================================================
* ESTIMATOR 4: BJS with controls, never-treated-only stage-1 (no autosample)
* ==========================================================================

display "" ;
display "--- BJS ctrl (controls, never-treated-only stage-1) ---" ;

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
* 2026-07-29: added pretrend(5) to show 5 pre-treatment periods in the figure ;
* 2026-08-03: reverted to allhorizons (see comment on ESTIMATOR 3 above) ;
did_imputation y unit_id year cohort,
    allhorizons nose pretrend(5) minn(0)
    controls(_ctl_lat_b2 _ctl_lat_b3 _ctl_lat_b4 _ctl_lat_b5
             _ctl_lon_b2 _ctl_lon_b3 _ctl_lon_b4 _ctl_lon_b5
             _ctl_pop_b2 _ctl_pop_b3 _ctl_pop_b4 _ctl_pop_b5) ;
/* 2026-07-22: minn(0) same reason as ESTIMATOR 3 above */
drop _ctl_lat_b* _ctl_lon_b* _ctl_pop_b* ;

local bjsc_cols : colnames e(b) ;
local bjsc_tau_cols "" ;
foreach cname of local bjsc_cols {;
    local istau = (substr("`cname'", 1, 3) == "tau") ;
    local bjsc_tau_cols = cond(`istau', "`bjsc_tau_cols' `cname'", "`bjsc_tau_cols'") ;
} ;

file open outfile using "`tables_stem'_bjs_ctrl_raw.csv", write replace ;
file write outfile "period,b,se" _n ;
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

* 2026-07-29: write pre-trend estimates from e(pre) if present ;
* nose suppresses post-period SEs but e(pre) may still be populated ;
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
display "run_one_spec_station.do  complete" ;
display c(current_date) " " c(current_time) ;
display "==============================================================" ;
log close ;
exit 0 ;
