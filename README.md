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
`ELSE` and subsetting `IF`, `OUTPUT` (single or multiple output datasets),
`DROP`/`KEEP`/`LENGTH`, `WHERE`, `PUT`, `CALL SYMPUT`/`CALL MISSING`,
`INPUT`/`DATALINES`/`CARDS`, `DELETE`, `RETURN`, `LABEL` (shown as
`PROC PRINT` column headers), and a broad function library (string,
numeric, date, `LAG`, `IFN`/`IFC`, `COALESCE`, etc).

**PROC steps:** `PRINT` (with an `Obs` column and `FORMAT`-aware display),
`SORT` (`BY`, `OUT=`, `NODUPKEY`), `MEANS`/`SUMMARY` (`CLASS`, `VAR`,
`OUTPUT OUT=`, and explicit stat keywords — `N MEAN STD MIN MAX SUM
MEDIAN VAR RANGE NMISS P1...P99` — in place of the N/MEAN/STD/MIN/MAX
default), `FREQ` (one-way and two-way `TABLES`), `APPEND`,
`TRANSPOSE` (`BY`/`VAR`, with or without `ID`), `IMPORT`/`EXPORT`
(CSV, via `DATAFILE=`/`OUTFILE=`), `DATASETS` (`DELETE`, `CHANGE`),
`UNIVARIATE` (moments, mode, quantiles, extreme observations —
printed report only, no `OUTPUT OUT=`), `RANK` (`VAR`/`RANKS`/`BY`,
`DESCENDING`, average-rank ties), `FORMAT` (see below), `GLM`, `FASTCLUS`
(see below), `REPORT` (`COLUMN`/`DEFINE ... / GROUP|ANALYSIS stat|DISPLAY`
— grouped-and-summarized when a `GROUP` and an `ANALYSIS` variable are
both defined, else a plain listing of the `COLUMN` variables),
`TABULATE` (`CLASS`/`VAR`/`TABLE row, col*var*stat` two-way pivots via
`SUM`/`MEAN`/`N`, with an optional `OUT=`), and `SQL` — SQL statements
are executed almost verbatim
against duckdb with all current datasets registered as views, so most
standard SQL (joins, GROUP BY/HAVING, window functions, CTEs) works
without any special-casing here.

**Statistics / ML:** `CORR` (Pearson r and p-value matrix, plus an
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
libref to an actual SQLite database file, or `LIBNAME libref
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

## Known limitations

These are deliberate scope cuts, not oversights — real SAS is enormous:

- **PDV typing** is inferred heuristically (assignment of a string
  literal or a char-returning function marks a variable as character);
  an uninitialized character variable read before any assignment may
  come back as missing-numeric instead of blank.
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
- Only `SET`/`MERGE`/`WHERE`/`INPUT`/`DATALINES` at the **top level** of
  a DATA step are supported (not nested inside `IF`/`DO`); table-lookup
  patterns like `SET ds POINT=;` aren't implemented.
- A `LIBNAME`-backed table can only be read/written via `SET`/`MERGE`/a
  DATA step's own output list, or (SQLite only) directly in `PROC SQL`.
  A PROC's own `DATA=`/`OUT=` doesn't resolve a libref table directly —
  stage it into a `work.` dataset with a `SET` first.
- Formats affect display (`PROC PRINT`, `PUT()`) but not the underlying
  stored value.
- PROC steps beyond `PRINT`/`SORT`/`MEANS`/`SUMMARY`/`FREQ`/`APPEND`/
  `FORMAT`/`TRANSPOSE`/`IMPORT`/`EXPORT`/`DATASETS`/`UNIVARIATE`/`RANK`/
  `CORR`/`REG`/`LOGISTIC`/`GLM`/`FASTCLUS`/`REPORT`/`TABULATE`/`SQL` raise a clear
  `NotImplementedError` naming the missing PROC, rather than silently
  doing nothing.

## Tests

```
pip install -e ".[dev]"
pytest tests/
```
