"""AST -> Python (pandas/duckdb) code generator."""
from __future__ import annotations

import re

from . import ast_nodes as A
from .parser import normalize_dsname


class CodegenError(Exception):
    pass


CHAR_FUNCS = {
    "upcase", "lowcase", "propcase", "substr", "scan", "put", "strip", "trim",
    "left", "compress", "cats", "catx", "cat", "tranwrd", "ifc", "coalescec",
}

_FUNC_MAP = {
    "int": "int_", "floor": "floor_", "ceil": "ceil_", "mod": "mod_",
    "abs": "abs_", "sqrt": "sqrt_", "exp": "exp_", "log": "log_",
    "log10": "log10_", "sign": "sign_", "sum": "sum_", "mean": "mean_",
    "min": "min_", "max": "max_", "n": "n_", "nmiss": "nmiss_",
    "missing": "missing_", "round": "round_", "year": "year_",
    "month": "month_", "day": "day_", "weekday": "weekday_",
    "index": "index_", "input": "input_", "std": "std_",
}

_DIRECT_FUNCS = {
    "substr", "upcase", "lowcase", "propcase", "trim", "strip", "left",
    "compress", "length", "lengthn", "scan", "countw", "cat", "catx",
    "cats", "tranwrd", "indexc", "today", "intck", "intnx", "coalesce",
    "coalescec", "ifn", "ifc", "put", "sas_date",
}

# PROC MEANS/SUMMARY statistic keyword -> Python expression computing it
# from `_s` (the analysis variable's non-missing values as a pandas Series)
# and, for "nmiss" only, the ungrouped `_grp[_v]`.
_STAT_EXPR = {
    "n": "float(len(_s))",
    "mean": "_s.mean() if len(_s) else float('nan')",
    "std": "_s.std() if len(_s) > 1 else float('nan')",
    "stddev": "_s.std() if len(_s) > 1 else float('nan')",
    "min": "_s.min() if len(_s) else float('nan')",
    "max": "_s.max() if len(_s) else float('nan')",
    "sum": "_s.sum() if len(_s) else float('nan')",
    "median": "_s.median() if len(_s) else float('nan')",
    "var": "_s.var() if len(_s) > 1 else float('nan')",
    "range": "(_s.max() - _s.min()) if len(_s) else float('nan')",
    "nmiss": "float(len(_grp[_v]) - len(_s))",
}
for _p in (1, 5, 10, 25, 50, 75, 90, 95, 99):
    _STAT_EXPR[f"p{_p}"] = f"_s.quantile({_p / 100}) if len(_s) else float('nan')"

_DEFAULT_MEANS_STATS = ["n", "mean", "std", "min", "max"]


def walk_stmts(stmts):
    for s in stmts:
        yield s
        if isinstance(s, A.If):
            yield from walk_stmts(s.then)
            yield from walk_stmts(s.orelse)
        elif isinstance(s, A.DoBlock):
            yield from walk_stmts(s.body)


def _rewrite_expr(e: A.Expr, arrname: str, idxvar: str) -> A.Expr:
    """Return a copy of expr `e` with every bare reference to array
    `arrname` replaced by an indexed reference using `idxvar` -- used to
    implement DO OVER, where the array name alone means "current element"."""
    if isinstance(e, A.Var):
        if e.name == arrname:
            return A.ArrayRef(name=arrname, index=A.Var(idxvar))
        return e
    if isinstance(e, A.ArrayRef):
        return A.ArrayRef(name=e.name, index=_rewrite_expr(e.index, arrname, idxvar))
    if isinstance(e, A.BinOp):
        return A.BinOp(e.op, _rewrite_expr(e.left, arrname, idxvar), _rewrite_expr(e.right, arrname, idxvar))
    if isinstance(e, A.UnaryOp):
        return A.UnaryOp(e.op, _rewrite_expr(e.operand, arrname, idxvar))
    if isinstance(e, A.Call):
        return A.Call(e.name, [_rewrite_expr(a, arrname, idxvar) for a in e.args])
    return e


def _rewrite_stmt(s, arrname: str, idxvar: str):
    if isinstance(s, A.Assign):
        target = s.target
        if isinstance(target, A.Var) and target.name == arrname:
            target = A.ArrayRef(name=arrname, index=A.Var(idxvar))
        else:
            target = _rewrite_expr(target, arrname, idxvar)
        return A.Assign(target=target, expr=_rewrite_expr(s.expr, arrname, idxvar))
    if isinstance(s, A.If):
        return A.If(
            cond=_rewrite_expr(s.cond, arrname, idxvar),
            then=[_rewrite_stmt(st, arrname, idxvar) for st in s.then],
            orelse=[_rewrite_stmt(st, arrname, idxvar) for st in s.orelse],
        )
    if isinstance(s, A.DoBlock):
        return A.DoBlock(
            kind=s.kind, var=s.var,
            start=_rewrite_expr(s.start, arrname, idxvar) if s.start else None,
            stop=_rewrite_expr(s.stop, arrname, idxvar) if s.stop else None,
            by=_rewrite_expr(s.by, arrname, idxvar) if s.by else None,
            cond=_rewrite_expr(s.cond, arrname, idxvar) if s.cond else None,
            body=[_rewrite_stmt(st, arrname, idxvar) for st in s.body],
            over_array=s.over_array,
        )
    if isinstance(s, A.PutStmt):
        return A.PutStmt(args=[_rewrite_expr(a, arrname, idxvar) for a in s.args])
    if isinstance(s, A.CallStmt):
        return A.CallStmt(name=s.name, args=[_rewrite_expr(a, arrname, idxvar) for a in s.args])
    if isinstance(s, A.Output):
        return s
    return s


class CodeGen:
    def __init__(self):
        self.lines: list[str] = []
        self.indent = 0
        self.step_idx = 0
        self.tmp = 0
        self.lag_ids: dict[int, str] = {}
        self.last_ds_name: str | None = None
        self.current_arrays: dict = {}
        self.hidden_vars: set = set()
        self.db_libs: dict = {}  # libref -> conn string, in program order

    def w(self, line: str):
        self.lines.append(("    " * self.indent) + line)

    def newtmp(self, prefix="t") -> str:
        self.tmp += 1
        return f"_{prefix}{self.tmp}"

    def _arr_lo(self, name: str) -> int:
        arrstmt = self.current_arrays.get(name)
        return arrstmt.lo_bound if arrstmt is not None else 1

    def generate(self, prog: A.Program) -> str:
        self.w("import sas_compiler.runtime as _r")
        self.w("import pandas as pd")
        self.w("import duckdb")
        self.w("_DS = {}")
        self.w("_FMT = {}")
        self.w("_LBL = {}")
        self.w("")
        fnames = []
        for step in prog.steps:
            if isinstance(step, A.DataStep):
                fnames.append(self.gen_data_step(step))
            elif isinstance(step, A.ProcStep):
                fnames.append(self.gen_proc_step(step))
            elif isinstance(step, A.LibnameStmt):
                fnames.append(self.gen_libname_step(step))
        self.w("")
        for fn in fnames:
            self.w(f"{fn}()")
        return "\n".join(self.lines) + "\n"

    # ---------------- expressions ----------------
    def gen_expr(self, e: A.Expr, varmap: str = "pdv") -> str:
        if isinstance(e, A.Num):
            return repr(float(e.value))
        if isinstance(e, A.Str):
            return repr(e.value)
        if isinstance(e, A.Missing):
            return "_r.MISSING"
        if isinstance(e, A.Var):
            return f"{varmap}.get({e.name!r}, _r.MISSING)"
        if isinstance(e, A.DotVar):
            return f"{varmap}.get({e.kind + '_' + e.var!r}, False)"
        if isinstance(e, A.ArrayRef):
            idx = self.gen_expr(e.index, varmap)
            lo = self._arr_lo(e.name)
            return f"{varmap}.get(_ARR_{e.name}[int({idx}) - {lo}], _r.MISSING)"
        if isinstance(e, A.UnaryOp):
            operand = self.gen_expr(e.operand, varmap)
            if e.op == "not":
                return f"(not _r.truthy({operand}))"
            if e.op == "-":
                return f"(-({operand}))"
            return operand
        if isinstance(e, A.BinOp):
            return self._gen_binop(e, varmap)
        if isinstance(e, A.Call):
            return self._gen_call(e, varmap)
        raise CodegenError(f"cannot generate expression for {e!r}")

    def _gen_binop(self, e: A.BinOp, varmap: str) -> str:
        if e.op == "in":
            items = e.right.args  # A.Call('__list__', items)
            item_codes = ", ".join(self.gen_expr(it, varmap) for it in items)
            left = self.gen_expr(e.left, varmap)
            return f"_r.sas_in({left}, [{item_codes}])"
        left = self.gen_expr(e.left, varmap)
        right = self.gen_expr(e.right, varmap)
        if e.op in ("+", "-", "*"):
            return f"({left} {e.op} {right})"
        if e.op == "/":
            return f"_r.sdiv({left}, {right})"
        if e.op == "**":
            return f"_r.spow({left}, {right})"
        if e.op == "||":
            return f"_r.concat({left}, {right})"
        if e.op == "and":
            return f"(_r.truthy({left}) and _r.truthy({right}))"
        if e.op == "or":
            return f"(_r.truthy({left}) or _r.truthy({right}))"
        cmp_map = {"=": "eq", "^=": "ne", "~=": "ne", "<": "lt", ">": "gt", "<=": "le", ">=": "ge"}
        if e.op in cmp_map:
            return f"_r.{cmp_map[e.op]}({left}, {right})"
        raise CodegenError(f"unsupported operator {e.op!r}")

    def _gen_call(self, e: A.Call, varmap: str) -> str:
        name = e.name.lower()
        if name in ("lag",) or re.match(r"^lag(\d+)$", name):
            m = re.match(r"^lag(\d+)$", name)
            depth = int(m.group(1)) if m else 1
            if not e.args:
                raise CodegenError("lag() requires an argument")
            key = e.args[0].name if isinstance(e.args[0], A.Var) else f"expr{id(e)}"
            argcode = self.gen_expr(e.args[0], varmap)
            return f"_lag.lag({key!r}, {argcode}, {depth})"
        if name == "mdy":
            m_, d_, y_ = (self.gen_expr(a, varmap) for a in e.args)
            return f"_r.sas_date({y_}, {m_}, {d_})"
        if name in ("dim", "hbound", "lbound") and e.args and isinstance(e.args[0], A.Var):
            arrstmt = self.current_arrays.get(e.args[0].name)
            if arrstmt is not None:
                if name == "lbound":
                    return repr(float(arrstmt.lo_bound))
                if name == "hbound":
                    return repr(float(arrstmt.lo_bound + arrstmt.dim - 1))
                return repr(float(arrstmt.dim))
        args_code = ", ".join(self.gen_expr(a, varmap) for a in e.args)
        if name in _FUNC_MAP:
            return f"_r.{_FUNC_MAP[name]}({args_code})"
        if name in _DIRECT_FUNCS:
            return f"_r.{name}({args_code})"
        return f"_r.unsupported({name!r}, {args_code})"

    def _is_static_char_expr(self, e: A.Expr) -> bool:
        if isinstance(e, A.Str):
            return True
        if isinstance(e, A.BinOp) and e.op == "||":
            return True
        if isinstance(e, A.Call) and e.name.lower() in CHAR_FUNCS:
            return True
        return False

    # ---------------- statements ----------------
    def gen_stmt(self, s):
        if isinstance(s, A.Assign):
            self._gen_assign(s)
        elif isinstance(s, A.If):
            self._gen_if(s)
        elif isinstance(s, A.DoBlock):
            self._gen_do(s)
        elif isinstance(s, A.Output):
            self._gen_output(s)
        elif isinstance(s, A.PutStmt):
            self._gen_put(s)
        elif isinstance(s, A.CallStmt):
            self._gen_call_stmt(s)
        elif isinstance(s, A.DeleteStmt):
            self.w("raise _r._RowDelete()")
        elif isinstance(s, A.ReturnStmt):
            self.w("raise _r._RowReturn()")
        elif isinstance(s, (A.SetStmt, A.MergeStmt, A.WhereStmt, A.InputStmt, A.DatalinesStmt)):
            raise CodegenError(
                f"{type(s).__name__} may only appear at the top level of a DATA step, "
                "not nested inside IF/DO"
            )
        elif isinstance(s, (A.ArrayStmt, A.RetainStmt, A.DropStmt, A.KeepStmt,
                             A.LengthStmt, A.FormatStmt, A.LabelStmt, A.ByStmt)):
            pass  # declarative; only meaningful at top level, already handled there
        else:
            raise CodegenError(f"cannot generate statement for {s!r}")

    def _gen_assign(self, s: A.Assign):
        expr_code = self.gen_expr(s.expr)
        if isinstance(s.target, A.Var):
            self.w(f"pdv[{s.target.name!r}] = {expr_code}")
        elif isinstance(s.target, A.ArrayRef):
            idx = self.gen_expr(s.target.index)
            lo = self._arr_lo(s.target.name)
            self.w(f"pdv[_ARR_{s.target.name}[int({idx}) - {lo}]] = {expr_code}")
        else:
            raise CodegenError(f"invalid assignment target {s.target!r}")

    def _gen_if(self, s: A.If):
        self.w(f"if _r.truthy({self.gen_expr(s.cond)}):")
        self.indent += 1
        if s.then:
            for st in s.then:
                self.gen_stmt(st)
        else:
            self.w("pass")
        self.indent -= 1
        if s.orelse:
            self.w("else:")
            self.indent += 1
            for st in s.orelse:
                self.gen_stmt(st)
            self.indent -= 1

    def _gen_do(self, s: A.DoBlock):
        if s.kind == "block":
            for st in s.body:
                self.gen_stmt(st)
            return
        if s.kind == "over":
            arrname = s.over_array
            arrstmt = self.current_arrays.get(arrname)
            if arrstmt is None:
                raise CodegenError(f"DO OVER {arrname}: no ARRAY statement declares {arrname!r}")
            v = self.newtmp("ov")
            idxkey = f"__ovidx_{v}__"
            self.hidden_vars.add(idxkey)
            self.w(f"{v} = 0")
            self.w(f"while {v} < {arrstmt.dim}:")
            self.indent += 1
            self.w(f"{v} = {v} + 1")
            # translate the 1..dim loop counter into the array's actual SAS
            # subscript value, since ArrayRef codegen subtracts lo_bound
            self.w(f"pdv[{idxkey!r}] = float({v} + {arrstmt.lo_bound - 1})")
            for st in s.body:
                self.gen_stmt(_rewrite_stmt(st, arrname, idxkey))
            self.indent -= 1
            return
        if s.kind == "iterative":
            lo, hi, by, v = (self.newtmp(p) for p in ("lo", "hi", "by", "v"))
            self.w(f"{lo} = {self.gen_expr(s.start)}")
            self.w(f"{hi} = {self.gen_expr(s.stop)}")
            self.w(f"{by} = {self.gen_expr(s.by) if s.by else '1.0'}")
            self.w(f"{v} = {lo}")
            self.w(f"while ({by} > 0 and {v} <= {hi}) or ({by} < 0 and {v} >= {hi}):")
            self.indent += 1
            self.w(f"pdv[{s.var!r}] = {v}")
            for st in s.body:
                self.gen_stmt(st)
            self.w(f"{v} = {v} + {by}")
            self.indent -= 1
            return
        if s.kind == "while":
            self.w(f"while _r.truthy({self.gen_expr(s.cond)}):")
            self.indent += 1
            for st in s.body:
                self.gen_stmt(st)
            self.indent -= 1
            return
        if s.kind == "until":
            self.w("while True:")
            self.indent += 1
            for st in s.body:
                self.gen_stmt(st)
            self.w(f"if _r.truthy({self.gen_expr(s.cond)}):")
            self.indent += 1
            self.w("break")
            self.indent -= 1
            self.indent -= 1
            return
        raise CodegenError(f"unknown DO kind {s.kind!r}")

    def _gen_output(self, s: A.Output):
        if s.dataset:
            self.w(f"_out_rows[{s.dataset!r}].append(dict(pdv))")
        else:
            self.w("for _dsname in _out_rows:")
            self.indent += 1
            self.w("_out_rows[_dsname].append(dict(pdv))")
            self.indent -= 1

    def _gen_put(self, s: A.PutStmt):
        parts = []
        for a in s.args:
            if isinstance(a, A.Str):
                parts.append(repr(a.value))
            else:
                parts.append(f"_r.sas_str({self.gen_expr(a)})")
        self.w(f"print({', '.join(parts)})" if parts else "print()")

    def _gen_call_stmt(self, s: A.CallStmt):
        name = s.name.lower()
        if name == "__sum__":
            target, expr = s.args
            expr_code = self.gen_expr(expr)
            if isinstance(target, A.Var):
                self.w(f"pdv[{target.name!r}] = _r.nomiss_sum(pdv.get({target.name!r}, 0.0), {expr_code})")
            elif isinstance(target, A.ArrayRef):
                idx = self.gen_expr(target.index)
                lo = self._arr_lo(target.name)
                self.w(f"_k = _ARR_{target.name}[int({idx}) - {lo}]")
                self.w(f"pdv[_k] = _r.nomiss_sum(pdv.get(_k, 0.0), {expr_code})")
            return
        if name == "symput":
            a0, a1 = s.args
            self.w(f"_r.MACRO_VARS[str({self.gen_expr(a0)}).strip().lower()] = _r.sas_str({self.gen_expr(a1)})")
            return
        if name == "missing":
            for a in s.args:
                if isinstance(a, A.Var):
                    self.w(f"pdv[{a.name!r}] = _r.MISSING")
            return
        self.w(f"pass  # unsupported: call {name}(...)")

    # ---------------- DATA step ----------------
    def gen_libname_step(self, stmt: A.LibnameStmt) -> str:
        self.step_idx += 1
        fname = f"_step_{self.step_idx}"
        self.w(f"def {fname}():")
        self.indent += 1
        if stmt.conn is not None:
            self.w(f"_r.libname({stmt.libref!r}, {stmt.conn!r})")
            self.db_libs[stmt.libref] = stmt.conn
        else:
            self.w(f"_r.libname_clear({stmt.libref!r})")
            self.db_libs.pop(stmt.libref, None)
        self.indent -= 1
        return fname

    def gen_data_step(self, ds: A.DataStep) -> str:
        self.step_idx += 1
        fname = f"_step_{self.step_idx}"

        set_stmt = merge_stmt = by_stmt = input_stmt = datalines_stmt = None
        arrays: dict[str, A.ArrayStmt] = {}
        retains: list = []
        drops: set = set()
        keeps: set = set()
        lengths: dict = {}
        formats: dict = {}
        labels: dict = {}
        body = []

        for s in ds.statements:
            if isinstance(s, A.SetStmt) and set_stmt is None and merge_stmt is None:
                set_stmt = s
            elif isinstance(s, A.MergeStmt) and merge_stmt is None:
                merge_stmt = s
            elif isinstance(s, A.ByStmt) and by_stmt is None:
                by_stmt = s
            elif isinstance(s, A.ArrayStmt):
                arrays[s.name] = s
            elif isinstance(s, A.RetainStmt):
                retains.extend(s.entries)
            elif isinstance(s, A.DropStmt):
                drops.update(s.vars)
            elif isinstance(s, A.KeepStmt):
                keeps.update(s.vars)
            elif isinstance(s, A.LengthStmt):
                for (n, is_char, ln) in s.entries:
                    lengths[n] = (is_char, ln)
            elif isinstance(s, A.FormatStmt):
                formats.update(dict(s.entries))
            elif isinstance(s, A.LabelStmt):
                labels.update(dict(s.entries))
            elif isinstance(s, A.InputStmt) and input_stmt is None:
                input_stmt = s
            elif isinstance(s, A.DatalinesStmt) and datalines_stmt is None:
                datalines_stmt = s
            elif isinstance(s, A.WhereStmt):
                body.append(A.If(cond=A.UnaryOp("not", s.cond), then=[A.DeleteStmt()], orelse=[]))
            else:
                body.append(s)

        char_vars = set()
        for n, (is_char, _) in lengths.items():
            if is_char:
                char_vars.add(n)
        for arrstmt in arrays.values():
            if arrstmt.is_char:
                char_vars.update(arrstmt.elements)
        for (n, val) in retains:
            if isinstance(val, str):
                char_vars.add(n)
        for s in walk_stmts(ds.statements):
            if isinstance(s, A.Assign) and isinstance(s.target, A.Var):
                if self._is_static_char_expr(s.expr):
                    char_vars.add(s.target.name)

        has_explicit_output = any(isinstance(s, A.Output) for s in walk_stmts(ds.statements))
        self.current_arrays = arrays
        self.hidden_vars = set()

        self.w(f"def {fname}():")
        self.indent += 1
        self.w("_lag = _r.new_lag_state()")
        self.w("pdv = {}")
        for v in sorted(char_vars):
            self.w(f"pdv[{v!r}] = ''")
        for name, arrstmt in arrays.items():
            for elt, val in zip(arrstmt.elements, arrstmt.init_values):
                lit = repr(val) if isinstance(val, str) else repr(float(val))
                self.w(f"pdv[{elt!r}] = {lit}")
        for (n, val) in retains:
            if val is not None:
                lit = repr(val) if isinstance(val, str) else repr(float(val))
                self.w(f"pdv[{n!r}] = {lit}")
        for name, arrstmt in arrays.items():
            elts = ", ".join(repr(x) for x in arrstmt.elements)
            self.w(f"_ARR_{name} = [{elts}]")

        out_init = ", ".join(f"{name!r}: []" for name, _, _ in ds.outputs)
        self.w(f"_out_rows = {{{out_init}}}")

        by_vars = [v for v, _ in (by_stmt.vars if by_stmt else [])]
        if merge_stmt is not None:
            self.w("_m_sources = []")
            for (name, opts, db_info) in merge_stmt.datasets:
                dfcode = self._gen_ds_opts_expr(name, opts, db_info)
                inflag = opts.get("in_flag")
                inflag_lit = repr(inflag) if inflag else "None"
                self.w(f"_m_sources.append(({name!r}, {dfcode}, {inflag_lit}))")
            if by_vars:
                byvars_lit = ", ".join(repr(v) for v in by_vars)
                self.w(f"_iter = _r.iter_merge_by(_m_sources, [{byvars_lit}])")
            else:
                self.w("_iter = _r.iter_merge_positional([_s for _, _s, _f in _m_sources])")
        elif set_stmt is not None:
            self.w("_s_dfs = []")
            for (name, opts, db_info) in set_stmt.datasets:
                dfcode = self._gen_ds_opts_expr(name, opts, db_info)
                self.w(f"_s_dfs.append({dfcode})")
            if by_vars:
                byvars_lit = ", ".join(repr(v) for v in by_vars)
                self.w(f"_iter = _r.iter_concat_by(_s_dfs, [{byvars_lit}])")
            else:
                self.w("_iter = _r.iter_concat(_s_dfs)")
        elif datalines_stmt is not None and input_stmt is not None:
            rows = self._parse_datalines_rows(datalines_stmt, input_stmt)
            rows_src = "[" + ", ".join(self._dict_literal(r) for r in rows) + "]"
            self.w(f"_iter = [(row, {{}}) for row in {rows_src}]")
        else:
            self.w("_iter = _r.iter_once()")

        self.w("for _row, _flags in _iter:")
        self.indent += 1
        self.w("pdv.update(_row)")
        self.w("for _byv, (_isf, _isl) in _flags.items():")
        self.indent += 1
        self.w("pdv['first_' + _byv] = _isf")
        self.w("pdv['last_' + _byv] = _isl")
        self.indent -= 1
        self.w("try:")
        self.indent += 1
        if body:
            for st in body:
                self.gen_stmt(st)
        else:
            self.w("pass")
        self.indent -= 1
        self.w("except _r._RowReturn:")
        self.indent += 1
        self.w("pass")
        self.indent -= 1
        self.w("except _r._RowDelete:")
        self.indent += 1
        self.w("continue")
        self.indent -= 1
        if not has_explicit_output:
            self.w("for _dsname in _out_rows:")
            self.indent += 1
            self.w("_out_rows[_dsname].append(dict(pdv))")
            self.indent -= 1
        self.indent -= 1  # end for _row

        temp_array_vars = {e for arrstmt in arrays.values() if arrstmt.is_temporary for e in arrstmt.elements}
        auto_drop = (
            {f"first_{v}" for v in by_vars} | {f"last_{v}" for v in by_vars}
            | self.hidden_vars | temp_array_vars
        )
        last_name = None
        for (name, opts, db_info) in ds.outputs:
            keep_list = sorted(keeps | set(opts.get("keep") or []))
            drop_list = sorted(drops | set(opts.get("drop") or []) | (auto_drop - keeps))
            rename_map = opts.get("rename") or {}
            self.w(
                f"_df = _r.finalize_dataset(_out_rows[{name!r}], "
                f"keep={keep_list!r} or None, drop={drop_list!r} or None, "
                f"rename={rename_map!r} or None, fallback_cols=list(pdv.keys()))"
            )
            if name != "_null_":
                self.w(f"_DS[{name!r}] = _df")
                last_name = name
                if formats:
                    ds_formats = {rename_map.get(k, k): v for k, v in formats.items()}
                    self.w(f"_FMT[{name!r}] = {{k: v for k, v in {ds_formats!r}.items() if k in _df.columns}}")
                if labels:
                    ds_labels = {rename_map.get(k, k): v for k, v in labels.items()}
                    self.w(f"_LBL[{name!r}] = {{k: v for k, v in {ds_labels!r}.items() if k in _df.columns}}")
                if db_info:
                    libref, table = db_info
                    self.w(f"_r.db_write_table({libref!r}, {table!r}, _df)")
        self.indent -= 1  # end def
        if last_name:
            self.last_ds_name = last_name
        return fname

    def _gen_ds_opts_expr(self, name: str, opts: dict, db_info=None) -> str:
        if db_info:
            libref, table = db_info
            src_expr = f"_r.db_read_table({libref!r}, {table!r})"
        else:
            src_expr = f"_DS[{name!r}]"
        keep = opts.get("keep") or []
        drop = opts.get("drop") or []
        rename = opts.get("rename") or {}
        where = opts.get("where")
        wherecode = "None"
        if where is not None:
            wherecode = f"(lambda row: _r.truthy({self.gen_expr(where, varmap='row')}))"
        return (
            f"_r.apply_ds_opts({src_expr}, keep={keep!r} or None, "
            f"drop={drop!r} or None, rename={rename!r} or None, where={wherecode})"
        )

    @staticmethod
    def _dict_literal(row: dict) -> str:
        def lit(v):
            if isinstance(v, float) and v != v:
                return "float('nan')"
            return repr(v)
        return "{" + ", ".join(f"{k!r}: {lit(v)}" for k, v in row.items()) + "}"

    @staticmethod
    def _parse_datalines_rows(datalines: A.DatalinesStmt, inp: A.InputStmt) -> list:
        rows = []
        for line in datalines.lines:
            fields = line.split()
            row = {}
            for i, (name, is_char) in enumerate(inp.vars):
                raw = fields[i] if i < len(fields) else ""
                if is_char:
                    row[name] = raw
                else:
                    try:
                        row[name] = float(raw)
                    except ValueError:
                        row[name] = float("nan")
            rows.append(row)
        return rows

    # ---------------- PROC steps ----------------
    def gen_proc_step(self, proc: A.ProcStep) -> str:
        self.step_idx += 1
        fname = f"_step_{self.step_idx}"
        self.w(f"def {fname}():")
        self.indent += 1
        name = proc.name.lower()
        if name == "sql":
            self._gen_proc_sql(proc)
        elif name == "print":
            self._gen_proc_print(proc)
        elif name == "sort":
            self._gen_proc_sort(proc)
        elif name in ("means", "summary"):
            self._gen_proc_means(proc)
        elif name == "freq":
            self._gen_proc_freq(proc)
        elif name == "append":
            self._gen_proc_append(proc)
        elif name == "format":
            self._gen_proc_format(proc)
        elif name == "transpose":
            self._gen_proc_transpose(proc)
        elif name == "import":
            self._gen_proc_import(proc)
        elif name == "export":
            self._gen_proc_export(proc)
        elif name == "datasets":
            self._gen_proc_datasets(proc)
        elif name == "univariate":
            self._gen_proc_univariate(proc)
        elif name == "rank":
            self._gen_proc_rank(proc)
        elif name == "corr":
            self._gen_proc_corr(proc)
        elif name == "reg":
            self._gen_proc_reg(proc)
        elif name == "logistic":
            self._gen_proc_logistic(proc)
        elif name == "glm":
            self._gen_proc_glm(proc)
        elif name == "fastclus":
            self._gen_proc_fastclus(proc)
        else:
            self.w(f"raise NotImplementedError({'PROC ' + name.upper() + ' is not supported by this compiler'!r})")
        self.indent -= 1
        return fname

    def _resolve_ds(self, proc: A.ProcStep) -> str:
        if "data" in proc.options and isinstance(proc.options["data"], str):
            return normalize_dsname(proc.options["data"])
        if self.last_ds_name:
            return self.last_ds_name
        raise CodegenError(f"PROC {proc.name.upper()} has no DATA= and no prior dataset to default to")

    def _clause(self, proc: A.ProcStep, key: str):
        for k, v in proc.clauses:
            if k == key:
                return v
        return None

    @staticmethod
    def _num_lit(v: float) -> str:
        if v == float("inf"):
            return "float('inf')"
        if v == float("-inf"):
            return "float('-inf')"
        return repr(float(v))

    def _gen_proc_format(self, proc: A.ProcStep):
        for (_, is_char, fmtname, entries, other_label) in proc.clauses:
            key = ("$" if is_char else "") + fmtname
            if is_char:
                pairs = ", ".join(f"{v!r}: {lbl!r}" for v, lbl in entries)
                self.w(f"_r.USER_FORMATS[{key!r}] = {{'values': {{{pairs}}}, 'other': {other_label!r}}}")
            else:
                ranges = ", ".join(
                    f"({self._num_lit(lo)}, {self._num_lit(hi)}, {lbl!r})" for lo, hi, lbl in entries
                )
                self.w(f"_r.USER_FORMATS[{key!r}] = {{'ranges': [{ranges}], 'other': {other_label!r}}}")

    def _gen_proc_sql(self, proc: A.ProcStep):
        self.w("_con = duckdb.connect()")
        self.w("for _n, _d in list(_DS.items()):")
        self.indent += 1
        self.w("_con.register(_n, _d)")
        self.indent -= 1
        sqlite_libs = {lr: c for lr, c in self.db_libs.items() if "://" not in c}
        if sqlite_libs:
            self.w("try:")
            self.indent += 1
            self.w("_con.execute('INSTALL sqlite; LOAD sqlite;')")
            for libref, conn in sqlite_libs.items():
                attach_sql = f"ATTACH {conn!r} AS {libref} (TYPE sqlite);"
                self.w(f"_con.execute({attach_sql!r})")
            self.indent -= 1
            self.w("except Exception as _e:")
            self.indent += 1
            self.w("print(f'warning: could not attach SQLite library: {_e}')")
            self.indent -= 1
        for _, stmt in proc.clauses:
            m = re.match(r"(?is)^\s*create\s+table\s+([A-Za-z_][A-Za-z0-9_.]*)\s+as\s+(select.*)$", stmt)
            self.w(f"_res = _con.execute({stmt!r})")
            if m:
                tbl = normalize_dsname(m.group(1))
                self.w(f"_DS[{tbl!r}] = _con.execute({('select * from ' + m.group(1))!r}).df()")
                self.last_ds_name = tbl
            elif re.match(r"(?is)^\s*select", stmt):
                self.w("print(_res.df().to_string(index=False))")

    def _gen_proc_print(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        self.w(f"_df = _DS[{dsname!r}]")
        var_clause = self._clause(proc, "var")
        if var_clause:
            cols = [n for n, _ in var_clause]
            self.w(f"_df = _df[{cols!r}]")
        self.w(f"_fmts = _FMT.get({dsname!r}, {{}})")
        self.w("_pf = _df.copy()")
        self.w("for _c, _fmt in _fmts.items():")
        self.indent += 1
        self.w("if _c in _pf.columns:")
        self.indent += 1
        self.w("_pf[_c] = _pf[_c].map(lambda v: _r.apply_format(v, _fmt))")
        self.indent -= 1
        self.indent -= 1
        self.w("_pf.index = range(1, len(_pf) + 1)")
        self.w("_pf.index.name = 'Obs'")
        self.w(f"_lbls = _LBL.get({dsname!r}, {{}})")
        self.w("if _lbls: _pf = _pf.rename(columns=_lbls)")
        self.w("print(_pf.to_string())")

    def _gen_proc_sort(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        out = normalize_dsname(proc.options["out"]) if isinstance(proc.options.get("out"), str) else dsname
        by_clause = self._clause(proc, "by")
        if not by_clause:
            raise CodegenError("PROC SORT requires a BY statement")
        cols = [n for n, _ in by_clause]
        ascending = [not desc for _, desc in by_clause]
        self.w(f"_df = _DS[{dsname!r}].copy()")
        self.w(f"_df = _df.sort_values(by={cols!r}, ascending={ascending!r}, kind='mergesort')")
        if proc.options.get("nodupkey"):
            self.w(f"_df = _df.drop_duplicates(subset={cols!r}, keep='first')")
        elif proc.options.get("nodup"):
            self.w("_df = _df.drop_duplicates(keep='first')")
        self.w("_df = _df.reset_index(drop=True)")
        self.w(f"_DS[{out!r}] = _df")
        if out != dsname:
            self.w(f"if {dsname!r} in _FMT: _FMT[{out!r}] = _FMT[{dsname!r}]")
            self.w(f"if {dsname!r} in _LBL: _LBL[{out!r}] = _LBL[{dsname!r}]")
        self.last_ds_name = out

    def _requested_stats(self, proc: A.ProcStep) -> list:
        requested = [s for s in _STAT_EXPR if proc.options.get(s) is True]
        return requested or list(_DEFAULT_MEANS_STATS)

    def _gen_proc_means(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        class_clause = self._clause(proc, "class") or self._clause(proc, "by")
        var_clause = self._clause(proc, "var")
        output_clause = self._clause(proc, "output")
        stat_names = self._requested_stats(proc)
        self.w(f"_df = _DS[{dsname!r}]")
        if var_clause:
            var_list = [n for n, _ in var_clause]
        else:
            self.w("_var_list = [c for c in _df.columns if pd.api.types.is_numeric_dtype(_df[c])]")
            var_list = None
        vl = repr(var_list) if var_list is not None else "_var_list"
        class_list = [n for n, _ in class_clause] if class_clause else []

        self.w("_rows = []")
        if class_list:
            self.w(f"for _key, _grp in _df.groupby({class_list!r}, dropna=False):")
            self.indent += 1
            self.w("_key = _key if isinstance(_key, tuple) else (_key,)")
            self.w(f"_row = dict(zip({class_list!r}, _key))")
        else:
            self.w("for _grp in [_df]:")
            self.indent += 1
            self.w("_row = {}")
        self.w(f"for _v in {vl}:")
        self.indent += 1
        self.w("_s = _grp[_v].dropna()")
        for stat in stat_names:
            self.w(f"_row[_v + '_{stat}'] = {_STAT_EXPR[stat]}")
        self.indent -= 1
        self.w("_rows.append(_row)")
        self.indent -= 1

        self.w("print(pd.DataFrame(_rows).to_string(index=False))")
        if output_clause and output_clause.get("out"):
            self._apply_output_rename(output_clause, var_list)
            out = output_clause["out"]
            self.w(f"_DS[{out!r}] = pd.DataFrame(_rows)")
            self.last_ds_name = out

    def _apply_output_rename(self, output_clause: dict, var_list):
        renames = {}
        for entry in output_clause.get("stats", []):
            statkw, var, newname_or_names = entry
            if var is not None:
                if newname_or_names and newname_or_names != statkw:
                    renames[f"{var}_{statkw}"] = newname_or_names
            elif var_list and isinstance(newname_or_names, list):
                for v, newname in zip(var_list, newname_or_names):
                    if newname and newname != statkw:
                        renames[f"{v}_{statkw}"] = newname
        if renames:
            mapping_lit = repr(renames)
            line = ("_rows = [{" + mapping_lit + ".get(k, k): v for k, v in r.items()} for r in _rows]")
            self.w(line)

    def _gen_proc_freq(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        self.w(f"_df = _DS[{dsname!r}]")
        tables_raw = self._clause(proc, "tables")
        if not tables_raw:
            var_clause = self._clause(proc, "var") or []
            tables_raw = " ".join(n for n, _ in var_clause)
        table_part = tables_raw.split("/")[0].strip() if tables_raw else ""
        for req in table_part.split():
            req = req.strip()
            if not req:
                continue
            if "*" in req:
                v1, v2 = [x.strip() for x in req.split("*", 1)]
                self.w(f"print(pd.crosstab(_df[{v1!r}], _df[{v2!r}]))")
            else:
                self.w(f"print(_df[{req!r}].value_counts(dropna=False))")

    def _gen_proc_transpose(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        out = normalize_dsname(proc.options["out"]) if isinstance(proc.options.get("out"), str) else f"{dsname}_transposed"
        by_clause = self._clause(proc, "by")
        var_clause = self._clause(proc, "var")
        id_clause = self._clause(proc, "id")
        by_vars = [n for n, _ in by_clause] if by_clause else []
        var_list = [n for n, _ in var_clause] if var_clause else None
        idvar = id_clause[0][0] if id_clause else None

        self.w(f"_df = _DS[{dsname!r}]")
        self.w(f"_byvars = {by_vars!r}")
        self.w(f"_varlist_fixed = {var_list!r}")
        self.w(f"_idvar = {idvar!r}")
        self.w("if _byvars:")
        self.indent += 1
        self.w("_group_iter = list(_df.groupby(_byvars, dropna=False, sort=False))")
        self.indent -= 1
        self.w("else:")
        self.indent += 1
        self.w("_group_iter = [((), _df)]")
        self.indent -= 1
        self.w("_rows = []")
        self.w("for _key, _grp in _group_iter:")
        self.indent += 1
        self.w("_key = _key if isinstance(_key, tuple) else (_key,)")
        self.w("_recs = _grp.to_dict('records')")
        self.w("if _varlist_fixed is not None:")
        self.indent += 1
        self.w("_vlist = _varlist_fixed")
        self.indent -= 1
        self.w("else:")
        self.indent += 1
        self.w(
            "_vlist = [c for c in _grp.columns if c not in _byvars "
            "and c != _idvar and pd.api.types.is_numeric_dtype(_grp[c])]"
        )
        self.indent -= 1
        self.w("if _idvar:")
        self.indent += 1
        self.w("_row = dict(zip(_byvars, _key))")
        self.w("for _rec in _recs:")
        self.indent += 1
        self.w("_colname = _r.sas_text(_rec.get(_idvar))")
        self.w("for _v in _vlist:")
        self.indent += 1
        self.w("_row[_colname] = _rec.get(_v)")
        self.indent -= 1
        self.indent -= 1
        self.w("_rows.append(_row)")
        self.indent -= 1
        self.w("else:")
        self.indent += 1
        self.w("for _v in _vlist:")
        self.indent += 1
        self.w("_row = dict(zip(_byvars, _key))")
        self.w("_row['_name_'] = _v")
        self.w("for _i, _rec in enumerate(_recs):")
        self.indent += 1
        self.w("_row[f'col{_i + 1}'] = _rec.get(_v)")
        self.indent -= 1
        self.w("_rows.append(_row)")
        self.indent -= 1
        self.indent -= 1
        self.indent -= 1
        self.w(f"_DS[{out!r}] = pd.DataFrame(_rows)")
        self.last_ds_name = out

    def _gen_proc_import(self, proc: A.ProcStep):
        datafile = proc.options.get("datafile")
        out = proc.options.get("out")
        if not isinstance(datafile, str) or not isinstance(out, str):
            raise CodegenError("PROC IMPORT requires DATAFILE= and OUT=")
        out = normalize_dsname(out)
        dbms = str(proc.options.get("dbms", "csv")).lower()
        if dbms not in ("csv", "dlm", "tab"):
            raise CodegenError(f"PROC IMPORT: DBMS={dbms.upper()} is not supported (use CSV)")
        sep = "\t" if dbms == "tab" else ","
        self.w(f"_df = pd.read_csv({datafile!r}, sep={sep!r})")
        self.w("_df.columns = [str(c).strip().lower() for c in _df.columns]")
        self.w(f"_DS[{out!r}] = _df")
        self.last_ds_name = out

    def _gen_proc_export(self, proc: A.ProcStep):
        outfile = proc.options.get("outfile")
        dsname = self._resolve_ds(proc)
        if not isinstance(outfile, str):
            raise CodegenError("PROC EXPORT requires OUTFILE=")
        dbms = str(proc.options.get("dbms", "csv")).lower()
        if dbms not in ("csv", "dlm", "tab"):
            raise CodegenError(f"PROC EXPORT: DBMS={dbms.upper()} is not supported (use CSV)")
        sep = "\t" if dbms == "tab" else ","
        self.w(f"_DS[{dsname!r}].to_csv({outfile!r}, sep={sep!r}, index=False)")

    def _gen_proc_datasets(self, proc: A.ProcStep):
        for (kind, payload) in proc.clauses:
            if kind == "delete":
                for name in payload:
                    self.w(f"_DS.pop({name!r}, None)")
                    self.w(f"_FMT.pop({name!r}, None)")
                    self.w(f"_LBL.pop({name!r}, None)")
            elif kind == "change":
                for old, new in payload:
                    self.w(f"if {old!r} in _DS: _DS[{new!r}] = _DS.pop({old!r})")
                    self.w(f"if {old!r} in _FMT: _FMT[{new!r}] = _FMT.pop({old!r})")
                    self.w(f"if {old!r} in _LBL: _LBL[{new!r}] = _LBL.pop({old!r})")
                    self.last_ds_name = new

    def _gen_proc_univariate(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        self.w(f"_df = _DS[{dsname!r}]")
        if var_clause:
            var_list = [n for n, _ in var_clause]
        else:
            self.w("_var_list = [c for c in _df.columns if pd.api.types.is_numeric_dtype(_df[c])]")
            var_list = None
        vl = repr(var_list) if var_list is not None else "_var_list"
        self.w(f"for _v in {vl}:")
        self.indent += 1
        self.w("_s = _df[_v].dropna()")
        self.w("print(f'Variable: {_v}')")
        self.w("print('Moments:')")
        self.w("print(f'  N                {len(_s)}')")
        self.w("print(f'  Mean             {_s.mean() if len(_s) else float(\"nan\")}')")
        self.w("print(f'  Std Deviation    {_s.std() if len(_s) > 1 else float(\"nan\")}')")
        self.w("print(f'  Variance         {_s.var() if len(_s) > 1 else float(\"nan\")}')")
        self.w("print(f'  Skewness         {_s.skew() if len(_s) > 2 else float(\"nan\")}')")
        self.w("print(f'  Kurtosis         {_s.kurt() if len(_s) > 3 else float(\"nan\")}')")
        self.w("_mode = _s.mode()")
        self.w("print(f'  Mode             ' + (str(_mode.iloc[0]) if len(_mode) else \".\"))")
        self.w("print('Quantiles:')")
        self.w("for _q in (100, 99, 95, 90, 75, 50, 25, 10, 5, 1, 0):")
        self.indent += 1
        self.w("_qv = _s.max() if _q == 100 else (_s.min() if _q == 0 else _s.quantile(_q / 100))")
        self.w("print(f'  {_q:>3}%  {_qv}')")
        self.indent -= 1
        self.w("_sorted = _s.sort_values()")
        self.w("print('Extreme Observations (lowest / highest):')")
        self.w("print('  lowest: ' + ', '.join(str(v) for v in _sorted.head(5).tolist()))")
        self.w("print('  highest: ' + ', '.join(str(v) for v in _sorted.tail(5).tolist()))")
        self.w("print()")
        self.indent -= 1

    @staticmethod
    def _parse_model_stmt(raw: str):
        m = re.match(r"^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*=\s*(.+)$", raw, re.S)
        if not m:
            raise CodegenError(f"could not parse MODEL statement: {raw!r}")
        y = m.group(1).lower()
        rhs = m.group(2)
        xs = [tok.lower() for tok in re.split(r"[\s+]+", rhs.strip()) if tok and tok != "+"]
        return y, xs

    @staticmethod
    def _output_stat_dict(output_clause: dict) -> dict:
        out_stats: dict = {}
        for (statkw, var, names) in output_clause.get("stats", []):
            if var is None and isinstance(names, list):
                out_stats.setdefault(statkw, []).extend(names)
        return out_stats

    def _gen_proc_corr(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        self.w(f"_df = _DS[{dsname!r}]")
        if var_clause:
            cols = [n for n, _ in var_clause]
        else:
            self.w("_cols = [c for c in _df.columns if pd.api.types.is_numeric_dtype(_df[c])]")
            cols = None
        cl = repr(cols) if cols is not None else "_cols"
        self.w(f"_corr = _r.proc_corr_report(_df, {cl})")
        out = proc.options.get("out") or proc.options.get("outp")
        if isinstance(out, str):
            out = normalize_dsname(out)
            self.w("_corr_out = _corr.reset_index().rename(columns={'index': '_name_'})")
            self.w(f"_DS[{out!r}] = _corr_out")
            self.last_ds_name = out

    def _gen_proc_reg(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        model_raw = self._clause(proc, "model")
        if not model_raw:
            raise CodegenError("PROC REG requires a MODEL statement")
        y, xs = self._parse_model_stmt(model_raw)
        output_clause = self._clause(proc, "output")
        self.w(f"_df = _DS[{dsname!r}]")
        out_stats_lit = "None"
        out = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            out_stats_lit = repr(self._output_stat_dict(output_clause))
        self.w(f"_scored = _r.proc_reg_fit(_df, {y!r}, {xs!r}, out_stats={out_stats_lit})")
        if out:
            self.w(f"_DS[{out!r}] = _scored")
            self.last_ds_name = out

    def _gen_proc_logistic(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        model_raw = self._clause(proc, "model")
        if not model_raw:
            raise CodegenError("PROC LOGISTIC requires a MODEL statement")
        y, xs = self._parse_model_stmt(model_raw)
        output_clause = self._clause(proc, "output")
        self.w(f"_df = _DS[{dsname!r}]")
        out_stats_lit = "None"
        out = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            out_stats_lit = repr(self._output_stat_dict(output_clause))
        self.w(f"_scored = _r.proc_logistic_fit(_df, {y!r}, {xs!r}, out_stats={out_stats_lit})")
        if out:
            self.w(f"_DS[{out!r}] = _scored")
            self.last_ds_name = out

    def _gen_proc_glm(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        model_raw = self._clause(proc, "model")
        if not model_raw:
            raise CodegenError("PROC GLM requires a MODEL statement")
        y, xs = self._parse_model_stmt(model_raw)
        class_clause = self._clause(proc, "class")
        class_vars = [n for n, _ in class_clause] if class_clause else []
        output_clause = self._clause(proc, "output")
        self.w(f"_df = _DS[{dsname!r}]")
        out_stats_lit = "None"
        out = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            out_stats_lit = repr(self._output_stat_dict(output_clause))
        self.w(
            f"_scored = _r.proc_glm_fit(_df, {y!r}, {xs!r}, {class_vars!r}, "
            f"out_stats={out_stats_lit})"
        )
        if out:
            self.w(f"_DS[{out!r}] = _scored")
            self.last_ds_name = out

    def _gen_proc_fastclus(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        if not var_clause:
            raise CodegenError("PROC FASTCLUS requires a VAR statement")
        var_list = [n for n, _ in var_clause]
        k = int(proc.options.get("maxclusters", 2))
        output_clause = self._clause(proc, "output")
        out = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
        elif isinstance(proc.options.get("out"), str):
            out = normalize_dsname(proc.options["out"])
        self.w(f"_df = _DS[{dsname!r}]")
        self.w(f"_clustered = _r.proc_fastclus_fit(_df, {var_list!r}, {k})")
        if out:
            self.w(f"_DS[{out!r}] = _clustered")
            self.last_ds_name = out

    def _gen_proc_rank(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        out = normalize_dsname(proc.options["out"]) if isinstance(proc.options.get("out"), str) else dsname
        var_clause = self._clause(proc, "var")
        if not var_clause:
            raise CodegenError("PROC RANK requires a VAR statement")
        var_list = [n for n, _ in var_clause]
        ranks_clause = self._clause(proc, "ranks")
        rank_names = ranks_clause if ranks_clause else var_list
        by_clause = self._clause(proc, "by")
        ascending = not proc.options.get("descending")

        self.w(f"_df = _DS[{dsname!r}].copy()")
        if by_clause:
            by_list = [n for n, _ in by_clause]
            self.w(f"_rank_src = _df.groupby({by_list!r}, dropna=False)[{var_list!r}]")
        else:
            self.w(f"_rank_src = _df[{var_list!r}]")
        self.w(f"_ranked = _rank_src.rank(method='average', ascending={ascending!r}, na_option='keep')")
        for v, rn in zip(var_list, rank_names):
            self.w(f"_df[{rn!r}] = _ranked[{v!r}]")
        self.w(f"_DS[{out!r}] = _df")
        self.last_ds_name = out

    def _gen_proc_append(self, proc: A.ProcStep):
        base = normalize_dsname(proc.options["base"]) if isinstance(proc.options.get("base"), str) else None
        data = normalize_dsname(proc.options["data"]) if isinstance(proc.options.get("data"), str) else None
        if not base or not data:
            raise CodegenError("PROC APPEND requires BASE= and DATA=")
        self.w(f"_DS[{base!r}] = pd.concat([_DS[{base!r}], _DS[{data!r}]], ignore_index=True)")
        self.last_ds_name = base


def generate(prog: A.Program) -> str:
    return CodeGen().generate(prog)
