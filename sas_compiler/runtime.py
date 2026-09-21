"""Runtime support library used by SAS-compiler-generated Python code.

Implements SAS DATA step semantics (missing-value propagation, PDV-style
row iteration, BY-group processing) and a library of SAS built-in
functions. Generated code does `import sas_compiler.runtime as _r` and
calls into this module.
"""
from __future__ import annotations

import csv
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


class _DataStop(Exception):
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


def translate(s, to, frm):
    """TRANSLATE(s, to, from): replace each char of `from` with the
    corresponding char of `to` (extra `to` chars ignored, missing ones
    delete the character)."""
    s, to, frm = sas_text(s), sas_text(to), sas_text(frm)
    table = {}
    for i, c in enumerate(frm):
        table[c] = to[i] if i < len(to) else ""
    return "".join(table.get(c, c) for c in s)


def verify(s, chars):
    """VERIFY(s, chars): 1-based position of the first char of s not in
    chars; 0 when every char is in chars (or s is empty)."""
    s = sas_text(s)
    for i, c in enumerate(s):
        if c not in chars:
            return i + 1
    return 0


def prxmatch(pattern, source):
    """PRXMATCH('/re/flags', source): 1-based position of the first regex
    match, or 0 when nothing matches. Only the trailing-i (IGNORECASE)
    flag is honored."""
    pat = sas_text(pattern)
    flags = 0
    m = re.match(r"^/(.*)/([a-zA-Z]*)$", pat, re.S)
    if m:
        pat, flagstr = m.group(1), m.group(2).lower()
        if "i" in flagstr:
            flags |= re.IGNORECASE
    try:
        hit = re.search(pat, sas_text(source), flags)
    except re.error:
        return 0.0
    return float(hit.start() + 1) if hit else 0.0


def indexc(s, chars):
    s = sas_text(s)
    for i, c in enumerate(s):
        if c in chars:
            return i + 1
    return 0


def compbl(s):
    """COMPBL: every run of 2+ blanks becomes a single blank."""
    return re.sub(r" {2,}", " ", sas_text(s))


def reverse_(s):
    return sas_text(s)[::-1]


def quote_(s, q='"'):
    s, q = sas_text(s), sas_text(q)[:1] or '"'
    return q + s.replace(q, q + q) + q


def dequote(s):
    s = sas_text(s)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].replace(s[0] * 2, s[0])
    return s


def findc(s, chars, modifiers=None, start=None):
    """FINDC(s, chars): 1-based position of the first char of s found in
    chars (0 when none). Modifiers: 'i' ignores case. A negative `start`
    searches right-to-left and returns a negative position."""
    s = sas_text(s)
    if isinstance(modifiers, str) and "i" in modifiers.lower():
        s, chars = s.lower(), sas_text(chars).lower()
    else:
        chars = sas_text(chars)
    try:
        pos = int(start) if start is not None else 1
    except (TypeError, ValueError):
        pos = 1
    if pos >= 1:
        for i in range(pos - 1, len(s)):
            if s[i] in chars:
                return i + 1
        return 0
    for i in range(len(s) + pos, -1, -1):
        if 0 <= i < len(s) and s[i] in chars:
            return -(i + 1)
    return 0


def findw(s, word, delims=None, modifiers=None, start=None):
    """FINDW(s, word): 1-based character position where `word` appears as a
    whole word (delimited); 0 when absent. Approximation of SAS's default
    delimiter set when `delims` is omitted; negative `start` is treated
    as 1 (forward search)."""
    s = sas_text(s)
    word = sas_text(word)
    d = sas_text(delims) if delims is not None else " .,;:-/()[]{}'\"_=+*^~!?|\\\t"
    mods = sas_text(modifiers).lower() if modifiers is not None else ""
    flags = re.IGNORECASE if "i" in mods else 0
    try:
        pos = int(start) if start is not None else 1
    except (TypeError, ValueError):
        pos = 1
    if pos < 1:
        pos = 1
    seq = s[pos - 1:]
    dc = re.escape(d)
    m = re.search(r"(?:(?<=^)|(?<=[" + dc + r"]))" + re.escape(word) + r"(?:(?=$)|(?=[" + dc + r"]))", seq, flags)
    if not m:
        return 0
    return pos + m.start()


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

    if name in ("date", "mmddyy", "yymmdd", "ddmmyy", "worddate", "time", "datetime"):
        if name == "time":
            t = _to_time(v)
            if not t:
                return "."
            return f"{t[0]:02d}:{t[1]:02d}:{t[2]:02d}"
        if name == "datetime":
            d = _to_datetime(v)
            if not d:
                return "."
            return d.strftime("%d%b%Y:%H:%M:%S").upper()
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


def time_():
    from datetime import datetime as _dt

    now = _dt.now()
    return float(now.hour * 3600 + now.minute * 60 + now.second)


def datetime_():
    from datetime import datetime as _dt

    return float((_dt.now() - _dt(1960, 1, 1)).total_seconds())


def sas_date(y, m, d):
    try:
        return float((date(int(y), int(m), int(d)) - SAS_EPOCH).days)
    except ValueError:
        return MISSING


def sas_time(h, m, s=0):
    try:
        h, m, s = int(h), int(m), int(s)
    except (TypeError, ValueError):
        return MISSING
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        return MISSING
    return float(h * 3600 + m * 60 + s)


def sas_datetime(y, mo, d, h=0, mi=0, s=0):
    try:
        from datetime import datetime as _dt

        return float((_dt(int(y), int(mo), int(d), int(h), int(mi), int(s)) - _dt(1960, 1, 1)).total_seconds())
    except (TypeError, ValueError):
        return MISSING


def _to_date(sasnum):
    if is_missing(sasnum):
        return None
    return SAS_EPOCH + timedelta(days=int(sasnum))


def _to_time(sastime):
    """SAS time (seconds since midnight) -> (h, m, s), or None if missing."""
    if is_missing(sastime):
        return None
    total = int(sastime) % 86400
    return total // 3600, (total % 3600) // 60, total % 60


def _to_datetime(sasdt):
    """SAS datetime (seconds since 1960-01-01) -> datetime, or None."""
    if is_missing(sasdt):
        return None
    from datetime import datetime as _dt

    return _dt(1960, 1, 1) + timedelta(seconds=float(sasdt))


def datepart(dt_val):
    if is_missing(dt_val):
        return MISSING
    return float(int(float(dt_val) // 86400))


def timepart(dt_val):
    if is_missing(dt_val):
        return MISSING
    return float(float(dt_val) % 86400)


def dhms(d, h, m, s):
    if any(is_missing(v) for v in (d, h, m, s)):
        return MISSING
    return float(float(d) * 86400 + float(h) * 3600 + float(m) * 60 + float(s))


def hms(h, m, s):
    return sas_time(h, m, s)


def hour_(sasdt):
    d = _to_datetime(sasdt)
    if d is None:
        t = _to_time(sasdt)
        return float(t[0]) if t else MISSING
    return float(d.hour)


def minute_(sasdt):
    d = _to_datetime(sasdt)
    if d is None:
        t = _to_time(sasdt)
        return float(t[1]) if t else MISSING
    return float(d.minute)


def second_(sasdt):
    d = _to_datetime(sasdt)
    if d is None:
        t = _to_time(sasdt)
        return float(t[2]) if t else MISSING
    return float(d.second)


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
    if interval in ("qtr", "quarter"):
        return float((d2.year - d1.year) * 4 + ((d2.month - 1) // 3 - (d1.month - 1) // 3))
    if interval in ("semiyear", "halfyear"):
        return float((d2.year - d1.year) * 2 + ((d2.month - 1) // 6 - (d1.month - 1) // 6))
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
    elif interval in ("qtr", "quarter"):
        total = d.year * 4 + (d.month - 1) // 3 + n
        y, q = divmod(total, 4)
        d2 = date(y, q * 3 + 1, 1)
    elif interval in ("semiyear", "halfyear"):
        total = d.year * 2 + (d.month - 1) // 6 + n
        y, h = divmod(total, 2)
        d2 = date(y, h * 6 + 1, 1)
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


def dif_(lag_state, key, value, depth=1):
    """DIF(x): x minus its lagged value (missing when the lag is missing)."""
    prev = lag_state.lag(key, value, depth)
    if is_missing(prev) or is_missing(value):
        return MISSING
    try:
        return float(value) - float(prev)
    except (TypeError, ValueError):
        return MISSING


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


def seed_char_defaults(pdv: dict, skip, *dfs):
    """Seed pdv[col] = '' for every object-dtype (character) column across
    the given SET/MERGE/UPDATE source DataFrame(s), so a character variable
    read before it's explicitly assigned in the DATA step body defaults to
    blank rather than falling through to numeric-missing (NaN). `skip` is
    the set of names the compiler already has static evidence for (either
    already seeded as char, or explicitly declared numeric via LENGTH/
    ARRAY) -- those are left alone. A name already present in `pdv` (e.g.
    a RETAIN-seeded numeric initial value) is also left alone."""
    for df in dfs:
        if df is None:
            continue
        for col in df.columns:
            if col in skip or col in pdv:
                continue
            dt = df[col].dtype
            if dt == object or pd.api.types.is_string_dtype(dt):
                pdv[col] = ""


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


def iter_update_by(named_dfs, by_vars):
    """SAS UPDATE semantics: first dataset is the master, the rest are
    transactions applied in order. For each BY key, start from the master
    row (if any) and overlay each transaction row's non-missing values;
    emit exactly one row per BY key."""
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
        merged: dict = {}
        for v in by_vars:
            merged[v] = key[by_vars.index(v)]
        for idx, (name, g, in_flag) in enumerate(grouped):
            rows_for_key = g.get(key)
            present = rows_for_key is not None
            if present:
                if idx == 0:
                    merged.update(rows_for_key[-1])
                else:
                    for trow in rows_for_key:
                        for col, val in trow.items():
                            if col in by_vars:
                                continue
                            if not is_missing(val):
                                merged[col] = val
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


def read_infile(path, varspec, dlm=None, dsd=False, firstobs=1, obs=None):
    """Read a raw text file for INFILE + INPUT (list input).

    varspec: [(name, is_char), ...]. Blank lines are skipped; FIRSTOBS/OBS
    select 1-based physical records before blank-line filtering. Without
    DSD, consecutive delimiters collapse; with DSD (csv parsing), empty
    fields and quoted values are honored. Short lines are padded with
    missing (MISSOVER behavior)."""
    with open(path, "r", newline="") as f:
        lines = f.read().splitlines()
    lo = max(int(firstobs or 1) - 1, 0)
    hi = int(obs) if obs is not None else None
    lines = lines[lo:hi]
    rows = []
    for line in lines:
        if line.strip() == "":
            continue
        if dlm is None:
            fields = line.split()
        elif dsd:
            fields = next(csv.reader([line], delimiter=dlm, skipinitialspace=True))
        else:
            fields = [p.strip() for p in line.split(dlm) if p.strip() != ""]
        row = {}
        for i, (name, is_char) in enumerate(varspec):
            raw = fields[i] if i < len(fields) else ""
            if is_char:
                row[name] = raw
            else:
                try:
                    row[name] = float(raw)
                except ValueError:
                    row[name] = MISSING
        rows.append(row)
    return rows


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


def proc_corr_with_report(df: pd.DataFrame, cols: list, with_cols: list):
    """PROC CORR with a WITH statement: Pearson r (and p-value) of each
    VAR variable against each WITH variable. Returns the r DataFrame
    (index=VAR vars, columns=WITH vars)."""
    from scipy import stats as _stats

    allc = list(dict.fromkeys(cols + with_cols))
    sub = df[allc].apply(pd.to_numeric, errors="coerce")
    print(f"Variables: {'  '.join(cols)}")
    print(f"With Variables: {'  '.join(with_cols)}")
    print()
    width = max(10, max(len(c) for c in allc) + 2)
    print("".rjust(width) + "".join(c.rjust(width) for c in with_cols))
    out = pd.DataFrame(index=cols, columns=with_cols, dtype=float)
    for c1 in cols:
        rvals, pvals = [], []
        for c2 in with_cols:
            pair = sub[[c1, c2]].dropna()
            if c1 == c2:
                r, p = 1.0, 0.0
            elif len(pair) < 2:
                r, p = float("nan"), float("nan")
            else:
                r, p = _stats.pearsonr(pair[c1], pair[c2])
            out.loc[c1, c2] = r
            rvals.append(r)
            pvals.append(p)
        print(c1.rjust(width) + "".join(f"{v:.4f}".rjust(width) for v in rvals))
        print("".rjust(width) + "".join(f"<{p:.4f}>".rjust(width) for p in pvals))
    print()
    return out


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
    """Register a LIBNAME connection: a plain SQLite file path, a full
    SQLAlchemy URL (postgresql://, mysql://, ...), or a directory path --
    in which case libref.table resolves to <table>.sas7bdat / <table>.csv
    files inside that directory."""
    DB_LIBS[libref.lower()] = conn


def libname_clear(libref: str):
    DB_LIBS.pop(libref.lower(), None)


def _decode_dir_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a directory-backed table: bytes -> str, lowercase columns."""
    for c in list(df.columns):
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: v.decode() if isinstance(v, (bytes, bytearray)) else v)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _dir_table_path(conn: str, table: str) -> str | None:
    """Find libref.table inside a directory lib: .sas7bdat first, then .csv."""
    import os

    for ext in (".sas7bdat", ".csv"):
        for cand in (table + ext, table.lower() + ext, table.upper() + ext):
            p = os.path.join(conn, cand)
            if os.path.isfile(p):
                return p
    return None


def sql_attach_dir(con, libref: str, conn: str):
    """Expose every table in a directory libref as libref.table views in a
    duckdb connection (via CREATE SCHEMA + per-table views), so PROC SQL
    can query real files with plain `libref.table` references."""
    import os

    libref = libref.lower()
    try:
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{libref}"')
        for fn in sorted(os.listdir(conn)):
            low = fn.lower()
            if low.endswith(".sas7bdat"):
                table, df = fn[:-9].lower(), _decode_dir_frame(pd.read_sas(os.path.join(conn, fn)))
            elif low.endswith(".csv"):
                table, df = fn[:-4].lower(), _decode_dir_frame(pd.read_csv(os.path.join(conn, fn)))
            else:
                continue
            if not table.replace("_", "").isalnum():
                continue
            reg = f"_saslib_{libref}_{table}"
            con.register(reg, df)
            con.execute(f'CREATE OR REPLACE VIEW "{libref}"."{table}" AS SELECT * FROM "{reg}"')
    except Exception as e:
        print(f"warning: could not attach directory library {libref!r}: {e}")


def db_read_table(libref: str, table: str) -> pd.DataFrame:
    conn = DB_LIBS.get(libref.lower())
    if conn is None:
        raise RuntimeError(f"LIBNAME {libref!r} is not assigned")
    import os

    if os.path.isdir(conn):
        path = _dir_table_path(conn, table)
        if path is None:
            raise RuntimeError(
                f"LIBNAME {libref!r} directory {conn!r} has no table {table!r} "
                f"(looked for {table}.sas7bdat / {table}.csv)"
            )
        if path.lower().endswith(".csv"):
            return _decode_dir_frame(pd.read_csv(path))
        return _decode_dir_frame(pd.read_sas(path))
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
    import os

    if os.path.isdir(conn):
        df.to_csv(os.path.join(conn, table.lower() + ".csv"), index=False)
        return
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


def db_delete_table(libref: str, table: str):
    """PROC DATASETS DELETE for a LIBNAME table: remove the backing file(s)
    (directory lib) or DROP TABLE (SQLite / server lib). Warns instead of
    raising when there is nothing to delete."""
    conn = DB_LIBS.get(libref.lower())
    if conn is None:
        raise RuntimeError(f"LIBNAME {libref!r} is not assigned")
    import os

    if os.path.isdir(conn):
        removed = False
        for fn in os.listdir(conn):
            low = fn.lower()
            if low in (table.lower() + ".sas7bdat", table.lower() + ".csv"):
                try:
                    os.remove(os.path.join(conn, fn))
                    removed = True
                except OSError as e:
                    print(f"warning: could not delete {fn!r}: {e}")
        if not removed:
            print(f"warning: LIBNAME {libref!r} has no table {table!r} to delete")
        return
    if "://" in conn:
        import sqlalchemy
        try:
            with sqlalchemy.create_engine(conn).begin() as c:
                c.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}"')
        except Exception as e:
            print(f"warning: could not delete {libref}.{table}: {e}")
        return
    import sqlite3
    con = sqlite3.connect(conn)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{table}"')
        con.commit()
    finally:
        con.close()


def db_rename_table(libref: str, old: str, new: str):
    """PROC DATASETS CHANGE for a LIBNAME table: rename the backing file
    (directory lib) or ALTER TABLE ... RENAME (SQLite / server lib)."""
    conn = DB_LIBS.get(libref.lower())
    if conn is None:
        raise RuntimeError(f"LIBNAME {libref!r} is not assigned")
    import os

    if os.path.isdir(conn):
        path = _dir_table_path(conn, old)
        if path is None:
            print(f"warning: LIBNAME {libref!r} has no table {old!r} to rename")
            return
        ext = ".csv" if path.lower().endswith(".csv") else ".sas7bdat"
        try:
            os.rename(path, os.path.join(conn, new.lower() + ext))
        except OSError as e:
            print(f"warning: could not rename {old!r} to {new!r}: {e}")
        return
    if "://" in conn:
        import sqlalchemy
        try:
            with sqlalchemy.create_engine(conn).begin() as c:
                c.exec_driver_sql(f'ALTER TABLE "{old}" RENAME TO "{new}"')
        except Exception as e:
            print(f"warning: could not rename {libref}.{old}: {e}")
        return
    import sqlite3
    con = sqlite3.connect(conn)
    try:
        con.execute(f'ALTER TABLE "{old}" RENAME TO "{new}"')
        con.commit()
    except Exception as e:
        print(f"warning: could not rename {libref}.{old}: {e}")
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
        elif kind == "hbar":
            cat = p["category"]
            if p.get("response"):
                agg = df.groupby(cat, dropna=False)[p["response"]].sum()
                xlabel = p["response"]
            else:
                agg = df[cat].value_counts()
                xlabel = "Count"
            ax.barh([str(v) for v in agg.index], agg.values)
            ylabel = cat
        elif kind == "histogram":
            var = p["var"]
            ax.hist(df[var].dropna())
            xlabel, ylabel = var, "Count"
        elif kind == "density":
            var = p["var"]
            sub = pd.to_numeric(df[var], errors="coerce").dropna()
            if len(sub) > 1 and sub.nunique() > 1:
                try:
                    from scipy.stats import gaussian_kde as _kde

                    xs = pd.Series(
                        [sub.min() + (sub.max() - sub.min()) * i / 199 for i in range(200)]
                    )
                    ax.plot(xs, _kde(sub)(xs))
                except Exception:
                    ax.hist(sub, density=True)
            else:
                ax.hist(sub, density=True)
            xlabel, ylabel = var, "Density"
        elif kind == "refline":
            for v in p.get("values", []):
                ax.axvline(v)
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


# ---------------- DATA step HASH object ----------------
class SasHash:
    """A DATA step HASH object: fast key -> data lookup, built either from
    a source dataset (`declare hash h(dataset: "lookup");`) or grown
    dynamically via .add(). Keys are stored as tuples of the raw (missing-
    aware) key values; data is stored as a dict of {varname: value}. With
    MULTIDATA:'Y', each key maps to a *list* of data dicts (one per row)."""

    def __init__(self, source_df: "pd.DataFrame | None" = None, multi: bool = False):
        self.keys: list = []
        self.datas: list = []
        self.table: dict = {}
        self.multi = multi
        self._source_df = source_df

    def definekey(self, *names):
        self.keys.extend(n.lower() for n in names)

    def definedata(self, *names):
        self.datas.extend(n.lower() for n in names)

    def _store(self, key, data):
        if self.multi:
            self.table.setdefault(key, []).append(data)
        else:
            self.table[key] = data

    def definedone(self):
        if self._source_df is None:
            return
        for row in self._source_df.to_dict("records"):
            key = tuple(row.get(k, MISSING) for k in self.keys)
            data = {d: row.get(d, MISSING) for d in self.datas}
            self._store(key, data)
        self._source_df = None

    def _current_key(self, pdv: dict, key_values=None) -> tuple:
        if key_values is not None:
            return tuple(key_values)
        return tuple(pdv.get(k, MISSING) for k in self.keys)

    def _first_entry(self, key):
        entry = self.table.get(key)
        if entry is None:
            return None
        return entry[0] if self.multi else entry

    def find(self, pdv: dict, key_values=None) -> float:
        entry = self._first_entry(self._current_key(pdv, key_values))
        if entry is None:
            return 1.0
        for d, v in entry.items():
            pdv[d] = v
        return 0.0

    def check(self, pdv: dict, key_values=None) -> float:
        """Like .find() but doesn't copy data values into the PDV."""
        return 0.0 if self._current_key(pdv, key_values) in self.table else 1.0

    def add(self, pdv: dict, key_values=None) -> float:
        key = self._current_key(pdv, key_values)
        self._store(key, {d: pdv.get(d, MISSING) for d in self.datas})
        return 0.0

    def remove(self, pdv: dict, key_values=None) -> float:
        self.table.pop(self._current_key(pdv, key_values), None)
        return 0.0

    def clear(self, pdv: dict = None) -> float:
        self.table.clear()
        return 0.0

    def __len__(self):
        return len(self.table)

    def _cursor(self) -> "SasHIter":
        if getattr(self, "_cursor_iter", None) is None:
            self._cursor_iter = SasHIter(self)
        return self._cursor_iter

    def first(self, pdv: dict) -> float:
        """Sequential walk over all items (also available on the hash
        object itself for convenience; SAS proper uses an HITER object)."""
        return self._cursor().first(pdv)

    def last(self, pdv: dict) -> float:
        return self._cursor().last(pdv)

    def next(self, pdv: dict) -> float:
        return self._cursor().next(pdv)

    def prev(self, pdv: dict) -> float:
        return self._cursor().prev(pdv)

    def ordered_items(self):
        """All (key, data) pairs in insertion order, expanding MULTIDATA
        lists -- backs the HITER iterator object."""
        items = []
        for key, entry in self.table.items():
            if self.multi:
                items.extend((key, data) for data in entry)
            else:
                items.append((key, entry))
        return items


class SasHIter:
    """A DATA step hash iterator object (DECLARE HITER): sequential
    FIRST()/NEXT()/PREV()/LAST() walks over its hash object's items,
    copying key+data values into the PDV. Returns 0.0 on success,
    1.0 when the walk runs off either end."""

    def __init__(self, sas_hash: SasHash | None = None):
        self.hash = sas_hash
        self.idx: int | None = None

    def _items(self):
        return self.hash.ordered_items() if self.hash is not None else []

    def _load(self, pdv: dict, i: int) -> float:
        items = self._items()
        if not items or i < 0 or i >= len(items):
            return 1.0
        self.idx = i
        (key, data) = items[i]
        if self.hash is not None:
            for k, v in zip(self.hash.keys, key):
                pdv[k] = v
        for d, v in data.items():
            pdv[d] = v
        return 0.0

    def first(self, pdv: dict) -> float:
        return self._load(pdv, 0)

    def last(self, pdv: dict) -> float:
        return self._load(pdv, len(self._items()) - 1)

    def next(self, pdv: dict) -> float:
        return self._load(pdv, 0 if self.idx is None else self.idx + 1)

    def prev(self, pdv: dict) -> float:
        return self._load(pdv, len(self._items()) - 1 if self.idx is None else self.idx - 1)


# ---------------- PROC COMPARE ----------------
def _compare_values_equal(a, b) -> bool:
    a_missing = a is None or (isinstance(a, float) and pd.isna(a))
    b_missing = b is None or (isinstance(b, float) and pd.isna(b))
    if a_missing and b_missing:
        return True
    if a_missing != b_missing:
        return False
    return a == b


def proc_compare_report(base: pd.DataFrame, compare: pd.DataFrame,
                         id_vars: list | None = None, var_list: list | None = None) -> pd.DataFrame:
    """Print a PROC COMPARE-style report (row alignment by ID or by
    position, per-variable mismatch counts, and a capped list of
    differing values) and return a long-form DataFrame of differences
    with columns _id_/_var_/_base_/_compare_ for optional OUT= use.

    No CRITERION=/fuzzy tolerance, no TRANSFORM=, no BY-group support --
    numeric/character values are compared for exact equality (missing
    values on both sides count as equal to each other)."""
    base_cols = list(base.columns)
    compare_cols = list(compare.columns)
    only_base_cols = [c for c in base_cols if c not in compare_cols]
    only_compare_cols = [c for c in compare_cols if c not in base_cols]
    exclude = set(id_vars or [])
    if var_list:
        common_cols = [c for c in var_list if c in base_cols and c in compare_cols]
    else:
        common_cols = [c for c in base_cols if c in compare_cols and c not in exclude]

    print("PROC COMPARE")
    print(f"  Base dataset:     {len(base)} observations, {len(base_cols)} variables")
    print(f"  Compare dataset:  {len(compare)} observations, {len(compare_cols)} variables")
    print()
    if only_base_cols:
        print("Variables in BASE but not in COMPARE: " + ", ".join(only_base_cols))
    if only_compare_cols:
        print("Variables in COMPARE but not in BASE: " + ", ".join(only_compare_cols))
    if only_base_cols or only_compare_cols:
        print()

    diffs: list = []
    var_match = {c: 0 for c in common_cols}
    var_diff = {c: 0 for c in common_cols}
    obs_compared = 0
    obs_equal = 0
    base_only_keys: list = []
    compare_only_keys: list = []

    if id_vars:
        b_map: dict = {}
        for r in base.to_dict("records"):
            b_map.setdefault(tuple(r.get(k) for k in id_vars), r)
        c_map: dict = {}
        for r in compare.to_dict("records"):
            c_map.setdefault(tuple(r.get(k) for k in id_vars), r)
        seen = set()
        all_keys = []
        for k in list(b_map.keys()) + list(c_map.keys()):
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

        def _key_label(k):
            return ", ".join(f"{kk}={vv}" for kk, vv in zip(id_vars, k))

        for key in all_keys:
            in_b, in_c = key in b_map, key in c_map
            if not in_b:
                compare_only_keys.append(key)
                continue
            if not in_c:
                base_only_keys.append(key)
                continue
            obs_compared += 1
            brow, crow = b_map[key], c_map[key]
            row_equal = True
            for col in common_cols:
                bv, cv = brow.get(col), crow.get(col)
                if _compare_values_equal(bv, cv):
                    var_match[col] += 1
                else:
                    var_diff[col] += 1
                    row_equal = False
                    diffs.append({"_id_": _key_label(key), "_var_": col, "_base_": bv, "_compare_": cv})
            if row_equal:
                obs_equal += 1
    else:
        n = min(len(base), len(compare))
        b_records = base.to_dict("records")[:n]
        c_records = compare.to_dict("records")[:n]
        if len(base) > n:
            base_only_keys = list(range(n + 1, len(base) + 1))
        if len(compare) > n:
            compare_only_keys = list(range(n + 1, len(compare) + 1))
        for i, (brow, crow) in enumerate(zip(b_records, c_records), start=1):
            obs_compared += 1
            row_equal = True
            for col in common_cols:
                bv, cv = brow.get(col), crow.get(col)
                if _compare_values_equal(bv, cv):
                    var_match[col] += 1
                else:
                    var_diff[col] += 1
                    row_equal = False
                    diffs.append({"_id_": i, "_var_": col, "_base_": bv, "_compare_": cv})
            if row_equal:
                obs_equal += 1

    if base_only_keys:
        print(f"Observations in BASE but not in COMPARE: {len(base_only_keys)}")
    if compare_only_keys:
        print(f"Observations in COMPARE but not in BASE: {len(compare_only_keys)}")
    if base_only_keys or compare_only_keys:
        print()

    print(f"Observations compared: {obs_compared}")
    print(f"Observations with all compared values equal: {obs_equal}")
    print()

    any_var_diff = any(v > 0 for v in var_diff.values())
    identical = (
        not only_base_cols and not only_compare_cols
        and not base_only_keys and not compare_only_keys
        and not any_var_diff
    )
    if identical:
        print("NOTE: No unequal values were found. All values compared are exactly equal.")
        return pd.DataFrame(columns=["_id_", "_var_", "_base_", "_compare_"])

    print("Variables with Unequal Values")
    width = max(10, max((len(c) for c in common_cols), default=10) + 2)
    print("Variable".ljust(width) + "Matches".rjust(10) + "Differences".rjust(14))
    for col in common_cols:
        if var_diff[col] > 0:
            print(col.ljust(width) + str(var_match[col]).rjust(10) + str(var_diff[col]).rjust(14))
    print()

    shown = diffs[:50]
    if shown:
        idw = max(10, max(len(str(d["_id_"])) for d in shown) + 2)
        varw = max(10, max(len(d["_var_"]) for d in shown) + 2)
        print("Values Comparison Summary")
        print("ID/Obs".ljust(idw) + "Variable".ljust(varw) + "Base".rjust(15) + "Compare".rjust(15))
        for d in shown:
            print(str(d["_id_"]).ljust(idw) + d["_var_"].ljust(varw) + str(d["_base_"]).rjust(15) + str(d["_compare_"]).rjust(15))
        if len(diffs) > 50:
            print(f"... and {len(diffs) - 50} more")

    return pd.DataFrame(diffs)
