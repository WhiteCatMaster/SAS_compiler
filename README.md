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
MEDIAN VAR RANGE NMISS P1...P99` — in place of the N/MEAN/STD/MIN/MAX
default — plus `TYPES`/`WAYS` class-combination control), `FREQ` (one-way
and two-way `TABLES`, one- and two-way `OUTPUT OUT=`
count/percent datasets, and a `CHISQ` table option on two-way `TABLES`
for a Pearson chi-square test of independence report), `APPEND`,
`TRANSPOSE` (`BY`/`VAR`, with or without `ID`, `PREFIX=`/`SUFFIX=`/`DELIMITER=`), `IMPORT`/`EXPORT`
(CSV, via `DATAFILE=`/`OUTFILE=`), `DATASETS` (`DELETE`, `CHANGE` —
including `libref.table` file/table renames and drops),
`UNIVARIATE` (moments, mode, quantiles, extreme observations, plus an
optional `OUTPUT OUT=` moments dataset), `RANK` (`VAR`/`RANKS`/`BY`,
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
statsmodels — full summary with R², F-stat, coefficient table, and
`OUTPUT OUT= P=/R=` for predicted values / residuals), `LOGISTIC`
(binary logistic regression via statsmodels — summary, odds ratios, and
`OUTPUT OUT= P=` for predicted probabilities), `GLM` (OLS via
statsmodels like `REG`, plus a `CLASS var1 var2;` statement that
dummy-encodes categorical predictors — drop-first indicator columns —
before fitting; same `OUTPUT OUT= P=/R=` support), and `FASTCLUS`
(k-means clustering via scikit-learn — `VAR var1 var2 ...;` for the
input columns, `MAXCLUSTERS=n` for k (default 2), and `OUTPUT OUT=`
for the input rows plus a 1-based `cluster` column, alongside a
printed cluster-frequency/cluster-means summary), and `TTEST` (via
scipy — one-sample against `H0=` (default 0, `VAR var1 var2 ...;`
alone), two-sample independent groups (`CLASS groupvar; VAR var1
var2 ...;`, reporting both pooled-variance and Satterthwaite/Welch
t-test results), or paired (`PAIRED var1*var2 ...;`, one paired
t-test per pair); prints N/Mean/StdDev/StdErr and the t/df/Pr>|t|
results, no `OUT=` dataset), and `ANOVA` (one-way only — one `CLASS`
variable and `MODEL y = classvar;` — via scipy's `f_oneway`; prints
Class Level Information plus the classic Source/DF/Sum of
Squares/Mean Square/F Value/Pr > F table, R-Square, Coeff Var, and
Root MSE, no `OUT=` dataset). `MODEL y = x1 x2 ...;` is the shared
syntax for `REG`, `LOGISTIC`, `GLM`, and `ANOVA`.

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
for narrow output or anything that doesn't parse as a table; RTF
output is a minimal valid `{\rtf1 ...}` document with the captured
text as monospace paragraphs. PDF output uses the same table-detection
heuristic as HTML — table-shaped chunks render as a real `reportlab`
`Table` flowable with a light grid and shaded header row, everything
else as monospace preformatted text — built into a PDF via
`SimpleDocTemplate`. Other ODS destinations/statements (`LISTING`,
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
  literal `.`). A bare `#n` leaves the column pointer wherever it was
  (matching real SAS -- only `/` and a fresh INPUT statement reset it to
  column 1). One file per DATA step; short lines are padded MISSOVER-style
  (no FLOWOVER); no `@` trailing-column-pointer line hold, no other
  informat families beyond numeric/character width. **FILE** is a real
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
  text instead of a table even if a human would call it tabular. RTF
  output is plain monospace paragraphs, not real RTF tables. Re-opening
  the *same* destination while it's already open closes and writes the
  previous one first rather than erroring or interleaving (HTML, RTF
  and PDF are independent destinations and can all be open at the same
  time).
- PROC steps beyond `PRINT`/`CONTENTS`/`SORT`/`MEANS`/`SUMMARY`/`FREQ`/`APPEND`/
  `FORMAT`/`TRANSPOSE`/`IMPORT`/`EXPORT`/`DATASETS`/`UNIVARIATE`/`RANK`/
  `CORR`/`REG`/`LOGISTIC`/`GLM`/`FASTCLUS`/`REPORT`/`TABULATE`/`COMPARE`/`FCMP`/
  `SQL` raise a clear `NotImplementedError` naming the missing PROC, rather
  than silently doing nothing.
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
  caller's SAS array once the call returns -- there's no general
  pass-by-reference support.
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
