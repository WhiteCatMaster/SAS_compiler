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
```

Or from the repo without installing: `python -m sas_compiler.cli program.sas --run`.

See `examples/*.sas` for programs covering macros, arrays, DO loops,
BY-group processing, MERGE, dataset options, and PROC SQL.

## What's supported

**Macro language:** `%LET`, `%MACRO`/`%MEND` (positional and keyword
params with defaults), `%IF`/`%THEN`/`%ELSE`, iterative/`%WHILE`/`%UNTIL`
`%DO` loops, `&var`/`&&var` resolution (including indirect references),
`%PUT`, `%GLOBAL`/`%LOCAL`, `%EVAL`/`%SYSEVALF`, `%STR`/`%NRSTR`, and the
text functions `%UPCASE`/`%LOWCASE`/`%SUBSTR`/`%SCAN`/`%INDEX`/`%LENGTH`/
`%TRIM`/`%CMPRES`. `%SYSFUNC` supports a small allowlist (`TODAY`, `TRIM`,
`UPCASE`, `LOWCASE`, `COMPRESS`).

**DATA step:** `SET`/`MERGE` (with `BY`, including multi-level
`first.`/`last.` group processing), dataset options (`DROP=`, `KEEP=`,
`RENAME=`, `WHERE=`, `IN=`), `ARRAY` (including `array x{n} (v1, v2, ...)`
initializer lists, numeric or character, with or without explicit element
names, and explicit bounds via `array x{2020:2023}`) plus
`DIM()`/`HBOUND()`/`LBOUND()` and `DO OVER`, `RETAIN`, the sum statement
(`var + expr;`), `DO`/`DO WHILE`/`DO UNTIL`/iterative `DO`, `IF`/`THEN`/
`ELSE` and subsetting `IF`, `OUTPUT` (single or multiple output datasets),
`DROP`/`KEEP`/`LENGTH`, `WHERE`, `PUT`, `CALL SYMPUT`/`CALL MISSING`,
`INPUT`/`DATALINES`/`CARDS`, `DELETE`, `RETURN`, and a broad function
library (string, numeric, date, `LAG`, `IFN`/`IFC`, `COALESCE`, etc).

**PROC steps:** `PRINT` (with an `Obs` column and `FORMAT`-aware display),
`SORT` (`BY`, `OUT=`, `NODUPKEY`), `MEANS`/`SUMMARY` (`CLASS`, `VAR`,
`OUTPUT OUT=`), `FREQ` (one-way and two-way `TABLES`), `APPEND`,
`TRANSPOSE` (`BY`/`VAR`, with or without `ID`), `FORMAT` (see below),
and `SQL` — SQL statements are executed almost
verbatim against duckdb with all current datasets registered as views,
so most standard SQL (joins, GROUP BY/HAVING, window functions, CTEs)
works without any special-casing here.

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
- **MERGE** handles the common one-to-one / lookup-merge case well.
  One-to-many merges where *more than one* input dataset has multiple
  matching rows for the same BY value are approximated (shorter
  dataset's last row is reused), which can diverge from SAS's documented
  multi-way merge behavior.
- **`CALL SYMPUT`** writes into a runtime dict but, because macro
  expansion is a complete pass that finishes before the DATA step ever
  runs, it cannot feed back into `%IF`/`%DO` control flow the way real
  SAS's interleaved macro/DATA-step execution can.
- Only `SET`/`MERGE`/`WHERE`/`INPUT`/`DATALINES` at the **top level** of
  a DATA step are supported (not nested inside `IF`/`DO`); table-lookup
  patterns like `SET ds POINT=;` aren't implemented.
- Formats affect display (`PROC PRINT`, `PUT()`) but not the underlying
  stored value, and there's no `PROC FORMAT` for user-defined value
  lists — only the built-in format families listed above.
- PROC steps beyond `PRINT`/`SORT`/`MEANS`/`SUMMARY`/`FREQ`/`APPEND`/`SQL`
  raise a clear `NotImplementedError` naming the missing PROC, rather
  than silently doing nothing.

## Tests

```
pip install -e ".[dev]"
pytest tests/
```
