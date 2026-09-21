"""Recursive-descent parser: tokens -> AST (Program of DataStep/ProcStep)."""
from __future__ import annotations

import re
from datetime import date as _date

from .lexer import Lexer, Token, TokType
from . import ast_nodes as A


class ParseError(Exception):
    pass


_CMP_WORDS = {"eq": "=", "ne": "^=", "lt": "<", "gt": ">", "le": "<=", "ge": ">="}

# SAS epoch: date literals ('01JAN2010'd) count days since 1960-01-01.
_SAS_EPOCH_ORD = _date(1960, 1, 1).toordinal()
_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_sas_date_literal(text: str) -> float | None:
    """Parse a DATE9-style literal body ('01JAN2010', '1Jan60') into a SAS
    date number (days since 1960-01-01). Returns None if it doesn't match."""
    m = re.match(r"^(\d{1,2})([A-Za-z]{3})(\d{2,4})$", text.strip())
    if not m:
        return None
    day, mon, year = int(m.group(1)), _MONTH_ABBR.get(m.group(2).lower()), m.group(3)
    if mon is None:
        return None
    year = int(year)
    if year < 100:
        # SAS YEARCUTOFF sliding window equivalent (default cutoff 1926).
        year += 2000 if year < 26 else 1900
    try:
        return float(_date(year, mon, day).toordinal() - _SAS_EPOCH_ORD)
    except ValueError:
        return None


def parse_sas_time_literal(text: str) -> float | None:
    """Parse a TIME literal body ('12:34', '12:34:56') into SAS time
    (seconds since midnight). Returns None if it doesn't match."""
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$", text.strip())
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h > 23 or mi > 59 or s > 59:
        return None
    return float(h * 3600 + mi * 60 + s)


def parse_sas_datetime_literal(text: str) -> float | None:
    """Parse a DATETIME literal body ('01JAN2010:12:34:56') into SAS
    datetime (seconds since 1960-01-01 00:00). Returns None if no match."""
    m = re.match(
        r"^(\d{1,2})([A-Za-z]{3})(\d{2,4}):(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$",
        text.strip(),
    )
    if not m:
        return None
    day, mon = int(m.group(1)), _MONTH_ABBR.get(m.group(2).lower())
    if mon is None:
        return None
    year = int(m.group(3))
    if year < 100:
        year += 2000 if year < 26 else 1900
    h, mi, s = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
    try:
        days = _date(year, mon, day).toordinal() - _SAS_EPOCH_ORD
    except ValueError:
        return None
    if h > 23 or mi > 59 or s > 59:
        return None
    return float(days * 86400 + h * 3600 + mi * 60 + s)


def normalize_dsname(name: str) -> str:
    parts = name.split(".")
    if len(parts) == 2 and parts[0].lower() in ("work",):
        return parts[1].lower()
    return "_".join(p.lower() for p in parts)


class Parser:
    def __init__(self, source: str):
        # The lexer strips a leading BOM for tokenizing; source slices
        # (error snippets, DATALINES bodies, PROC SQL raw text, date-literal
        # adjacency checks) must use the same stripped text so token
        # positions line up.
        _lexer = Lexer(source)
        self.source = _lexer.text
        self.toks = _lexer.tokenize()
        self.i = 0
        self.db_librefs: set = set()  # librefs registered via LIBNAME, tracked
        # in parse order so later SET/MERGE/DATA-step-output references to
        # libref.table can be flagged for real-database read/write-through.

    def _line_col(self, pos: int) -> tuple[int, int]:
        line = self.source.count("\n", 0, pos) + 1
        col_start = self.source.rfind("\n", 0, pos) + 1
        return line, pos - col_start + 1

    def _err(self, msg: str, pos: int | None = None) -> "ParseError":
        if pos is None:
            pos = self.peek().pos
        line, col = self._line_col(pos)
        snippet = self.source.splitlines()[line - 1] if line - 1 < len(self.source.splitlines()) else ""
        return ParseError(f"line {line}, col {col}: {msg}\n    {snippet.strip()}")

    # ------------- token cursor helpers -------------
    def peek(self, off: int = 0) -> Token:
        j = self.i + off
        if j >= len(self.toks):
            return self.toks[-1]
        return self.toks[j]

    def advance(self) -> Token:
        t = self.toks[self.i]
        if self.i < len(self.toks) - 1:
            self.i += 1
        return t

    def at_eof(self) -> bool:
        return self.peek().type == TokType.EOF

    def is_kw(self, word: str, off: int = 0) -> bool:
        t = self.peek(off)
        return t.type == TokType.IDENT and t.value.lower() == word.lower()

    def is_kw_any(self, words, off: int = 0) -> bool:
        t = self.peek(off)
        return t.type == TokType.IDENT and t.value.lower() in words

    def expect(self, ttype: TokType) -> Token:
        t = self.peek()
        if t.type != ttype:
            raise self._err(f"expected {ttype.name} but found {t.value!r}", t.pos)
        return self.advance()

    def eat_kw(self, word: str) -> bool:
        if self.is_kw(word):
            self.advance()
            return True
        return False

    def skip_semis(self):
        while self.peek().type == TokType.SEMI:
            self.advance()

    def skip_to_semi(self):
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            self.advance()
        if self.peek().type == TokType.SEMI:
            self.advance()

    # ------------- top level -------------
    def parse_program(self) -> A.Program:
        steps = []
        self.skip_semis()
        while not self.at_eof():
            if self.is_kw("data"):
                steps.append(self.parse_data_step())
            elif self.is_kw("proc"):
                steps.append(self.parse_proc_step())
            elif self.is_kw("libname"):
                steps.append(self.parse_libname())
            elif self.is_kw("ods"):
                stmt = self.parse_ods()
                if stmt is not None:
                    steps.append(stmt)
            elif self.peek().type == TokType.IDENT and re.match(
                r"^(title|footnote)\d*$", self.peek().value.lower()
            ):
                steps.append(self.parse_title())
            elif self.peek().type == TokType.SEMI:
                self.advance()
            else:
                # stray token (e.g. leftover libname/options statement) -
                # skip the whole statement
                self.skip_to_semi()
            self.skip_semis()
        return A.Program(steps=steps)

    def parse_title(self) -> A.TitleStmt:
        kind = self.advance().value.lower()
        kind = "footnote" if kind.startswith("footnote") else "title"
        text = ""
        if self.peek().type == TokType.STRING:
            text = self.advance().value
        else:
            parts = []
            while self.peek().type not in (TokType.SEMI, TokType.EOF):
                parts.append(self.advance().value)
            text = " ".join(parts)
        self.skip_to_semi()
        return A.TitleStmt(text=text, kind=kind)

    def parse_ods(self):
        self.advance()  # 'ods'
        dest = None
        if self.peek().type == TokType.IDENT:
            dest = self.advance().value.lower()
        is_close = False
        path = None
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.is_kw("close"):
                is_close = True
                self.advance()
            elif self.is_kw("file") or self.is_kw("body"):
                self.advance()
                if self.peek().type == TokType.OP and self.peek().value == "=":
                    self.advance()
                if self.peek().type == TokType.STRING:
                    path = self.advance().value
            elif self.peek().type == TokType.IDENT and self.peek(1).type == TokType.OP and self.peek(1).value == "=":
                # unrecognized key=value option (e.g. STYLE=...): skip it
                self.advance()
                self.advance()
                if self.peek().type not in (TokType.SEMI, TokType.EOF):
                    self.advance()
            else:
                self.advance()
        self.skip_to_semi()

        if dest not in ("html", "rtf"):
            # only HTML and RTF are implemented; every other ODS
            # destination/form (LISTING, PDF, _ALL_, SELECT/EXCLUDE, ...)
            # is safely ignored rather than raising a parse error.
            return None
        if is_close:
            return A.OdsStmt(action="close", destination=dest)
        if path is not None:
            return A.OdsStmt(action="open", destination=dest, path=path)
        return None  # bare "ods html;"/"ods rtf;" with no FILE=: no-op

    def parse_libname(self) -> A.LibnameStmt:
        self.advance()  # 'libname'
        libref = self.advance().value.lower()
        conn = None
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.peek().type == TokType.STRING:
                conn = self.advance().value
            else:
                self.advance()
        self.skip_to_semi()
        if conn is not None:
            self.db_librefs.add(libref)
        else:
            self.db_librefs.discard(libref)
        return A.LibnameStmt(libref=libref, conn=conn)

    def _resolve_ds_ref(self, dotted_name: str):
        """Split a dotted dataset reference into (flat_name, db_info),
        where db_info is (libref, table) if the libref was registered via
        a preceding LIBNAME with a real connection, else None."""
        parts = dotted_name.split(".")
        flat = normalize_dsname(dotted_name)
        if len(parts) == 2 and parts[0].lower() in self.db_librefs:
            return flat, (parts[0].lower(), parts[1].lower())
        return flat, None

    # ------------- DATA step -------------
    def parse_data_step(self):
        self.advance()  # 'data'
        outputs = []
        is_null = False
        while True:
            t = self.peek()
            if t.type == TokType.IDENT:
                name_parts = [self.advance().value]
                while self.peek().type == TokType.OP and self.peek().value == "." and self.peek(1).type == TokType.IDENT:
                    self.advance()
                    name_parts.append(self.advance().value)
                dsname, db_info = self._resolve_ds_ref(".".join(name_parts))
                if dsname == "_null_":
                    is_null = True
                options = {}
                if self.peek().type == TokType.LPAREN:
                    options = self._parse_dataset_options()
                outputs.append((dsname, options, db_info))
                if self.peek().type == TokType.COMMA:
                    self.advance()
                    continue
                continue
            break
        self.skip_to_semi()
        statements = self._parse_stmt_list(stop_kws={"run"})
        if self.is_kw("run"):
            self.advance()
            self.skip_to_semi()
        return A.DataStep(outputs=outputs, statements=statements, is_null=is_null)

    def _parse_dataset_options(self) -> dict:
        self.expect(TokType.LPAREN)
        opts = {"drop": [], "keep": [], "rename": {}, "where": None, "in_flag": None}
        while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
            if self.peek().type == TokType.IDENT and self.peek(1).type == TokType.OP and self.peek(1).value == "=":
                key = self.advance().value.lower()
                self.advance()  # '='
                if key in ("drop", "keep"):
                    names = []
                    while self.peek().type == TokType.IDENT:
                        names.append(self.advance().value.lower())
                    opts[key].extend(names)
                elif key == "rename":
                    if self.peek().type == TokType.LPAREN:
                        self.advance()
                        while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
                            old = self.advance().value.lower()
                            if self.peek().type == TokType.OP and self.peek().value == "=":
                                self.advance()
                            new = self.advance().value.lower()
                            opts["rename"][old] = new
                        if self.peek().type == TokType.RPAREN:
                            self.advance()
                elif key == "where":
                    if self.peek().type == TokType.LPAREN:
                        self.advance()
                        cond = self.parse_expr()
                        if self.peek().type == TokType.RPAREN:
                            self.advance()
                        opts["where"] = cond
                    else:
                        opts["where"] = self.parse_expr()
                elif key == "in":
                    opts["in_flag"] = self.advance().value.lower()
                elif key in ("point", "nobs", "firstobs", "obs"):
                    if self.peek().type not in (TokType.RPAREN,):
                        opts[key] = self.advance().value
                else:
                    # unknown option: consume one value token and ignore
                    if self.peek().type not in (TokType.RPAREN,):
                        self.advance()
            else:
                self.advance()
        if self.peek().type == TokType.RPAREN:
            self.advance()
        return opts

    def _parse_stmt_list(self, stop_kws) -> list:
        stmts = []
        while not self.at_eof():
            self.skip_semis()
            if self.at_eof():
                break
            if self.is_kw_any(stop_kws):
                break
            if self.is_kw("data") or self.is_kw("proc"):
                break
            stmt = self._parse_statement()
            if stmt is not None:
                if isinstance(stmt, list):
                    stmts.extend(stmt)
                else:
                    stmts.append(stmt)
        return stmts

    def _parse_statement(self):
        t = self.peek()
        if t.type != TokType.IDENT:
            self.skip_to_semi()
            return None
        kw = t.value.lower()

        # A bare HASH-object method call used as its own statement, e.g.
        # `h.definekey("id");` or `h.add();` (as opposed to `rc = h.find();`,
        # which is an ordinary assignment whose RHS is parsed as an
        # expression -- see parse_atom's matching check).
        if (self.peek(1).type == TokType.OP and self.peek(1).value == "."
                and self.peek(2).type == TokType.IDENT and self.peek(3).type == TokType.LPAREN):
            hashname = self.advance().value.lower()
            call = self._parse_hash_call_tail(hashname)
            self.skip_to_semi()
            return A.ExprStmt(expr=call)

        dispatch = {
            "set": self._parse_set,
            "merge": self._parse_merge,
            "update": self._parse_update,
            "by": self._parse_by,
            "array": self._parse_array,
            "retain": self._parse_retain,
            "drop": self._parse_drop,
            "keep": self._parse_keep,
            "length": self._parse_length,
            "format": self._parse_format,
            "label": self._parse_label,
            "output": self._parse_output,
            "if": self._parse_if,
            "do": self._parse_do,
            "select": self._parse_select,
            "stop": self._parse_stop,
            "leave": self._parse_leave,
            "continue": self._parse_continue,
            "where": self._parse_where,
            "put": self._parse_put,
            "call": self._parse_call,
            "input": self._parse_input,
            "infile": self._parse_infile,
            "file": self._parse_file,
            "abort": self._parse_abort,
            "datalines": self._parse_datalines,
            "cards": self._parse_datalines,
            "delete": self._parse_delete,
            "return": self._parse_return,
            "declare": self._parse_declare_hash,
        }
        if kw in dispatch:
            return dispatch[kw]()

        return self._parse_assignment_or_sum()

    def _parse_named_arg_list(self) -> list:
        """Parse a parenthesized argument list where each argument is
        either `name: expr` or a bare `expr` -- used for HASH object
        constructor/method calls, e.g. `(dataset: "lookup")` or
        `(key: id, key: region)`. Returns [(argname_or_None, Expr), ...]."""
        self.advance()  # '('
        args = []
        while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
            argname = None
            if (self.peek().type == TokType.IDENT and self.peek(1).type == TokType.OP
                    and self.peek(1).value == ":"):
                argname = self.advance().value.lower()
                self.advance()  # ':'
            expr = self.parse_expr()
            args.append((argname, expr))
            if self.peek().type == TokType.COMMA:
                self.advance()
        if self.peek().type == TokType.RPAREN:
            self.advance()
        return args

    def _parse_hash_call_tail(self, hashname: str) -> A.HashMethodCall:
        """Assumes `hashname` (the leading IDENT) is already consumed and
        the cursor sits at the following '.': parses `.method(args)`."""
        self.advance()  # '.'
        method = self.advance().value.lower()
        args = []
        if self.peek().type == TokType.LPAREN:
            args = self._parse_named_arg_list()
        return A.HashMethodCall(hashname=hashname, method=method, args=args)

    def _parse_declare_hash(self):
        self.advance()  # 'declare'
        if self.is_kw("hash"):
            self.advance()
            hashname = self.advance().value.lower()
            args = []
            if self.peek().type == TokType.LPAREN:
                args = self._parse_named_arg_list()
            self.skip_to_semi()
            return A.DeclareHashStmt(hashname=hashname, args=args)
        if self.is_kw("hiter"):
            self.advance()
            itername = self.advance().value.lower() if self.peek().type == TokType.IDENT else ""
            hashname = ""
            if self.peek().type == TokType.LPAREN:
                self.advance()
                if self.peek().type == TokType.STRING:
                    hashname = self.advance().value.lower()
                elif self.peek().type == TokType.IDENT:
                    hashname = self.advance().value.lower()
                while self.peek().type not in (TokType.RPAREN, TokType.EOF):
                    self.advance()
                if self.peek().type == TokType.RPAREN:
                    self.advance()
            self.skip_to_semi()
            return A.DeclareHiterStmt(itername=itername, hashname=hashname)
        hashname = self.advance().value.lower()
        args = []
        if self.peek().type == TokType.LPAREN:
            args = self._parse_named_arg_list()
        self.skip_to_semi()
        return A.DeclareHashStmt(hashname=hashname, args=args)

    # ---- individual statement parsers ----
    # SET-statement-level options (valid with or without parens, e.g.
    # `set s point=p nobs=n end=eof`); other key= tokens end the list.
    _SET_STMT_OPTS = ("point", "nobs", "end", "firstobs", "obs", "key")

    def _parse_set(self):
        self.advance()
        datasets = []
        while self.peek().type == TokType.IDENT and not (
            self.peek(1).type == TokType.OP and self.peek(1).value == "="
        ):
            name = self._read_dotted_name()
            opts = {}
            if self.peek().type == TokType.LPAREN:
                opts = self._parse_dataset_options()
            flat, db_info = self._resolve_ds_ref(name)
            datasets.append((flat, opts, db_info))
        # bare SET-statement options attach to the last dataset read
        while (
            self.peek().type == TokType.IDENT
            and self.peek(1).type == TokType.OP
            and self.peek(1).value == "="
            and self.peek().value.lower() in self._SET_STMT_OPTS
            and datasets
        ):
            key = self.advance().value.lower()
            self.advance()  # '='
            datasets[-1][1][key] = self.advance().value
        self.skip_to_semi()
        return A.SetStmt(datasets=datasets)

    def _parse_merge(self):
        self.advance()
        datasets = []
        while self.peek().type == TokType.IDENT:
            name = self._read_dotted_name()
            opts = {}
            if self.peek().type == TokType.LPAREN:
                opts = self._parse_dataset_options()
            flat, db_info = self._resolve_ds_ref(name)
            datasets.append((flat, opts, db_info))
        self.skip_to_semi()
        return A.MergeStmt(datasets=datasets, by=[])

    def _parse_update(self):
        self.advance()
        datasets = []
        while self.peek().type == TokType.IDENT:
            name = self._read_dotted_name()
            opts = {}
            if self.peek().type == TokType.LPAREN:
                opts = self._parse_dataset_options()
            flat, db_info = self._resolve_ds_ref(name)
            datasets.append((flat, opts, db_info))
        self.skip_to_semi()
        return A.UpdateStmt(datasets=datasets, by=[])

    def _parse_stop(self):
        self.advance()
        self.skip_to_semi()
        return A.StopStmt()

    def _parse_leave(self):
        self.advance()
        self.skip_to_semi()
        return A.LeaveStmt()

    def _parse_continue(self):
        self.advance()
        self.skip_to_semi()
        return A.ContinueStmt()

    def _read_dotted_name(self) -> str:
        parts = [self.advance().value]
        while self.peek().type == TokType.OP and self.peek().value == "." and self.peek(1).type == TokType.IDENT:
            self.advance()
            parts.append(self.advance().value)
        return ".".join(parts)

    def _parse_by(self):
        self.advance()
        varnames = []
        while self.peek().type == TokType.IDENT:
            desc = False
            if self.is_kw("descending"):
                self.advance()
                desc = True
            name = self.advance().value.lower()
            varnames.append((name, desc))
        self.skip_to_semi()
        return A.ByStmt(vars=varnames)

    def _parse_array(self):
        self.advance()
        name = self.advance().value.lower()
        dim = None
        lo_bound = 1
        if self.peek().type == TokType.OP and self.peek().value in ("{", "["):
            self.advance()

            def _read_signed_int():
                sign = -1 if self.peek().type == TokType.OP and self.peek().value == "-" else 1
                if sign == -1:
                    self.advance()
                if self.peek().type == TokType.NUMBER:
                    return sign * int(float(self.advance().value))
                return None

            if self.peek().type == TokType.OP and self.peek().value == "*":
                self.advance()
                dim = None
            else:
                n1 = _read_signed_int()
                if n1 is not None:
                    if self.peek().type == TokType.OP and self.peek().value == ":":
                        self.advance()
                        n2 = _read_signed_int()
                        if n2 is not None:
                            lo_bound = n1
                            dim = n2 - n1 + 1
                    else:
                        dim = n1
            while not (self.peek().type == TokType.OP and self.peek().value in ("}", "]")) and self.peek().type != TokType.EOF:
                self.advance()
            if self.peek().type == TokType.OP and self.peek().value in ("}", "]"):
                self.advance()
        is_char = False
        length = None
        if self.peek().type == TokType.OP and self.peek().value == "$":
            is_char = True
            self.advance()
            if self.peek().type == TokType.NUMBER:
                length = int(float(self.advance().value))

        is_temporary = False
        if self.is_kw("_temporary_"):
            self.advance()
            is_temporary = True

        elements = []
        if is_temporary:
            pass
        elif self.peek().type == TokType.IDENT:
            while self.peek().type == TokType.IDENT:
                elt = self.advance().value.lower()
                if self.peek().type == TokType.OP and self.peek().value == "-" and self.peek(1).type == TokType.IDENT:
                    nxt = self.peek(1).value.lower()
                    expanded = self._expand_dash_range(elt, nxt)
                    if expanded is not None:
                        self.advance()
                        self.advance()
                        elements.extend(expanded)
                        continue
                    # Unrecognized dash: consume '-' so init list still parses;
                    # nxt is picked up next loop iteration.
                    self.advance()
                    elements.append(elt)
                    continue
                elements.append(elt)

        init_values = []
        if self.peek().type == TokType.LPAREN:
            init_values = self._parse_array_init_values()

        if dim is None:
            dim = len(elements) if elements else len(init_values)
        if not elements:
            prefix = f"__tmp_{name}_" if is_temporary else name
            elements = [f"{prefix}{i}" for i in range(1, dim + 1)]

        self.skip_to_semi()
        return A.ArrayStmt(name=name, dim=dim, elements=elements, is_char=is_char,
                            length=length, init_values=init_values, lo_bound=lo_bound,
                            is_temporary=is_temporary)

    @staticmethod
    def _expand_dash_range(elt: str, nxt: str):
        """Expand a SAS name-range like 'h1-h3' -> ['h1','h2','h3'], or
        'a-c' / 'xa-xc' -> ['a','b','c'] / ['xa','xb','xc']. Returns None
        if elt/nxt don't form a recognized range pattern."""
        m1 = re.match(r"^([a-zA-Z_]+)(\d+)$", elt)
        m2 = re.match(r"^([a-zA-Z_]+)(\d+)$", nxt)
        if m1 and m2 and m1.group(1) == m2.group(1):
            prefix = m1.group(1)
            lo, hi = int(m1.group(2)), int(m2.group(2))
            return [f"{prefix}{k}" for k in range(lo, hi + 1)]
        if len(elt) == 1 and len(nxt) == 1 and elt.isalpha() and nxt.isalpha():
            lo_c, hi_c = ord(elt), ord(nxt)
            step = 1 if hi_c >= lo_c else -1
            return [chr(c) for c in range(lo_c, hi_c + step, step)]
        if (len(elt) > 1 and len(nxt) > 1 and elt[:-1] == nxt[:-1]
                and elt[-1].isalpha() and nxt[-1].isalpha()):
            prefix = elt[:-1]
            lo_c, hi_c = ord(elt[-1]), ord(nxt[-1])
            step = 1 if hi_c >= lo_c else -1
            return [f"{prefix}{chr(c)}" for c in range(lo_c, hi_c + step, step)]
        return None

    def _parse_array_init_values(self) -> list:
        self.advance()  # '('
        values = []
        while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
            neg = False
            if self.peek().type == TokType.OP and self.peek().value == "-":
                neg = True
                self.advance()
            if self.peek().type == TokType.NUMBER:
                v = float(self.advance().value)
                values.append(-v if neg else v)
            elif self.peek().type == TokType.STRING:
                values.append(self.advance().value)
            else:
                self.advance()
            if self.peek().type == TokType.COMMA:
                self.advance()
        if self.peek().type == TokType.RPAREN:
            self.advance()
        return values

    def _parse_retain(self):
        self.advance()
        entries = []
        while self.peek().type == TokType.IDENT or self.peek().type == TokType.NUMBER:
            if self.peek().type == TokType.IDENT:
                name = self.advance().value.lower()
                if self.peek().type == TokType.OP and self.peek().value == "-" and self.peek(1).type == TokType.IDENT:
                    nxt = self.peek(1).value.lower()
                    expanded = self._expand_dash_range(name, nxt)
                    if expanded is not None:
                        self.advance()
                        self.advance()
                        val = None
                        if self.peek().type == TokType.NUMBER:
                            val = float(self.advance().value)
                        elif self.peek().type == TokType.STRING:
                            val = self.advance().value
                        entries.extend((n, val) for n in expanded)
                        continue
                    # Unrecognized dash pattern: consume it so the loop
                    # doesn't stall on a token neither IDENT nor NUMBER.
                    self.advance()
                val = None
                if self.peek().type == TokType.NUMBER:
                    val = float(self.advance().value)
                elif self.peek().type == TokType.STRING:
                    val = self.advance().value
                entries.append((name, val))
            else:
                self.advance()
        self.skip_to_semi()
        return A.RetainStmt(entries=entries)

    def _parse_drop(self):
        self.advance()
        return A.DropStmt(vars=self._namelist_until_semi())

    def _parse_keep(self):
        self.advance()
        return A.KeepStmt(vars=self._namelist_until_semi())

    def _namelist_until_semi(self) -> list:
        names = []
        while self.peek().type == TokType.IDENT:
            names.append(self.advance().value.lower())
        self.skip_to_semi()
        return names

    def _parse_length(self):
        self.advance()
        entries = []
        while self.peek().type == TokType.IDENT:
            name = self.advance().value.lower()
            is_char = False
            length = 8
            if self.peek().type == TokType.OP and self.peek().value == "$":
                is_char = True
                self.advance()
            if self.peek().type == TokType.NUMBER:
                length = int(float(self.advance().value))
            entries.append((name, is_char, length))
        self.skip_to_semi()
        return A.LengthStmt(entries=entries)

    def _consume_format_suffix(self, fmt: str) -> str:
        """Consume the '.' / '.N' that follows a format name. The lexer fuses
        '.N' (dot immediately followed by a digit) into one NUMBER token, so
        both 'name.' (OP '.') and 'name.2' (NUMBER '.2') must be handled."""
        if self.peek().type == TokType.NUMBER and self.peek().value.startswith("."):
            fmt += self.advance().value
        elif self.peek().type == TokType.OP and self.peek().value == ".":
            self.advance()
            if self.peek().type == TokType.NUMBER:
                fmt += "." + self.advance().value
        return fmt

    def _looks_like_format_entry(self) -> bool:
        if self.peek().type != TokType.IDENT:
            return False
        off = 1
        if self.peek(off).type == TokType.OP and self.peek(off).value == "$":
            off += 1
        return self.peek(off).type == TokType.IDENT

    def _parse_format(self):
        self.advance()
        entries = []
        while self._looks_like_format_entry():
            name = self.advance().value.lower()
            is_char = False
            if self.peek().type == TokType.OP and self.peek().value == "$":
                is_char = True
                self.advance()
            fmt = ("$" if is_char else "") + self.advance().value
            fmt = self._consume_format_suffix(fmt)
            entries.append((name, fmt))
        self.skip_to_semi()
        return A.FormatStmt(entries=entries)

    def _parse_label(self):
        self.advance()
        entries = []
        while self.peek().type == TokType.IDENT:
            name = self.advance().value.lower()
            if self.peek().type == TokType.OP and self.peek().value == "=":
                self.advance()
            label = self.advance().value if self.peek().type == TokType.STRING else ""
            entries.append((name, label))
        self.skip_to_semi()
        return A.LabelStmt(entries=entries)

    def _parse_output(self):
        self.advance()
        ds = None
        if self.peek().type == TokType.IDENT:
            ds = normalize_dsname(self._read_dotted_name())
        self.skip_to_semi()
        return A.Output(dataset=ds)

    def _parse_if(self):
        self.advance()
        cond = self.parse_expr()
        if self.is_kw("then"):
            self.advance()
            then_stmts = self._parse_branch_body()
            else_stmts = []
            if self.is_kw("else"):
                self.advance()
                else_stmts = self._parse_branch_body()
            return A.If(cond=cond, then=then_stmts, orelse=else_stmts)
        else:
            # subsetting IF: keep row only if cond true
            self.skip_semis_single()
            return A.If(cond=A.UnaryOp("not", cond), then=[A.DeleteStmt()], orelse=[])

    def skip_semis_single(self):
        if self.peek().type == TokType.SEMI:
            self.advance()

    def _parse_branch_body(self) -> list:
        if self.is_kw("do"):
            do = self._parse_do()
            return do.body if isinstance(do, A.DoBlock) else [do]
        stmt = self._parse_statement()
        return [stmt] if stmt is not None else []

    def _parse_do(self):
        self.advance()  # 'do'
        if self.peek().type == TokType.SEMI:
            self.advance()
            body = self._parse_stmt_list(stop_kws={"end"})
            if self.is_kw("end"):
                self.advance()
                self.skip_to_semi()
            return A.DoBlock(kind="block", var=None, start=None, stop=None, by=None, cond=None, body=body)

        if self.is_kw("over"):
            self.advance()
            arrname = self.advance().value.lower()
            self.skip_to_semi()
            body = self._parse_stmt_list(stop_kws={"end"})
            if self.is_kw("end"):
                self.advance()
                self.skip_to_semi()
            return A.DoBlock(kind="over", var=None, start=None, stop=None, by=None, cond=None,
                              body=body, over_array=arrname)

        if self.is_kw("while"):
            self.advance()
            self.expect(TokType.LPAREN)
            cond = self.parse_expr()
            if self.peek().type == TokType.RPAREN:
                self.advance()
            self.skip_to_semi()
            body = self._parse_stmt_list(stop_kws={"end"})
            if self.is_kw("end"):
                self.advance()
                self.skip_to_semi()
            return A.DoBlock(kind="while", var=None, start=None, stop=None, by=None, cond=cond, body=body)

        if self.is_kw("until"):
            self.advance()
            self.expect(TokType.LPAREN)
            cond = self.parse_expr()
            if self.peek().type == TokType.RPAREN:
                self.advance()
            self.skip_to_semi()
            body = self._parse_stmt_list(stop_kws={"end"})
            if self.is_kw("end"):
                self.advance()
                self.skip_to_semi()
            return A.DoBlock(kind="until", var=None, start=None, stop=None, by=None, cond=cond, body=body)

        if self.peek().type == TokType.IDENT and self.peek(1).type == TokType.OP and self.peek(1).value == "=":
            var = self.advance().value.lower()
            self.advance()  # '='
            start = self.parse_expr()
            self.eat_kw("to")
            stop = self.parse_expr()
            by = None
            if self.eat_kw("by"):
                by = self.parse_expr()
            self.skip_to_semi()
            body = self._parse_stmt_list(stop_kws={"end"})
            if self.is_kw("end"):
                self.advance()
                self.skip_to_semi()
            return A.DoBlock(kind="iterative", var=var, start=start, stop=stop, by=by, cond=None, body=body)

        # fallback: treat as block
        body = self._parse_stmt_list(stop_kws={"end"})
        if self.is_kw("end"):
            self.advance()
            self.skip_to_semi()
        return A.DoBlock(kind="block", var=None, start=None, stop=None, by=None, cond=None, body=body)

    def _parse_select(self):
        self.advance()  # 'select'
        select_expr = None
        if self.peek().type == TokType.LPAREN:
            self.advance()
            select_expr = self.parse_expr()
            if self.peek().type == TokType.RPAREN:
                self.advance()
        self.skip_to_semi()
        whens = []
        otherwise: list = []
        while not self.at_eof():
            self.skip_semis()
            if self.is_kw("end"):
                self.advance()
                self.skip_to_semi()
                break
            if self.is_kw("when"):
                self.advance()
                conds: list = []
                if self.peek().type == TokType.LPAREN:
                    self.advance()
                    while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
                        conds.append(self.parse_expr())
                        if self.peek().type == TokType.COMMA:
                            self.advance()
                    if self.peek().type == TokType.RPAREN:
                        self.advance()
                # a WHEN body is a single statement (usually DO...END)
                body = self._parse_branch_body()
                # consume the ';' terminating that statement if still pending
                if self.peek().type == TokType.SEMI:
                    self.advance()
                whens.append((conds, body))
                continue
            if self.is_kw("otherwise"):
                self.advance()
                otherwise = self._parse_branch_body()
                if self.peek().type == TokType.SEMI:
                    self.advance()
                continue
            break
        return A.SelectStmt(select_expr=select_expr, whens=whens, otherwise=otherwise)

    def _parse_where(self):
        self.advance()
        cond = self.parse_expr()
        self.skip_to_semi()
        return A.WhereStmt(cond=cond)

    def _parse_put(self):
        self.advance()
        args = []
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.peek().type == TokType.STRING:
                args.append(A.Str(self.advance().value))
            elif self.peek().type == TokType.IDENT:
                args.append(A.Var(self.advance().value.lower()))
            elif self.peek().type == TokType.OP and self.peek().value == "=":
                self.advance()
            else:
                self.advance()
        self.skip_to_semi()
        return A.PutStmt(args=args)

    def _parse_call(self):
        self.advance()
        name = self.advance().value.lower()
        args = []
        if self.peek().type == TokType.LPAREN:
            self.advance()
            while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
                args.append(self.parse_expr())
                if self.peek().type == TokType.COMMA:
                    self.advance()
            if self.peek().type == TokType.RPAREN:
                self.advance()
        self.skip_to_semi()
        return A.CallStmt(name=name, args=args)

    def _parse_input(self):
        self.advance()
        varlist = []
        while self.peek().type == TokType.IDENT:
            name = self.advance().value.lower()
            is_char = False
            if self.peek().type == TokType.OP and self.peek().value == "$":
                is_char = True
                self.advance()
            varlist.append((name, is_char))
        self.skip_to_semi()
        return A.InputStmt(vars=varlist)

    def _parse_infile(self):
        self.advance()  # 'infile'
        if self.peek().type == TokType.STRING:
            path = self.advance().value
        elif self.peek().type == TokType.IDENT:
            path = self.advance().value
        else:
            path = ""
        dlm = None
        dsd = False
        firstobs = 1
        obs = None
        truncover = False
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.peek().type == TokType.IDENT:
                key = self.advance().value.lower()
                if self.peek().type == TokType.OP and self.peek().value == "=":
                    self.advance()
                    if key in ("dlm", "delimiter"):
                        if self.peek().type == TokType.STRING:
                            v = self.advance().value
                            dlm = v[0] if v else None
                        elif self.peek().type != TokType.SEMI:
                            v = self.advance().value
                            dlm = v[0] if v else None
                    elif key in ("firstobs",):
                        if self.peek().type == TokType.NUMBER:
                            firstobs = max(int(float(self.advance().value)), 1)
                        else:
                            self.advance()
                    elif key in ("obs", "lastobs"):
                        if self.peek().type == TokType.NUMBER:
                            obs = int(float(self.advance().value))
                        else:
                            self.advance()
                    else:
                        # lrecl=, etc: consume one value token and ignore
                        if self.peek().type not in (TokType.SEMI,):
                            self.advance()
                else:
                    if key == "dsd":
                        dsd = True
                    elif key in ("truncover", "missover"):
                        truncover = True
                    # pad/flowover/lastobs n without '=' and friends: ignore
            else:
                self.advance()
        self.skip_to_semi()
        return A.InfileStmt(path=path, dlm=dlm, dsd=dsd, firstobs=firstobs,
                            obs=obs, truncover=truncover)

    def _parse_file(self):
        self.advance()  # 'file'
        if self.peek().type == TokType.STRING:
            path = self.advance().value
        elif self.peek().type == TokType.IDENT:
            path = self.advance().value
        else:
            path = ""
        mod = False
        dlm = None
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.peek().type == TokType.IDENT:
                key = self.advance().value.lower()
                if self.peek().type == TokType.OP and self.peek().value == "=":
                    self.advance()
                    if key in ("dlm", "delimiter"):
                        if self.peek().type == TokType.STRING:
                            v = self.advance().value
                            dlm = v[0] if v else None
                        elif self.peek().type != TokType.SEMI:
                            v = self.advance().value
                            dlm = v[0] if v else None
                    elif self.peek().type not in (TokType.SEMI,):
                        self.advance()
                else:
                    if key == "mod":
                        mod = True
            else:
                self.advance()
        self.skip_to_semi()
        return A.FileStmt(path=path, mod=mod, dlm=dlm)

    def _parse_abort(self):
        self.advance()  # 'abort'
        msg = None
        # ABORT CANCEL "msg"; / ABORT RETURN ... / ABORT "msg";
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.peek().type == TokType.STRING:
                msg = self.advance().value
            else:
                self.advance()
        self.skip_to_semi()
        return A.AbortStmt(message=msg)

    def _parse_datalines(self):
        # find raw text from current token's source position up to a line
        # containing only ';' (or 'run;') and treat each line as one record.
        kw_pos = self.peek().pos
        self.advance()  # 'datalines' / 'cards'
        # find the ';' that terminates the DATALINES statement itself
        j = self.source.find(";", kw_pos)
        body_start = j + 1
        m = re.search(r"\n[ \t]*;", self.source[body_start:])
        end = body_start + m.start() if m else len(self.source)
        raw = self.source[body_start:end]
        lines = [ln for ln in raw.split("\n") if ln.strip() != ""]
        # resync token cursor past the consumed raw region and the terminator ';'
        target_pos = (body_start + m.end()) if m else len(self.source)
        while self.peek().pos < target_pos and self.peek().type != TokType.EOF:
            self.advance()
        return A.DatalinesStmt(lines=lines)

    def _parse_delete(self):
        self.advance()
        self.skip_to_semi()
        return A.DeleteStmt()

    def _parse_return(self):
        self.advance()
        if self.peek().type == TokType.LPAREN:
            # RETURN(expr); -- only meaningful inside a PROC FCMP function
            # body; the DATA step's bare RETURN; keeps its own meaning.
            self.advance()
            expr = A.Missing()
            if self.peek().type != TokType.RPAREN:
                expr = self.parse_expr()
            if self.peek().type == TokType.RPAREN:
                self.advance()
            self.skip_to_semi()
            return A.FcmpReturnStmt(expr=expr)
        self.skip_to_semi()
        return A.ReturnStmt()

    def _parse_assignment_or_sum(self):
        target = self._parse_lvalue()
        if self.peek().type == TokType.OP and self.peek().value == "+":
            self.advance()
            expr = self.parse_expr()
            self.skip_to_semi()
            return A.CallStmt(name="__sum__", args=[target, expr])
        if self.peek().type == TokType.OP and self.peek().value == "=":
            self.advance()
            expr = self.parse_expr()
            self.skip_to_semi()
            return A.Assign(target=target, expr=expr)
        # unrecognized statement shape; skip it
        self.skip_to_semi()
        return None

    def _parse_lvalue(self):
        name = self.advance().value.lower()
        if self.peek().type == TokType.OP and self.peek().value in ("{", "["):
            self.advance()
            idx = self.parse_expr()
            if self.peek().type == TokType.OP and self.peek().value in ("}", "]"):
                self.advance()
            return A.ArrayRef(name=name, index=idx)
        return A.Var(name=name)

    # ------------- expressions -------------
    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.is_kw("or") or (self.peek().type == TokType.OP and self.peek().value == "|" and not (self.peek(1).type == TokType.OP and self.peek(1).value == "|")):
            self.advance()
            right = self.parse_and()
            left = A.BinOp("or", left, right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.is_kw("and"):
            self.advance()
            right = self.parse_not()
            left = A.BinOp("and", left, right)
        return left

    def parse_not(self):
        if self.is_kw("not"):
            self.advance()
            return A.UnaryOp("not", self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self):
        left = self.parse_concat()
        while True:
            t = self.peek()
            op = None
            if t.type == TokType.OP and t.value in ("=", "^=", "~=", "<", ">", "<=", ">="):
                op = t.value
                self.advance()
            elif t.type == TokType.IDENT and t.value.lower() in _CMP_WORDS:
                op = _CMP_WORDS[t.value.lower()]
                self.advance()
            elif t.type == TokType.IDENT and t.value.lower() == "in":
                self.advance()
                negate = False
                items = []
                self.expect(TokType.LPAREN)
                while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
                    items.append(self.parse_expr())
                    if self.peek().type == TokType.COMMA:
                        self.advance()
                if self.peek().type == TokType.RPAREN:
                    self.advance()
                left = A.BinOp("in", left, A.Call("__list__", items))
                continue
            else:
                break
            right = self.parse_concat()
            left = A.BinOp(op, left, right)
        return left

    def parse_concat(self):
        left = self.parse_add()
        while self.peek().type == TokType.OP and self.peek().value in ("||", "!!"):
            self.advance()
            right = self.parse_add()
            left = A.BinOp("||", left, right)
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.peek().type == TokType.OP and self.peek().value in ("+", "-"):
            op = self.advance().value
            right = self.parse_mul()
            left = A.BinOp(op, left, right)
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.peek().type == TokType.OP and self.peek().value in ("*", "/"):
            op = self.advance().value
            right = self.parse_unary()
            left = A.BinOp(op, left, right)
        return left

    def parse_unary(self):
        if self.peek().type == TokType.OP and self.peek().value == "-":
            self.advance()
            return A.UnaryOp("-", self.parse_unary())
        if self.peek().type == TokType.OP and self.peek().value == "+":
            self.advance()
            return self.parse_unary()
        return self.parse_pow()

    def parse_pow(self):
        left = self.parse_atom()
        if self.peek().type == TokType.OP and self.peek().value == "**":
            self.advance()
            right = self.parse_unary()
            return A.BinOp("**", left, right)
        return left

    def parse_atom(self):
        t = self.peek()
        if t.type == TokType.LPAREN:
            self.advance()
            e = self.parse_expr()
            if self.peek().type == TokType.RPAREN:
                self.advance()
            return e
        if t.type == TokType.NUMBER:
            self.advance()
            return A.Num(float(t.value))
        if t.type == TokType.STRING:
            self.advance()
            # SAS date/time/datetime literals: '01JAN2010'd, '12:34't,
            # '01JAN2010:12:34:56'dt -- the suffix must immediately follow
            # the closing quote (char right before it is the quote itself).
            nxt = self.peek()
            if nxt.type == TokType.IDENT and nxt.pos > 0 and self.source[nxt.pos - 1] in ("'", '"'):
                suffix = nxt.value.lower()
                sasnum = None
                if suffix == "d":
                    sasnum = parse_sas_date_literal(t.value)
                elif suffix == "t":
                    sasnum = parse_sas_time_literal(t.value)
                elif suffix == "dt":
                    sasnum = parse_sas_datetime_literal(t.value)
                if sasnum is not None:
                    self.advance()
                    return A.Num(sasnum)
            return A.Str(t.value)
        if t.type == TokType.OP and t.value == ".":
            self.advance()
            return A.Missing()
        if t.type == TokType.IDENT:
            name = t.value
            name_l = name.lower()
            if name_l in ("first", "last") and self.peek(1).type == TokType.OP and self.peek(1).value == "." and self.peek(2).type == TokType.IDENT:
                self.advance()
                self.advance()
                var = self.advance().value.lower()
                return A.DotVar(kind=name_l, var=var)
            self.advance()
            if (self.peek().type == TokType.OP and self.peek().value == "."
                    and self.peek(1).type == TokType.IDENT and self.peek(2).type == TokType.LPAREN):
                return self._parse_hash_call_tail(name_l)
            if self.peek().type == TokType.LPAREN:
                self.advance()
                if name_l in ("put", "input"):
                    args = [self.parse_expr()]
                    if self.peek().type == TokType.COMMA:
                        self.advance()
                        args.append(A.Str(self._parse_format_spec()))
                    if self.peek().type == TokType.RPAREN:
                        self.advance()
                    return A.Call(name=name_l, args=args)
                args = []
                while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
                    args.append(self.parse_expr())
                    if self.peek().type == TokType.COMMA:
                        self.advance()
                if self.peek().type == TokType.RPAREN:
                    self.advance()
                return A.Call(name=name_l, args=args)
            if self.peek().type == TokType.OP and self.peek().value in ("{", "["):
                self.advance()
                idx = self.parse_expr()
                if self.peek().type == TokType.OP and self.peek().value in ("}", "]"):
                    self.advance()
                return A.ArrayRef(name=name_l, index=idx)
            return A.Var(name=name_l)
        # fallback: consume token, return missing
        self.advance()
        return A.Missing()

    def _parse_format_spec(self) -> str:
        if self.peek().type == TokType.STRING:
            return self.advance().value
        parts = []
        if self.peek().type == TokType.OP and self.peek().value == "$":
            parts.append("$")
            self.advance()
        if self.peek().type == TokType.IDENT:
            parts.append(self.advance().value)
        return self._consume_format_suffix("".join(parts))

    # ------------- PROC step -------------
    def parse_proc_step(self) -> A.ProcStep:
        self.advance()  # 'proc'
        name = self.advance().value.lower()
        options = {}
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.peek().type == TokType.IDENT and self.peek(1).type == TokType.OP and self.peek(1).value == "=":
                key = self.advance().value.lower()
                self.advance()
                val = self._read_dotted_name() if self.peek().type == TokType.IDENT else self.advance().value
                options[key] = val
            elif self.peek().type == TokType.IDENT:
                flag = self.advance().value
                if flag.lower() == "data":
                    raise self._err(
                        f"PROC {name.upper()} has bare 'DATA' with no '=' "
                        f"(did you mean DATA=...? e.g. 'DATA+...' is not valid SAS)"
                    )
                options[flag.lower()] = True
            else:
                self.advance()
        self.skip_to_semi()

        if name == "sql":
            return self._parse_proc_sql(options)
        if name == "format":
            return self._parse_proc_format(options)
        if name == "fcmp":
            return self._parse_proc_fcmp(options)

        clauses = []
        while not self.at_eof() and not self.is_kw("run") and not self.is_kw("quit") and not self.is_kw("data") and not self.is_kw("proc"):
            if self.peek().type != TokType.IDENT:
                self.skip_to_semi()
                continue
            ckw = self.peek().value.lower()
            if name == "datasets" and ckw == "delete":
                self.advance()
                names = []
                while self.peek().type == TokType.IDENT:
                    raw = self._read_dotted_name()
                    names.append((normalize_dsname(raw), raw))
                clauses.append(("delete", names))
                self.skip_to_semi()
            elif name == "datasets" and ckw in ("change", "rename"):
                self.advance()
                pairs = []
                while self.peek().type == TokType.IDENT:
                    old_raw = self._read_dotted_name()
                    if self.peek().type == TokType.OP and self.peek().value == "=":
                        self.advance()
                    if self.peek().type == TokType.IDENT:
                        new_raw = self._read_dotted_name()
                    else:
                        new_raw = old_raw
                    pairs.append((normalize_dsname(old_raw), normalize_dsname(new_raw),
                                  old_raw, new_raw))
                clauses.append(("change", pairs))
                self.skip_to_semi()
            elif ckw in ("var", "by", "class", "id", "freq", "with"):
                self.advance()
                names = []
                while self.peek().type == TokType.IDENT:
                    desc = False
                    if self.is_kw("descending"):
                        self.advance()
                        desc = True
                    names.append((self.advance().value.lower(), desc))
                clauses.append((ckw, names))
                self.skip_to_semi()
            elif ckw == "output":
                self.advance()
                clauses.append(("output", self._parse_output_clause()))
            elif ckw == "tables":
                self.advance()
                start = self.peek().pos
                self.skip_to_semi()
                end_tok_pos = self.peek().pos
                raw = self.source[start:end_tok_pos]
                clauses.append(("tables", raw.strip().rstrip(";")))
            elif ckw == "where":
                self.advance()
                cond = self.parse_expr()
                clauses.append(("where", cond))
                self.skip_to_semi()
            elif ckw == "ranks":
                self.advance()
                names = []
                while self.peek().type == TokType.IDENT:
                    names.append(self.advance().value.lower())
                clauses.append(("ranks", names))
                self.skip_to_semi()
            elif ckw == "model":
                self.advance()
                start = self.peek().pos
                self.skip_to_semi()
                end_tok_pos = self.peek().pos
                raw = self.source[start:end_tok_pos].strip()
                if raw.endswith(";"):
                    raw = raw[:-1]
                clauses.append(("model", raw.strip()))
            elif name == "report" and ckw == "compute":
                self.advance()
                start = self.peek().pos
                self.skip_to_semi()
                end_tok_pos = self.peek().pos
                target = self.source[start:end_tok_pos].strip()
                if target.endswith(";"):
                    target = target[:-1]
                body = self._parse_stmt_list(stop_kws={"endcomp"})
                if self.is_kw("endcomp"):
                    self.advance()
                    self.skip_to_semi()
                clauses.append(("compute", (target.strip(), body)))
            elif name == "report" and ckw == "column":
                self.advance()
                names = []
                while self.peek().type == TokType.IDENT:
                    names.append(self.advance().value.lower())
                clauses.append(("column", names))
                self.skip_to_semi()
            elif name == "report" and ckw == "define":
                self.advance()
                var = self.advance().value.lower() if self.peek().type == TokType.IDENT else ""
                mods = []
                if self.peek().type == TokType.OP and self.peek().value == "/":
                    self.advance()
                    while self.peek().type == TokType.IDENT:
                        mods.append(self.advance().value.lower())
                clauses.append(("define", (var, mods)))
                self.skip_to_semi()
            elif name == "tabulate" and ckw in ("table", "tables"):
                self.advance()
                start = self.peek().pos
                self.skip_to_semi()
                end_tok_pos = self.peek().pos
                raw = self.source[start:end_tok_pos].strip()
                if raw.endswith(";"):
                    raw = raw[:-1]
                clauses.append(("table", raw.strip()))
            else:
                start = self.peek().pos
                self.skip_to_semi()
                end_tok_pos = self.peek().pos
                raw = self.source[start:end_tok_pos]
                clauses.append((ckw, raw.strip()))
        if self.is_kw("run") or self.is_kw("quit"):
            self.advance()
            self.skip_to_semi()
        return A.ProcStep(name=name, options=options, clauses=clauses)

    def _parse_output_clause(self) -> dict:
        # info["stats"]: list of (statkw, var_or_None, newname_or_None)
        # var_or_None is set only for the stat(var)=name form; the more common
        # stat=name1 name2 ... form maps positionally onto the VAR list at
        # codegen time (var left None, names collected in "names").
        info = {"out": None, "stats": []}
        while self.peek().type not in (TokType.SEMI, TokType.EOF):
            if self.peek().type == TokType.IDENT and self.peek(1).type == TokType.OP and self.peek(1).value == "=":
                key = self.advance().value.lower()
                self.advance()
                if key == "out":
                    raw = self._read_dotted_name()
                    info["out"] = normalize_dsname(raw)
                    info["out_raw"] = raw
                    continue
                var = None
                if self.peek().type == TokType.LPAREN:
                    self.advance()
                    var = self.advance().value.lower() if self.peek().type == TokType.IDENT else None
                    if self.peek().type == TokType.RPAREN:
                        self.advance()
                names = []
                while self.peek().type == TokType.IDENT and not (
                    self.peek(1).type == TokType.OP and self.peek(1).value == "="
                ):
                    names.append(self.advance().value.lower())
                if var is not None:
                    newname = names[0] if names else key
                    info["stats"].append((key, var, newname))
                else:
                    info["stats"].append((key, None, names))
            else:
                self.advance()
        self.skip_to_semi()
        return info

    def _parse_format_bound(self):
        neg = False
        if self.peek().type == TokType.OP and self.peek().value == "-":
            neg = True
            self.advance()
        if self.is_kw("low"):
            self.advance()
            return float("-inf")
        if self.is_kw("high"):
            self.advance()
            return float("inf")
        if self.peek().type == TokType.NUMBER:
            v = float(self.advance().value)
            return -v if neg else v
        return 0.0

    def _parse_proc_format(self, options) -> A.ProcStep:
        clauses = []
        while not self.at_eof() and not self.is_kw("run") and not self.is_kw("quit") and not self.is_kw("data") and not self.is_kw("proc"):
            if not self.is_kw("value"):
                self.skip_to_semi()
                continue
            self.advance()
            is_char = False
            if self.peek().type == TokType.OP and self.peek().value == "$":
                is_char = True
                self.advance()
            fmtname = self.advance().value.lower()
            entries = []
            other_label = None
            while self.peek().type not in (TokType.SEMI, TokType.EOF):
                if self.is_kw("other"):
                    self.advance()
                    if self.peek().type == TokType.OP and self.peek().value == "=":
                        self.advance()
                    other_label = self.advance().value if self.peek().type == TokType.STRING else ""
                    continue
                if is_char:
                    if self.peek().type == TokType.STRING:
                        val = self.advance().value
                        if self.peek().type == TokType.OP and self.peek().value == "=":
                            self.advance()
                        label = self.advance().value if self.peek().type == TokType.STRING else ""
                        entries.append((val, label))
                    else:
                        self.advance()
                else:
                    lo = self._parse_format_bound()
                    hi = lo
                    if self.peek().type == TokType.OP and self.peek().value == "-":
                        self.advance()
                        hi = self._parse_format_bound()
                    if self.peek().type == TokType.OP and self.peek().value == "=":
                        self.advance()
                    label = self.advance().value if self.peek().type == TokType.STRING else ""
                    entries.append((lo, hi, label))
            self.skip_to_semi()
            clauses.append(("value", is_char, fmtname, entries, other_label))
        if self.is_kw("run") or self.is_kw("quit"):
            self.advance()
            self.skip_to_semi()
        return A.ProcStep(name="format", options=options, clauses=clauses)

    def _parse_proc_fcmp(self, options) -> A.ProcStep:
        """PROC FCMP outlib=libref.ds.package;
        FUNCTION name(arg1, arg2) [$]; <DATA-step-flavored statements>
        ENDSUB; ... RUN;
        OUTLIB= is parsed (into `options`, already captured generically by
        the caller) but not used -- functions become callable from any
        later DATA step in the same compiled program regardless of
        OUTLIB=/CMPLIB=, since everything lives in one Python process."""
        clauses = []
        while not self.at_eof() and not self.is_kw("run") and not self.is_kw("quit") and not self.is_kw("data") and not self.is_kw("proc"):
            if not self.is_kw("function") and not self.is_kw("subroutine"):
                self.skip_to_semi()
                continue
            self.advance()  # 'function' / 'subroutine'
            fname = self.advance().value.lower() if self.peek().type == TokType.IDENT else ""
            params = []
            if self.peek().type == TokType.LPAREN:
                self.advance()
                while self.peek().type != TokType.RPAREN and self.peek().type != TokType.EOF:
                    if self.peek().type == TokType.IDENT:
                        params.append(self.advance().value.lower())
                    else:
                        self.advance()
                    if self.peek().type == TokType.COMMA:
                        self.advance()
                if self.peek().type == TokType.RPAREN:
                    self.advance()
            is_char = False
            if self.peek().type == TokType.OP and self.peek().value == "$":
                is_char = True
                self.advance()
            self.skip_to_semi()
            body = self._parse_stmt_list(stop_kws={"endsub"})
            if self.is_kw("endsub"):
                self.advance()
                self.skip_to_semi()
            clauses.append(("function", fname, params, is_char, body))
        if self.is_kw("run") or self.is_kw("quit"):
            self.advance()
            self.skip_to_semi()
        return A.ProcStep(name="fcmp", options=options, clauses=clauses)

    def _parse_proc_sql(self, options) -> A.ProcStep:
        start = self.peek().pos
        m = re.search(r"\bquit\s*;", self.source[start:], re.I)
        if m:
            end = start + m.start()
            after = start + m.end()
        else:
            end = len(self.source)
            after = end
        raw = self.source[start:end]
        stmts = self._split_top_level_semi(raw)
        while self.peek().pos < after and self.peek().type != TokType.EOF:
            self.advance()
        if self.is_kw("quit"):
            self.advance()
            self.skip_to_semi()
        return A.ProcStep(name="sql", options=options, clauses=[("sql_stmt", s.strip()) for s in stmts if s.strip()])

    @staticmethod
    def _split_top_level_semi(text: str) -> list:
        parts = []
        depth = 0
        buf = []
        in_s = in_d = False
        for c in text:
            if in_s:
                buf.append(c)
                if c == "'":
                    in_s = False
                continue
            if in_d:
                buf.append(c)
                if c == '"':
                    in_d = False
                continue
            if c == "'":
                in_s = True
                buf.append(c)
                continue
            if c == '"':
                in_d = True
                buf.append(c)
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            if c == ";" and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
            buf.append(c)
        if "".join(buf).strip():
            parts.append("".join(buf))
        return parts


def parse(source: str) -> A.Program:
    return Parser(source).parse_program()
