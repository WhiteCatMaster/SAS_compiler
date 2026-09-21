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
names, explicit bounds via `array x{2020:2023}`, and `_TEMPORARY_`
lookup arrays) plus `DIM()`/`HBOUND()`/`LBOUND()` and `DO OVER`,
`RETAIN`, the sum statement
(`var + expr;`), `DO`/`DO WHILE`/`DO UNTIL`/iterative `DO`, `IF`/`THEN`/
`ELSE` and subsetting `IF`, `SELECT (expr); WHEN (...) ...; OTHERWISE ...; END;`
(both value-list and bare-condition forms), `STOP`/`LEAVE`/`CONTINUE` (with
correct `CONTINUE` semantics inside iterative and `DO UNTIL` loops),
`UPDATE master trans; BY ...;` (non-missing transaction values overlay the
master, one output row per BY group), `OUTPUT` (single or multiple output datasets),
`DROP`/`KEEP`/`LENGTH`, `WHERE`, `PUT`, `CALL SYMPUT`/`CALL MISSING`,
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
count/percent datasets), `APPEND`,
`TRANSPOSE` (`BY`/`VAR`, with or without `ID`, `PREFIX=`/`SUFFIX=`/`DELIMITER=`), `IMPORT`/`EXPORT`
(CSV, via `DATAFILE=`/`OUTFILE=`), `DATASETS` (`DELETE`, `CHANGE` —
including `libref.table` file/table renames and drops),
`UNIVARIATE` (moments, mode, quantiles, extreme observations, plus an
optional `OUTPUT OUT=` moments dataset), `RANK` (`VAR`/`RANKS`/`BY`,
`DESCENDING`, average-rank ties, `GROUPS=` ntile buckets),
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
printed cluster-frequency/cluster-means summary). `MODEL y = x1 x2
...;` is the shared syntax for `REG`, `LOGISTIC`, and `GLM`.

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
extra sync step. `LIBNAME libref CLEAR;` unassigns it. Non-SQLite URLs
(Postgres/MySQL/etc) work for `SET`/`MERGE`/`DATA` read/write-through
via SQLAlchemy, but are not yet wired into `PROC SQL`'s duckdb ATTACH
(only SQLite is) — query those through a `work.` dataset staged via a
`SET` first.

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

**ODS:** `ODS HTML FILE="report.html"; ... ODS HTML CLOSE;` and
`ODS RTF FILE="report.rtf"; ... ODS RTF CLOSE;` redirect everything any
PROC step would otherwise print (between the open/close pair) into a
report file instead of the console — this works for every PROC
automatically, via stdout redirection, not by special-casing each one.
HTML and RTF each track their own open/closed state independently, so
both can be open at once (each capturing the same printed output into
its own file); closing one leaves the other open. HTML output detects
blocks of `to_string()`-style whitespace-aligned tabular text and
renders them as real `<table>`/`<tr>`/`<td>` markup with light CSS
borders/padding, falling back to a plain `<pre>` block for narrow
output or anything that doesn't parse as a table; RTF output is a
minimal valid `{\rtf1 ...}` document with the captured text as
monospace paragraphs. Other ODS destinations/statements (`LISTING`,
`PDF`, `SELECT`/`EXCLUDE`, `_ALL_`, ...) are parsed and safely ignored
rather than raising an error.

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
- The **HASH object** covers single- or multi-key lookups built from a
  `DATASET:` or grown via `.ADD()`, `MULTIDATA:'Y'` (multiple data rows
  per key), sequential `.FIRST()`/`.NEXT()`/`.PREV()`/`.LAST()` walks,
  a companion hash iterator object (`DECLARE HITER hi("h")`), and
  `.OUTPUT(DATASET: "name")` (must be a standalone statement, not
  assigned to a variable).
- `SET` nested inside `IF`/`DO` supports a single dataset with sequential
  cursor reads, `POINT=` random access, or `KEY=` keyed lookup, plus
  `NOBS=`/`END=`; other statements (`MERGE`/`UPDATE`/`WHERE`/`INPUT`/
  `INFILE`/`FILE`/`DATALINES`) stay top-level-only. `SET ... KEY=` doesn't
  use a real SAS index (there's no persistent index infrastructure here):
  the lookup key is inferred as whichever columns of the KEY= dataset are
  also present in the DATA step's own current source row (the `/UNIQUE`
  modifier is accepted and ignored, since a plain lookup is already
  single-match). A miss sets `_IORC_` to a fixed nonzero sentinel (`1`;
  this doesn't match any particular real SAS return-code constant — only
  the zero/nonzero found/not-found distinction is meaningful here) and
  leaves the KEY= dataset's variables at their prior PDV values, matching
  SAS's "unrefreshed variables keep their prior value" behavior.
- **INFILE** covers list input only (no column/pointer controls like `@n`,
  `/`, or informats) with one file per DATA step; short lines are padded
  MISSOVER-style (no FLOWOVER). **FILE** supports one output file per DATA
  step (last `FILE` wins); `PUT ... FILE=` per-statement routing isn't
  implemented.
- A `LIBNAME`-backed table can be read via `SET`/`MERGE`/a
  DATA step's own output list, any PROC's `DATA=`/`OUT=`, `PROC APPEND`,
  `PROC IMPORT`, `PROC EXPORT`, and `PROC SQL` (SQLite files are `ATTACH`ed
  into duckdb; directories are exposed as `libref.table` views; server-URL
  libraries have their referenced `libref.table`s staged as views).
- Formats affect display (`PROC PRINT`, `PUT()`) but not the underlying
  stored value.
- **ODS** table detection is heuristic (whitespace-aligned columns with
  a consistent field count across the first couple of lines); output
  that doesn't match that shape renders as `<pre>` narrative text
  instead of a `<table>` even if a human would call it tabular. RTF
  output is plain monospace paragraphs, not real RTF tables. Re-opening
  the *same* destination while it's already open closes and writes the
  previous one first rather than erroring or interleaving (HTML and RTF
  are independent destinations and can be open at the same time).
- PROC steps beyond `PRINT`/`CONTENTS`/`SORT`/`MEANS`/`SUMMARY`/`FREQ`/`APPEND`/
  `FORMAT`/`TRANSPOSE`/`IMPORT`/`EXPORT`/`DATASETS`/`UNIVARIATE`/`RANK`/
  `CORR`/`REG`/`LOGISTIC`/`GLM`/`FASTCLUS`/`REPORT`/`TABULATE`/`COMPARE`/`FCMP`/
  `SQL` raise a clear `NotImplementedError` naming the missing PROC, rather
  than silently doing nothing.
- **PROC COMPARE** supports `BY` (a separate report per BY-group) and
  `CRITERION=` (a numeric fuzzy-equality tolerance), but has no
  `TRANSFORM=`, and its `ID` alignment takes the first row per key
  value when a key repeats rather than matching multiple occurrences
  pairwise.
- **PROC FCMP** functions support scalar numeric/character parameters,
  assignment, `IF`/`THEN`/`ELSE`, and `RETURN(expr)` in their bodies (the
  same DATA-step statement grammar, minus anything dataset-shaped like
  `SET`/arrays-as-parameters); there's no `OUTLIB=`-backed persistent
  package dataset (a defined function is simply callable from any later
  step in the same compiled program, `OPTIONS CMPLIB=` is accepted but
  not required), no `ARRAY` parameters, and no `PROC PROTO`/external
  C-function calls.

## Tests

```
pip install -e ".[dev]"
pytest tests/
```
