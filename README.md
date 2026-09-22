# sas-compiler

A compiler for (a substantial subset of) the SAS language: the macro
language, the DATA step, and several common PROC steps. It compiles SAS
source to plain Python (pandas + duckdb), which you can run directly.

It is a real multi-stage compiler, not a line-by-line translator:

```
SAS source
   │
   ▼
sas_compiler/macro.py     macro preprocessor (%macro/%mend, %let, %if/%do,
   │                      &var resolution) — a recursive-descent scanner
   │                      over the raw text, producing plain SAS text
   ▼
sas_compiler/lexer.py     tokenizer
   ▼
sas_compiler/parser.py    recursive-descent parser -> AST
   │                      (sas_compiler/ast_nodes.py)
   ▼
sas_compiler/codegen.py   AST -> Python source (pandas/duckdb)
   ▼
sas_compiler/runtime.py   support library the generated code calls into
                          (missing-value semantics, BY-group iteration,
                          SAS built-in functions)
```

## Install

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Usage

```
sasc program.sas --run                 # compile and execute immediately
sasc program.sas -o program.py         # write the generated Python
sasc program.sas --emit-macro          # show macro-expanded SAS (debugging)
sasc program.sas --check               # parse only; report errors, generate/run nothing
sasc --version                         # print the installed version and exit
```

Or from the repo without installing: `python -m sas_compiler.cli program.sas --run`.

See `examples/*.sas` for programs covering macros, arrays, DO loops,
BY-group processing, MERGE, dataset options, and PROC SQL.

## What's supported

**Macro language:** `%LET`, `%MACRO`/`%MEND` (positional and keyword
params with defaults, arbitrarily nested calls), `%IF`/`%THEN`/`%ELSE`,
iterative/`%WHILE`/`%UNTIL` `%DO` loops (nestable inside each other and
inside `%IF`/`%THEN`/`%ELSE` branches), `&var`/`&&var` resolution
(including indirect references), `%PUT`, `%GLOBAL`/`%LOCAL`,
`%EVAL`/`%SYSEVALF` (`%EVAL`/`%IF` use SAS's integer, truncating-division
arithmetic; `%SYSEVALF` uses real division), `%STR`/`%NRSTR`, and the
text functions `%UPCASE`/`%QUPCASE`/`%LOWCASE`/`%SUBSTR`/`%QSUBSTR`/
`%SCAN`/`%QSCAN`/`%INDEX`/`%LENGTH`/`%TRIM`/`%CMPRES` (the `Q`-prefixed
forms behave the same as their plain counterparts here, since this
implementation never re-scans a function's result for further macro
triggers — there's nothing extra for the "quoted" variant to suppress),
and `%INCLUDE "file.sas"` to splice in another source file. `%SYSFUNC`
supports `TODAY`, `TRIM`, `UPCASE`, `LOWCASE`, `COMPRESS`, `MDY`,
`YEAR`/`MONTH`/`DAY`, `INTCK`/`INTNX` (day/week/month/year units), `PUTN`
(a small format subset: `COMMAw.d`/`DOLLARw.d`/`PERCENTw.d`/`Zw.d`), and
`INPUTN`.

**DATA step:** `SET`/`MERGE` (with `BY`, including multi-level
`first.`/`last.` group processing), dataset options (`DROP=`, `KEEP=`,
`RENAME=`, `WHERE=`, `IN=`), `ARRAY` (including `array x{n} (v1, v2, ...)`
initializer lists, numeric or character, with or without explicit element
names, explicit bounds via `array x{2020:2023}`, multi-dimensional arrays
(`array grid{3,4} g1-g12;` / `array rev{1:2, 2023:2024} ...;`, subscripted
`grid{i,j}`, row-major flat storage, up to 3 dimensions tested) and
`_TEMPORARY_` lookup arrays) plus `DIM()`/`HBOUND()`/`LBOUND()` (with an
explicit dimension-number argument for multi-dim arrays, e.g. `DIM(grid,1)`)
and `DO OVER` (flat row-major iteration for multi-dim arrays),
`RETAIN`, the sum statement
(`var + expr;`), `DO`/`DO WHILE`/`DO UNTIL`/iterative `DO`, `IF`/`THEN`/
`ELSE` and subsetting `IF`, `SELECT (expr); WHEN (...) ...; OTHERWISE ...; END;`
(both value-list and bare-condition forms), `STOP`/`LEAVE`/`CONTINUE` (with
correct `CONTINUE` semantics inside iterative and `DO UNTIL` loops),
`UPDATE master trans; BY ...;` (non-missing transaction values overlay the
master, one output row per BY group), `OUTPUT` (single or multiple output datasets),
`DROP`/`KEEP`/`LENGTH`, `WHERE`, `PUT`, `CALL SYMPUT`/`CALL MISSING`/
`CALL EXECUTE` (queued SAS source runs after the current step, before the
next one, sharing the same WORK library -- the classic "generate one code
block per driver-dataset row" idiom),
`INPUT`/`DATALINES`/`CARDS`, `INFILE "path" [DLM=..] [DSD] [FIRSTOBS=n]
[OBS=n]` for external text files (short lines padded MISSOVER-style),
`FILE "path" [MOD]` redirecting `PUT` to a file (`FILE LOG`/`PRINT` stay on
stdout), `ABORT ["msg"]`, `DELETE`, `RETURN`, `LABEL` (shown as
`PROC PRINT` column headers), the `_N_` automatic row counter, a HASH
object for key-based lookups (`DECLARE HASH h(DATASET: "ds")`,
`.DEFINEKEY()`/`.DEFINEDATA()`/`.DEFINEDONE()`, `.FIND()`/`.ADD()`/
`.REMOVE()`/`.CHECK()`/`.OUTPUT(DATASET: "name")`, with `KEY: expr`
arguments or the current PDV's key-variable values when omitted), a
`SET dataset KEY=indexname;` keyed lookup for the same kind of
table-lookup pattern without a separate HASH object, date/time/datetime
literals (`'01JAN2010'd`, `'12:34't`, `'01JAN2010:12:34:56'dt`),
`TITLE`/`FOOTNOTE` (shown on `PROC PRINT` output), and a broad function
library (string,
numeric, date, `LAG`, `DIF`, `IFN`/`IFC`, `COALESCE`,
`TRANSLATE`, `VERIFY`, `PRXMATCH`, `COMPBL`, `REVERSE`, `QUOTE`,
`DEQUOTE`, `FINDC`, `FINDW`, etc).

**PROC steps:** `PRINT` (with an `Obs` column (or `ID` values) and
`FORMAT`-aware display,
plus `VAR`, `WHERE`, `OBS=`/`FIRSTOBS=`, `NOOBS`, and a `SUM` totals row),
`CONTENTS` (NOBS plus variable names/types), `SORT` (`BY`, `OUT=`,
`NODUPKEY`, `NODUP`/`NODUPRECS`, `DUPOUT=`), `MEANS`/`SUMMARY` (`CLASS`, `VAR`,
`OUTPUT OUT=`, explicit stat keywords — `N MEAN STD MIN MAX SUM
MEDIAN VAR RANGE NMISS P1...P99 LCLM UCLM` (`LCLM`/`UCLM` are the lower/upper
bounds of a 95% confidence interval for the mean, fixed at 95% — there is no
`ALPHA=` option to change the confidence level) — in place of the
N/MEAN/STD/MIN/MAX default — plus `TYPES`/`WAYS` class-combination control), `FREQ` (one-way
and two-way `TABLES`, one- and two-way `OUTPUT OUT=`
count/percent datasets, and a `CHISQ` table option on both one-way
`TABLES` — a Pearson chi-square goodness-of-fit test against equal
proportions across levels — and two-way `TABLES` — a Pearson chi-square
test of independence report; `TESTP=` for narrowing the one-way expected
proportions is out of scope; a `MEASURES` (or `RELRISK`/`RISKDIFF`)
two-way `TABLES` option reports the odds ratio and relative risk (risk
ratio), each with a 95% confidence interval, for a strictly 2x2 table —
any other table shape prints a warning instead of computing them; an
`AGREE` two-way `TABLES` option reports Cohen's Kappa (Simple Kappa
Coefficient) plus overall percent agreement, for a square table where
both variables share the same set of categories (e.g. two raters/methods
scoring the same items) — any other table shape prints a warning instead
of computing them; the Kappa asymptotic standard error and confidence
interval that real SAS also reports are out of scope), `APPEND`,
`TRANSPOSE` (`BY`/`VAR`, with or without `ID`, `PREFIX=`/`SUFFIX=`/`DELIMITER=`), `IMPORT`/`EXPORT`
(CSV, via `DATAFILE=`/`OUTFILE=`), `DATASETS` (`DELETE`, `CHANGE` —
including `libref.table` file/table renames and drops),
`UNIVARIATE` (moments, mode, quantiles, extreme observations, a Tests for
Normality section with Shapiro-Wilk and Kolmogorov-Smirnov statistics/
p-values — Cramer-von Mises and Anderson-Darling are not implemented,
plus an optional `OUTPUT OUT=` moments dataset), `RANK` (`VAR`/`RANKS`/`BY`,
`DESCENDING`, average-rank ties, `GROUPS=` ntile buckets),
`STANDARD` (`VAR var1 var2 ...;` required; rescales each VAR column to a
target `MEAN=`/`STD=` — a z-score transform by default, `MEAN=0 STD=1` —
`REPLACE` fills missing input values with that column's own original
mean before rescaling, and a zero-variance/constant column comes back
entirely missing instead of dividing by zero; `OUT=` defaults to
overwriting `DATA=` in place, like `RANK`),
`TABULATE` (`CLASS`/`VAR`/`TABLE row, col*var*stat` two-way pivots via `FORMAT` (see below), `GLM`, `FASTCLUS`
(see below), `REPORT` (`COLUMN`/`DEFINE ... / GROUP|ANALYSIS stat|DISPLAY|COMPUTED`
— grouped-and-summarized when a `GROUP` and an `ANALYSIS` variable are
both defined, else a plain listing of the `COLUMN` variables — plus
`COMPUTE`/`ENDCOMP` row blocks and
`BREAK`/`RBREAK ... / SUMMARIZE` subtotal/total rows),
`TABULATE` (`CLASS`/`VAR`/`TABLE row, col*var*stat` two-way pivots via
`SUM`/`MEAN`/`N` in either `var*stat` or `stat*var` order, multi-stat
`var*(sum mean)` cells, with an
optional `OUT=` stacked with a `_stat_` column), `SGPLOT` (`SCATTER`,
`SERIES`, `VBAR`/`HBAR` with or without `RESPONSE=`, `HISTOGRAM`,
`DENSITY`, `REFLINE` — multiple
plot statements overlay onto one figure; saved to a PNG via
`OUT="path.png"`, or a default `sgplot_N.png` if omitted, since there's
no interactive display here), `COMPARE` (`BASE=`/`COMPARE=`, `ID`/`VAR`
statements — rows aligned by `ID` value or, without one, by position;
prints a per-variable mismatch summary and a capped differences table,
plus a `NOTE: No unequal values were found` message when the datasets
truly match; an optional `OUT=` gets the differences as a long-form
`_id_`/`_var_`/`_base_`/`_compare_` dataset; `BY` re-runs the whole
comparison, with its own printed report, per BY-group; `CRITERION=`
gives numeric comparisons a fuzzy-equality tolerance), `FCMP` (user-defined
functions — `FUNCTION name(args) [$]; ... RETURN(expr); ENDSUB;` —
callable from any later DATA step by name, using the same statement
grammar as the DATA step itself), and `SQL` — SQL statements are executed
almost verbatim against duckdb with all current datasets registered as
views, so most standard SQL (joins, GROUP BY/HAVING, window functions,
CTEs) works without any special-casing here. A `WHERE` statement and the
`OBS=`/`FIRSTOBS=` dataset options are honored by every PROC that reads
a `DATA=` dataset.

**Statistics / ML:** `CORR` (Pearson r and p-value matrix, `WITH`
rectangular form, plus an
optional `OUT=`/`OUTP=` correlation-matrix dataset), `REG` (OLS via
statsmodels — full summary with R², F-stat, coefficient table,
`OUTPUT OUT= P=/R=` for predicted values / residuals, and a
`MODEL ... / VIF;` option that prints a per-predictor Variance
Inflation Factor multicollinearity table, and a
`MODEL ... / SELECTION=BACKWARD|FORWARD;` option for greedy predictor
selection — `BACKWARD` starts with all predictors and repeatedly drops
the one with the highest p-value while it exceeds `SLSTAY=` (default
0.05); `FORWARD` starts with none and repeatedly adds whichever
candidate gives the lowest entry p-value while it is below `SLENTRY=`
(default 0.05); each elimination/addition step is logged, then the
final selected model is reported through the same summary/VIF/
`OUTPUT OUT=` machinery as the no-`SELECTION=` path; `SELECTION=STEPWISE`
(SAS's combined backward+forward algorithm) is a deliberate scope cut
and raises a compile error rather than being approximated), `LOGISTIC`
(binary logistic regression via statsmodels — summary, odds ratios, an
"Association of Predicted Probabilities and Observed Responses" section
with the `c` statistic (concordance / ROC AUC, via scikit-learn) and
Somers' D, `OUTPUT OUT= P=` for predicted probabilities, and a
`CLASS var1 var2;` statement that dummy-encodes categorical predictors
— drop-first indicator columns — before fitting; scope cut:
the Percent Concordant/Discordant/Tied, Gamma, and Tau-a figures real
PROC LOGISTIC also prints there are not computed), `GLM` (OLS via
statsmodels like `REG`, plus a `CLASS var1 var2;` statement that
dummy-encodes categorical predictors — drop-first indicator columns —
before fitting; same `OUTPUT OUT= P=/R=` support), and `FASTCLUS`
(k-means clustering via scikit-learn — `VAR var1 var2 ...;` for the
input columns, `MAXCLUSTERS=n` for k (default 2), and `OUTPUT OUT=`
for the input rows plus a 1-based `cluster` column, alongside a
printed cluster-frequency/cluster-means summary), `CLUSTER`
(agglomerative/hierarchical clustering via scipy —
`VAR var1 var2 ...;` for the input columns, `METHOD=` for the linkage
method — `AVERAGE` (default), `WARD`/`WARDS`, `SINGLE`, `COMPLETE`, or
`CENTROID`, mapped onto `scipy.cluster.hierarchy.linkage`'s `method=`
— and an optional `ID idvar;` to label observations by that
variable's values instead of row numbers; prints the classic Cluster
History table, one row per merge step counting down from N-1 to 1
clusters, showing which two clusters/observations joined and at what
distance. Scope cut: no `OUTTREE=` dataset — SAS's OUTTREE is a
fairly involved specialized tree-structure dataset, and real PROC
CLUSTER users mostly care about the printed Cluster History and/or a
dendrogram, neither of which needs it; no `OUT=` dataset either, this
PROC is print-only like `TTEST`/`ANOVA`/`NPAR1WAY`), and `TTEST` (via
scipy — one-sample against `H0=` (default 0, `VAR var1 var2 ...;`
alone), two-sample independent groups (`CLASS groupvar; VAR var1
var2 ...;`, reporting both pooled-variance and Satterthwaite/Welch
t-test results), or paired (`PAIRED var1*var2 ...;`, one paired
t-test per pair); prints N/Mean/StdDev/StdErr and the t/df/Pr>|t|
results, no `OUT=` dataset), and `ANOVA` (one-way with exactly one
`CLASS` variable and `MODEL y = classvar;` — via scipy's `f_oneway`;
prints Class Level Information plus the classic Source/DF/Sum of
Squares/Mean Square/F Value/Pr > F table, R-Square, Coeff Var, and
Root MSE, no `OUT=` dataset; two-way and N-way with 2+ `CLASS`
variables and a `MODEL` naming main-effect terms (`a`) and/or
`*`-joined interaction terms (`a*b`, `a*b*c`, ...) — every MODEL term
must reference only declared `CLASS` variables — via a statsmodels
formula (`C(a) + C(b) + C(a):C(b)`, intercept included, matching real
PROC ANOVA's default parameterization) fit with
`statsmodels.formula.api.ols` and printed with
`statsmodels.stats.anova.anova_lm(..., typ=2)`; Type II sums of
squares is a documented simplification — real PROC ANOVA is itself
documented as requiring a *balanced* design, for which Type I/II/III
SS all agree, so Type II stands in without attempting to replicate
SAS's exact SS partitioning for unbalanced data; no `OUT=` dataset
here either), and `NPAR1WAY` (`CLASS groupvar; VAR
var1 var2 ...;`, one report block per `VAR`; always prints a
Wilcoxon-scores rank-sums-by-group table, then a Wilcoxon rank-sum
test — via scipy's `mannwhitneyu` — when `CLASS` has exactly 2
non-missing levels, or a Kruskal-Wallis test — via scipy's
`kruskal`, reported as Chi-Square/DF/Pr > Chi-Square — when it has
more than 2; no `OUT=` dataset. Scope cut: only this default
Wilcoxon/Kruskal-Wallis behavior is implemented — `EDF`, `MEDIAN`,
`SAVAGE`, and other NPAR1WAY test-selection options are not, and the
printed Wilcoxon two-sample statistic is scipy's Mann-Whitney U
rather than SAS's normalized S statistic), and `PRINCOMP` (principal
component analysis via scikit-learn — `VAR var1 var2 ...;` for the
input columns, `N=n` for how many components to compute (default: all
of them), `COV` to fit PCA on the raw covariance matrix instead of the
default standardize-then-correlation-matrix behavior, and `OUTPUT
OUT=`/`OUT=` for the input rows plus 1-based `Prin1..PrinN` score
columns, alongside printed Eigenvalues (Eigenvalue/Difference/
Proportion/Cumulative) and Eigenvectors tables), and `SURVEYSELECT`
(simple random sampling — `METHOD=SRS`, the only supported method and
real SAS's own default, so `METHOD=` can be omitted entirely; exactly
one of `N=n` (an exact sample size) or `SAMPRATE=rate` (a proportion,
sampling `round(rate * nrows)` rows) is required; optional `SEED=` for
reproducible sampling, omitted for a random seed each run; and an
optional `STRATA var1 var2 ...;` that samples independently within
each distinct combination of STRATA values, applying the same
`N=`/`SAMPRATE=` per stratum — a stratum with fewer rows than `N=`
requests contributes all of its rows rather than erroring; `OUT=` is
required, like real SAS. Scope cuts: only `METHOD=SRS` is implemented
— systematic, PPS, and SAS's other sampling methods are not — and
there is no `OUTALL=`/`SELECTALL` or other advanced option support),
and `ARIMA` (ARIMA time series modeling/forecasting via
statsmodels — scoped hard to a single batch-mode `IDENTIFY`/`ESTIMATE`/
`FORECAST` block: `IDENTIFY VAR=var;` names the series, `ESTIMATE
P=p D=d Q=q;` fits `ARIMA(p, d, q)` (any subset of `P=`/`D=`/`Q=`
defaults to 0) and prints the model summary (AIC/BIC/coefficient
table, via `model.summary()` like `REG`/`LOGISTIC`), and an optional
`FORECAST LEAD=n OUT=ds;` forecasts `n` steps ahead, printing a
Period/Forecast/Lower 95%/Upper 95% table and writing it to `OUT=` with
columns `period`/`FORECAST`/`L95`/`U95`. Both `IDENTIFY VAR=` and
`ESTIMATE` are required (a degenerate, unrequested `p=0,d=0,q=0` model
is refused rather than silently fit); `FORECAST` is optional — without
it, `ARIMA` is print-only like `TTEST`/`ANOVA`/`CLUSTER`. Scope cuts:
only one `IDENTIFY`/`ESTIMATE`/`FORECAST` block is supported (no
iterative re-`ESTIMATE`), no differencing-in-`VAR` syntax
(`VAR=y(1)`) — use `ESTIMATE D=` instead, no seasonal
`P=(...)(...)`/`SEASONAL=` multi-factor terms, no `INPUT=`
transfer-function (ARIMAX) terms, no `OUTLIER` statement, no `ESTIMATE
METHOD=`; `FORECAST ID=` is parsed (so it doesn't error) but real
date-arithmetic extrapolation of that variable is not implemented —
forecast periods in `OUT=` are always sequential integers `1..LEAD`),
`LIFETEST` (Kaplan-Meier survival curve estimation via
`statsmodels.duration.survfunc.SurvfuncRight` — `TIME
timevar*censorvar(censorvalue);` is required and names the
time-to-event variable, the censoring-status variable, and the single
value of that status variable meaning "censored" (any other value
means the event occurred); prints the KM table (Time/Surv prob/Surv
prob SE/num at risk/num events, via `.summary()`), a brief N/events/
censored line, and the median survival time (the first time the curve
drops to <= 50% survival, or `.` when never reached — its confidence
interval is not computed). An optional `STRATA groupvar;` prints one
KM table per distinct stratum value plus, when there are 2+ strata, an
overall log-rank test (`statsmodels.duration.survfunc.survdiff`,
Chi-Square and p-value) comparing survival across them. Scope cuts:
exactly one `CENSOR()` value is supported — real SAS's `CENSOR(0, 2)`-
style multi-value lists are not — and there is no `OUTSURV=` output
dataset; like `TTEST`/`ANOVA`/`CLUSTER`, `LIFETEST` is print-only),
and `PHREG` (Cox proportional hazards regression via statsmodels'
`PHReg` — `MODEL timevar*censorvar(censorvalue) = x1 x2 ...;` (note
this is a *different* MODEL shape from the shared `y = x1 x2 ...;`
form below: the left-hand side names the survival time variable and a
censoring indicator, with the single value in parentheses marking a
*censored* observation — any other observed value of `censorvar`
means the event occurred); prints the statsmodels summary
(coefficient/log-hazard-ratio table) plus a "Hazard Ratio Estimates"
section listing each predictor's `exp(coef)`. Scope cuts: only a
single censor value is supported (real SAS PHREG allows
`censor(v1, v2, ...)`), there is no `OUTPUT OUT=` for risk
scores/residuals, no `STRATA` statement, and no time-dependent
covariates — print-only, like `TTEST`/`ANOVA`/`CLUSTER`), and
`GENMOD` (generalized linear models via statsmodels `GLM` — generalizes
`REG` (Gaussian/identity) and `LOGISTIC` (Binomial/logit) to other
exponential-family distributions with a configurable link: `MODEL y =
x1 x2 ... / DIST=dist LINK=link;`, where `DIST=` is `NORMAL`/`GAUSSIAN`
(the default when `DIST=` is omitted, matching real GENMOD),
`POISSON`, `GAMMA`, or `BINOMIAL`/`BIN`, and `LINK=` is `IDENTITY`,
`LOG`, or `LOGIT` — omitting `LINK=` uses each family's own canonical
default link, matching both SAS's and statsmodels' default behavior;
prints the fitted model summary via `model.summary()` like
`REG`/`LOGISTIC`/`ARIMA`. Scope cuts: no `CLASS` statement (predictors
are always coerced to numeric, unlike `GLM`/`LOGISTIC`'s dummy-encoding
path), no `LINK=POWER(exponent)` (raises a compile error rather than
guessing the exponent — `IDENTITY`/`LOG`/`LOGIT` are the only supported
links), and no `OUTPUT OUT=` (GENMOD's `OUTPUT` statement has many
`DIST=`-specific statistic keywords that are out of scope here;
`GENMOD` is print-only, like `TTEST`/`ANOVA`/`CLUSTER`/`ARIMA` without
`FORECAST`).
`MODEL y = x1 x2
...;` is the shared syntax for `REG`, `LOGISTIC`, `GLM`, `ANOVA`, and
`GENMOD`.

**Real databases:** `LIBNAME libref "path/to/file.db";` connects a
libref to an actual SQLite database file, `LIBNAME libref
"path/to/dir/";` to a directory (where `libref.table` resolves to
`table.sas7bdat` or `table.csv` inside it), or `LIBNAME libref
"postgresql://user:pass@host/db";` (any SQLAlchemy URL) to a real
server. `SET libref.table;` / `MERGE libref.table ...;` reads real rows
from it; `DATA libref.table; ... run;` writes the result back to a real
table (`if_exists="replace"`); and `PROC SQL` transparently `ATTACH`es
any SQLite libref into its duckdb session, so `SELECT ... FROM
libref.table` in a query — including one that joins a real database
table against an in-memory SAS dataset — works natively without any
extra sync step. Non-SQLite URLs (Postgres/MySQL/etc) can't be
`ATTACH`ed directly, so `PROC SQL` instead scans the query text for
`libref.table` references and stages each one as a duckdb view (via
SQLAlchemy through `db_read_table`) before running the query — this
works the same as the SQLite case from the query author's point of
view, just via a different mechanism under the hood. `LIBNAME libref
CLEAR;` unassigns it.

**Formats:** a `FORMAT` statement's assignments are tracked per dataset
(and propagate through `PROC SORT`), and `PROC PRINT` renders formatted
columns accordingly; `PUT(value, format.)` applies a format inline. The
built-in format families are `COMMAw.d`, `DOLLARw.d`, `PERCENTw.d`,
`Zw.d`, `BESTw.d`, `$w.`/`$CHARw.`, and the date formats `DATE9.`,
`MMDDYYw.`, `YYMMDDw.`, `DDMMYYw.`, `WORDDATE.`. On top of those,
`PROC FORMAT` user-defined value lists are supported — numeric ranges
(`low-17`, `65-high`, `OTHER`) and character value maps
(`value $gender "M"="Male" ... other="Unknown";`) — referenced the same
way via `FORMAT var fmtname.` or `PUT(var, fmtname.)`.

**ODS:** `ODS HTML FILE="report.html"; ... ODS HTML CLOSE;`,
`ODS RTF FILE="report.rtf"; ... ODS RTF CLOSE;`, and
`ODS PDF FILE="report.pdf"; ... ODS PDF CLOSE;` redirect everything any
PROC step would otherwise print (between the open/close pair) into a
report file instead of the console — this works for every PROC
automatically, via stdout redirection, not by special-casing each one.
HTML, RTF and PDF each track their own open/closed state independently,
so any combination can be open at once (each capturing the same
printed output into its own file); closing one leaves the others open.
HTML output detects blocks of `to_string()`-style whitespace-aligned
tabular text and renders them as real `<table>`/`<tr>`/`<td>` markup
with light CSS borders/padding, falling back to a plain `<pre>` block
for narrow output or anything that doesn't parse as a table. RTF and
PDF output use the same table-detection heuristic as HTML: table-shaped
chunks render as real structured tables — `\trowd`/`\cellx`/`\intbl`/
`\row` markup with a bold header row for RTF, a `reportlab` `Table`
flowable with a light grid and shaded header row for PDF — and
everything else falls back to monospace text (`\par`-separated
paragraphs for RTF, preformatted text for PDF, built into a PDF via
`SimpleDocTemplate`). Other ODS destinations/statements (`LISTING`,
`SELECT`/`EXCLUDE`, `_ALL_`, ...) are parsed and safely ignored rather
than raising an error.

## Known limitations

These are deliberate scope cuts, not oversights — real SAS is enormous:

- **PDV typing**: a `SET`/`MERGE`/`UPDATE` source column's real dtype
  (checked live against the actual source DataFrame at run time, not
  guessed) now decides whether that name defaults to blank instead of
  numeric-missing before it's ever assigned — closing the common case
  where a `MERGE`/`UPDATE` source is absent for the first BY-group
  processed. An explicit static declaration (`LENGTH`/`ARRAY` numeric)
  still wins if it conflicts with the dtype guess. What's left
  heuristic-only: a variable with **no** SET/MERGE/UPDATE source and
  **no** static evidence either way (only ever produced by computation
  inside the step) still relies on finding a string-literal or
  char-returning-function assignment somewhere in the step; one that's
  read before being set on every code path it could reach can still
  come back missing-numeric instead of blank.
- **MERGE** handles one-to-one and one-to-many BY-merges correctly.
  When *more than one* input dataset has multiple rows for the same BY
  value (many-to-many merges, which SAS itself discourages as
  order-dependent), an exhausted dataset's last row is held rather than
  cleared for the remaining iterations — matching SAS's actual
  "unrefreshed variables keep their prior value" behavior for the common
  case, though it is not a guarantee for every many-to-many pairing.
- **`CALL SYMPUT`** writes into a runtime dict but, because macro
  expansion is a complete pass that finishes before the DATA step ever
  runs, it cannot feed back into `%IF`/`%DO` control flow the way real
  SAS's interleaved macro/DATA-step execution can.
- **`CALL EXECUTE`** re-runs the full macro-expand + parse + codegen
  pipeline on every queued snippet at runtime, so it is not free the way
  real SAS's more integrated execution is -- fine for the typical
  "one small block per driver-dataset row" idiom, but not meant for
  queuing thousands of snippets in a tight loop. Queued text is plain
  SAS source, not a resolved macro call, so it cannot itself invoke
  `%macro` control flow beyond what the `DATA _NULL_` step producing it
  already computed into the string.
- The **HASH object** covers single- or multi-key lookups built from a
  `DATASET:` or grown via `.ADD()`, `MULTIDATA:'Y'` (multiple data rows
  per key), sequential `.FIRST()`/`.NEXT()`/`.PREV()`/`.LAST()` walks,
  a companion hash iterator object (`DECLARE HITER hi("h")`), and
  `.OUTPUT(DATASET: "name")` (must be a standalone statement, not
  assigned to a variable).
- `SET` nested inside `IF`/`DO` supports a single dataset with sequential
  cursor reads, `POINT=` random access, or `KEY=` keyed lookup, plus
  `NOBS=`/`END=`; other statements (`MERGE`/`UPDATE`/`WHERE`/`INPUT`/
  `INFILE`/`DATALINES`) stay top-level-only (`FILE` is the exception --
  see below, it's a real per-statement effect and may appear nested inside
  `IF`/`DO`). `SET ... KEY=` doesn't
  use a real SAS index (there's no persistent index infrastructure here):
  the lookup key is inferred as whichever columns of the KEY= dataset are
  also present in the DATA step's own current source row (the `/UNIQUE`
  modifier is accepted and ignored, since a plain lookup is already
  single-match). A miss sets `_IORC_` to a fixed nonzero sentinel (`1`;
  this doesn't match any particular real SAS return-code constant — only
  the zero/nonzero found/not-found distinction is meaningful here) and
  leaves the KEY= dataset's variables at their prior PDV values, matching
  SAS's "unrefreshed variables keep their prior value" behavior.
- **INFILE** supports list input as well as column/pointer-controlled
  ("formatted") input: absolute (`@n`) and relative (`+n`) column pointers,
  `/` to hold the record across the next physical line, `#n` to jump the
  line pointer directly to the nth physical line of the current record,
  `var start-end` (numeric) / `var $ start-end` (character) column ranges,
  and width informats (`var w.` / `var $w.`, with an accepted `.d` decimal
  suffix that applies implied-decimal scaling when the field text has no
  literal `.`), plus a small set of named informats: `DATE9.` (and other
  `DATEw.` widths -- the day/MMM/year text is parsed regardless of the
  declared width), `MMDDYYw.`, `YYMMDDw.`, `COMMAw.d`, and `DOLLARw.d`
  (`COMMA`/`DOLLAR` strip `,`/`$`/whitespace before converting to a
  number). `DATE9.`/`MMDDYYw.`/`YYMMDDw.` tolerate `-`/`/` separators
  between date parts as well as none (`01JAN2020`, `01-JAN-2020`,
  `01/15/2020`, `20200115`) and convert into the same SAS date serial a
  `'ddMONyyyy'd` date literal would produce. A 2-digit year is windowed
  with a pivot at 26 (`00`-`25` -> `20xx`, `26`-`99` -> `19xx`), matching
  the date-literal parser's convention elsewhere in this codebase. Any
  other named informat (e.g. `TIME8.`, `DATETIMEw.`, custom informats)
  falls back to plain numeric parsing (`float(text)`, else missing) rather
  than being recognized -- an explicit scope cut. A bare `#n` leaves the
  column pointer wherever it was (matching real SAS -- only `/` and a
  fresh INPUT statement reset it to column 1). One file per DATA step;
  short lines are padded MISSOVER-style (no FLOWOVER); no `@`
  trailing-column-pointer line hold. **FILE** is a real
  runtime statement, matching SAS: it switches the current `PUT`
  destination at the point it executes, so a DATA step can write to
  several output files -- every `PUT` after a `FILE` statement (until the
  next one executes, following normal `IF`/`DO` control flow) writes to
  that fileref, and `PUT` before any `FILE` has run (or after `FILE
  LOG`/`PRINT`) writes to stdout. A path is opened lazily on first use and
  kept open even if its `FILE` statement re-executes (e.g. inside a loop),
  so re-running the same `FILE "x"` does not truncate or reopen it; `MOD`
  only controls that first open (append vs. truncate). Not supported: a
  dynamic fileref computed at runtime (e.g. a `FILE` target read from a
  variable) -- the path in a `FILE` statement must be a literal string.
- A `LIBNAME`-backed table can be read via `SET`/`MERGE`/a
  DATA step's own output list, any PROC's `DATA=`/`OUT=`, `PROC APPEND`,
  `PROC IMPORT`, `PROC EXPORT`, and `PROC SQL` (SQLite files are `ATTACH`ed
  into duckdb; directories are exposed as `libref.table` views; server-URL
  libraries have their referenced `libref.table`s staged as views).
- Formats affect display (`PROC PRINT`, `PUT()`) but not the underlying
  stored value.
- **ODS** table detection is heuristic (whitespace-aligned columns with
  a consistent field count across the first couple of lines); output
  that doesn't match that shape renders as `<pre>`/monospace narrative
  text (RTF/PDF: preformatted text) instead of a table even if a human
  would call it tabular. Re-opening the *same* destination while it's
  already open closes and writes the
  previous one first rather than erroring or interleaving (HTML, RTF
  and PDF are independent destinations and can all be open at the same
  time).
- PROC steps beyond `PRINT`/`CONTENTS`/`SORT`/`MEANS`/`SUMMARY`/`FREQ`/`APPEND`/
  `FORMAT`/`TRANSPOSE`/`IMPORT`/`EXPORT`/`DATASETS`/`UNIVARIATE`/`RANK`/
  `CORR`/`REG`/`LOGISTIC`/`GLM`/`GENMOD`/`FASTCLUS`/`PRINCOMP`/`CLUSTER`/`TTEST`/
  `ANOVA`/`NPAR1WAY`/`STANDARD`/`REPORT`/`TABULATE`/`COMPARE`/`FCMP`/
  `SQL`/`SURVEYSELECT`/`ARIMA`/`LIFETEST`/`PHREG` raise a clear
  `NotImplementedError` naming the missing PROC, rather than silently
  doing nothing.
- **PROC COMPARE** supports `BY` (a separate report per BY-group),
  `CRITERION=` (a numeric fuzzy-equality tolerance), and `TRANSFORM`
  (applies `LOG`/`SQRT`/`EXP`/`ABS` to named variables in both BASE and
  COMPARE before comparing — no argument forms like `LOG(var+1)` and no
  per-variable functions within one `TRANSFORM` statement). `ID`
  alignment matches repeated key values pairwise in encounter order
  (1st BASE row for a key vs. 1st COMPARE row for that key, 2nd vs.
  2nd, ...), with any extra occurrences on one side reported as
  unmatched.
- **PROC FCMP** functions support scalar numeric/character parameters,
  assignment, `IF`/`THEN`/`ELSE`, and `RETURN(expr)` in their bodies (the
  same DATA-step statement grammar, minus anything dataset-shaped like
  `SET`); there's no `OUTLIB=`-backed persistent package dataset (a
  defined function is simply callable from any later step in the same
  compiled program, `OPTIONS CMPLIB=` is accepted but not required), and
  no `PROC PROTO`/external C-function calls. **`ARRAY` parameters** are
  supported for the common 1-D numeric case only -- declare them
  `FUNCTION name(arr[*], ...)` (or `{*}`), index with `arr{i}`/`arr[i]`,
  and `DIM(arr)` returns the element count; there's no `$` character
  array parameter, no fixed-size (`arr[10]`) or multi-dimensional array
  parameter, and no `HBOUND`/`LBOUND` on one (bounds aren't tracked for
  a plain parameter). At the call site the argument must be a bare
  name of an `ARRAY` currently declared in the calling DATA step. The
  array is passed **by value**: its current element values are copied
  into a fresh list when the function is called, so assignments to
  `arr{i}` inside the function body do not propagate back to the
  caller's SAS array once the call returns. `CALL` to any `PROC FCMP`
  routine (`FUNCTION` or `SUBROUTINE`) now actually runs it -- this is
  a bug fix: it used to be a silent no-op, generating a `pass` and
  discarding the call entirely, regardless of whether the routine had
  side effects. **`OUTARGS`** gives scalar (non-`ARRAY`) parameters
  pass-by-reference, real-SAS style: `OUTARGS name1, name2, ...;` must
  be the routine body's first statement, naming one or more of the
  routine's own scalar parameters; at the call site the corresponding
  argument must be a bare variable name (mirroring the `ARRAY`
  call-site requirement above), which is updated with the routine's
  value for that parameter after the call returns. A routine with
  `OUTARGS` can only be invoked via `CALL name(...)`, not used as a
  value-returning expression (matching real SAS). There's no general
  pass-by-reference support beyond this -- `ARRAY` parameters stay
  by-value as described above, and `OUTARGS` itself is scalar-only.
- **Multi-dimensional ARRAYs** are stored flat in row-major order and
  tested through 3 dimensions (`array c{2,2,2} ...;`); there is no
  declared cap, but going much higher than that is unverified. Both a
  single flat, comma/space-separated initializer list
  (`array g{2,3} g1-g6 (1 2 3 4 5 6)`) and real SAS's nested
  per-row `(1,2,3) (4,5,6)` initializer grouping are supported and
  flatten to the same row-major values.
  `HBOUND`/`LBOUND` on a multi-dim array require an explicit dimension
  number (`HBOUND(grid, 1)`); calling them with just the array name
  raises a clear error instead of guessing which dimension was meant.

## Tests

```
pip install -e ".[dev]"
pytest tests/
```
