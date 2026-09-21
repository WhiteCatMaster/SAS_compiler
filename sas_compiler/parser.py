"""Recursive-descent parser: tokens -> AST (Program of DataStep/ProcStep)."""
from __future__ import annotations

import re

from .lexer import Lexer, Token, TokType
from . import ast_nodes as A


class ParseError(Exception):
    pass


_CMP_WORDS = {"eq": "=", "ne": "^=", "lt": "<", "gt": ">", "le": "<=", "ge": ">="}


def normalize_dsname(name: str) -> str:
    parts = name.split(".")
    if len(parts) == 2 and parts[0].lower() in ("work",):
        return parts[1].lower()
    return "_".join(p.lower() for p in parts)


class Parser:
    def __init__(self, source: str):
        self.source = source
        self.toks = Lexer(source).tokenize()
        self.i = 0

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
            raise ParseError(f"expected {ttype} but got {t} near pos {t.pos}")
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
            elif self.peek().type == TokType.SEMI:
                self.advance()
            else:
                # stray token (e.g. leftover libname/options/title statement) -
                # skip the whole statement
                self.skip_to_semi()
            self.skip_semis()
        return A.Program(steps=steps)

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
                dsname = normalize_dsname(".".join(name_parts))
                if dsname == "_null_":
                    is_null = True
                options = {}
                if self.peek().type == TokType.LPAREN:
                    options = self._parse_dataset_options()
                outputs.append((dsname, options))
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
                else:
                    # obs=, firstobs=, etc: consume one value token and ignore
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

        dispatch = {
            "set": self._parse_set,
            "merge": self._parse_merge,
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
            "where": self._parse_where,
            "put": self._parse_put,
            "call": self._parse_call,
            "input": self._parse_input,
            "datalines": self._parse_datalines,
            "cards": self._parse_datalines,
            "delete": self._parse_delete,
            "return": self._parse_return,
        }
        if kw in dispatch:
            return dispatch[kw]()

        return self._parse_assignment_or_sum()

    # ---- individual statement parsers ----
    def _parse_set(self):
        self.advance()
        datasets = []
        while self.peek().type == TokType.IDENT:
            name = self._read_dotted_name()
            opts = {}
            if self.peek().type == TokType.LPAREN:
                opts = self._parse_dataset_options()
            datasets.append((normalize_dsname(name), opts))
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
            datasets.append((normalize_dsname(name), opts))
        self.skip_to_semi()
        return A.MergeStmt(datasets=datasets, by=[])

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
        if self.peek().type == TokType.OP and self.peek().value in ("{", "["):
            self.advance()
            if self.peek().type == TokType.NUMBER:
                dim = int(float(self.advance().value))
            elif self.peek().type == TokType.OP and self.peek().value == "*":
                self.advance()
                dim = None
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

        elements = []
        if self.peek().type == TokType.IDENT:
            while self.peek().type == TokType.IDENT:
                elt = self.advance().value.lower()
                if self.peek().type == TokType.OP and self.peek().value == "-" and self.peek(1).type == TokType.IDENT:
                    nxt = self.peek(1).value.lower()
                    m1 = re.match(r"^([a-zA-Z_]+)(\d+)$", elt)
                    m2 = re.match(r"^([a-zA-Z_]+)(\d+)$", nxt)
                    if m1 and m2 and m1.group(1) == m2.group(1):
                        self.advance()
                        self.advance()
                        prefix = m1.group(1)
                        lo, hi = int(m1.group(2)), int(m2.group(2))
                        for k in range(lo, hi + 1):
                            elements.append(f"{prefix}{k}")
                        continue
                elements.append(elt)

        init_values = []
        if self.peek().type == TokType.LPAREN:
            init_values = self._parse_array_init_values()

        if dim is None:
            dim = len(elements) if elements else len(init_values)
        if not elements:
            elements = [f"{name}{i}" for i in range(1, dim + 1)]

        self.skip_to_semi()
        return A.ArrayStmt(name=name, dim=dim, elements=elements, is_char=is_char,
                            length=length, init_values=init_values)

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
                options[self.advance().value.lower()] = True
            else:
                self.advance()
        self.skip_to_semi()

        if name == "sql":
            return self._parse_proc_sql(options)

        clauses = []
        while not self.at_eof() and not self.is_kw("run") and not self.is_kw("quit") and not self.is_kw("data") and not self.is_kw("proc"):
            if self.peek().type != TokType.IDENT:
                self.skip_to_semi()
                continue
            ckw = self.peek().value.lower()
            if ckw in ("var", "by", "class", "id", "freq"):
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
                    info["out"] = normalize_dsname(self._read_dotted_name())
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
