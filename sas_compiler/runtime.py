"""Runtime support library used by SAS-compiler-generated Python code.

Implements SAS DATA step semantics (missing-value propagation, PDV-style
row iteration, BY-group processing) and a library of SAS built-in
functions. Generated code does `import sas_compiler.runtime as _r` and
calls into this module.
"""
from __future__ import annotations

import atexit
import csv
import html as _html
import math
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

MISSING = float("nan")
SAS_EPOCH = date(1960, 1, 1)

MACRO_VARS: dict = {}

# CALL EXECUTE queue: strings of raw SAS source queued by DATA step CALL
# EXECUTE(...) statements, drained (compiled + run) right after the
# currently-running step finishes, before the next step -- matching real
# SAS's "queued statements execute at the end of the current step" timing.
_EXECUTE_QUEUE: list = []


def call_execute(text) -> None:
    """CALL EXECUTE(text) -- queue a string of SAS source to run after the
    current step. Draining happens in drain_execute_queue(), invoked by the
    generated top-level program after each step call."""
    _EXECUTE_QUEUE.append(str(text))


def drain_execute_queue(_DS, _FMT, _LBL, _TITLES=None, _FOOTNOTES=None) -> None:
    """Compile and run every SAS snippet queued by CALL EXECUTE, in the
    order queued, sharing the SAME _DS/_FMT/_LBL dicts (and _TITLES/
    _FOOTNOTES lists) as the caller -- so datasets created by queued code
    are visible to the rest of the program and vice versa, matching real
    SAS's shared WORK library. A snippet's own CALL EXECUTE calls queue
    into this same list, so this drains transitively until truly empty.
    Imports sas_compiler lazily to avoid a circular import (sas_compiler
    imports this module at package load time)."""
    import sas_compiler as _sc

    if _TITLES is None:
        _TITLES = [""] * 10
    if _FOOTNOTES is None:
        _FOOTNOTES = [""] * 10
    while _EXECUTE_QUEUE:
        text = _EXECUTE_QUEUE.pop(0)
        code = _sc.compile_source(text, nested=True)
        ns = {
            "_DS": _DS,
            "_FMT": _FMT,
            "_LBL": _LBL,
            "_TITLES": _TITLES,
            "_FOOTNOTES": _FOOTNOTES,
        }
        exec(compile(code, "<call_execute>", "exec"), ns)


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
    for item in entry["ranges"]:
        # Ranges are stored as (lo, hi, label) or
        # (lo, hi, label, lo_excl, hi_excl) when the PROC FORMAT source
        # used `-<` / `>-` exclusive markers.
        if len(item) == 5:
            lo, hi, label, lo_excl, hi_excl = item
        else:
            lo, hi, label = item
            lo_excl = hi_excl = False
        lo_ok = (v > lo) if lo_excl else (v >= lo)
        hi_ok = (v < hi) if hi_excl else (v <= hi)
        if lo_ok and hi_ok:
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


def contains(haystack, needle) -> bool:
    """SAS CONTAINS (and `?`) operator: case-sensitive substring test."""
    if is_missing(haystack) or is_missing(needle):
        return False
    return sas_text(needle) in sas_text(haystack)


def sas_like(value, pattern) -> bool:
    """SAS LIKE: `%` matches any run, `_` matches one char, case-insensitive."""
    if is_missing(value) or is_missing(pattern):
        return False
    pat = sas_text(pattern)
    # Translate SQL-LIKE to regex, escaping everything else.
    rx = "".join(
        ".*" if c == "%" else ("." if c == "_" else re.escape(c))
        for c in pat
    )
    try:
        return re.search("^" + rx + "$", sas_text(value), re.IGNORECASE) is not None
    except re.error:
        return False


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

    if name in ("date", "mmddyy", "yymmdd", "ddmmyy", "worddate", "monyy", "time", "datetime"):
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
            if width and width <= 6:
                return d.strftime("%m%d%y")
            return d.strftime("%m/%d/%Y" if not width or width >= 10 else "%m/%d/%y")
        if name == "yymmdd":
            if width and width <= 6:
                return d.strftime("%y%m%d")
            return d.strftime("%Y-%m-%d" if not width or width >= 10 else "%y-%m-%d")
        if name == "ddmmyy":
            if width and width <= 6:
                return d.strftime("%d%m%y")
            return d.strftime("%d/%m/%Y" if not width or width >= 10 else "%d/%m/%y")
        if name == "worddate":
            return d.strftime("%B %d, %Y")
        if name == "monyy":
            # SAS MONYYw.: MMMYY / MMMYYYY (e.g. SEP26 / SEP2026).
            s = d.strftime("%b%Y").upper()
            if width and width <= 5:
                s = d.strftime("%b%y").upper()
            return s

    if name == "comma":
        return f"{v:,.{dec}f}"
    if name in ("commax", "commaX".lower()):
        # COMMAXw.d: European style ('.' thousands, ',' decimals).
        s = f"{v:,.{dec}f}"
        return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
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


_INFORMAT_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _informat_2digit_year(year: int) -> int:
    """2-digit year windowing for column-input date informats. Matches the
    convention the 'ddMONyy'd date-literal parser already uses elsewhere in
    this codebase (pivot at 26): 00-25 -> 20xx, 26-99 -> 19xx."""
    return year + (2000 if year < 26 else 1900)


def _parse_informat_date(text: str):
    """DATE9.-style value ('01JAN2020', '01-JAN-2020', '01/JAN/2020') ->
    SAS date serial, or None if it doesn't parse."""
    m = re.match(r"^(\d{1,2})[-/]?([A-Za-z]{3})[-/]?(\d{2,4})$", text.strip())
    if not m:
        return None
    mon = _INFORMAT_MONTH_ABBR.get(m.group(2).lower())
    if mon is None:
        return None
    day, year = int(m.group(1)), int(m.group(3))
    if year < 100:
        year = _informat_2digit_year(year)
    v = sas_date(year, mon, day)
    return None if is_missing(v) else v


def _parse_informat_mmddyy(text: str):
    """MMDDYYw.-style value ('01/15/2020', '01152020', '01/15/20') -> SAS
    date serial, or None if it doesn't parse."""
    m = re.match(r"^(\d{2})[-/]?(\d{2})[-/]?(\d{2}|\d{4})$", text.strip())
    if not m:
        return None
    mo, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year = _informat_2digit_year(year)
    v = sas_date(year, mo, day)
    return None if is_missing(v) else v


def _parse_informat_yymmdd(text: str):
    """YYMMDDw.-style value ('2020-01-15', '20200115') -> SAS date serial,
    or None if it doesn't parse."""
    m = re.match(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})$", text.strip())
    if not m:
        return None
    year, mo, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    v = sas_date(year, mo, day)
    return None if is_missing(v) else v


def _parse_informat_comma(text: str):
    """COMMAw.d/DOLLARw.d-style value ('1,234.56', '$1,234.56') -> float,
    or None if it doesn't parse."""
    cleaned = text.strip().replace("$", "").replace(",", "").strip()
    if cleaned == "":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def read_infile_columns(path, items, firstobs=1, obs=None):
    """Read a raw text file for INFILE + INPUT using column/pointer-controlled
    (formatted) input: @n, +n, /, #n, column ranges (start-end) and width
    informats (w. / w.d), plus a small set of named informats (DATE9.,
    MMDDYYw., YYMMDDw., COMMAw.d, DOLLARw.d).

    items: list of tagged tuples produced by the parser --
      ("var", name, is_char, width, decimals, start, end, informat)
      ("ptr_abs", n) / ("ptr_rel", n) / ("newline",) / ("line_abs", n)

    Unlike list input, blank physical lines are kept (a blank line is a
    valid fixed-column record). FIRSTOBS/OBS select 1-based physical lines
    before any of this. Each full pass over `items` produces one output
    row; a `newline` item or running out of items just advances the line
    pointer -- the next row starts on the next unconsumed physical line.

    `line_abs` (#n) jumps the line pointer directly to the nth physical
    line of the *current record* (1-based, relative to the record's first
    line -- i.e. `line_idx` below), unlike `/` which only steps forward one
    line at a time. Design choice: a bare `#n` leaves the column pointer
    (`col`) exactly where it was, matching real SAS (which does not reset
    the column pointer for `#n` alone -- only `/` and a fresh INPUT
    statement reset it to column 1). Because `#n` can jump forward past
    lines a trailing `/` never touched, or backward to a line already read
    earlier in this same INPUT statement, advancing to the next record
    can't just use the final `cur` -- it must use the *highest* line index
    touched anywhere in the pass (`max_cur`), so a `#n` jump never causes a
    physical line to be re-read into a later record, and never skips a
    line that this record already consumed."""
    with open(path, "r", newline="") as f:
        lines = f.read().splitlines()
    lo = max(int(firstobs or 1) - 1, 0)
    hi = int(obs) if obs is not None else None
    lines = lines[lo:hi]

    def get_line(idx):
        return lines[idx] if 0 <= idx < len(lines) else ""

    rows = []
    n = len(lines)
    line_idx = 0
    while line_idx < n:
        row = {}
        col = 0
        cur = line_idx
        max_cur = cur
        for item in items:
            tag = item[0]
            if tag == "ptr_abs":
                col = max(int(item[1]) - 1, 0)
            elif tag == "ptr_rel":
                col = max(col + int(item[1]), 0)
            elif tag == "newline":
                cur += 1
                col = 0
                max_cur = max(max_cur, cur)
            elif tag == "line_abs":
                # Jump straight to the nth physical line of this record.
                # Column pointer is deliberately left unchanged (see
                # docstring). Track max_cur separately from cur so a
                # forward-or-backward jump can't corrupt end-of-record
                # advancement below.
                cur = max(line_idx + int(item[1]) - 1, 0)
                max_cur = max(max_cur, cur)
            elif tag == "var":
                _, name, is_char, width, decimals, start, end, informat = item
                line = get_line(cur)
                if start is not None and end is not None:
                    raw = line[start - 1 : end]
                    col = end
                elif width is not None:
                    raw = line[col : col + width]
                    col += width
                else:
                    raw = line[col:]
                    col = len(line)
                if is_char:
                    row[name] = raw.rstrip()
                elif informat == "date":
                    v = _parse_informat_date(raw)
                    row[name] = MISSING if v is None else v
                elif informat == "mmddyy":
                    v = _parse_informat_mmddyy(raw)
                    row[name] = MISSING if v is None else v
                elif informat == "yymmdd":
                    v = _parse_informat_yymmdd(raw)
                    row[name] = MISSING if v is None else v
                elif informat in ("comma", "dollar"):
                    v = _parse_informat_comma(raw)
                    row[name] = MISSING if v is None else v
                else:
                    # Plain numeric width informat, and the fallback for any
                    # named informat outside the small set above (an
                    # explicit, documented scope cut -- see README).
                    text = raw.strip()
                    try:
                        val = float(text)
                        if decimals and "." not in text and text != "":
                            val = val / (10 ** int(decimals))
                        row[name] = val
                    except ValueError:
                        row[name] = MISSING
        rows.append(row)
        line_idx = max_cur + 1
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


def proc_univariate_normality(s: pd.Series):
    """Print PROC UNIVARIATE's 'Tests for Normality' block for one variable's
    non-missing values. Matches real SAS's default battery (Shapiro-Wilk,
    Kolmogorov-Smirnov, Cramer-von Mises, Anderson-Darling) only in part:
    this implementation covers Shapiro-Wilk and Kolmogorov-Smirnov. See the
    README for the documented scope cut on the other two.
    """
    from scipy import stats as _stats

    n = len(s)
    print("Tests for Normality")

    if n < 3:
        print("  Shapiro-Wilk       not computed (N < 3)")
    elif n > 2000:
        print("  Shapiro-Wilk       not computed (N > 2000)")
    else:
        try:
            w, p = _stats.shapiro(s)
            print(f"  Shapiro-Wilk       W={w:.4f}  Pr < W={p:.4f}")
        except Exception:
            print("  Shapiro-Wilk       not computed")

    if n < 2:
        print("  Kolmogorov-Smirnov not computed (N < 2)")
    else:
        std = s.std(ddof=1)
        if not std or pd.isna(std):
            print("  Kolmogorov-Smirnov not computed (zero variance)")
        else:
            try:
                # Equivalent to kstest(s, "norm", args=(mean, std)): test
                # against a normal CDF fit to the sample's own mean/std,
                # matching what real SAS's K-S normality test does.
                d, p = _stats.kstest(s, _stats.norm(loc=s.mean(), scale=std).cdf)
                print(f"  Kolmogorov-Smirnov D={d:.4f}  Pr > D={p:.4f}")
            except Exception:
                print("  Kolmogorov-Smirnov not computed")


def _ttest_stat_line(s: pd.Series) -> tuple:
    """N/Mean/StdDev/StdErr for a numeric Series, SAS-TTEST style."""
    n = len(s)
    mean = s.mean() if n else float("nan")
    std = s.std() if n > 1 else float("nan")
    se = std / (n ** 0.5) if n > 1 else float("nan")
    return n, mean, std, se


def _ttest_welch_df(a: pd.Series, b: pd.Series) -> float:
    """Welch-Satterthwaite approximate degrees of freedom for two samples."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return float("nan")
    v1, v2 = a.var(ddof=1), b.var(ddof=1)
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    return num / den if den else float("nan")


def proc_ttest_report(df: pd.DataFrame, var_names: list, class_var: str | None,
                        paired_pairs: list, h0: float = 0.0):
    """Print a PROC TTEST-style report and return None (real PROC TTEST
    has no OUT= dataset either). Exactly one of the three SAS TTEST modes
    is used, based on what's given:
      - `paired_pairs` non-empty: paired t-test on each (v1, v2) difference.
      - `class_var` given (with `var_names`): two-sample (independent
        groups) t-test, reporting both the pooled-variance and
        Satterthwaite (Welch) results, like real PROC TTEST.
      - otherwise: one-sample t-test of each var against H0 (default 0).
    """
    from scipy import stats as _stats

    print("The TTEST Procedure")

    if paired_pairs:
        print("Paired t-test")
        print()
        for v1, v2 in paired_pairs:
            sub = df[[v1, v2]].apply(pd.to_numeric, errors="coerce").dropna()
            diff = sub[v1] - sub[v2]
            n, mean, std, se = _ttest_stat_line(diff)
            print(f"Difference: {v1} - {v2}")
            print(f"  N          {n}")
            print(f"  Mean       {mean:.4f}")
            print(f"  Std Dev    {std:.4f}")
            print(f"  Std Err    {se:.4f}")
            if n > 1:
                t, p = _stats.ttest_rel(sub[v1], sub[v2])
                print(f"  DF         {n - 1}")
                print(f"  t Value    {t:.4f}")
                print(f"  Pr > |t|   {p:.4f}")
            print()
        return None

    if class_var:
        print(f"Class: {class_var}")
        print()
        levels = sorted(df[class_var].dropna().unique().tolist())
        if len(levels) != 2:
            raise ValueError(
                f"PROC TTEST: CLASS variable {class_var!r} must have exactly 2 "
                f"non-missing levels, found {len(levels)}: {levels!r}"
            )
        for v in var_names:
            sub = df[[class_var, v]].copy()
            sub[v] = pd.to_numeric(sub[v], errors="coerce")
            sub = sub.dropna()
            print(f"Variable: {v}")
            groups = {}
            for lvl in levels:
                s = sub.loc[sub[class_var] == lvl, v]
                groups[lvl] = s
                n, mean, std, se = _ttest_stat_line(s)
                print(f"  {str(lvl):<12} N={n:<6} Mean={mean:.4f}  "
                      f"StdDev={std:.4f}  StdErr={se:.4f}")
            a, b = groups[levels[0]], groups[levels[1]]
            t_eq, p_eq = _stats.ttest_ind(a, b, equal_var=True)
            df_eq = len(a) + len(b) - 2
            t_un, p_un = _stats.ttest_ind(a, b, equal_var=False)
            df_un = _ttest_welch_df(a, b)
            print(f"  Pooled        t={t_eq:.4f}  DF={df_eq}          Pr > |t|={p_eq:.4f}")
            print(f"  Satterthwaite t={t_un:.4f}  DF={df_un:.4f}  Pr > |t|={p_un:.4f}")
            print()
        return None

    print(f"H0: Mean = {h0}")
    print()
    for v in var_names:
        s = pd.to_numeric(df[v], errors="coerce").dropna()
        n, mean, std, se = _ttest_stat_line(s)
        print(f"Variable: {v}")
        print(f"  N          {n}")
        print(f"  Mean       {mean:.4f}")
        print(f"  Std Dev    {std:.4f}")
        print(f"  Std Err    {se:.4f}")
        if n > 1:
            t, p = _stats.ttest_1samp(s, h0)
            print(f"  DF         {n - 1}")
            print(f"  t Value    {t:.4f}")
            print(f"  Pr > |t|   {p:.4f}")
        print()
    return None


def proc_freq_chisq(df: pd.DataFrame, v1: str, v2: str):
    """Print a PROC FREQ CHISQ-style Pearson chi-square test of
    independence report for a two-way table (v1 rows x v2 columns).
    Also reports Likelihood Ratio and Mantel-Haenszel chi-square when
    they can be computed cheaply; falls back to Pearson-only otherwise.
    Returns None (real PROC FREQ's CHISQ option has no OUT= dataset)."""
    from scipy import stats as _stats

    ct = pd.crosstab(df[v1], df[v2])
    print("Statistics for Table")
    print()
    if ct.shape[0] < 2 or ct.shape[1] < 2 or (ct.values == 0).all():
        print("WARNING: Table has a zero row or column, or fewer than 2 "
              "levels in one or both variables; chi-square statistics "
              "cannot be computed.")
        print()
        return None
    try:
        chi2, p, dof, expected = _stats.chi2_contingency(ct, correction=False)
    except ValueError as e:
        print(f"WARNING: Chi-square statistics could not be computed "
              f"(sparse table): {e}")
        print()
        return None

    print("Statistic                     DF       Value      Prob")
    print(f"Chi-Square                    {dof:<8} {chi2:>10.4f}  {p:.4f}")

    observed = ct.values.astype(float)
    nonzero = observed > 0
    lr_chi2 = 2.0 * float(np.sum(
        observed[nonzero] * np.log(observed[nonzero] / expected[nonzero])
    ))
    lr_p = float(_stats.chi2.sf(lr_chi2, dof))
    print(f"Likelihood Ratio Chi-Square   {dof:<8} {lr_chi2:>10.4f}  {lr_p:.4f}")

    try:
        rows = pd.to_numeric(pd.Series(ct.index), errors="coerce")
        cols = pd.to_numeric(pd.Series(ct.columns), errors="coerce")
        if not rows.isna().any() and not cols.isna().any():
            row_codes = rows.to_numpy()
            col_codes = cols.to_numpy()
            x = np.repeat(row_codes, len(col_codes))
            y_ = np.tile(col_codes, len(row_codes))
            w = observed.flatten()
            n = w.sum()
            if n > 1:
                r, _ = _stats.pearsonr(np.repeat(x, w.astype(int)),
                                        np.repeat(y_, w.astype(int)))
                mh_chi2 = (n - 1) * (r ** 2)
                mh_p = float(_stats.chi2.sf(mh_chi2, 1))
                print(f"Mantel-Haenszel Chi-Square    1        {mh_chi2:>10.4f}  {mh_p:.4f}")
    except (ValueError, TypeError):
        pass
    print()
    return None


def proc_freq_measures(df: pd.DataFrame, v1: str, v2: str):
    """Print a PROC FREQ MEASURES-style report of the odds ratio and
    relative risk (risk ratio), each with a 95% confidence interval, for
    a strictly 2x2 table (v1 rows x v2 columns). Real SAS's MEASURES
    option is only defined for a 2x2 table; a table of any other shape
    prints a SAS-style warning instead of crashing. As a scope cut, only
    one relative risk direction (row 1 vs row 2, using column 1 as the
    reference) is reported, rather than SAS's both Row1/Col1 and
    Row2/Col2 relative risks. Returns None (real PROC FREQ's MEASURES
    option has no OUT= dataset)."""
    from statsmodels.stats.contingency_tables import Table2x2

    ct = pd.crosstab(df[v1], df[v2])
    print("Estimates of the Relative Risk (Row1/Col1)")
    print()
    if ct.shape != (2, 2):
        print("WARNING: Table does not have exactly 2 rows and 2 columns; "
              "odds ratio and relative risk statistics cannot be computed.")
        print()
        return None
    try:
        t = Table2x2(ct.to_numpy().astype(float))
        odds_ratio = float(t.oddsratio)
        or_lcl, or_ucl = (float(x) for x in t.oddsratio_confint())
        risk_ratio = float(t.riskratio)
        rr_lcl, rr_ucl = (float(x) for x in t.riskratio_confint())
    except (ValueError, ZeroDivisionError) as e:
        print(f"WARNING: Odds ratio and relative risk statistics could not "
              f"be computed (degenerate table): {e}")
        print()
        return None

    print("Statistic                       Value      95% Confidence Limits")
    print(f"Odds Ratio                    {odds_ratio:>10.4f}  {or_lcl:>10.4f}  {or_ucl:>10.4f}")
    print(f"Relative Risk (Column 1)      {risk_ratio:>10.4f}  {rr_lcl:>10.4f}  {rr_ucl:>10.4f}")
    print()
    return None


def proc_freq_agree(df: pd.DataFrame, v1: str, v2: str):
    """Print a PROC FREQ AGREE-style report of Cohen's Kappa (Simple Kappa
    Coefficient) and overall percent agreement for a square two-way table
    (v1 rows x v2 columns) where both variables represent the same set of
    categories rated by two different raters/methods. Real SAS's AGREE
    option is only meaningful for such a square, same-category table; a
    table of any other shape prints a SAS-style warning instead of
    computing a nonsensical statistic. As a documented scope cut, only the
    Kappa point estimate and percent agreement are reported; the
    asymptotic standard error and confidence interval that real SAS also
    prints are omitted (they require statistical machinery beyond what
    sklearn's cohen_kappa_score gives directly, and a hand-derived CI
    formula risks being wrong). Returns None (real PROC FREQ's AGREE
    option has no OUT= dataset for the Kappa statistic)."""
    from sklearn.metrics import cohen_kappa_score

    ct = pd.crosstab(df[v1], df[v2])
    print("Simple Kappa Coefficient")
    print()
    if ct.shape[0] != ct.shape[1] or set(ct.index) != set(ct.columns):
        print("WARNING: Table is not square over the same set of "
              "categories on both variables; the kappa coefficient "
              "cannot be computed.")
        print()
        return None

    sub = df[[v1, v2]].dropna()
    try:
        kappa = float(cohen_kappa_score(sub[v1], sub[v2]))
    except ValueError as e:
        print(f"WARNING: Kappa coefficient could not be computed "
              f"(degenerate table): {e}")
        print()
        return None

    pct_agree = float((sub[v1] == sub[v2]).mean() * 100.0)

    print("Statistic                       Value")
    print(f"Kappa                          {kappa:>10.4f}")
    print(f"Percent Agreement                {pct_agree:>8.1f}")
    print()
    return None


def proc_freq_chisq_oneway(df: pd.DataFrame, var: str):
    """Print a PROC FREQ CHISQ-style Pearson chi-square goodness-of-fit
    report for a one-way table (var), testing the null hypothesis that
    all observed levels are equally likely (SAS's default when no
    TESTP= option narrows the expected proportions; TESTP= itself is
    out of scope here). Returns None (real PROC FREQ's CHISQ option has
    no OUT= dataset)."""
    from scipy import stats as _stats

    s = df[var].dropna()
    print("Chi-Square Goodness-of-Fit Test")
    print()
    vc = s.value_counts(dropna=True)
    k = len(vc)
    if k < 2:
        print("WARNING: Variable has fewer than 2 non-missing levels; "
              "chi-square goodness-of-fit statistics cannot be computed.")
        print()
        return None

    observed = vc.to_numpy(dtype=float)
    n = observed.sum()
    expected = np.full(k, n / k)
    chi2, p = _stats.chisquare(observed, f_exp=expected)
    dof = k - 1

    print("Statistic                     DF       Value      Prob")
    print(f"Chi-Square                    {dof:<8} {chi2:>10.4f}  {p:.4f}")
    print()
    return None


def proc_anova_oneway_report(df: pd.DataFrame, y: str, group_var: str):
    """Print a PROC ANOVA-style one-way ANOVA report (Class Level
    Information, the classic Source/DF/SS/MS/F/Pr>F table, R-Square, Coeff
    Var, Root MSE, and the dependent variable's mean) and return None (real
    PROC ANOVA's OUT= is for per-observation residuals/predicted values,
    which is out of scope here, like PROC TTEST also skips OUT=)."""
    from scipy import stats as _stats

    sub = df[[group_var, y]].copy()
    sub[y] = pd.to_numeric(sub[y], errors="coerce")
    sub = sub.dropna()

    levels = sorted(sub[group_var].dropna().unique().tolist())
    k = len(levels)
    if k < 2:
        raise ValueError(
            f"PROC ANOVA: CLASS variable {group_var!r} must have at least 2 "
            f"non-missing levels, found {k}: {levels!r}"
        )

    print("The ANOVA Procedure")
    print()
    print("Class Level Information")
    print(f"  Class     Levels    Values")
    print(f"  {group_var:<10}{k:<10}{' '.join(str(lvl) for lvl in levels)}")
    print()

    groups = [sub.loc[sub[group_var] == lvl, y] for lvl in levels]
    n = len(sub)
    print(f"Number of observations: {n}")
    print()

    f_val, p_val = _stats.f_oneway(*groups)

    ybar = sub[y].mean()
    sst = float(((sub[y] - ybar) ** 2).sum())
    ssb = float(sum(len(g) * (g.mean() - ybar) ** 2 for g in groups))
    sse = sst - ssb

    df_model = k - 1
    df_error = n - k
    df_total = n - 1

    msb = ssb / df_model if df_model else float("nan")
    mse = sse / df_error if df_error else float("nan")
    # f_val/p_val (scipy.stats.f_oneway, above) and f.sf on the formula's
    # DF agree exactly -- used here as a consistency check on the SS
    # breakdown; the printed F Value / Pr > F are f_val/p_val themselves.

    print("Dependent Variable: " + y)
    print()
    header = f"{'Source':<20}{'DF':>6}{'Sum of Squares':>20}{'Mean Square':>16}{'F Value':>12}{'Pr > F':>12}"
    print(header)
    print(f"{'Model':<20}{df_model:>6}{ssb:>20.6f}{msb:>16.6f}{f_val:>12.4f}{p_val:>12.4f}")
    print(f"{'Error':<20}{df_error:>6}{sse:>20.6f}{mse:>16.6f}")
    print(f"{'Corrected Total':<20}{df_total:>6}{sst:>20.6f}")
    print()

    r_square = ssb / sst if sst else float("nan")
    root_mse = mse ** 0.5 if mse == mse else float("nan")
    coeff_var = 100 * root_mse / ybar if ybar else float("nan")
    print(f"{'R-Square':<12}{'Coeff Var':>14}{'Root MSE':>14}{y + ' Mean':>16}")
    print(f"{r_square:<12.6f}{coeff_var:>14.6f}{root_mse:>14.6f}{ybar:>16.6f}")
    print()

    return None


def proc_anova_multiway_report(df: pd.DataFrame, y: str, terms: list, class_vars: list):
    """Print a PROC ANOVA-style two-way/N-way ANOVA report for a MODEL
    statement naming 2+ CLASS variables. `terms` is the raw MODEL
    right-hand-side token list as returned by _parse_model_stmt, e.g.
    ["a", "b", "a*b"] for `model y = a b a*b;` -- each bare token is a
    main-effect term, each `*`-joined token (e.g. "a*b", "a*b*c") is an
    interaction term. Every term's variable(s) are required (by the caller,
    at codegen time) to be among `class_vars`.

    Builds a statsmodels formula translating each SAS main-effect term `a`
    to `C(a)` and each SAS interaction term `a*b` to the statsmodels pure-
    interaction operator `C(a):C(b)` (not `*`, which in statsmodels/patsy
    formula syntax also implicitly adds the main effects -- SAS's MODEL
    statement already lists main effects and interactions as separate,
    explicit terms, so `:` is the correct translation). An intercept is
    included, matching real PROC ANOVA's default parameterization (no
    `- 1` suppression). Fits with statsmodels.formula.api.ols and prints
    statsmodels.stats.anova.anova_lm(model, typ=2) -- Type II sums of
    squares. This is a documented simplification: real PROC ANOVA requires
    a balanced design (unlike PROC GLM, which defaults to Type III SS for
    unbalanced designs), and for a balanced design Type I/II/III SS all
    agree, so Type II is a reasonable, defensible stand-in here rather than
    attempting to exactly replicate SAS's SS partitioning for unbalanced
    data. Returns None (print-only, like proc_anova_oneway_report -- no
    OUT= dataset)."""
    from statsmodels.formula.api import ols
    from statsmodels.stats.anova import anova_lm

    used_vars = sorted({v for term in terms for v in term.split("*")})

    print("The ANOVA Procedure")
    print()
    print("Class Level Information")
    print(f"  Class     Levels    Values")
    for v in class_vars:
        levels = sorted(df[v].dropna().astype(str).unique().tolist())
        print(f"  {v:<10}{len(levels):<10}{' '.join(levels)}")
    print()

    sub = df[used_vars + [y]].copy()
    sub[y] = pd.to_numeric(sub[y], errors="coerce")
    sub = sub.dropna(subset=[y] + used_vars)
    for v in used_vars:
        sub[v] = sub[v].astype(str)

    n = len(sub)
    print(f"Number of observations: {n}")
    print()

    def _translate(term: str) -> str:
        return ":".join(f"C({v})" for v in term.split("*"))

    formula = f"{y} ~ " + " + ".join(_translate(t) for t in terms)

    print("Dependent Variable: " + y)
    print("Model: " + formula)
    print()

    model = ols(formula, data=sub).fit()
    table = anova_lm(model, typ=2)
    print(table)
    print()

    return None


def proc_npar1way_report(df: pd.DataFrame, var_names: list, group_var: str):
    """Print a PROC NPAR1WAY-style report for each VAR: a Wilcoxon-scores
    (rank-sums-by-group) table, then a Wilcoxon rank-sum test (via
    scipy.stats.mannwhitneyu) when the CLASS variable has exactly 2
    non-missing levels, or a Kruskal-Wallis test (via scipy.stats.kruskal)
    when it has more than 2. Returns None (real PROC NPAR1WAY has no OUT=
    dataset here). Scope cut: only this default Wilcoxon/Kruskal-Wallis
    behavior is implemented -- EDF, MEDIAN, SAVAGE, and other NPAR1WAY
    test options are not."""
    from scipy import stats as _stats

    print("The NPAR1WAY Procedure")
    print()

    for v in var_names:
        sub = df[[group_var, v]].copy()
        sub[v] = pd.to_numeric(sub[v], errors="coerce")
        sub = sub.dropna()

        levels = sorted(sub[group_var].dropna().unique().tolist())
        k = len(levels)
        if k < 2:
            raise ValueError(
                f"PROC NPAR1WAY: CLASS variable {group_var!r} must have at "
                f"least 2 non-missing levels, found {k}: {levels!r}"
            )

        print(f"Variable: {v}")
        print("Classified by Variable: " + group_var)
        print()

        n_total = len(sub)
        ranks = pd.Series(_stats.rankdata(sub[v].to_numpy()), index=sub.index)
        expected_mean_rank = (n_total + 1) / 2.0

        print("Wilcoxon Scores (Rank Sums)")
        header = (f"{'Level':<12}{'N':>8}{'Sum of Scores':>16}"
                  f"{'Expected Under H0':>20}{'Std Dev Under H0':>20}{'Mean Score':>14}")
        print(header)
        groups = {}
        for lvl in levels:
            mask = sub[group_var] == lvl
            r = ranks[mask]
            groups[lvl] = sub.loc[mask, v]
            n_i = len(r)
            sum_scores = float(r.sum())
            expected = n_i * (n_total + 1) / 2.0
            std_dev = math.sqrt(
                n_i * (n_total - n_i) * (n_total + 1) / 12.0
            ) if n_total > 1 else float("nan")
            mean_score = sum_scores / n_i if n_i else float("nan")
            print(f"{str(lvl):<12}{n_i:>8}{sum_scores:>16.4f}"
                  f"{expected:>20.4f}{std_dev:>20.4f}{mean_score:>14.4f}")
        print()

        if k == 2:
            a, b = groups[levels[0]], groups[levels[1]]
            stat, p = _stats.mannwhitneyu(a, b, alternative="two-sided")
            print("Wilcoxon Two-Sample Test (equivalent to the Mann-Whitney U test)")
            print("NOTE: this reports the Mann-Whitney U statistic and its "
                  "two-sided p-value rather than SAS's normalized S statistic.")
            print(f"  Statistic (Mann-Whitney U) = {stat:.4f}")
            print(f"  Pr > |Z| (two-sided)       = {p:.4f}")
            print()
        else:
            group_values = [groups[lvl] for lvl in levels]
            chi2, p = _stats.kruskal(*group_values)
            dof = k - 1
            print("Kruskal-Wallis Test")
            print(f"{'Chi-Square':<14}{'DF':>6}{'Pr > Chi-Square':>20}")
            print(f"{chi2:<14.4f}{dof:>6}{p:>20.4f}")
            print()

    return None


def _proc_reg_backward_select(sub: pd.DataFrame, y: str, xs: list, slstay: float):
    """Greedily drop the highest-p-value predictor while it exceeds
    `slstay`, refitting OLS each time. Stops at one remaining predictor
    (never drops down to an intercept-only model) or when every
    remaining predictor's p-value is <= slstay. Returns (final_xs, model, X)."""
    import statsmodels.api as sm

    current = list(xs)
    while len(current) > 1:
        X = sm.add_constant(sub[current])
        model = sm.OLS(sub[y], X).fit()
        pvals = model.pvalues.drop("const", errors="ignore")
        worst = pvals.idxmax()
        worst_p = pvals[worst]
        if worst_p <= slstay:
            break
        print(f"Step: removed {worst!r} (p={worst_p:.4f})")
        current.remove(worst)
    X = sm.add_constant(sub[current])
    model = sm.OLS(sub[y], X).fit()
    return current, model, X


def _proc_reg_forward_select(sub: pd.DataFrame, y: str, xs: list, slentry: float):
    """Greedily add whichever not-yet-included predictor gives the lowest
    entry p-value, while that p-value is below `slentry`, refitting OLS
    each time. Stops when no candidate qualifies or all predictors are
    in. Returns (final_xs, model, X)."""
    import statsmodels.api as sm

    current: list = []
    remaining = list(xs)
    while remaining:
        best_name = None
        best_p = None
        for cand in remaining:
            trial_xs = current + [cand]
            X = sm.add_constant(sub[trial_xs])
            trial_model = sm.OLS(sub[y], X).fit()
            p = trial_model.pvalues[cand]
            if best_p is None or p < best_p:
                best_p = p
                best_name = cand
        if best_p is None or best_p >= slentry:
            break
        print(f"Step: added {best_name!r} (p={best_p:.4f})")
        current.append(best_name)
        remaining.remove(best_name)
    if current:
        X = sm.add_constant(sub[current])
    else:
        # No predictor qualified: report an intercept-only model.
        X = pd.DataFrame({"const": 1.0}, index=sub.index)
    model = sm.OLS(sub[y], X).fit()
    return current, model, X


def proc_reg_fit(df: pd.DataFrame, y: str, xs: list, out_stats: dict | None = None,
                  vif: bool = False, selection: str | None = None,
                  slstay: float = 0.05, slentry: float = 0.05):
    """Fit an OLS regression (statsmodels), print its summary, and
    optionally return the input rows augmented with predicted/residual
    columns per `out_stats` (e.g. {'p': ['pred'], 'r': ['resid']}). When
    `vif` is true, also print a Variance Inflation Factor table (one row
    per predictor in `xs`) after the summary.

    `selection` of None (the default) fits the full model with all of
    `xs`, exactly as before. `selection="backward"` or `"forward"` runs
    a greedy stepwise-style search (SAS's SELECTION=BACKWARD/FORWARD)
    against `slstay`/`slentry` thresholds, printing a short log line per
    elimination/addition step, and then reports the *final* selected
    model through the same summary/VIF/out_stats tail as the default
    path. SELECTION=STEPWISE is rejected earlier, at compile time."""
    import statsmodels.api as sm

    sub = df[[y] + xs].apply(pd.to_numeric, errors="coerce").dropna()

    if selection == "backward":
        final_xs, model, X = _proc_reg_backward_select(sub, y, xs, slstay)
    elif selection == "forward":
        final_xs, model, X = _proc_reg_forward_select(sub, y, xs, slentry)
    else:
        final_xs = xs
        X = sm.add_constant(sub[xs])
        model = sm.OLS(sub[y], X).fit()

    print(model.summary())
    if vif:
        from statsmodels.stats.outliers_influence import variance_inflation_factor

        print()
        print("Variance Inflation Factor")
        print(f"  {'Variable':<16}{'VIF':>12}")
        # X = [const, x1, x2, ...] (add_constant places the constant first),
        # so predictor final_xs[i] is column index i + 1 in X.
        for i, name in enumerate(final_xs):
            try:
                v = variance_inflation_factor(X.values, i + 1)
            except Exception:
                v = float("inf")
            if v != v:  # NaN
                vstr = "Undefined"
            elif v in (float("inf"), float("-inf")):
                vstr = "Inf"
            else:
                vstr = f"{v:.4f}"
            print(f"  {name:<16}{vstr:>12}")
        print()
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
    print()
    print("Association of Predicted Probabilities and Observed Responses")
    predicted_probs = model.predict(X)
    if sub[y].nunique() < 2:
        print("  c statistic undefined (the response has only one observed level)")
    else:
        from sklearn.metrics import roc_auc_score

        c_stat = roc_auc_score(sub[y], predicted_probs)
        print(f"  c            {c_stat:.3f}")
        print(f"  Somers' D    {2 * c_stat - 1:.3f}")
    if not out_stats:
        return None
    result = df.loc[sub.index].copy()
    for name in out_stats.get("p", []):
        result[name] = predicted_probs
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

    def _fallback_same_table(exc):
        # The LIBNAME path may be a Windows path from a teaching file
        # (e.g. "C:\\Users\\Frame\\Downloads") or otherwise point at a
        # directory that does not exist on this machine.  Before giving
        # up, look for the same table name in any other directory lib
        # that does exist, so `libname class "C:\\..."` still resolves
        # to the real sales table when it is available elsewhere.
        for other_lib, other_conn in DB_LIBS.items():
            if other_lib == libref.lower():
                continue
            try:
                if isinstance(other_conn, str) and os.path.isdir(other_conn):
                    p = _dir_table_path(other_conn, table)
                    if p is not None:
                        print(
                            f"warning: LIBNAME {libref!r} has no table {table!r} ({exc}); "
                            f"using {other_lib}.{table} instead"
                        )
                        if p.lower().endswith(".csv"):
                            return _decode_dir_frame(pd.read_csv(p))
                        return _decode_dir_frame(pd.read_sas(p))
            except Exception:
                continue
        # Last resort: search likely on-disk locations for
        # `<table>.sas7bdat` / `<table>.csv` (the student's Downloads dir,
        # the current working directory, and its neighbours). This lets
        # files that hardcode a Windows LIBNAME still run on Linux.
        search_dirs = []
        for cand in (
            os.getcwd(),
            os.path.join(os.path.expanduser("~"), "Descargas"),
            os.path.join(os.path.expanduser("~"), "Downloads"),
            "/home/mionocastro/Descargas",
        ):
            if cand and cand not in search_dirs:
                search_dirs.append(cand)
        for d in search_dirs:
            try:
                if os.path.isdir(d):
                    p = _dir_table_path(d, table)
                    if p is not None:
                        print(
                            f"warning: LIBNAME {libref!r} {conn!r} not accessible ({exc}); "
                            f"using file {p!r} instead"
                        )
                        if p.lower().endswith(".csv"):
                            return _decode_dir_frame(pd.read_csv(p))
                        return _decode_dir_frame(pd.read_sas(p))
            except Exception:
                continue
        raise RuntimeError(
            f"LIBNAME {libref!r} directory {conn!r} has no table {table!r} "
            f"(looked for {table}.sas7bdat / {table}.csv): {exc}"
        )

    if os.path.isdir(conn):
        path = _dir_table_path(conn, table)
        if path is None:
            return _fallback_same_table(f"looked for {table}.sas7bdat / {table}.csv")
        try:
            if path.lower().endswith(".csv"):
                return _decode_dir_frame(pd.read_csv(path))
            return _decode_dir_frame(pd.read_sas(path))
        except Exception as e:
            return _fallback_same_table(str(e))
    # A Windows-style path (e.g. "C:\\Users\\...") is never a valid SQLite
    # file on Linux: fall back to another lib holding the same table.
    if isinstance(conn, str) and ("\\" in conn or re.match(r"^[A-Za-z]:", conn)):
        try:
            raise RuntimeError(f"Windows path {conn!r} is not accessible on this machine")
        except RuntimeError as e:
            return _fallback_same_table(str(e))
    if "://" in conn:
        import sqlalchemy
        engine = sqlalchemy.create_engine(conn)
        df = pd.read_sql_table(table, engine)
    else:
        import sqlite3
        # A directory path that does not exist (or any other non-DB file)
        # should not create a stray SQLite file: fall back first.
        if isinstance(conn, str) and (conn.endswith("/") or conn.endswith("\\") or "/" in conn or "\\" in conn):
            if not os.path.isfile(conn):
                return _fallback_same_table(f"{conn!r} is not a database file")
        con = sqlite3.connect(conn)
        try:
            try:
                df = pd.read_sql_query(f"SELECT * FROM {table}", con)
            except Exception as e:
                return _fallback_same_table(str(e))
        finally:
            con.close()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def get_proc_df(DS: dict, flat_name: str) -> pd.DataFrame:
    """Fetch a PROC's DATA= dataset tolerantly: exact match first, then a
    same-table fallback (any key ending with the same table suffix), then
    an on-disk `<table>.sas7bdat`/`<table>.csv` search, else an empty frame
    with a warning instead of a KeyError."""
    if flat_name in DS:
        return DS[flat_name]
    # `flat_name` is usually `lib_table`; fall back to any dataset with
    # the same table suffix (e.g. `data_sales` -> `class_sales`).
    table = flat_name.split("_")[-1] if "_" in flat_name else flat_name
    cands = [k for k in DS if k == table or k.endswith("_" + table)]
    if cands:
        print(f"warning: dataset {flat_name!r} not found; using {cands[0]!r} instead")
        return DS[cands[0]]
    # Also try a bare table name without lib prefix.
    if table in DS:
        print(f"warning: dataset {flat_name!r} not found; using {table!r} instead")
        return DS[table]
    # Last resort for unknown librefs (e.g. `data.sales` typo for
    # `class.sales` when nothing is loaded yet): look for the table file
    # on disk in the usual download locations.
    import os

    for d in (
        os.getcwd(),
        os.path.join(os.path.expanduser("~"), "Descargas"),
        os.path.join(os.path.expanduser("~"), "Downloads"),
        "/home/mionocastro/Descargas",
    ):
        try:
            if d and os.path.isdir(d):
                p = _dir_table_path(d, table)
                if p is not None:
                    print(f"warning: dataset {flat_name!r} not found; using file {p!r} instead")
                    if p.lower().endswith(".csv"):
                        return _decode_dir_frame(pd.read_csv(p))
                    return _decode_dir_frame(pd.read_sas(p))
        except Exception:
            continue
    print(f"warning: dataset {flat_name!r} not found; using empty dataset")
    return pd.DataFrame()


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


def proc_princomp_fit(df: pd.DataFrame, cols: list, n: int | None = None,
                       use_cov: bool = False, std_scores: bool = False) -> pd.DataFrame:
    """Principal component analysis (scikit-learn). By default (matching
    real PROC PRINCOMP) each VAR column is standardized (zero mean, unit
    variance) before PCA, i.e. components are eigenvectors of the
    *correlation* matrix; COV=True skips standardization, so PCA fits the
    (mean-centered only) raw variables, i.e. components are eigenvectors of
    the *covariance* matrix. Prints an Eigenvalues table (Eigenvalue,
    Difference, Proportion, Cumulative) and an Eigenvectors table (VAR
    variables x retained components), then returns the input rows (missing
    VAR values dropped) augmented with 1-based Prin1..PrinN score columns."""
    from sklearn.decomposition import PCA

    sub = df[cols].apply(pd.to_numeric, errors="coerce")
    mask = sub.notna().all(axis=1)
    clean = sub[mask]

    if use_cov:
        fit_data = clean.to_numpy()
    else:
        # Standardize with the sample (ddof=1) std, not sklearn's
        # StandardScaler (which uses the population, ddof=0, std): PCA's
        # own internal covariance estimate is ddof=1, so ddof=1
        # standardization here makes its eigenvalues exactly the
        # eigenvalues of the correlation matrix (summing to len(cols)),
        # matching real PROC PRINCOMP.
        fit_data = ((clean - clean.mean()) / clean.std(ddof=1)).to_numpy()

    pca = PCA(n_components=n)
    scores = pca.fit_transform(fit_data)
    n_comp = scores.shape[1]

    eigenvalues = pca.explained_variance_
    proportion = pca.explained_variance_ratio_
    cumulative = proportion.cumsum()
    diffs = [eigenvalues[i] - eigenvalues[i + 1] for i in range(len(eigenvalues) - 1)]
    diffs.append(float("nan"))

    matrix_kind = "Covariance" if use_cov else "Correlation"
    print("The PRINCOMP Procedure")
    print(f"Eigenvalues of the {matrix_kind} Matrix")
    eig_table = pd.DataFrame({
        "Eigenvalue": eigenvalues,
        "Difference": diffs,
        "Proportion": proportion,
        "Cumulative": cumulative,
    }, index=[f"PRIN{i + 1}" for i in range(len(eigenvalues))])
    print(eig_table.to_string())
    print()

    print("Eigenvectors")
    vec_table = pd.DataFrame(
        pca.components_.T,
        index=cols,
        columns=[f"Prin{i + 1}" for i in range(n_comp)],
    )
    print(vec_table.to_string())
    print()

    if std_scores:
        score_std = scores.std(axis=0, ddof=1)
        score_std[score_std == 0] = 1.0
        scores = scores / score_std

    result = df.loc[mask].copy()
    for i in range(n_comp):
        result[f"Prin{i + 1}"] = scores[:, i]
    return result


# SAS METHOD= name (lowercased) -> scipy.cluster.hierarchy.linkage method=
# name. WARD and WARDS are both accepted spellings for scipy's "ward".
_CLUSTER_METHODS = {
    "average": "average",
    "ward": "ward",
    "wards": "ward",
    "single": "single",
    "complete": "complete",
    "centroid": "centroid",
}


def proc_cluster_report(df: pd.DataFrame, cols: list, method: str = "average",
                         id_var: str | None = None):
    """PROC CLUSTER: agglomerative/hierarchical clustering (scipy). Prints
    the classic Cluster History table -- one row per merge step, from N
    singleton clusters down to 1, showing which two clusters/observations
    joined and at what distance -- which is exactly what
    scipy.cluster.hierarchy.linkage returns.

    Scope cuts: no OUTTREE= dataset (SAS's OUTTREE is a fairly involved
    specialized tree-structure dataset; real PROC CLUSTER users mostly care
    about the printed Cluster History and/or a dendrogram, neither of which
    needs it) and no rendered dendrogram image (that's PROC TREE /
    graphical territory -- scipy.cluster.hierarchy.dendrogram would be the
    natural next step if someone wants one later). Print-only: returns
    None, like PROC TTEST/ANOVA/NPAR1WAY."""
    from scipy.cluster.hierarchy import linkage

    scipy_method = _CLUSTER_METHODS.get(method.lower())
    if scipy_method is None:
        raise ValueError(
            f"PROC CLUSTER: unrecognized METHOD= {method!r}; supported "
            f"methods are {sorted(_CLUSTER_METHODS)!r}"
        )

    sub = df[cols].apply(pd.to_numeric, errors="coerce")
    mask = sub.notna().all(axis=1)
    clean = sub[mask]

    if len(clean) < 2:
        raise ValueError(
            "PROC CLUSTER: at least 2 non-missing observations are "
            f"required, found {len(clean)}"
        )

    if id_var is not None:
        labels = [str(v) for v in df.loc[mask, id_var].tolist()]
    else:
        labels = [str(i) for i in range(1, len(clean) + 1)]

    n = len(clean)
    Z = linkage(clean.to_numpy(), method=scipy_method)

    print("The CLUSTER Procedure")
    print(f"Clustering Method: {method.upper()}")
    print()

    # Running map from synthetic cluster index (n, n+1, ... in merge order,
    # as scipy numbers newly-formed clusters) -> its display label, so a
    # later merge that references an earlier-formed cluster can show a
    # readable name instead of a bare synthetic index.
    cluster_labels: dict[int, str] = {}

    print("Cluster History")
    header = f"{'Number of Clusters':>19}   {'Clusters Joined':<33}{'Distance':>12}"
    print(header)
    for step, (idx1, idx2, dist, _size) in enumerate(Z):
        idx1, idx2 = int(idx1), int(idx2)
        new_cluster_id = n + step

        def _label(idx: int) -> str:
            if idx < n:
                return labels[idx]
            return cluster_labels[idx]

        l1, l2 = _label(idx1), _label(idx2)
        display_name = f"CL{new_cluster_id - n + 1}"
        cluster_labels[new_cluster_id] = display_name

        n_clusters_remaining = n - step - 1
        joined = f"{l1} + {l2}"
        print(f"{n_clusters_remaining:>19}   {joined:<33}{dist:>12.4f}")
    print()

    return None


def proc_standardize(df: pd.DataFrame, var_names: list, target_mean: float = 0.0,
                      target_std: float = 1.0, replace: bool = False) -> pd.DataFrame:
    """PROC STANDARD: rescale each VAR column to a target mean/std
    (z-score transform by default). Missing values stay missing unless
    REPLACE is set, in which case they are filled with the column's
    original (pre-transform) mean before rescaling. A zero-variance
    (e.g. constant) column would divide by zero, so its transformed
    values are set to missing instead of raising/producing inf."""
    result = df.copy()
    for name in var_names:
        col = pd.to_numeric(result[name], errors="coerce")
        colmean = col.mean()
        colstd = col.std()
        if replace:
            col = col.fillna(colmean)
        if pd.isna(colstd) or colstd == 0:
            result[name] = float("nan")
        else:
            result[name] = (col - colmean) / colstd * target_std + target_mean
    return result


def _surveyselect_sample(group: pd.DataFrame, n: int | None, samprate: float | None,
                          seed: int | None) -> pd.DataFrame:
    """Draw a simple random sample from a single group of rows (the whole
    dataset when there is no STRATA, or one stratum's rows when there is).
    N= is clamped to the group size instead of erroring when the group has
    fewer rows than requested, matching real SAS's per-stratum allocation."""
    if n is not None:
        k = min(n, len(group))
        return group.sample(n=k, random_state=seed)
    return group.sample(frac=samprate, random_state=seed)


# ---------------- PROC SURVEYSELECT ----------------
def proc_surveyselect(df: pd.DataFrame, n: int | None = None, samprate: float | None = None,
                       seed: int | None = None, strata: list | None = None) -> pd.DataFrame:
    """PROC SURVEYSELECT, scoped to METHOD=SRS (simple random sampling).
    Exactly one of N= (exact sample size) or SAMPRATE= (proportion, sampling
    round(SAMPRATE * nrows) rows) is expected to have been validated by the
    caller. With STRATA, sampling is done independently within each distinct
    combination of STRATA variable values, applying the same N=/SAMPRATE=
    to every stratum (a stratum smaller than N= contributes all its rows,
    no error). Prints a short selection summary and returns the sampled
    rows (same columns as the input, just a row subset)."""
    print("The SURVEYSELECT Procedure")
    if strata:
        groups = df.groupby(strata, dropna=False, sort=False)
        parts = []
        counts = []
        for key, group in groups:
            sampled = _surveyselect_sample(group, n, samprate, seed)
            parts.append(sampled)
            key_tuple = key if isinstance(key, tuple) else (key,)
            counts.append(list(key_tuple) + [len(sampled)])
        result = pd.concat(parts) if parts else df.iloc[0:0].copy()
        summary = pd.DataFrame(counts, columns=list(strata) + ["Selected"])
        print("Selection Probabilities and Sample Sizes by Stratum")
        print(summary.to_string(index=False))
    else:
        result = _surveyselect_sample(df, n, samprate, seed)
        print(f"NOTE: {len(result)} of {len(df)} observations selected.")
    return result


# ---------------- PROC ARIMA ----------------
def proc_arima_fit(df: pd.DataFrame, var: str, order: tuple = (0, 0, 0),
                    lead: int | None = None) -> pd.DataFrame | None:
    """PROC ARIMA, scoped to a single IDENTIFY VAR= / ESTIMATE P=/D=/Q= /
    FORECAST LEAD=[/OUT=] batch-mode block (see _parse_proc_arima's and
    _gen_proc_arima's docstrings for the full list of scope cuts: no
    differencing-in-VAR syntax, no seasonal terms, no INPUT= transfer
    functions, no OUTLIER statement, no ESTIMATE METHOD=/competing
    ESTIMATE blocks).

    Fits statsmodels' ARIMA(order=(p, d, q)) on `var` and prints its
    summary (AIC/BIC/coefficient table), matching how proc_reg_fit /
    proc_logistic_fit just print `model.summary()`.

    When `lead` is given (FORECAST LEAD=...), also forecasts that many
    steps ahead and prints a small forecast table, returning a DataFrame
    with columns "period" (sequential integers 1..lead -- see the FORECAST
    ID= scope cut above: no real date extrapolation), "FORECAST" (the
    predicted mean) and "L95"/"U95" (the 95% confidence interval bounds).
    Without `lead`, nothing is forecast and this returns None (print-only,
    like PROC TTEST/ANOVA/CLUSTER).

    Guards against degenerate inputs with a clear ValueError rather than
    an opaque statsmodels traceback: an unknown VAR=, an all-missing
    series, missing values in the *middle* of the series (leading/trailing
    missing values are trimmed automatically -- real contiguous time
    series data shouldn't have gaps, but a leading/trailing warm-up period
    is common), and too few observations for the requested (p, d, q)
    order."""
    from statsmodels.tsa.arima.model import ARIMA

    if var not in df.columns:
        raise ValueError(f"PROC ARIMA: VAR={var!r} is not a column in the input dataset")

    series = pd.to_numeric(df[var], errors="coerce").reset_index(drop=True)
    valid_pos = series.notna().to_numpy().nonzero()[0]
    if valid_pos.size == 0:
        raise ValueError(f"PROC ARIMA: VAR={var!r} has no numeric (non-missing) observations")
    trimmed = series.iloc[valid_pos[0]:valid_pos[-1] + 1]
    if trimmed.isna().any():
        raise ValueError(
            f"PROC ARIMA: VAR={var!r} has missing values in the middle of the "
            "series -- ARIMA requires a contiguous series (leading/trailing "
            "missing values are trimmed automatically, but interior gaps are not)"
        )

    p, d, q = order
    min_obs = p + d + q + 1
    if len(trimmed) < min_obs:
        raise ValueError(
            f"PROC ARIMA: not enough observations ({len(trimmed)}) to fit "
            f"ARIMA(p={p}, d={d}, q={q}), which needs at least {min_obs}"
        )

    try:
        model = ARIMA(trimmed.reset_index(drop=True), order=(p, d, q)).fit()
    except Exception as exc:
        raise ValueError(
            f"PROC ARIMA: model fit failed for ARIMA(p={p}, d={d}, q={q}) on "
            f"VAR={var!r}: {exc}"
        ) from exc

    print("The ARIMA Procedure")
    print(f"Series: {var}   Model: ARIMA(p={p}, d={d}, q={q})")
    print()
    print(model.summary())

    if not lead or lead <= 0:
        return None

    fc = model.get_forecast(steps=lead)
    mean = fc.predicted_mean.to_numpy()
    ci = fc.conf_int(alpha=0.05)
    lower = ci.iloc[:, 0].to_numpy()
    upper = ci.iloc[:, 1].to_numpy()
    periods = list(range(1, lead + 1))

    print()
    print("Forecasts")
    print(f"  {'Period':>6}  {'Forecast':>12}  {'Lower 95%':>12}  {'Upper 95%':>12}")
    for i, per in enumerate(periods):
        print(f"  {per:>6}  {mean[i]:>12.4f}  {lower[i]:>12.4f}  {upper[i]:>12.4f}")
    print()

    return pd.DataFrame({
        "period": periods,
        "FORECAST": mean,
        "L95": lower,
        "U95": upper,
    })


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


# ---------------- SET ... KEY= keyed lookup ----------------
def keyed_lookup(df: pd.DataFrame, pdv: dict, row: dict | None = None) -> bool:
    """`SET dataset KEY=indexname;` -- this compiler has no persistent
    SAS index infrastructure, so KEY= is treated pragmatically: the
    lookup key is whichever of `df`'s columns are also columns of the
    *current source row* (`row`, the dict most recently read by this
    DATA step's driving SET/MERGE) -- not the full PDV, which can
    already contain same-named keys seeded to a static default (e.g. a
    character variable pre-seeded to "" because it's assigned a string
    literal later in the step) that would otherwise look like a "key"
    and never match real data. Falls back to the PDV's own keys if no
    row is given. The actual values compared come from the PDV (so a
    key variable computed/transformed earlier in the step is still
    used), only the *set of which columns count as keys* comes from
    `row`. Returns True and updates `pdv` with the first matching row's
    values on a hit; returns False and leaves `pdv` untouched on a miss
    (matching SAS's "unrefreshed variables keep their prior value"
    behavior for a failed lookup)."""
    candidates = row if row is not None else pdv
    key_cols = [c for c in df.columns if c in candidates]
    if not key_cols:
        return False
    mask = pd.Series(True, index=df.index)
    for c in key_cols:
        pv = pdv.get(c)
        if is_missing(pv):
            mask &= df[c].isna() if pd.api.types.is_numeric_dtype(df[c]) else (df[c] == "")
        else:
            mask &= df[c] == pv
    matched = df[mask]
    if len(matched) == 0:
        return False
    pdv.update(matched.iloc[0].to_dict())
    return True


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

    def output(self) -> pd.DataFrame:
        """.OUTPUT(DATASET: "name") -- dump the hash's current contents
        (key + data variables, one row per entry, or per MULTIDATA row)
        as a DataFrame; codegen registers the result into `_DS`."""
        rows = []
        for key, entry in self.table.items():
            for data in (entry if self.multi else [entry]):
                row = dict(zip(self.keys, key))
                row.update(data)
                rows.append(row)
        return pd.DataFrame(rows)

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
def _compare_values_equal(a, b, criterion: float = 0.0) -> bool:
    a_missing = a is None or (isinstance(a, float) and pd.isna(a))
    b_missing = b is None or (isinstance(b, float) and pd.isna(b))
    if a_missing and b_missing:
        return True
    if a_missing != b_missing:
        return False
    if criterion and isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= criterion
    return a == b


def proc_compare_report(base: pd.DataFrame, compare: pd.DataFrame,
                         id_vars: list | None = None, var_list: list | None = None,
                         by_vars: list | None = None, criterion: float = 0.0,
                         transforms: dict | None = None) -> pd.DataFrame:
    """Print a PROC COMPARE-style report (row alignment by ID or by
    position, per-variable mismatch counts, and a capped list of
    differing values) and return a long-form DataFrame of differences
    with columns _id_/_var_/_base_/_compare_ (plus one column per BY
    variable, when BY is used) for optional OUT= use.

    CRITERION= gives a numeric equality tolerance (abs(a - b) <=
    criterion counts as equal); BY= runs a separate comparison, with
    its own printed report, per distinct combination of BY-variable
    values found across BASE/COMPARE. TRANSFORM= (via `transforms`, a
    {var_name: func_name} map with func_name one of "log"/"sqrt"/
    "exp"/"abs") applies the named function to that variable in both
    BASE and COMPARE before comparing; when an ID value repeats,
    repeated occurrences on each side are matched pairwise in
    encounter order."""
    if by_vars:
        all_keys: list = []
        seen = set()
        for df in (base, compare):
            for k in df[by_vars].drop_duplicates().itertuples(index=False, name=None):
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)
        all_keys.sort(key=lambda k: tuple("" if v is None else v for v in k))
        all_diffs = []
        for key in all_keys:
            label = ", ".join(f"{c}={sas_str(v)}" for c, v in zip(by_vars, key))
            print(f"--- {label} ---")
            mask_b = pd.Series(True, index=base.index)
            mask_c = pd.Series(True, index=compare.index)
            for c, v in zip(by_vars, key):
                mask_b &= base[c] == v
                mask_c &= compare[c] == v
            sub_diffs = _proc_compare_single(
                base[mask_b].drop(columns=by_vars), compare[mask_c].drop(columns=by_vars),
                id_vars=id_vars, var_list=var_list, criterion=criterion,
                transforms=transforms,
            )
            for d in sub_diffs:
                for c, v in zip(by_vars, key):
                    d[c] = v
            all_diffs.extend(sub_diffs)
            print()
        cols = list(by_vars) + ["_id_", "_var_", "_base_", "_compare_"]
        return pd.DataFrame(all_diffs, columns=cols) if all_diffs else pd.DataFrame(columns=cols)

    diffs = _proc_compare_single(base, compare, id_vars=id_vars, var_list=var_list, criterion=criterion,
                                  transforms=transforms)
    return pd.DataFrame(diffs, columns=["_id_", "_var_", "_base_", "_compare_"]) if diffs else \
        pd.DataFrame(columns=["_id_", "_var_", "_base_", "_compare_"])


_COMPARE_TRANSFORM_FUNCS = {
    "log": np.log,
    "sqrt": np.sqrt,
    "exp": np.exp,
    "abs": np.abs,
}


def _proc_compare_single(base: pd.DataFrame, compare: pd.DataFrame,
                          id_vars: list | None, var_list: list | None,
                          criterion: float, transforms: dict | None = None) -> list:
    """Run and print one PROC COMPARE report for a single BASE/COMPARE
    pair (one BY-group's worth, or the whole datasets when BY isn't
    used); returns the list of difference records."""
    if transforms:
        base = base.copy()
        compare = compare.copy()
        for col, func_name in transforms.items():
            func = _COMPARE_TRANSFORM_FUNCS.get(func_name)
            if func is None:
                raise ValueError(f"unsupported PROC COMPARE TRANSFORM= function: {func_name!r}")
            if col in base.columns:
                base[col] = func(base[col])
            if col in compare.columns:
                compare[col] = func(compare[col])
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
        b_map: dict = defaultdict(list)
        for r in base.to_dict("records"):
            b_map[tuple(r.get(k) for k in id_vars)].append(r)
        c_map: dict = defaultdict(list)
        for r in compare.to_dict("records"):
            c_map[tuple(r.get(k) for k in id_vars)].append(r)
        seen = set()
        all_keys = []
        for k in list(b_map.keys()) + list(c_map.keys()):
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

        def _key_label(k):
            return ", ".join(f"{kk}={vv}" for kk, vv in zip(id_vars, k))

        for key in all_keys:
            b_rows = b_map.get(key, [])
            c_rows = c_map.get(key, [])
            if not b_rows:
                compare_only_keys.extend([key] * len(c_rows))
                continue
            if not c_rows:
                base_only_keys.extend([key] * len(b_rows))
                continue
            # Repeated ID values are matched pairwise in encounter
            # order (1st BASE row for key K vs. 1st COMPARE row for
            # key K, 2nd vs. 2nd, ...); any extra occurrences on the
            # longer side count as unmatched, like a wholly-missing key.
            for brow, crow in zip(b_rows, c_rows):
                obs_compared += 1
                row_equal = True
                for col in common_cols:
                    bv, cv = brow.get(col), crow.get(col)
                    if _compare_values_equal(bv, cv, criterion):
                        var_match[col] += 1
                    else:
                        var_diff[col] += 1
                        row_equal = False
                        diffs.append({"_id_": _key_label(key), "_var_": col, "_base_": bv, "_compare_": cv})
                if row_equal:
                    obs_equal += 1
            if len(b_rows) > len(c_rows):
                base_only_keys.extend([key] * (len(b_rows) - len(c_rows)))
            elif len(c_rows) > len(b_rows):
                compare_only_keys.extend([key] * (len(c_rows) - len(b_rows)))
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
                if _compare_values_equal(bv, cv, criterion):
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
        return []

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

    return diffs


# ---------------- ODS (HTML / RTF) ----------------
class _OdsHtmlCapture:
    """A stdout-like per-destination buffer used while an ODS destination
    is open. Everything the generated code prints (PROC output, PUT, ...)
    while that destination is open is collected here. (Despite the name,
    kept for backward compatibility, this generic buffer backs every ODS
    destination -- not just HTML.)"""

    def __init__(self):
        self.buffer = []

    def write(self, text):
        self.buffer.append(text)
        return len(text)

    def flush(self):
        pass


class _OdsBroadcast:
    """What sys.stdout actually points to while >=1 ODS destination is
    open: fans every write out to each currently-open destination's own
    buffer, so e.g. HTML, RTF and PDF can be open at the same time and
    each independently captures everything printed while IT is open."""

    def write(self, text):
        for state in (_ODS_HTML_STATE, _ODS_RTF_STATE, _ODS_PDF_STATE):
            if state["active"]:
                state["capture"].write(text)
        return len(text)

    def flush(self):
        pass


_ODS_BROADCAST = _OdsBroadcast()
_ODS_SAVED_STDOUT = {"value": None}

_ODS_HTML_STATE = {"active": False, "path": None, "capture": None, "real_stdout": None}
_ODS_RTF_STATE = {"active": False, "path": None, "capture": None, "real_stdout": None}
_ODS_PDF_STATE = {"active": False, "path": None, "capture": None, "real_stdout": None}


def _ods_redirect_if_needed():
    if _ODS_SAVED_STDOUT["value"] is None:
        _ODS_SAVED_STDOUT["value"] = sys.stdout
        sys.stdout = _ODS_BROADCAST


def _ods_restore_if_idle():
    if not _ODS_HTML_STATE["active"] and not _ODS_RTF_STATE["active"] and not _ODS_PDF_STATE["active"]:
        if _ODS_SAVED_STDOUT["value"] is not None:
            sys.stdout = _ODS_SAVED_STDOUT["value"]
            _ODS_SAVED_STDOUT["value"] = None


_ODS_MIN_TABLE_COLS = 3  # narrower blocks (e.g. a single-VAR PROC PRINT)
# stay <pre> rather than risk mangling narrow narrative text into a table


def _ods_split_chunks(text: str) -> list:
    return re.split(r"\n\s*\n", text)


def _ods_try_parse_table(chunk: str):
    """Return (header, [data_rows]) if `chunk` looks like a monospace
    -aligned table (every non-blank line splits into the same nonzero
    field count on runs of 2+ spaces, with at least MIN_TABLE_COLS
    columns and at least one data row), else None."""
    lines = [ln for ln in chunk.split("\n") if ln.strip() != ""]
    if len(lines) < 2:
        return None

    def split_fields(ln):
        return [f for f in re.split(r"\s{2,}", ln.strip()) if f != ""]

    header = split_fields(lines[0])
    data_start = 1
    # pandas prints a DataFrame's named index (e.g. PROC PRINT's "Obs")
    # on its own line right after the header rather than folding it into
    # the header line; fold it back in as the header's first column.
    if len(lines) >= 3:
        second = split_fields(lines[1])
        if len(second) == 1 and len(split_fields(lines[2])) == len(header) + 1:
            header = second + header
            data_start = 2

    data_lines = lines[data_start:]
    if not data_lines:
        return None
    rows = [split_fields(ln) for ln in data_lines]
    ncols = len(header)
    if ncols < _ODS_MIN_TABLE_COLS:
        return None
    if any(len(r) != ncols for r in rows):
        return None
    return header, rows


def _ods_html_document(text: str) -> str:
    parts = []
    for chunk in _ods_split_chunks(text):
        if chunk.strip() == "":
            continue
        table = _ods_try_parse_table(chunk)
        if table:
            header, body_rows = table
            trs = ["<tr>" + "".join(f"<th>{_html.escape(c)}</th>" for c in header) + "</tr>"]
            for r in body_rows:
                trs.append("<tr>" + "".join(f"<td>{_html.escape(c)}</td>" for c in r) + "</tr>")
            parts.append("<table>\n" + "\n".join(trs) + "\n</table>")
        else:
            parts.append(f"<pre>{_html.escape(chunk)}</pre>")
    body_html = "\n".join(parts)
    style = (
        "body{font-family:monospace;font-size:14px;}"
        "table{border-collapse:collapse;margin:1em 0;}"
        "th,td{border:1px solid #888;padding:4px 10px;text-align:right;}"
        "th{background:#e8e8e8;}"
        "pre{white-space:pre-wrap;}"
    )
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>SAS Output</title><style>{style}</style></head>\n"
        f"<body>\n{body_html}\n</body></html>\n"
    )


def _ods_rtf_escape(text: str) -> str:
    text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return "\\par\n".join(text.split("\n"))


def _ods_rtf_table(header: list, body_rows: list) -> str:
    """Render one table-shaped chunk as real RTF table markup: a
    \\trowd/\\cellx row definition (repeated per row, since RTF has no
    single table-wide element) with \\intbl-marked, \\cell-terminated
    cells and a trailing \\row. Column widths are sized (roughly) by
    each column's widest cell, in twips. Ends with \\pard so content
    following the table (joined in by the caller) isn't left "inside"
    table formatting."""
    ncols = len(header)
    widths = []
    for i in range(ncols):
        widest = max(len(header[i]), max((len(r[i]) for r in body_rows), default=0))
        widths.append(max(1200, widest * 120 + 400))
    cellx = []
    running = 0
    for w in widths:
        running += w
        cellx.append(running)
    cellx_str = "".join(f"\\cellx{x}" for x in cellx)

    def row_rtf(cells, bold):
        line = "\\trowd " + cellx_str + "\n\\intbl "
        for c in cells:
            esc = _ods_rtf_escape(c)
            line += f"\\b {esc}\\b0 \\cell " if bold else f"{esc}\\cell "
        return line + "\\row"

    rows = [row_rtf(header, True)] + [row_rtf(r, False) for r in body_rows]
    return "\n".join(rows) + "\n\\pard"


def _ods_rtf_document(text: str) -> str:
    """Render captured ODS text as an RTF document body, using the same
    chunk-splitting/table-detection heuristic as the HTML/PDF renderers:
    table-shaped chunks become real \\trowd/\\cellx/\\intbl/\\row table
    markup (bold header row); everything else stays \\par-separated
    preformatted text, exactly as before."""
    parts = []
    for chunk in _ods_split_chunks(text):
        if chunk.strip() == "":
            continue
        table = _ods_try_parse_table(chunk)
        if table:
            header, body_rows = table
            parts.append(_ods_rtf_table(header, body_rows))
        else:
            parts.append(_ods_rtf_escape(chunk))
    body = "\n\\par\n".join(parts)
    return (
        "{\\rtf1\\ansi\\deff0\n"
        "{\\fonttbl{\\f0\\fmodern Courier New;}}\n"
        "\\f0\\fs20\n"
        f"{body}\n"
        "}\n"
    )


def _ods_pdf_document(text: str, path: str):
    """Render captured ODS text as a PDF file at `path`, using the same
    chunk-splitting/table-detection heuristic as the HTML renderer:
    table-shaped chunks become a reportlab Table flowable (light grid,
    shaded header row); everything else becomes monospace Preformatted
    text, mirroring <pre> in _ods_html_document()."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Preformatted, Spacer

    styles = getSampleStyleSheet()
    mono_style = ParagraphStyle(
        "ODSMono", parent=styles["Normal"], fontName="Courier", fontSize=9, leading=11
    )

    flowables = []
    for chunk in _ods_split_chunks(text):
        if chunk.strip() == "":
            continue
        table = _ods_try_parse_table(chunk)
        if table:
            header, body_rows = table
            data = [header] + body_rows
            tbl = Table(data, repeatRows=1)
            tbl.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                        ("FONTNAME", (0, 0), (-1, -1), "Courier"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            flowables.append(tbl)
        else:
            flowables.append(Preformatted(chunk, mono_style))
        flowables.append(Spacer(1, 12))

    if not flowables:
        # SimpleDocTemplate.build([]) raises on an empty flowables list;
        # a single empty Spacer keeps this a valid (if blank) PDF.
        flowables = [Spacer(1, 1)]

    SimpleDocTemplate(path, pagesize=letter).build(flowables)


def ods_proc_boundary():
    """Called once after every PROC step's own output. Inserts a blank
    line, but only while an ODS destination is actually capturing --
    this is how the HTML/RTF/PDF renderer tells where one PROC's report
    ends and the next begins (they'd otherwise run together with no
    separator, since PROC steps don't print a trailing blank line on
    their own). A complete no-op otherwise, so it never changes
    ordinary (non-ODS) program output."""
    if _ODS_HTML_STATE["active"] or _ODS_RTF_STATE["active"] or _ODS_PDF_STATE["active"]:
        print()


def ods_html_open(path):
    """ODS HTML FILE="path"; -- start capturing everything printed until
    ods_html_close(). Re-opening while already open closes (and writes)
    the previous destination first, rather than losing it silently.
    HTML and RTF may be open at the same time; each captures
    independently starting from when it was opened."""
    if _ODS_HTML_STATE["active"]:
        ods_html_close()
    _ods_redirect_if_needed()
    capture = _OdsHtmlCapture()
    _ODS_HTML_STATE.update(active=True, path=path, capture=capture, real_stdout=_ODS_SAVED_STDOUT["value"])


def ods_html_close():
    """ODS HTML CLOSE; -- stop capturing for HTML and write the
    accumulated output to the destination file. A no-op if HTML isn't
    currently open. Real stdout is only restored once no other ODS
    destination (e.g. RTF) is still open."""
    if not _ODS_HTML_STATE["active"]:
        return
    capture = _ODS_HTML_STATE["capture"]
    path = _ODS_HTML_STATE["path"]
    _ODS_HTML_STATE.update(active=False, path=None, capture=None, real_stdout=None)
    _ods_restore_if_idle()

    doc = _ods_html_document("".join(capture.buffer))
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def ods_rtf_open(path):
    """ODS RTF FILE="path"; -- same semantics as ods_html_open() but for
    the RTF destination; HTML and RTF may both be open at once."""
    if _ODS_RTF_STATE["active"]:
        ods_rtf_close()
    _ods_redirect_if_needed()
    capture = _OdsHtmlCapture()
    _ODS_RTF_STATE.update(active=True, path=path, capture=capture, real_stdout=_ODS_SAVED_STDOUT["value"])


def ods_rtf_close():
    """ODS RTF CLOSE; -- see ods_html_close()."""
    if not _ODS_RTF_STATE["active"]:
        return
    capture = _ODS_RTF_STATE["capture"]
    path = _ODS_RTF_STATE["path"]
    _ODS_RTF_STATE.update(active=False, path=None, capture=None, real_stdout=None)
    _ods_restore_if_idle()

    doc = _ods_rtf_document("".join(capture.buffer))
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def ods_pdf_open(path):
    """ODS PDF FILE="path"; -- same semantics as ods_html_open() but for
    the PDF destination; HTML, RTF and PDF may all be open at once."""
    if _ODS_PDF_STATE["active"]:
        ods_pdf_close()
    _ods_redirect_if_needed()
    capture = _OdsHtmlCapture()
    _ODS_PDF_STATE.update(active=True, path=path, capture=capture, real_stdout=_ODS_SAVED_STDOUT["value"])


def ods_pdf_close():
    """ODS PDF CLOSE; -- see ods_html_close()."""
    if not _ODS_PDF_STATE["active"]:
        return
    capture = _ODS_PDF_STATE["capture"]
    path = _ODS_PDF_STATE["path"]
    _ODS_PDF_STATE.update(active=False, path=None, capture=None, real_stdout=None)
    _ods_restore_if_idle()

    _ods_pdf_document("".join(capture.buffer), path)


def _ods_html_atexit_restore():
    """Safety net: if the process exits (e.g. an unhandled exception in a
    step between ODS HTML FILE= and ODS HTML CLOSE;) while still
    redirected, restore stdout and flush whatever was captured rather
    than silently swallowing output or leaving stdout broken."""
    if _ODS_HTML_STATE["active"]:
        try:
            ods_html_close()
        except Exception:
            pass


def _ods_rtf_atexit_restore():
    """Safety net: see _ods_html_atexit_restore(), for RTF."""
    if _ODS_RTF_STATE["active"]:
        try:
            ods_rtf_close()
        except Exception:
            pass


def _ods_pdf_atexit_restore():
    """Safety net: see _ods_html_atexit_restore(), for PDF."""
    if _ODS_PDF_STATE["active"]:
        try:
            ods_pdf_close()
        except Exception:
            pass


atexit.register(_ods_html_atexit_restore)
atexit.register(_ods_rtf_atexit_restore)
atexit.register(_ods_pdf_atexit_restore)
