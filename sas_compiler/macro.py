"""
SAS macro-language preprocessor.

Real SAS compiles a program in two interleaved passes: the macro processor
(word scanner) expands %macro calls, &variables, %if/%do control flow, etc,
producing plain SAS text, which the DATA step / PROC compiler then parses.

This module implements that first pass: given raw SAS source, it returns
expanded plain SAS source with all macro activity resolved.

Design: rather than a line-based expander, this is a recursive-descent
scanner over the character stream. Control constructs (%if/%then/%else,
%do/%end, %macro/%mend) first *locate* their raw sub-spans without
executing them (so an untaken %if branch's %let etc. never runs), then
selectively re-enter the main parse loop on the chosen span. This mirrors
how the real macro processor only executes the taken branch.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field


class MacroError(Exception):
    pass


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# macro statement keywords that introduce a construct we special-case
_KEYWORDS = {
    "macro", "mend", "let", "if", "then", "else", "do", "end",
    "to", "by", "until", "while", "put", "global", "local",
    "eval", "str", "nrstr", "quote", "bquote", "upcase", "lowcase",
    "length", "substr", "scan", "index", "sysfunc", "qsysfunc",
    "sysevalf", "trim", "left", "cmpres", "syscall",
}


@dataclass
class MacroDef:
    name: str
    params: list  # list of (name, default_or_None)
    body: str


@dataclass
class Scanner:
    text: str
    pos: int = 0

    def eof(self) -> bool:
        return self.pos >= len(self.text)

    def peek(self, n: int = 1) -> str:
        return self.text[self.pos:self.pos + n]

    def startswith_word(self, word: str) -> bool:
        """Case-insensitive match of `%word` (or bare `word` for &-context) at pos,
        requiring the char after the word not continue an identifier."""
        t = self.text
        p = self.pos
        wl = len(word)
        if t[p:p + wl].lower() != word.lower():
            return False
        end = p + wl
        if end < len(t) and (t[end].isalnum() or t[end] == "_"):
            return False
        return True

    def match_pct_word(self, word: str) -> bool:
        """Match literal '%word' at current position (word boundary)."""
        if self.peek(1) != "%":
            return False
        save = self.pos
        self.pos += 1
        ok = self.startswith_word(word)
        self.pos = save
        return ok


class MacroProcessor:
    def __init__(self, trace=False):
        self.macros: dict[str, MacroDef] = {}
        self.scopes: list[dict] = [{}]  # index 0 = global
        self.trace = trace
        self._builtin_names = {
            "sysdate", "sysdate9", "systime", "sysday",
        }
        today = datetime.date.today()
        self.scopes[0]["sysdate9"] = today.strftime("%d%b%Y").upper()

    # ---------------- variable scoping ----------------
    def get_var(self, name: str) -> str:
        name_l = name.lower()
        for scope in reversed(self.scopes):
            if name_l in scope:
                return scope[name_l]
        return ""

    def has_var(self, name: str) -> bool:
        name_l = name.lower()
        return any(name_l in s for s in self.scopes)

    def set_local(self, name: str, value: str):
        self.scopes[-1][name.lower()] = value

    def set_global(self, name: str, value: str):
        self.scopes[0][name.lower()] = value

    def set_auto(self, name: str, value: str):
        """%let semantics: assign in the innermost scope that already
        defines it, else local to current scope (global if at top level)."""
        name_l = name.lower()
        for scope in reversed(self.scopes):
            if name_l in scope:
                scope[name_l] = value
                return
        self.scopes[-1][name_l] = value

    # ---------------- entry point ----------------
    def expand(self, text: str) -> str:
        sc = Scanner(text)
        return self._parse_program(sc, stop_at_mend=False)

    # ---------------- core scanning helpers ----------------
    def _skip_ws(self, sc: Scanner):
        while not sc.eof() and sc.text[sc.pos] in " \t\r\n":
            sc.pos += 1

    def _find_block_end(self, sc: Scanner, open_kw: str, close_kw: str) -> str:
        """Assuming sc.pos is positioned right after the opening keyword's
        own terminator setup is handled by caller; this scans forward
        tracking nested open/close keyword pairs (also independently
        tracking the *other* pair type so a %macro inside a %do, or vice
        versa, doesn't confuse matching) and returns the raw text up to
        (not including) the matching close keyword, leaving sc.pos right
        after the close keyword's own trailing ';' is NOT consumed here."""
        depth = 1
        start = sc.pos
        text = sc.text
        n = len(text)
        i = sc.pos
        in_squote = False
        in_dquote = False
        while i < n:
            c = text[i]
            if in_squote:
                if c == "'":
                    in_squote = False
                i += 1
                continue
            if in_dquote:
                if c == '"':
                    in_dquote = False
                i += 1
                continue
            if c == "'":
                in_squote = True
                i += 1
                continue
            if c == '"':
                in_dquote = True
                i += 1
                continue
            if c == "/" and text[i:i + 2] == "/*":
                j = text.find("*/", i + 2)
                i = (j + 2) if j != -1 else n
                continue
            if c == "%":
                tmp = Scanner(text, i)
                if tmp.startswith_word("%" + open_kw) if False else False:
                    pass
                # check open_kw
                if self._match_kw_at(text, i, open_kw):
                    depth += 1
                    i += 1 + len(open_kw)
                    continue
                if self._match_kw_at(text, i, close_kw):
                    depth -= 1
                    if depth == 0:
                        result = text[start:i]
                        sc.pos = i + 1 + len(close_kw)
                        return result
                    i += 1 + len(close_kw)
                    continue
            i += 1
        raise MacroError(f"unterminated %{open_kw} block (missing %{close_kw})")

    @staticmethod
    def _match_kw_at(text: str, i: int, kw: str) -> bool:
        if text[i] != "%":
            return False
        wl = len(kw)
        if text[i + 1:i + 1 + wl].lower() != kw.lower():
            return False
        end = i + 1 + wl
        if end < len(text) and (text[end].isalnum() or text[end] == "_"):
            return False
        return True

    def _find_top_level(self, sc: Scanner, stop_words) -> tuple[str, str | None]:
        """Scan raw (unexpanded) text from sc.pos until one of the given
        keywords (case-insensitive, e.g. '%then', ';', '%to', '%by') is
        found at nesting depth 0 (not inside quotes/parens/%do../%macro..
        blocks). Advances sc.pos past the matched keyword. Returns
        (raw_text_before, matched_keyword) or (raw_text, None) at EOF."""
        text = sc.text
        n = len(text)
        i = sc.pos
        start = i
        paren_depth = 0
        block_depth = 0  # tracks %do/%macro nesting so their %end/%mend isn't mistaken
        in_squote = in_dquote = False
        while i < n:
            c = text[i]
            if in_squote:
                if c == "'":
                    in_squote = False
                i += 1
                continue
            if in_dquote:
                if c == '"':
                    in_dquote = False
                i += 1
                continue
            if c == "'":
                in_squote = True
                i += 1
                continue
            if c == '"':
                in_dquote = True
                i += 1
                continue
            if c == "/" and text[i:i + 2] == "/*":
                j = text.find("*/", i + 2)
                i = (j + 2) if j != -1 else n
                continue
            if c == "(":
                paren_depth += 1
                i += 1
                continue
            if c == ")":
                paren_depth -= 1
                i += 1
                continue
            if paren_depth == 0 and c == "%":
                if self._match_kw_at(text, i, "do") or self._match_kw_at(text, i, "macro"):
                    block_depth += 1
                elif self._match_kw_at(text, i, "end") or self._match_kw_at(text, i, "mend"):
                    if block_depth > 0:
                        block_depth -= 1
            if paren_depth == 0 and block_depth == 0:
                if c == ";" and ";" in stop_words:
                    sc.pos = i + 1
                    return text[start:i], ";"
                if c == "%":
                    for kw in stop_words:
                        if kw == ";":
                            continue
                        bare = kw[1:] if kw.startswith("%") else kw
                        if self._match_kw_at(text, i, bare):
                            sc.pos = i + 1 + len(bare)
                            return text[start:i], kw
            i += 1
        sc.pos = n
        return text[start:i], None

    # ---------------- main dispatch loop ----------------
    def _parse_program(self, sc: Scanner, stop_at_mend: bool) -> str:
        out = []
        text = sc.text
        n = len(text)
        while not sc.eof():
            c = sc.peek()
            if c == "'":
                out.append(self._consume_quoted(sc, "'"))
                continue
            if c == '"':
                out.append(self._consume_quoted(sc, '"'))
                continue
            if c == "/" and sc.peek(2) == "/*":
                j = text.find("*/", sc.pos + 2)
                sc.pos = (j + 2) if j != -1 else n
                continue
            if c == "%":
                nxt = text[sc.pos + 1:sc.pos + 2]
                if nxt == "*":
                    j = text.find(";", sc.pos)
                    sc.pos = (j + 1) if j != -1 else n
                    continue
                handled, chunk = self._try_dispatch(sc)
                if handled:
                    out.append(chunk)
                    continue
            if c == "&":
                handled, chunk = self._resolve_amp(sc)
                if handled:
                    out.append(chunk)
                    continue
            out.append(c)
            sc.pos += 1
        return "".join(out)

    def _resolve_amp(self, sc: Scanner) -> tuple[bool, str]:
        """Resolve one or more leading '&' as macro-variable dereference(s).
        '&&x' resolves the inner '&x' first (one less '&'), then re-resolves
        the result -- this is how SAS implements indirect ("doubly indexed")
        macro variables."""
        text = sc.text
        p = sc.pos
        amp_count = 0
        i = p
        while i < len(text) and text[i] == "&":
            amp_count += 1
            i += 1
        m = _WORD_RE.match(text, i)
        if not m:
            return False, ""
        name = m.group(0)
        end = m.end()
        has_dot = end < len(text) and text[end] == "."
        if amp_count >= 2:
            # collapse one level: && -> &, then re-scan from there
            reduced = "&" * (amp_count - 1) + name + ("." if has_dot else "")
            first_pass = self._parse_program(Scanner(reduced), stop_at_mend=False)
            sc.pos = end + (1 if has_dot else 0)
            second = self._parse_program(Scanner(first_pass), stop_at_mend=False)
            return True, second
        value = self.get_var(name)
        sc.pos = end + (1 if has_dot else 0)
        return True, value

    def _consume_quoted(self, sc: Scanner, q: str) -> str:
        text = sc.text
        start = sc.pos
        sc.pos += 1
        while not sc.eof() and sc.peek() != q:
            sc.pos += 1
        sc.pos += 1  # closing quote
        raw = text[start:sc.pos]
        if q == "'":
            return raw  # single-quoted: no macro resolution
        # double-quoted: resolve macro triggers inside, keep quotes
        inner = raw[1:-1]
        inner_sc = Scanner(inner)
        resolved = self._parse_program(inner_sc, stop_at_mend=False)
        return '"' + resolved + '"'

    def _try_dispatch(self, sc: Scanner) -> tuple[bool, str]:
        text = sc.text
        p = sc.pos
        if text[p] != "%":
            return False, ""
        m = _WORD_RE.match(text, p + 1)
        if not m:
            if text[p + 1:p + 2] == "%":
                return False, ""
            return False, ""
        word = m.group(0)
        wl = word.lower()

        if wl == "macro":
            sc.pos = m.end()
            self._do_macro_def(sc)
            return True, ""
        if wl == "let":
            sc.pos = m.end()
            return True, self._do_let(sc)
        if wl in ("global", "local"):
            sc.pos = m.end()
            return True, self._do_scope_decl(sc, wl)
        if wl == "if":
            sc.pos = m.end()
            return True, self._do_if(sc)
        if wl == "do":
            sc.pos = m.end()
            return True, self._do_do(sc)
        if wl == "put":
            sc.pos = m.end()
            return True, self._do_put(sc)
        if wl == "eval":
            sc.pos = m.end()
            return True, str(self._do_eval(sc))
        if wl == "sysevalf":
            sc.pos = m.end()
            return True, str(self._do_sysevalf(sc))
        if wl in ("str", "nrstr"):
            sc.pos = m.end()
            return True, self._do_str(sc, resolve_inner=False)
        if wl in ("quote", "bquote", "nrbquote", "nrquote"):
            sc.pos = m.end()
            return True, self._do_str(sc, resolve_inner=True)
        if wl == "upcase":
            sc.pos = m.end()
            return True, self._simple_text_func(sc, str.upper)
        if wl == "lowcase":
            sc.pos = m.end()
            return True, self._simple_text_func(sc, str.lower)
        if wl == "trim":
            sc.pos = m.end()
            return True, self._simple_text_func(sc, lambda s: s.strip())
        if wl == "cmpres":
            sc.pos = m.end()
            return True, self._simple_text_func(sc, lambda s: re.sub(r"\s+", " ", s.strip()))
        if wl == "length":
            sc.pos = m.end()
            return True, self._do_length(sc)
        if wl == "substr":
            sc.pos = m.end()
            return True, self._do_macro_substr(sc)
        if wl == "scan":
            sc.pos = m.end()
            return True, self._do_macro_scan(sc)
        if wl == "index":
            sc.pos = m.end()
            return True, self._do_macro_index(sc)
        if wl in ("sysfunc", "qsysfunc"):
            sc.pos = m.end()
            return True, self._do_sysfunc(sc)
        if wl in ("then", "else", "end", "mend", "to", "by", "until", "while"):
            # bare occurrence not consumed by a construct above -> stray;
            # treat literally (shouldn't normally happen at top level)
            return False, ""

        # user-defined macro call, or unknown -> attempt call if defined
        if wl in self.macros:
            sc.pos = m.end()
            return True, self._do_macro_call(sc, wl)
        return False, ""

    # ---------------- %macro / %mend ----------------
    def _do_macro_def(self, sc: Scanner):
        self._skip_ws(sc)
        name_m = _WORD_RE.match(sc.text, sc.pos)
        if not name_m:
            raise MacroError("expected macro name after %macro")
        name = name_m.group(0)
        sc.pos = name_m.end()
        self._skip_ws(sc)
        params: list[tuple[str, str | None]] = []
        if sc.peek() == "(":
            sc.pos += 1
            depth = 1
            buf = []
            while depth > 0:
                c = sc.text[sc.pos]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        sc.pos += 1
                        break
                buf.append(c)
                sc.pos += 1
            arglist = "".join(buf)
            for part in self._split_top_commas(arglist):
                part = part.strip()
                if not part:
                    continue
                if "=" in part:
                    pn, pv = part.split("=", 1)
                    params.append((pn.strip(), pv.strip()))
                else:
                    params.append((part, None))
        # skip to end of the %macro name(...); statement (its own ';')
        j = sc.text.find(";", sc.pos)
        if j != -1:
            sc.pos = j + 1
        body = self._find_block_end(sc, "macro", "mend")
        # consume optional trailing name + ';' after %mend
        j = sc.text.find(";", sc.pos)
        if j != -1:
            sc.pos = j + 1
        self.macros[name.lower()] = MacroDef(name=name, params=params, body=body)

    @staticmethod
    def _split_top_commas(s: str) -> list[str]:
        parts = []
        depth = 0
        buf = []
        for c in s:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            if c == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
            else:
                buf.append(c)
        parts.append("".join(buf))
        return parts

    def _do_macro_call(self, sc: Scanner, name_lower: str) -> str:
        mdef = self.macros[name_lower]
        args_raw = []
        if sc.peek() == "(":
            sc.pos += 1
            depth = 1
            buf = []
            while depth > 0:
                if sc.eof():
                    raise MacroError(f"unterminated call to %{mdef.name}")
                c = sc.text[sc.pos]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        sc.pos += 1
                        break
                buf.append(c)
                sc.pos += 1
            args_raw = self._split_top_commas("".join(buf))
            args_raw = [a for a in args_raw] if "".join(buf).strip() != "" else []

        # resolve each argument's macro triggers in caller's context
        resolved_args = []
        for a in args_raw:
            a_sc = Scanner(a)
            resolved_args.append(self._parse_program(a_sc, stop_at_mend=False).strip())

        new_scope: dict[str, str] = {}
        # separate keyword args (name=value) from positional
        kw_args = {}
        pos_args = []
        for a in resolved_args:
            mkw = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", a, re.S)
            if mkw and mkw.group(1).lower() in [p[0].lower() for p in mdef.params]:
                kw_args[mkw.group(1).lower()] = mkw.group(2).strip()
            else:
                pos_args.append(a)
        pi = 0
        for pname, pdefault in mdef.params:
            if pname.lower() in kw_args:
                new_scope[pname.lower()] = kw_args[pname.lower()]
            elif pi < len(pos_args):
                new_scope[pname.lower()] = pos_args[pi]
                pi += 1
            else:
                new_scope[pname.lower()] = pdefault if pdefault is not None else ""

        self.scopes.append(new_scope)
        try:
            body_sc = Scanner(mdef.body)
            result = self._parse_program(body_sc, stop_at_mend=False)
        finally:
            self.scopes.pop()
        return result

    # ---------------- %let / %global / %local ----------------
    def _do_let(self, sc: Scanner):
        self._skip_ws(sc)
        raw, _ = self._find_top_level(sc, [";"])
        if "=" not in raw:
            raise MacroError(f"%let missing '=': {raw!r}")
        name, value = raw.split("=", 1)
        name = name.strip()
        val_sc = Scanner(value)
        resolved_value = self._parse_program(val_sc, stop_at_mend=False).strip()
        self.set_auto(name, resolved_value)
        return ""

    def _do_scope_decl(self, sc: Scanner, kind: str):
        raw, _ = self._find_top_level(sc, [";"])
        for part in raw.split():
            for name in part.split(","):
                name = name.strip()
                if not name:
                    continue
                if "=" in name:
                    n, v = name.split("=", 1)
                else:
                    n, v = name, ""
                if kind == "global":
                    if not self.has_var(n):
                        self.set_global(n, v)
                else:
                    self.scopes[-1].setdefault(n.lower(), v)
        return ""

    # ---------------- %if / %then / %else ----------------
    def _do_if(self, sc: Scanner):
        cond_raw, _ = self._find_top_level(sc, ["%then"])
        cond_sc = Scanner(cond_raw)
        cond_resolved = self._parse_program(cond_sc, stop_at_mend=False).strip()
        truth = self._eval_condition(cond_resolved)
        self._skip_ws(sc)

        then_text = self._capture_branch(sc)
        result = ""
        if truth:
            result = self._parse_program(Scanner(then_text), stop_at_mend=False)

        self._skip_ws(sc)
        if self._peek_kw(sc, "else"):
            sc.pos += 5  # '%else'
            self._skip_ws(sc)
            else_text = self._capture_branch(sc)
            if not truth:
                result = self._parse_program(Scanner(else_text), stop_at_mend=False)
        return result

    def _peek_kw(self, sc: Scanner, kw: str) -> bool:
        return self._match_kw_at(sc.text, sc.pos, kw)

    def _capture_branch(self, sc: Scanner) -> str:
        """Capture raw text of one %then/%else action: either a %do..%end
        block, or a simple statement up to the next top-level ';'."""
        if self._peek_kw(sc, "do"):
            sc.pos += 3  # '%do'
            j = sc.text.find(";", sc.pos)
            if j != -1:
                sc.pos = j + 1
            body = self._find_block_end(sc, "do", "end")
            j = sc.text.find(";", sc.pos)
            if j != -1:
                sc.pos = j + 1
            return body
        raw, _ = self._find_top_level(sc, [";"])
        return raw + ";"

    # ---------------- %do / %end ----------------
    def _do_do(self, sc: Scanner):
        self._skip_ws(sc)
        if sc.peek() == ";":
            sc.pos += 1
            body = self._find_block_end(sc, "do", "end")
            j = sc.text.find(";", sc.pos)
            if j != -1:
                sc.pos = j + 1
            return self._parse_program(Scanner(body), stop_at_mend=False)

        if self._peek_kw(sc, "while") or self._peek_kw(sc, "until"):
            is_while = self._peek_kw(sc, "while")
            sc.pos += 6 if is_while else 6
            self._skip_ws(sc)
            assert sc.peek() == "("
            sc.pos += 1
            depth = 1
            start = sc.pos
            while depth > 0:
                c = sc.text[sc.pos]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                sc.pos += 1
            cond_raw = sc.text[start:sc.pos - 1]
            j = sc.text.find(";", sc.pos)
            if j != -1:
                sc.pos = j + 1
            body = self._find_block_end(sc, "do", "end")
            j = sc.text.find(";", sc.pos)
            if j != -1:
                sc.pos = j + 1
            out = []
            iterations = 0
            while iterations < 100000:
                cr = self._parse_program(Scanner(cond_raw), stop_at_mend=False).strip()
                truth = self._eval_condition(cr)
                if is_while and not truth:
                    break
                if not is_while and truth:
                    break
                out.append(self._parse_program(Scanner(body), stop_at_mend=False))
                iterations += 1
                if not is_while and iterations >= 100000:
                    break
            return "".join(out)

        # iterative: var = start %to stop [%by inc]
        header_raw, _ = self._find_top_level(sc, [";"])
        if "=" not in header_raw:
            raise MacroError(f"malformed %do: {header_raw!r}")
        varname, rest = header_raw.split("=", 1)
        varname = varname.strip()
        rest_sc = Scanner(rest)
        start_raw, kw = self._find_top_level(rest_sc, ["%to"])
        start_val = self._parse_program(Scanner(start_raw), stop_at_mend=False).strip()
        stop_raw, kw2 = self._find_top_level(rest_sc, ["%by"])
        by_val = "1"
        if kw2 == "%by":
            by_raw = rest_sc.text[rest_sc.pos:]
            by_val = self._parse_program(Scanner(by_raw), stop_at_mend=False).strip()
        stop_val = self._parse_program(Scanner(stop_raw), stop_at_mend=False).strip()

        body = self._find_block_end(sc, "do", "end")
        j = sc.text.find(";", sc.pos)
        if j != -1:
            sc.pos = j + 1

        start_n = self._to_num(start_val)
        stop_n = self._to_num(stop_val)
        by_n = self._to_num(by_val)
        out = []
        i = start_n
        guard = 0
        while (by_n > 0 and i <= stop_n) or (by_n < 0 and i >= stop_n):
            self.set_auto(varname, self._fmt_num(i))
            out.append(self._parse_program(Scanner(body), stop_at_mend=False))
            i += by_n
            guard += 1
            if guard > 1000000:
                raise MacroError("macro %do loop exceeded iteration limit")
        return "".join(out)

    @staticmethod
    def _to_num(s: str):
        s = s.strip()
        try:
            if re.match(r"^-?\d+$", s):
                return int(s)
            return float(s)
        except ValueError:
            return 0

    @staticmethod
    def _fmt_num(n) -> str:
        if isinstance(n, float) and n.is_integer():
            return str(int(n))
        return str(n)

    # ---------------- %put ----------------
    def _do_put(self, sc: Scanner):
        raw, _ = self._find_top_level(sc, [";"])
        resolved = self._parse_program(Scanner(raw), stop_at_mend=False)
        print(resolved)
        return ""

    # ---------------- macro text functions ----------------
    def _read_call_args(self, sc: Scanner) -> str:
        self._skip_ws(sc)
        if sc.peek() != "(":
            return ""
        sc.pos += 1
        depth = 1
        buf = []
        while depth > 0:
            c = sc.text[sc.pos]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    sc.pos += 1
                    break
            buf.append(c)
            sc.pos += 1
        return "".join(buf)

    def _do_str(self, sc: Scanner, resolve_inner: bool) -> str:
        inner = self._read_call_args(sc)
        if resolve_inner:
            return self._parse_program(Scanner(inner), stop_at_mend=False)
        return inner

    def _simple_text_func(self, sc: Scanner, fn) -> str:
        inner = self._read_call_args(sc)
        resolved = self._parse_program(Scanner(inner), stop_at_mend=False)
        return fn(resolved)

    def _do_length(self, sc: Scanner) -> str:
        inner = self._read_call_args(sc)
        resolved = self._parse_program(Scanner(inner), stop_at_mend=False)
        return str(len(resolved))

    def _do_macro_substr(self, sc: Scanner) -> str:
        inner = self._read_call_args(sc)
        parts = self._split_top_commas(inner)
        parts = [self._parse_program(Scanner(p), stop_at_mend=False).strip() for p in parts]
        s = parts[0]
        start = int(self._to_num(parts[1]))
        if len(parts) > 2:
            length = int(self._to_num(parts[2]))
            return s[start - 1:start - 1 + length]
        return s[start - 1:]

    def _do_macro_scan(self, sc: Scanner) -> str:
        inner = self._read_call_args(sc)
        parts = self._split_top_commas(inner)
        parts = [self._parse_program(Scanner(p), stop_at_mend=False).strip() for p in parts]
        s = parts[0]
        n = int(self._to_num(parts[1]))
        delims = parts[2] if len(parts) > 2 else " ,;."
        words = re.split("[" + re.escape(delims) + "]+", s)
        words = [w for w in words if w != ""]
        if 1 <= n <= len(words):
            return words[n - 1]
        return ""

    def _do_macro_index(self, sc: Scanner) -> str:
        inner = self._read_call_args(sc)
        parts = self._split_top_commas(inner)
        parts = [self._parse_program(Scanner(p), stop_at_mend=False).strip() for p in parts]
        pos = parts[0].find(parts[1])
        return str(pos + 1)

    def _do_eval(self, sc: Scanner):
        inner = self._read_call_args(sc)
        resolved = self._parse_program(Scanner(inner), stop_at_mend=False)
        val = self._eval_arith(resolved)
        if isinstance(val, float) and val.is_integer():
            return int(val)
        return val

    def _do_sysevalf(self, sc: Scanner):
        inner = self._read_call_args(sc)
        parts = self._split_top_commas(inner)
        expr = self._parse_program(Scanner(parts[0]), stop_at_mend=False)
        val = self._eval_arith(expr)
        mode = parts[1].strip().lower() if len(parts) > 1 else None
        if mode == "ceil":
            import math
            return int(math.ceil(val))
        if mode == "floor":
            import math
            return int(math.floor(val))
        if mode == "integer" or mode == "int":
            return int(val)
        if mode == "boolean":
            return 1 if val else 0
        return self._fmt_num(val)

    def _do_sysfunc(self, sc: Scanner) -> str:
        inner = self._read_call_args(sc)
        resolved = self._parse_program(Scanner(inner), stop_at_mend=False).strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*(?:,\s*(.*))?$", resolved, re.S)
        if not m:
            return resolved
        fname = m.group(1).lower()
        fargs = self._split_top_commas(m.group(2)) if m.group(2).strip() else []
        fargs = [a.strip() for a in fargs]
        if fname == "today" or fname == "date":
            days = (datetime.date.today() - datetime.date(1960, 1, 1)).days
            return str(days)
        if fname == "trim":
            return fargs[0].strip("'\" ") if fargs else ""
        if fname == "upcase":
            return fargs[0].strip("'\"").upper() if fargs else ""
        if fname == "lowcase":
            return fargs[0].strip("'\"").lower() if fargs else ""
        if fname == "compress":
            return re.sub(r"\s+", "", fargs[0].strip("'\"")) if fargs else ""
        # unrecognized: best-effort passthrough of the raw call text
        return resolved

    # ---------------- expression evaluation for %if / %eval ----------------
    def _eval_condition(self, text: str) -> bool:
        val = self._MacroExprEval(text).parse_or()
        if isinstance(val, str):
            return val.strip() not in ("", "0")
        return bool(val)

    def _eval_arith(self, text: str):
        return self._MacroExprEval(text).parse_or()

    class _MacroExprEval:
        def __init__(self, text: str):
            self.text = text
            self.pos = 0
            self.n = len(text)

        def _ws(self):
            while self.pos < self.n and self.text[self.pos] in " \t\r\n":
                self.pos += 1

        def _peek_word(self, word):
            self._ws()
            seg = self.text[self.pos:self.pos + len(word)]
            end = self.pos + len(word)
            nextc = self.text[end:end + 1]
            if seg.lower() == word.lower() and not (nextc.isalnum() or nextc == "_"):
                return True
            return False

        def parse_or(self):
            v = self.parse_and()
            while True:
                self._ws()
                if self._peek_word("or") or self.text[self.pos:self.pos + 2] == "||":
                    self.pos += 2 if self.text[self.pos:self.pos + 2] == "||" else 2
                    if self.text[self.pos - 2:self.pos].lower() == "or":
                        pass
                    right = self.parse_and()
                    v = (self._truthy(v) or self._truthy(right))
                else:
                    break
            return v

        def parse_and(self):
            v = self.parse_not()
            while True:
                self._ws()
                if self._peek_word("and") or self.text[self.pos:self.pos + 2] == "&&":
                    self.pos += 3 if self._peek_word("and") else 2
                    right = self.parse_not()
                    v = (self._truthy(v) and self._truthy(right))
                else:
                    break
            return v

        def parse_not(self):
            self._ws()
            if self._peek_word("not") or self.text[self.pos:self.pos + 1] == "^":
                self.pos += 3 if self._peek_word("not") else 1
                return not self._truthy(self.parse_not())
            return self.parse_cmp()

        def parse_cmp(self):
            v = self.parse_add()
            self._ws()
            ops = [("=", "eq"), ("^=", "ne"), ("ne", None), ("<=", "le"), (">=", "ge"),
                   ("<", "lt"), (">", "gt"), ("eq", None), ("le", None), ("ge", None),
                   ("lt", None), ("gt", None)]
            for sym, word in [(">=", None), ("<=", None), ("^=", None), ("=", None),
                               ("<", None), (">", None)]:
                if self.text[self.pos:self.pos + len(sym)] == sym:
                    self.pos += len(sym)
                    right = self.parse_add()
                    return self._compare(v, right, {">=": "ge", "<=": "le", "^=": "ne",
                                                     "=": "eq", "<": "lt", ">": "gt"}[sym])
            for word, canon in [("eq", "eq"), ("ne", "ne"), ("le", "le"), ("ge", "ge"),
                                 ("lt", "lt"), ("gt", "gt")]:
                if self._peek_word(word):
                    self.pos += len(word)
                    right = self.parse_add()
                    return self._compare(v, right, canon)
            return v

        def _compare(self, a, b, op):
            an = self._maybe_num(a)
            bn = self._maybe_num(b)
            if an is not None and bn is not None:
                a, b = an, bn
            else:
                a, b = str(a).strip(), str(b).strip()
            if op == "eq":
                return a == b
            if op == "ne":
                return a != b
            if op == "lt":
                return a < b
            if op == "gt":
                return a > b
            if op == "le":
                return a <= b
            if op == "ge":
                return a >= b

        @staticmethod
        def _maybe_num(v):
            if isinstance(v, (int, float)):
                return v
            s = str(v).strip()
            try:
                if re.match(r"^-?\d+$", s):
                    return int(s)
                return float(s)
            except ValueError:
                return None

        @staticmethod
        def _truthy(v):
            if isinstance(v, str):
                s = v.strip()
                if s == "":
                    return False
                try:
                    return float(s) != 0
                except ValueError:
                    return True
            return bool(v)

        def parse_add(self):
            v = self.parse_mul()
            while True:
                self._ws()
                if self.pos < self.n and self.text[self.pos] in "+-":
                    op = self.text[self.pos]
                    self.pos += 1
                    right = self.parse_mul()
                    v = (self._num(v) + self._num(right)) if op == "+" else (self._num(v) - self._num(right))
                else:
                    break
            return v

        def parse_mul(self):
            v = self.parse_unary()
            while True:
                self._ws()
                if self.pos < self.n and self.text[self.pos] in "*/":
                    op = self.text[self.pos]
                    self.pos += 1
                    right = self.parse_unary()
                    v = (self._num(v) * self._num(right)) if op == "*" else (self._num(v) / self._num(right))
                else:
                    break
            return v

        def parse_unary(self):
            self._ws()
            if self.pos < self.n and self.text[self.pos] == "-":
                self.pos += 1
                return -self._num(self.parse_unary())
            if self.pos < self.n and self.text[self.pos] == "+":
                self.pos += 1
                return self.parse_unary()
            return self.parse_atom()

        def parse_atom(self):
            self._ws()
            if self.pos < self.n and self.text[self.pos] == "(":
                self.pos += 1
                v = self.parse_or()
                self._ws()
                if self.pos < self.n and self.text[self.pos] == ")":
                    self.pos += 1
                return v
            if self.pos < self.n and self.text[self.pos] in "'\"":
                q = self.text[self.pos]
                self.pos += 1
                start = self.pos
                while self.pos < self.n and self.text[self.pos] != q:
                    self.pos += 1
                s = self.text[start:self.pos]
                self.pos += 1
                return s
            m = re.match(r"-?\d+(\.\d+)?", self.text[self.pos:])
            if m and m.group(0):
                self.pos += m.end()
                s = m.group(0)
                return float(s) if "." in s else int(s)
            m = _WORD_RE.match(self.text, self.pos)
            if m:
                self.pos = m.end()
                return m.group(0)
            # bare token until whitespace/operator
            start = self.pos
            while self.pos < self.n and self.text[self.pos] not in " \t\r\n()=<>^":
                self.pos += 1
            return self.text[start:self.pos]

        @staticmethod
        def _num(v):
            if isinstance(v, (int, float)):
                return v
            s = str(v).strip()
            try:
                return int(s) if re.match(r"^-?\d+$", s) else float(s)
            except ValueError:
                return 0
