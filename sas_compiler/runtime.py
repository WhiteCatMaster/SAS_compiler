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
def finalize_dataset(rows, keep=None, drop=None, rename=None) -> pd.DataFrame:
    if not rows:
        cols = []
        if keep:
            cols = list(keep)
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
