"""Runtime support library used by SAS-compiler-generated Python code.

Implements SAS DATA step semantics (missing-value propagation, PDV-style
row iteration, BY-group processing) and a library of SAS built-in
functions. Generated code does `import sas_compiler.runtime as _r` and
calls into this module.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

import pandas as pd

MISSING = float("nan")
SAS_EPOCH = date(1960, 1, 1)

MACRO_VARS: dict = {}

# PROC FORMAT value lists, keyed by format name (character formats keyed
# with their leading '$', matching how FORMAT statements reference them).
USER_FORMATS: dict = {}


def _lookup_user_format(fmt_name: str, value):
    entry = USER_FORMATS.get(fmt_name.lower())
    if entry is None:
        return None
    if "values" in entry:
        v = sas_text(value)
        if v in entry["values"]:
            return entry["values"][v]
        return entry.get("other")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return entry.get("other")
    for lo, hi, label in entry["ranges"]:
        if lo <= v <= hi:
            return label
    return entry.get("other")


class _RowDelete(Exception):
    pass


class _RowReturn(Exception):
    pass


# ---------------- missing-value helpers ----------------
def is_missing(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def truthy(v) -> bool:
    if is_missing(v):
        return False
    if isinstance(v, str):
        return v.strip() != ""
    try:
        return float(v) != 0
    except (TypeError, ValueError):
        return bool(v)


def sas_str(v) -> str:
    if is_missing(v):
        return "."
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return str(v)
    return str(v)


# ---------------- comparisons ----------------
def _num_rank(v):
    """Order key for numeric SAS comparisons: missing sorts lowest."""
    if is_missing(v):
        return float("-inf")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")


def _cmp(a, b):
    if isinstance(a, str) or isinstance(b, str):
        sa = "" if is_missing(a) else str(a)
        sb = "" if is_missing(b) else str(b)
        return -1 if sa < sb else (1 if sa > sb else 0)
    ra, rb = _num_rank(a), _num_rank(b)
    return -1 if ra < rb else (1 if ra > rb else 0)


def eq(a, b):
    return _cmp(a, b) == 0


def ne(a, b):
    return _cmp(a, b) != 0


def lt(a, b):
    return _cmp(a, b) < 0


def gt(a, b):
    return _cmp(a, b) > 0


def le(a, b):
    return _cmp(a, b) <= 0


def ge(a, b):
    return _cmp(a, b) >= 0


def sas_in(v, items) -> bool:
    return any(eq(v, it) for it in items)


# ---------------- arithmetic ----------------
def sdiv(a, b):
    if is_missing(a) or is_missing(b):
        return MISSING
    try:
        if float(b) == 0:
            return MISSING
        return float(a) / float(b)
    except (TypeError, ValueError):
        return MISSING


def spow(a, b):
    try:
        if is_missing(a) or is_missing(b):
            return MISSING
        return float(a) ** float(b)
    except (TypeError, ValueError, OverflowError):
        return MISSING


def concat(a, b) -> str:
    return ("" if is_missing(a) else sas_text(a)) + ("" if is_missing(b) else sas_text(b))


def sas_text(v) -> str:
    if isinstance(v, str):
        return v
    return sas_str(v)


def nomiss_sum(acc, val):
    acc = 0.0 if is_missing(acc) else float(acc)
    if is_missing(val):
        return acc
    return acc + float(val)


# ---------------- string functions ----------------
def substr(s, start, length=None):
    s = sas_text(s)
    start = int(start)
    if start < 1:
        start = 1
    if length is None:
        return s[start - 1:]
    return s[start - 1:start - 1 + int(length)]


def upcase(s):
    return sas_text(s).upper()


def lowcase(s):
    return sas_text(s).lower()


def propcase(s):
    return sas_text(s).title()


def trim(s):
    return sas_text(s).rstrip()


def strip(s):
    return sas_text(s).strip()


def left(s):
    return sas_text(s).lstrip()


def compress(s, chars=None):
    s = sas_text(s)
    if chars is None:
        return re.sub(r"\s+", "", s)
    return "".join(c for c in s if c not in chars)


def length(s):
    if is_missing(s):
        return 0
    return len(sas_text(s))


def lengthn(s):
    return length(s)


def index_(s, sub):
    return sas_text(s).find(sas_text(sub)) + 1


def scan(s, n, delims=" ,;.-/"):
    s = sas_text(s)
    n = int(n)
    words = [w for w in re.split("[" + re.escape(delims) + "]+", s) if w != ""]
    if n < 0:
        n = len(words) + n + 1
    if 1 <= n <= len(words):
        return words[n - 1]
    return ""


def countw(s, delims=" ,;.-/"):
    s = sas_text(s)
    return len([w for w in re.split("[" + re.escape(delims) + "]+", s) if w != ""])


def repeat(s, n):
    s = sas_text(s)
    return s * (int(n) + 1)


def cat(*args):
    return "".join(sas_text(a) for a in args)


def catx(sep, *args):
    parts = [sas_text(a) for a in args if not is_missing(a) and sas_text(a) != ""]
    return sas_text(sep).join(parts)


def cats(*args):
    return "".join(sas_text(a).strip() for a in args if not is_missing(a))


def tranwrd(s, target, repl):
    return sas_text(s).replace(sas_text(target), sas_text(repl))


def indexc(s, chars):
    s = sas_text(s)
    for i, c in enumerate(s):
        if c in chars:
            return i + 1
    return 0


# ---------------- numeric functions ----------------
def round_(x, unit=1.0):
    if is_missing(x):
        return MISSING
    unit = float(unit) if unit else 1.0
    return round(float(x) / unit) * unit


def int_(x):
    if is_missing(x):
        return MISSING
    return float(math.trunc(float(x)))


def floor_(x):
    if is_missing(x):
        return MISSING
    return float(math.floor(float(x)))


def ceil_(x):
    if is_missing(x):
        return MISSING
    return float(math.ceil(float(x)))


def mod_(a, b):
    if is_missing(a) or is_missing(b):
        return MISSING
    b = float(b)
    if b == 0:
        return MISSING
    return math.fmod(float(a), b)


def abs_(x):
    if is_missing(x):
        return MISSING
    return abs(float(x))


def sqrt_(x):
    if is_missing(x) or float(x) < 0:
        return MISSING
    return math.sqrt(float(x))


def exp_(x):
    if is_missing(x):
        return MISSING
    return math.exp(float(x))


def log_(x):
    if is_missing(x) or float(x) <= 0:
        return MISSING
    return math.log(float(x))


def log10_(x):
    if is_missing(x) or float(x) <= 0:
        return MISSING
    return math.log10(float(x))


def sign_(x):
    if is_missing(x):
        return MISSING
    v = float(x)
    return float((v > 0) - (v < 0))


def sum_(*args):
    vals = [float(a) for a in args if not is_missing(a)]
    return sum(vals) if vals else MISSING


def mean_(*args):
    vals = [float(a) for a in args if not is_missing(a)]
    return (sum(vals) / len(vals)) if vals else MISSING


def min_(*args):
    vals = [float(a) for a in args if not is_missing(a)]
    return min(vals) if vals else MISSING


def max_(*args):
    vals = [float(a) for a in args if not is_missing(a)]
    return max(vals) if vals else MISSING


def n_(*args):
    return float(len([a for a in args if not is_missing(a)]))


def nmiss_(*args):
    return float(len([a for a in args if is_missing(a)]))


def std_(*args):
    vals = [float(a) for a in args if not is_missing(a)]
    if len(vals) < 2:
        return MISSING
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(var)


def missing_(x):
    return 1.0 if is_missing(x) else 0.0


def ifn(cond, a, b, c=None):
    if truthy(cond):
        return a
    if is_missing(cond) and c is not None:
        return c
    return b


def ifc(cond, a, b, c=None):
    if truthy(cond):
        return a
    if is_missing(cond) and c is not None:
        return c
    return b


def coalesce(*args):
    for a in args:
        if not is_missing(a):
            return a
    return MISSING


def coalescec(*args):
    for a in args:
        if not is_missing(a) and sas_text(a) != "":
            return a
    return ""


def input_(s, informat=None):
    if informat and informat.strip().startswith("$"):
        return sas_text(s).strip()
    s = sas_text(s).strip()
    if s == "":
        return MISSING
    try:
        return float(s)
    except ValueError:
        return MISSING


def put(x, fmt=None):
    if fmt:
        return apply_format(x, fmt)
    return sas_str(x) if not isinstance(x, str) else x


_FMT_RE = re.compile(r"^\$?([A-Za-z]*)(\d*)\.(\d*)$")


def apply_format(value, fmt: str) -> str:
    """Best-effort rendering of a SAS format (e.g. 'comma12.2', 'dollar10.',
    'date9.', '$char10.', 'percent8.1', 'z5.') for display purposes."""
    if fmt is None:
        return sas_str(value)
    fmt = fmt.strip()
    user_result = _lookup_user_format(fmt.rstrip("."), value)
    if user_result is not None:
        return user_result
    if "." not in fmt:
        fmt += "."
    m = _FMT_RE.match(fmt)
    if not m:
        return sas_str(value)
    name, width, decimals = m.group(1).lower(), m.group(2), m.group(3)
    is_char_fmt = fmt.startswith("$") or name == "char"
    width = int(width) if width else None
    dec = int(decimals) if decimals else 0

    if is_char_fmt:
        s = sas_text(value)
        if width:
            s = s[:width]
        return s

    if is_missing(value):
        return "."

    try:
        v = float(value)
    except (TypeError, ValueError):
        return sas_str(value)

    if name in ("date", "mmddyy", "yymmdd", "ddmmyy", "worddate"):
        d = _to_date(v)
        if not d:
            return "."
        if name == "date":
            return d.strftime("%d%b%Y").upper()
        if name == "mmddyy":
            return d.strftime("%m/%d/%Y" if not width or width >= 10 else "%m/%d/%y")
        if name == "yymmdd":
            return d.strftime("%Y-%m-%d" if not width or width >= 10 else "%y-%m-%d")
        if name == "ddmmyy":
            return d.strftime("%d/%m/%Y" if not width or width >= 10 else "%d/%m/%y")
        if name == "worddate":
            return d.strftime("%B %d, %Y")

    if name == "comma":
        return f"{v:,.{dec}f}"
    if name == "dollar":
        return f"${v:,.{dec}f}"
    if name == "percent":
        return f"{v * 100:.{dec}f}%"
    if name == "z":
        w = width or (dec + 2)
        return f"{v:0{w}.{dec}f}"
    if name in ("best", "", "f"):
        s = f"{v:.{dec}f}" if dec else sas_str(v)
        return s

    return sas_str(v)


# ---------------- date/time functions ----------------
def today():
    return float((date.today() - SAS_EPOCH).days)


def sas_date(y, m, d):
    try:
        return float((date(int(y), int(m), int(d)) - SAS_EPOCH).days)
    except ValueError:
        return MISSING


def _to_date(sasnum):
    if is_missing(sasnum):
        return None
    return SAS_EPOCH + timedelta(days=int(sasnum))


def datepart(dt_val):
    return dt_val


def year_(sasnum):
    d = _to_date(sasnum)
    return float(d.year) if d else MISSING


def month_(sasnum):
    d = _to_date(sasnum)
    return float(d.month) if d else MISSING


def day_(sasnum):
    d = _to_date(sasnum)
    return float(d.day) if d else MISSING


def weekday_(sasnum):
    d = _to_date(sasnum)
    return float(d.isoweekday() % 7 + 1) if d else MISSING


def intck(interval, start, end):
    d1, d2 = _to_date(start), _to_date(end)
    if not d1 or not d2:
        return MISSING
    interval = interval.strip().lower()
    if interval == "day":
        return float((d2 - d1).days)
    if interval == "week":
        return float((d2 - d1).days // 7)
    if interval == "month":
        return float((d2.year - d1.year) * 12 + (d2.month - d1.month))
    if interval == "year":
        return float(d2.year - d1.year)
    return MISSING


def intnx(interval, start, n, alignment=None):
    d = _to_date(start)
    if not d:
        return MISSING
    interval = interval.strip().lower()
    n = int(n)
    if interval == "day":
        d2 = d + timedelta(days=n)
    elif interval == "week":
        d2 = d + timedelta(weeks=n)
    elif interval == "month":
        total = d.year * 12 + (d.month - 1) + n
        y, m = divmod(total, 12)
        day = 1
        d2 = date(y, m + 1, day)
    elif interval == "year":
        d2 = date(d.year + n, d.month, 1)
    else:
        return MISSING
    return float((d2 - SAS_EPOCH).days)


# ---------------- lag ----------------
class _LagQueues:
    def __init__(self):
        self.queues: dict = {}

    def lag(self, key, value, depth=1):
        q = self.queues.setdefault(key, [])
        q.append(value)
        if len(q) > depth:
            result = q[-depth - 1]
        else:
            result = MISSING
        if len(q) > depth + 1:
            del q[0]
        return result


def new_lag_state():
    return _LagQueues()


# ---------------- dataset construction / BY-group iteration ----------------
def _records(df: pd.DataFrame):
    return df.to_dict("records") if df is not None and len(df) else []


def iter_concat(dfs):
    for df in dfs:
        for row in _records(df):
            yield row, {}


def iter_merge_positional(dfs):
    all_rows = [_records(df) for df in dfs]
    n = max((len(r) for r in all_rows), default=0)
    for i in range(n):
        merged = {}
        for rows in all_rows:
            if i < len(rows):
                merged.update(rows[i])
        yield merged, {}


def unsupported(name, *args):
    raise NotImplementedError(f"SAS function {name!r} is not supported by this compiler")


def apply_ds_opts(df, keep=None, drop=None, rename=None, where=None):
    if where is not None:
        rows = [r for r in _records(df) if where(r)]
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=list(df.columns))
    if keep:
        present = [c for c in keep if c in df.columns]
        df = df[present]
    if drop:
        df = df.drop(columns=[c for c in drop if c in df.columns])
    if rename:
        df = df.rename(columns=rename)
    return df


def _by_key(row, by_vars):
    return tuple(row.get(v, MISSING) for v in by_vars)


def _key_changed_prefix(k1, k2, i):
    """True if any of the first i+1 elements differ between two keys."""
    if k1 is None:
        return True
    for j in range(i + 1):
        a, b = k1[j], k2[j]
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            continue
        if a != b:
            return True
    return False


def iter_concat_by(dfs, by_vars):
    rows = []
    for df in dfs:
        rows.extend(_records(df))
    rows.sort(key=lambda r: [_num_rank(r.get(v, MISSING)) if not isinstance(r.get(v, MISSING), str) else r.get(v, "") for v in by_vars])
    n = len(rows)
    for i, row in enumerate(rows):
        key = _by_key(row, by_vars)
        prev_key = _by_key(rows[i - 1], by_vars) if i > 0 else None
        next_key = _by_key(rows[i + 1], by_vars) if i < n - 1 else None
        flags = {}
        for j, v in enumerate(by_vars):
            flags[v] = (
                _key_changed_prefix(prev_key, key, j),
                _key_changed_prefix(key, next_key, j) if next_key is not None else True,
            )
        yield row, flags


def iter_merge_by(named_dfs, by_vars):
    """named_dfs: list of (name, DataFrame, in_flag_var_or_None)."""
    grouped = []
    for name, df, in_flag in named_dfs:
        g: dict = {}
        for row in _records(df):
            k = _by_key(row, by_vars)
            g.setdefault(k, []).append(row)
        grouped.append((name, g, in_flag))

    all_keys = set()
    for _, g, _ in grouped:
        all_keys.update(g.keys())

    def sort_key(k):
        return [_num_rank(v) if not isinstance(v, str) else v for v in k]

    sorted_keys = sorted(all_keys, key=sort_key)
    n = len(sorted_keys)
    for i, key in enumerate(sorted_keys):
        max_len = max((len(g.get(key, [])) for _, g, _ in grouped), default=1)
        max_len = max(max_len, 1)
        for r in range(max_len):
            merged = {}
            for v in by_vars:
                merged[v] = key[by_vars.index(v)]
            for name, g, in_flag in grouped:
                rows_for_key = g.get(key)
                present = rows_for_key is not None
                if present:
                    idx = min(r, len(rows_for_key) - 1)
                    merged.update(rows_for_key[idx])
                if in_flag:
                    merged[in_flag] = present
            flags = {}
            for j, v in enumerate(by_vars):
                prev_key = sorted_keys[i - 1] if i > 0 else None
                next_key = sorted_keys[i + 1] if i < n - 1 else None
                flags[v] = (
                    _key_changed_prefix(prev_key, key, j),
                    _key_changed_prefix(key, next_key, j) if next_key is not None else True,
                )
            yield merged, flags


def iter_once():
    yield {}, {}


# ---------------- output finalization ----------------
def finalize_dataset(rows, keep=None, drop=None, rename=None, fallback_cols=None) -> pd.DataFrame:
    if not rows:
        if keep:
            cols = list(keep)
        elif fallback_cols:
            cols = [c for c in fallback_cols if not drop or c not in drop]
        else:
            cols = []
        df = pd.DataFrame(columns=cols)
    else:
        df = pd.DataFrame(rows)
    if keep:
        present = [c for c in keep if c in df.columns]
        df = df[present]
    if drop:
        df = df.drop(columns=[c for c in drop if c in df.columns])
    if rename:
        df = df.rename(columns=rename)
    return df


# ---------------- statistical PROCs (CORR / REG / LOGISTIC) ----------------
def proc_corr_report(df: pd.DataFrame, cols: list):
    """Print a PROC CORR-style Pearson correlation report (r and p-value
    per pair) and return the correlation matrix as a DataFrame."""
    from scipy import stats as _stats

    sub = df[cols].apply(pd.to_numeric, errors="coerce")
    print(f"{len(cols)} Variables: " + "  ".join(cols))
    print()
    print("Pearson Correlation Coefficients, N = " + str(len(sub)))
    print("Prob > |r| under H0: Rho=0")
    print()
    width = max(10, max(len(c) for c in cols) + 2)
    print("".rjust(width) + "".join(c.rjust(width) for c in cols))
    corr = pd.DataFrame(index=cols, columns=cols, dtype=float)
    for c1 in cols:
        rvals, pvals = [], []
        for c2 in cols:
            pair = sub[[c1, c2]].dropna()
            if c1 == c2:
                r, p = 1.0, 0.0
            elif len(pair) < 2:
                r, p = float("nan"), float("nan")
            else:
                r, p = _stats.pearsonr(pair[c1], pair[c2])
            corr.loc[c1, c2] = r
            rvals.append(r)
            pvals.append(p)
        print(c1.rjust(width) + "".join(f"{v:.4f}".rjust(width) for v in rvals))
        print("".rjust(width) + "".join(
            ("".rjust(width) if c1 == c2n else f"<{p:.4f}>".rjust(width))
            for c2n, p in zip(cols, pvals)
        ))
    print()
    return corr


def proc_reg_fit(df: pd.DataFrame, y: str, xs: list, out_stats: dict | None = None):
    """Fit an OLS regression (statsmodels), print its summary, and
    optionally return the input rows augmented with predicted/residual
    columns per `out_stats` (e.g. {'p': ['pred'], 'r': ['resid']})."""
    import statsmodels.api as sm

    sub = df[[y] + xs].apply(pd.to_numeric, errors="coerce").dropna()
    X = sm.add_constant(sub[xs])
    model = sm.OLS(sub[y], X).fit()
    print(model.summary())
    if not out_stats:
        return None
    result = df.loc[sub.index].copy()
    for name in out_stats.get("p", []):
        result[name] = model.predict(X)
    for name in out_stats.get("r", []):
        result[name] = model.resid
    return result


def proc_logistic_fit(df: pd.DataFrame, y: str, xs: list, out_stats: dict | None = None):
    """Fit a binary logistic regression (statsmodels), print its summary,
    and optionally return predicted-probability columns per `out_stats`."""
    import math as _math
    import statsmodels.api as sm

    sub = df[[y] + xs].apply(pd.to_numeric, errors="coerce").dropna()
    X = sm.add_constant(sub[xs])
    model = sm.Logit(sub[y], X).fit(disp=0)
    print(model.summary())
    print()
    print("Odds Ratio Estimates")
    for name, coef in model.params.items():
        if name == "const":
            continue
        print(f"  {name}: {_math.exp(coef):.4f}")
    if not out_stats:
        return None
    result = df.loc[sub.index].copy()
    for name in out_stats.get("p", []):
        result[name] = model.predict(X)
    return result


# ---------------- LIBNAME / real database integration ----------------
DB_LIBS: dict = {}


def libname(libref: str, conn: str):
    """Register a LIBNAME connection: either a plain SQLite file path, or
    a full SQLAlchemy URL (postgresql://, mysql://, ...)."""
    DB_LIBS[libref.lower()] = conn


def libname_clear(libref: str):
    DB_LIBS.pop(libref.lower(), None)


def db_read_table(libref: str, table: str) -> pd.DataFrame:
    conn = DB_LIBS.get(libref.lower())
    if conn is None:
        raise RuntimeError(f"LIBNAME {libref!r} is not assigned")
    if "://" in conn:
        import sqlalchemy
        engine = sqlalchemy.create_engine(conn)
        df = pd.read_sql_table(table, engine)
    else:
        import sqlite3
        con = sqlite3.connect(conn)
        try:
            df = pd.read_sql_query(f"SELECT * FROM {table}", con)
        finally:
            con.close()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def db_write_table(libref: str, table: str, df: pd.DataFrame, if_exists: str = "replace"):
    conn = DB_LIBS.get(libref.lower())
    if conn is None:
        raise RuntimeError(f"LIBNAME {libref!r} is not assigned")
    if "://" in conn:
        import sqlalchemy
        engine = sqlalchemy.create_engine(conn)
        df.to_sql(table, engine, if_exists=if_exists, index=False)
    else:
        import sqlite3
        con = sqlite3.connect(conn)
        try:
            df.to_sql(table, con, if_exists=if_exists, index=False)
            con.commit()
        finally:
            con.close()


# ---------------- statistical PROCs (GLM / FASTCLUS) ----------------
def proc_glm_fit(df: pd.DataFrame, y: str, xs: list, class_vars: list | None = None,
                  out_stats: dict | None = None):
    """Fit an OLS model (statsmodels) like proc_reg_fit, but variables
    named in `class_vars` are dummy-encoded (drop_first) as categorical
    predictors instead of being coerced to numeric."""
    import statsmodels.api as sm

    class_vars = class_vars or []
    cont_vars = [x for x in xs if x not in class_vars]

    sub = df[[y] + xs].copy()
    sub[y] = pd.to_numeric(sub[y], errors="coerce")
    for c in cont_vars:
        sub[c] = pd.to_numeric(sub[c], errors="coerce")
    for c in class_vars:
        sub[c] = sub[c].astype(str)
    sub = sub.dropna()

    design_parts = []
    if cont_vars:
        design_parts.append(sub[cont_vars].astype(float))
    if class_vars:
        design_parts.append(pd.get_dummies(sub[class_vars], drop_first=True, dtype=float))
    X = pd.concat(design_parts, axis=1) if design_parts else pd.DataFrame(index=sub.index)
    X = sm.add_constant(X)

    model = sm.OLS(sub[y], X).fit()
    print(model.summary())
    if not out_stats:
        return None
    result = df.loc[sub.index].copy()
    for name in out_stats.get("p", []):
        result[name] = model.predict(X)
    for name in out_stats.get("r", []):
        result[name] = model.resid
    return result


def proc_fastclus_fit(df: pd.DataFrame, cols: list, k: int = 2):
    """K-means clustering (scikit-learn). Prints a cluster-frequency /
    cluster-means summary and returns the input rows augmented with a
    1-based `cluster` column (matching SAS's 1-based CLUSTER numbering)."""
    from sklearn.cluster import KMeans

    sub = df[cols].apply(pd.to_numeric, errors="coerce")
    mask = sub.notna().all(axis=1)
    clean = sub[mask]

    km = KMeans(n_clusters=k, n_init=10, random_state=0)
    labels = km.fit_predict(clean) + 1  # 1-based, like SAS

    print("The FASTCLUS Procedure")
    print(f"Number of Clusters: {k}")
    print()
    label_series = pd.Series(labels, index=clean.index, name="cluster")
    sizes = label_series.value_counts().sort_index()
    means = clean.groupby(label_series).mean()
    summary = means.copy()
    summary.insert(0, "Frequency", sizes)
    print("Cluster Means")
    print(summary.to_string())
    print()

    result = df.loc[mask].copy()
    result["cluster"] = labels
    return result


# ---------------- PROC SGPLOT ----------------
def proc_sgplot_render(df: pd.DataFrame, plots: list, out_path: str, title: str | None = None):
    """Render one or more overlaid SGPLOT-style plot statements onto a
    single figure and save it as a PNG (there is no interactive display
    in this environment, so PROC SGPLOT always writes to a file)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    xlabel = ylabel = None
    for p in plots:
        kind = p["kind"]
        if kind == "scatter":
            sub = df[[p["x"], p["y"]]].dropna()
            ax.scatter(sub[p["x"]], sub[p["y"]])
            xlabel, ylabel = p["x"], p["y"]
        elif kind == "series":
            sub = df[[p["x"], p["y"]]].dropna().sort_values(p["x"])
            ax.plot(sub[p["x"]], sub[p["y"]])
            xlabel, ylabel = p["x"], p["y"]
        elif kind == "vbar":
            cat = p["category"]
            if p.get("response"):
                agg = df.groupby(cat, dropna=False)[p["response"]].sum()
                ylabel = p["response"]
            else:
                agg = df[cat].value_counts()
                ylabel = "Count"
            ax.bar([str(v) for v in agg.index], agg.values)
            xlabel = cat
        elif kind == "histogram":
            var = p["var"]
            ax.hist(df[var].dropna())
            xlabel, ylabel = var, "Count"
        else:
            raise ValueError(f"unsupported PROC SGPLOT statement kind: {kind!r}")
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    print(f"PROC SGPLOT: saved {out_path}")
    return out_path
