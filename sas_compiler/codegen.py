"""AST -> Python (pandas/duckdb) code generator."""
from __future__ import annotations

import itertools
import os
import re

from . import ast_nodes as A
from .parser import normalize_dsname


class CodegenError(Exception):
    pass


# PROC CLUSTER METHOD= (SAS name, lowercased) -> scipy.cluster.hierarchy.linkage
# method= name. Kept in sync with the mapping validated again in
# runtime.proc_cluster_report (which may be called directly).
_CLUSTER_METHODS = {
    "average": "average",
    "ward": "ward",
    "wards": "ward",
    "single": "single",
    "complete": "complete",
    "centroid": "centroid",
}


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
    "time": "time_", "datetime": "datetime_",
    "hour": "hour_", "minute": "minute_", "second": "second_",
    "reverse": "reverse_", "quote": "quote_",
}

_DIRECT_FUNCS = {
    "substr", "upcase", "lowcase", "propcase", "trim", "strip", "left",
    "compress", "length", "lengthn", "scan", "countw", "cat", "catx",
    "cats", "tranwrd", "translate", "verify", "prxmatch",
    "compbl", "findc", "findw", "dequote",
    "indexc", "today", "intck", "intnx", "coalesce",
    "coalescec", "ifn", "ifc", "put", "sas_date",
    "sas_time", "sas_datetime", "datepart", "timepart", "dhms", "hms",
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
        elif isinstance(s, A.SelectStmt):
            for (_, body) in s.whens:
                yield from walk_stmts(body)
            yield from walk_stmts(s.otherwise)


def _rewrite_expr(e: A.Expr, arrname: str, idxvar: str) -> A.Expr:
    """Return a copy of expr `e` with every bare reference to array
    `arrname` replaced by an indexed reference using `idxvar` -- used to
    implement DO OVER, where the array name alone means "current element"."""
    if isinstance(e, A.Var):
        if e.name == arrname:
            return A.ArrayRef(name=arrname, index=A.Var(idxvar))
        return e
    if isinstance(e, A.ArrayRef):
        if e.indices is not None:
            return A.ArrayRef(name=e.name, indices=[_rewrite_expr(i, arrname, idxvar) for i in e.indices])
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
    if isinstance(s, A.SelectStmt):
        return A.SelectStmt(
            select_expr=_rewrite_expr(s.select_expr, arrname, idxvar) if s.select_expr is not None else None,
            whens=[([_rewrite_expr(c, arrname, idxvar) for c in conds],
                    [_rewrite_stmt(st, arrname, idxvar) for st in body])
                   for (conds, body) in s.whens],
            otherwise=[_rewrite_stmt(st, arrname, idxvar) for st in s.otherwise],
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
        self.sgplot_counter = 0  # for default OUT= filenames (sgplot_1.png, ...)
        self.loop_stack: list = []
        self.fcmp_functions: set = set()  # PROC FCMP function names, callable by name
        self.fcmp_char_functions: set = set()  # ...and which of those return character
        self.fcmp_array_param_positions: dict = {}  # fname -> set of 0-based ARRAY-param arg positions
        self.fcmp_array_params: set = set()  # names of array params of the FCMP function body currently being generated
        self.fcmp_outargs: dict = {}  # fname -> list of OUTARGS parameter names (in order), for routines that have any

    def w(self, line: str):
        self.lines.append(("    " * self.indent) + line)

    def newtmp(self, prefix="t") -> str:
        self.tmp += 1
        return f"_{prefix}{self.tmp}"

    def _arr_lo(self, name: str) -> int:
        arrstmt = self.current_arrays.get(name)
        return arrstmt.lo_bound if arrstmt is not None else 1

    def _arr_offset_expr(self, name: str, index_exprs: list, varmap: str = "pdv") -> str:
        """Python expr computing a flat 0-based offset into _ARR_<name> from
        one or more SAS subscripts, using row-major layout for multi-dim
        arrays: offset = sum((idx_k - lo_k) * product(sizes after dim k))."""
        arrstmt = self.current_arrays.get(name)
        dims = arrstmt.dims if arrstmt is not None else None
        if dims is None or len(index_exprs) == 1:
            # A single subscript: either a genuinely one-dimensional array,
            # or a flat offset into a multi-dim array's underlying storage
            # (used internally by DO OVER, which iterates the flat elements
            # list rather than per-dimension subscripts).
            if len(index_exprs) != 1:
                raise CodegenError(f"array {name!r} is one-dimensional but was given {len(index_exprs)} subscripts")
            ix = self.gen_expr(index_exprs[0], varmap)
            lo = self._arr_lo(name)
            return f"int({ix}) - {lo}"
        if len(index_exprs) != len(dims):
            raise CodegenError(f"array {name!r} has {len(dims)} dimension(s) but was given {len(index_exprs)} subscripts")
        parts = []
        for i, (idx_expr, (_size, lo)) in enumerate(zip(index_exprs, dims)):
            ix = self.gen_expr(idx_expr, varmap)
            mult = 1
            for (sz2, _lo2) in dims[i + 1:]:
                mult *= sz2
            term = f"(int({ix}) - {lo})"
            parts.append(f"{term} * {mult}" if mult != 1 else term)
        return " + ".join(parts)

    def _fcmp_array_index_code(self, name: str, indices, index, varmap: str) -> str:
        """Python expr for the single SAS subscript of a PROC FCMP `arr[*]`
        array parameter -- these are plain 1-based Python lists with no
        bounds info, so only a single dimension is supported."""
        idxs = indices if indices is not None else [index]
        if len(idxs) != 1:
            raise CodegenError(f"PROC FCMP array parameter {name!r} only supports 1-D indexing (arr[*])")
        return self.gen_expr(idxs[0], varmap)

    def generate(self, prog: A.Program, nested: bool = False) -> str:
        self.w("import sas_compiler.runtime as _r")
        self.w("import pandas as pd")
        self.w("import duckdb")
        if not nested:
            # `nested=True` (used by CALL EXECUTE's runtime drain) skips
            # these: the caller injects the SAME dict/list objects into
            # the exec() namespace so the queued snippet shares the
            # outer program's WORK library instead of getting a fresh one.
            self.w("_DS = {}")
            self.w("_FMT = {}")
            self.w("_LBL = {}")
            self.w("_TITLES = [''] * 10")
            self.w("_FOOTNOTES = [''] * 10")
        self.w("")
        fnames = []
        for step in prog.steps:
            if isinstance(step, A.DataStep):
                fnames.append(self.gen_data_step(step))
            elif isinstance(step, A.ProcStep) and step.name.lower() == "fcmp":
                # Function defs, not a runnable step: emitted directly at
                # module level (not wrapped in a _step_N() to call later)
                # so later DATA steps can call them by name.
                self.gen_proc_fcmp(step)
            elif isinstance(step, A.ProcStep):
                fnames.append(self.gen_proc_step(step))
            elif isinstance(step, A.LibnameStmt):
                fnames.append(self.gen_libname_step(step))
            elif isinstance(step, A.TitleStmt):
                fnames.append(self.gen_title_step(step))
            elif isinstance(step, A.OdsStmt):
                fnames.append(self.gen_ods_step(step))
        self.w("")
        for fn in fnames:
            self.w(f"{fn}()")
            # CALL EXECUTE queues raw SAS text to run after the current
            # step finishes and before the next one; drain it here.
            self.w("if _r._EXECUTE_QUEUE:")
            self.indent += 1
            self.w("_r.drain_execute_queue(_DS, _FMT, _LBL, _TITLES, _FOOTNOTES)")
            self.indent -= 1
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
            if e.name in self.fcmp_array_params:
                idx_code = self._fcmp_array_index_code(e.name, e.indices, e.index, varmap)
                return f"{e.name}[int({idx_code})-1]"
            idx = self._arr_offset_expr(e.name, e.indices if e.indices is not None else [e.index], varmap)
            return f"{varmap}.get(_ARR_{e.name}[{idx}], _r.MISSING)"
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
        if isinstance(e, A.HashMethodCall):
            return self._gen_hash_method(e, varmap)
        raise CodegenError(f"cannot generate expression for {e!r}")

    def _gen_hash_method(self, e: A.HashMethodCall, varmap: str) -> str:
        hashref = f"_hashes[{e.hashname!r}]"
        method = e.method.lower()
        if method in ("definekey", "definedata"):
            names = []
            for (_, expr) in e.args:
                if not isinstance(expr, A.Str):
                    raise CodegenError(f"hash .{method}() expects string literal argument(s)")
                names.append(repr(expr.value.lower()))
            return f"{hashref}.{method}({', '.join(names)})"
        if method == "definedone":
            return f"{hashref}.definedone()"
        if method in ("find", "check", "remove"):
            key_exprs = [self.gen_expr(v, varmap) for (k, v) in e.args if (k or "").lower() == "key"]
            if key_exprs:
                return f"{hashref}.{method}({varmap}, key_values=[{', '.join(key_exprs)}])"
            return f"{hashref}.{method}({varmap})"
        if method == "add":
            return f"{hashref}.add({varmap})"
        if method == "clear":
            return f"{hashref}.clear()"
        if method in ("first", "last", "next", "prev"):
            return f"{hashref}.{method}({varmap})"
        if method == "output":
            raise CodegenError(
                "hash .OUTPUT(dataset: \"name\") must be used as a standalone "
                "statement, not assigned to a variable"
            )
        raise CodegenError(f"unsupported hash object method .{e.method}()")

    def _gen_hash_output(self, e: A.HashMethodCall):
        ds_arg = None
        for (argname, expr) in e.args:
            if (argname or "").lower() == "dataset":
                if not isinstance(expr, A.Str):
                    raise CodegenError('hash .output() expects dataset: "name"')
                ds_arg = normalize_dsname(expr.value)
        if not ds_arg:
            raise CodegenError('hash .output() requires dataset: "name"')
        self.w(f"_DS[{ds_arg!r}] = _hashes[{e.hashname!r}].output()")
        self.last_ds_name = ds_arg

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
        if e.op == "contains":
            return f"_r.contains({left}, {right})"
        if e.op == "like":
            return f"_r.sas_like({left}, {right})"
        cmp_map = {"=": "eq", "^=": "ne", "~=": "ne", "<": "lt", ">": "gt", "<=": "le", ">=": "ge"}
        if e.op in cmp_map:
            return f"_r.{cmp_map[e.op]}({left}, {right})"
        raise CodegenError(f"unsupported operator {e.op!r}")

    def _fcmp_call_args_code(self, name: str, args: list, varmap: str) -> str:
        """Build the comma-joined Python argument-expression list for a call
        to PROC FCMP function/subroutine `name`, handling ARRAY-parameter
        positions (which require a bare declared-ARRAY name at the call
        site and pass a copied-in list of its current element values)
        exactly like the plain-scalar-argument case. Shared by both the
        expression call site (_gen_call) and the CALL-statement call site
        (_gen_call_stmt) so the two never drift apart."""
        array_positions = self.fcmp_array_param_positions.get(name)
        if not array_positions:
            return ", ".join(self.gen_expr(a, varmap) for a in args)
        arg_parts = []
        for i, a in enumerate(args):
            if i in array_positions:
                if not (isinstance(a, A.Var) and a.name in self.current_arrays):
                    raise CodegenError(
                        f"call to {name}(): argument {i + 1} must be a bare ARRAY name "
                        f"declared with ARRAY in this DATA step -- PROC FCMP array "
                        f"parameters only accept a previously declared SAS ARRAY, "
                        f"passed by value (its current element values are copied in; "
                        f"mutations inside the function do not propagate back)"
                    )
                arg_parts.append(f"[{varmap}.get(v, _r.MISSING) for v in _ARR_{a.name}]")
            else:
                arg_parts.append(self.gen_expr(a, varmap))
        return ", ".join(arg_parts)

    def _gen_call(self, e: A.Call, varmap: str) -> str:
        name = e.name.lower()
        m = re.match(r"^dif(\d*)$", name)
        if m:
            depth = int(m.group(1)) if m.group(1) else 1
            if not e.args:
                raise CodegenError("dif() requires an argument")
            key = e.args[0].name if isinstance(e.args[0], A.Var) else f"expr{id(e)}"
            argcode = self.gen_expr(e.args[0], varmap)
            return f"_r.dif_(_lag, {key!r}, {argcode}, {depth})"
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
            arrname = e.args[0].name
            if arrname in self.fcmp_array_params:
                if name == "dim":
                    return f"float(len({arrname}))"
                raise CodegenError(
                    f"{name}({arrname}): bounds are unknown for a PROC FCMP array parameter "
                    f"(passed by value, with no bounds info) -- use DIM() instead"
                )
            arrstmt = self.current_arrays.get(arrname)
            if arrstmt is not None:
                dim_arg = None
                if len(e.args) >= 2:
                    if not isinstance(e.args[1], A.Num):
                        raise CodegenError(f"{name}(): dimension-number argument must be a constant")
                    dim_arg = int(e.args[1].value)
                if arrstmt.dims is not None:
                    if dim_arg is None:
                        if name == "dim":
                            return repr(float(arrstmt.dim))
                        raise CodegenError(
                            f"{name}({arrname}) needs an explicit dimension number for "
                            f"multi-dimensional array {arrname!r}, e.g. {name}({arrname}, 1)"
                        )
                    if not (1 <= dim_arg <= len(arrstmt.dims)):
                        raise CodegenError(f"array {arrname!r} has no dimension {dim_arg}")
                    size, lo = arrstmt.dims[dim_arg - 1]
                    if name == "lbound":
                        return repr(float(lo))
                    if name == "hbound":
                        return repr(float(lo + size - 1))
                    return repr(float(size))
                if name == "lbound":
                    return repr(float(arrstmt.lo_bound))
                if name == "hbound":
                    return repr(float(arrstmt.lo_bound + arrstmt.dim - 1))
                return repr(float(arrstmt.dim))
        if name in self.fcmp_functions:
            if self.fcmp_outargs.get(name):
                raise CodegenError(
                    f"PROC FCMP subroutine {name!r} has OUTARGS and must be invoked via "
                    f"CALL {name}(...), not used as an expression"
                )
            args_code = self._fcmp_call_args_code(name, e.args, varmap)
            return f"_fcmp_{name}({args_code})"
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
        if isinstance(e, A.Call) and (e.name.lower() in CHAR_FUNCS or e.name.lower() in self.fcmp_char_functions):
            return True
        return False

    # ---------------- statements ----------------
    def gen_stmt(self, s):
        if isinstance(s, A.Assign):
            self._gen_assign(s)
        elif isinstance(s, A.If):
            self._gen_if(s)
        elif isinstance(s, A.SelectStmt):
            self._gen_select(s)
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
        elif isinstance(s, A.FcmpReturnStmt):
            self.w(f"pdv['__ret__'] = {self.gen_expr(s.expr)}")
            self.w("raise _r._RowReturn()")
        elif isinstance(s, A.StopStmt):
            self.w("raise _r._DataStop()")
        elif isinstance(s, A.AbortStmt):
            msg = f": {s.message}" if s.message else ""
            self.w(f"raise RuntimeError('DATA step ABORT{msg}')")
        elif isinstance(s, A.LeaveStmt):
            if not self.loop_stack:
                raise CodegenError("LEAVE outside a DO loop")
            self.w("break")
        elif isinstance(s, A.ContinueStmt):
            self._gen_continue()
        elif isinstance(s, A.ExprStmt):
            if isinstance(s.expr, A.HashMethodCall) and s.expr.method.lower() == "output":
                self._gen_hash_output(s.expr)
            else:
                self.w(self.gen_expr(s.expr))
        elif isinstance(s, A.DeclareHashStmt):
            self._gen_declare_hash(s)
        elif isinstance(s, A.DeclareHiterStmt):
            self.w(f"_hashes[{s.itername!r}] = _r.SasHIter(_hashes.get({s.hashname!r}))")
        elif isinstance(s, A.FileStmt):
            self._gen_file(s)
        elif isinstance(s, (A.MergeStmt, A.UpdateStmt, A.WhereStmt, A.InputStmt,
                              A.InfileStmt, A.DatalinesStmt)):
            raise CodegenError(
                f"{type(s).__name__} may only appear at the top level of a DATA step, "
                "not nested inside IF/DO"
            )
        elif isinstance(s, A.SetStmt):
            self._gen_nested_set(s)
        elif isinstance(s, (A.ArrayStmt, A.RetainStmt, A.DropStmt, A.KeepStmt,
                             A.LengthStmt, A.FormatStmt, A.LabelStmt, A.ByStmt)):
            pass  # declarative; only meaningful at top level, already handled there
        else:
            raise CodegenError(f"cannot generate statement for {s!r}")

    def _gen_declare_hash(self, s: A.DeclareHashStmt):
        ds_arg = None
        multi = False
        for (argname, expr) in s.args:
            if (argname or "").lower() == "dataset":
                if not isinstance(expr, A.Str):
                    raise CodegenError("declare hash: dataset: expects a string literal")
                ds_arg = normalize_dsname(expr.value)
            elif (argname or "").lower() == "multidata":
                multi = isinstance(expr, A.Str) and expr.value.lower().startswith("y")
        if ds_arg:
            self.w(f"_hashes[{s.hashname!r}] = _r.SasHash(_DS.get({ds_arg!r}), multi={multi!r})")
        else:
            self.w(f"_hashes[{s.hashname!r}] = _r.SasHash(multi={multi!r})")

    def _gen_assign(self, s: A.Assign):
        expr_code = self.gen_expr(s.expr)
        if isinstance(s.target, A.Var):
            self.w(f"pdv[{s.target.name!r}] = {expr_code}")
        elif isinstance(s.target, A.ArrayRef):
            if s.target.name in self.fcmp_array_params:
                idx_code = self._fcmp_array_index_code(s.target.name, s.target.indices, s.target.index, "pdv")
                self.w(f"{s.target.name}[int({idx_code})-1] = {expr_code}")
            else:
                idx = self._arr_offset_expr(s.target.name, s.target.indices if s.target.indices is not None else [s.target.index])
                self.w(f"pdv[_ARR_{s.target.name}[{idx}]] = {expr_code}")
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

    def _gen_select(self, s: A.SelectStmt):
        selvar = None
        if s.select_expr is not None:
            selvar = self.newtmp("sel")
            self.w(f"{selvar} = {self.gen_expr(s.select_expr)}")
        first = True
        for (conds, body) in s.whens:
            if selvar is not None:
                tests = " or ".join(
                    f"_r.truthy(_r.eq({selvar}, {self.gen_expr(c)}))" for c in conds
                ) or "False"
            else:
                tests = " or ".join(
                    f"_r.truthy({self.gen_expr(c)})" for c in conds
                ) or "False"
            self.w(f"{'if' if first else 'elif'} {tests}:")
            first = False
            self.indent += 1
            if body:
                for st in body:
                    self.gen_stmt(st)
            else:
                self.w("pass")
            self.indent -= 1
        if s.otherwise:
            self.w("else:" if not first else "if True:")
            self.indent += 1
            for st in s.otherwise:
                self.gen_stmt(st)
            self.indent -= 1

    def _gen_continue(self):
        if not self.loop_stack:
            raise CodegenError("CONTINUE outside a DO loop")
        top = self.loop_stack[-1]
        kind = top["kind"]
        if kind == "iterative":
            self.w(f"{top['v']} = {top['v']} + {top['by']}")
            self.w("continue")
        elif kind == "until":
            self.w(f"if _r.truthy({top['cond']}):")
            self.indent += 1
            self.w("break")
            self.indent -= 1
            self.w("continue")
        else:
            self.w("continue")

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
            self.loop_stack.append({"kind": "over"})
            try:
                for st in s.body:
                    self.gen_stmt(_rewrite_stmt(st, arrname, idxkey))
            finally:
                self.loop_stack.pop()
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
            self.loop_stack.append({"kind": "iterative", "v": v, "by": by})
            try:
                for st in s.body:
                    self.gen_stmt(st)
            finally:
                self.loop_stack.pop()
            self.w(f"{v} = {v} + {by}")
            self.indent -= 1
            return
        if s.kind == "while":
            self.w(f"while _r.truthy({self.gen_expr(s.cond)}):")
            self.indent += 1
            self.loop_stack.append({"kind": "while"})
            try:
                for st in s.body:
                    self.gen_stmt(st)
            finally:
                self.loop_stack.pop()
            self.indent -= 1
            return
        if s.kind == "until":
            cond_code = self.gen_expr(s.cond)
            self.w("while True:")
            self.indent += 1
            self.loop_stack.append({"kind": "until", "cond": cond_code})
            try:
                for st in s.body:
                    self.gen_stmt(st)
            finally:
                self.loop_stack.pop()
            self.w(f"if _r.truthy({cond_code}):")
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

    def _gen_file(self, s: A.FileStmt):
        if s.path.lower() in ("log", "print"):
            self.w("_put_target = None")
            return
        self.w(f"if {s.path!r} not in _file_handles:")
        self.indent += 1
        mode = "a" if s.mod else "w"
        self.w(f"_file_handles[{s.path!r}] = open({s.path!r}, {mode!r})")
        self.indent -= 1
        self.w(f"_put_target = _file_handles[{s.path!r}]")
        self.w(f"_put_sep = {(s.dlm or ' ')!r}")

    def _gen_put(self, s: A.PutStmt):
        parts = []
        for a in s.args:
            if isinstance(a, A.Str):
                parts.append(repr(a.value))
            else:
                parts.append(f"_r.sas_str({self.gen_expr(a)})")
        self.w("if _put_target is not None:")
        self.indent += 1
        if parts:
            self.w(f"_put_target.write(_put_sep.join([{', '.join(parts)}]) + '\\n')")
        else:
            self.w("_put_target.write('\\n')")
        self.indent -= 1
        self.w("else:")
        self.indent += 1
        self.w(f"print({', '.join(parts)})" if parts else "print()")
        self.indent -= 1

    def _gen_call_stmt(self, s: A.CallStmt):
        name = s.name.lower()
        if name == "__sum__":
            target, expr = s.args
            expr_code = self.gen_expr(expr)
            if isinstance(target, A.Var):
                self.w(f"pdv[{target.name!r}] = _r.nomiss_sum(pdv.get({target.name!r}, 0.0), {expr_code})")
            elif isinstance(target, A.ArrayRef):
                if target.name in self.fcmp_array_params:
                    idx_code = self._fcmp_array_index_code(target.name, target.indices, target.index, "pdv")
                    self.w(f"_k = int({idx_code}) - 1")
                    self.w(f"{target.name}[_k] = _r.nomiss_sum({target.name}[_k], {expr_code})")
                else:
                    idx = self._arr_offset_expr(target.name, target.indices if target.indices is not None else [target.index])
                    self.w(f"_k = _ARR_{target.name}[{idx}]")
                    self.w(f"pdv[_k] = _r.nomiss_sum(pdv.get(_k, 0.0), {expr_code})")
            return
        if name == "symput":
            a0, a1 = s.args
            self.w(f"_r.MACRO_VARS[str({self.gen_expr(a0)}).strip().lower()] = _r.sas_str({self.gen_expr(a1)})")
            return
        if name == "symputx":
            a0, a1 = s.args[0], s.args[1]
            self.w(f"_r.MACRO_VARS[str({self.gen_expr(a0)}).strip().lower()] = _r.sas_str({self.gen_expr(a1)}).strip()")
            return
        if name == "missing":
            for a in s.args:
                if isinstance(a, A.Var):
                    self.w(f"pdv[{a.name!r}] = _r.MISSING")
            return
        if name == "execute":
            a0 = s.args[0]
            self.w(f"_r.call_execute({self.gen_expr(a0)})")
            return
        if name in self.fcmp_functions:
            args_code = self._fcmp_call_args_code(name, s.args, "pdv")
            outarg_positions = self.fcmp_outargs.get(name)
            if not outarg_positions:
                self.w(f"_fcmp_{name}({args_code})")
                return
            call_site_names = []
            for pos, oname in outarg_positions:
                arg = s.args[pos] if pos < len(s.args) else None
                if not isinstance(arg, A.Var):
                    raise CodegenError(
                        f"call to {name}(): argument {pos + 1} corresponds to OUTARGS "
                        f"parameter {oname!r} and must be a bare variable name -- PROC "
                        f"FCMP OUTARGS parameters are pass-by-reference and only accept "
                        f"a bare variable at the call site"
                    )
                call_site_names.append(arg.name)
            self.w(f"_ret_tuple = _fcmp_{name}({args_code})")
            for i, call_site_name in enumerate(call_site_names, start=1):
                self.w(f"pdv[{call_site_name!r}] = _ret_tuple[{i}]")
            return
        self.w(f"pass  # unsupported: call {name}(...)")

    # ---------------- DATA step ----------------
    def gen_title_step(self, stmt: A.TitleStmt) -> str:
        self.step_idx += 1
        fname = f"_step_{self.step_idx}"
        self.w(f"def {fname}():")
        self.indent += 1
        var = "_FOOTNOTES" if stmt.kind == "footnote" else "_TITLES"
        slot = stmt.number - 1
        # Setting slot n clears every slot numbered higher than n first,
        # matching real SAS -- a bare TITLE;/FOOTNOTE; (slot 0, blank
        # text) therefore clears all of them.
        self.w(f"for _i in range({slot}, 10): {var}[_i] = ''")
        self.w(f"{var}[{slot}] = {stmt.text!r}")
        self.indent -= 1
        return fname

    def gen_ods_step(self, stmt: A.OdsStmt) -> str:
        self.step_idx += 1
        fname = f"_step_{self.step_idx}"
        self.w(f"def {fname}():")
        self.indent += 1
        if stmt.action == "open":
            self.w(f"_r.ods_{stmt.destination}_open({stmt.path!r})")
        else:
            self.w(f"_r.ods_{stmt.destination}_close()")
        self.indent -= 1
        return fname

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

        set_stmt = merge_stmt = update_stmt = by_stmt = input_stmt = datalines_stmt = None
        infile_stmt = None
        arrays: dict[str, A.ArrayStmt] = {}
        retains: list = []
        drops: set = set()
        keeps: set = set()
        lengths: dict = {}
        formats: dict = {}
        labels: dict = {}
        body = []

        for s in ds.statements:
            if isinstance(s, A.SetStmt) and set_stmt is None and merge_stmt is None and update_stmt is None:
                set_stmt = s
            elif isinstance(s, A.MergeStmt) and merge_stmt is None and update_stmt is None:
                merge_stmt = s
            elif isinstance(s, A.UpdateStmt) and update_stmt is None and merge_stmt is None:
                update_stmt = s
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
            elif isinstance(s, A.InfileStmt) and infile_stmt is None:
                infile_stmt = s
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

        # Names with an explicit *numeric* static declaration: a live
        # SET/MERGE/UPDATE source's object dtype must not override these
        # (see seed_char_defaults below).
        declared_numeric = set()
        for n, (is_char, _) in lengths.items():
            if not is_char:
                declared_numeric.add(n)
        for arrstmt in arrays.values():
            if not arrstmt.is_char:
                declared_numeric.update(arrstmt.elements)

        has_explicit_output = any(isinstance(s, A.Output) for s in walk_stmts(ds.statements))
        self.current_arrays = arrays
        self.hidden_vars = set()
        self.loop_stack = []

        self.w(f"def {fname}():")
        self.indent += 1
        self.w("_lag = _r.new_lag_state()")
        self.w("_hashes = {}")
        self.w("_put_target = None")
        self.w("_put_sep = ' '")
        self.w("_file_handles = {}")
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
        char_skip = char_vars | declared_numeric
        if merge_stmt is not None:
            self.w("_m_sources = []")
            for (name, opts, db_info) in merge_stmt.datasets:
                dfcode = self._gen_ds_opts_expr(name, opts, db_info)
                inflag = opts.get("in_flag")
                inflag_lit = repr(inflag) if inflag else "None"
                self.w(f"_m_sources.append(({name!r}, {dfcode}, {inflag_lit}))")
            self.w(f"_r.seed_char_defaults(pdv, {char_skip!r}, *[_s for _, _s, _f in _m_sources])")
            if by_vars:
                byvars_lit = ", ".join(repr(v) for v in by_vars)
                self.w(f"_iter = _r.iter_merge_by(_m_sources, [{byvars_lit}])")
            else:
                self.w("_iter = _r.iter_merge_positional([_s for _, _s, _f in _m_sources])")
        elif update_stmt is not None:
            self.w("_m_sources = []")
            for (name, opts, db_info) in update_stmt.datasets:
                dfcode = self._gen_ds_opts_expr(name, opts, db_info)
                inflag = opts.get("in_flag")
                inflag_lit = repr(inflag) if inflag else "None"
                self.w(f"_m_sources.append(({name!r}, {dfcode}, {inflag_lit}))")
            self.w(f"_r.seed_char_defaults(pdv, {char_skip!r}, *[_s for _, _s, _f in _m_sources])")
            if by_vars:
                byvars_lit = ", ".join(repr(v) for v in by_vars)
                self.w(f"_iter = _r.iter_update_by(_m_sources, [{byvars_lit}])")
            else:
                self.w("_iter = _r.iter_merge_positional([_s for _, _s, _f in _m_sources])")
        elif set_stmt is not None:
            self.w("_s_dfs = []")
            for (name, opts, db_info) in set_stmt.datasets:
                dfcode = self._gen_ds_opts_expr(name, opts, db_info)
                self.w(f"_s_dfs.append({dfcode})")
            self.w(f"_r.seed_char_defaults(pdv, {char_skip!r}, *_s_dfs)")
            if by_vars:
                byvars_lit = ", ".join(repr(v) for v in by_vars)
                self.w(f"_iter = _r.iter_concat_by(_s_dfs, [{byvars_lit}])")
            else:
                self.w("_iter = _r.iter_concat(_s_dfs)")
        elif datalines_stmt is not None and input_stmt is not None:
            rows = self._parse_datalines_rows(datalines_stmt, input_stmt)
            rows_src = "[" + ", ".join(self._dict_literal(r) for r in rows) + "]"
            self.w(f"_iter = [(row, {{}}) for row in {rows_src}]")
        elif infile_stmt is not None and input_stmt is not None:
            if input_stmt.items is not None:
                self.w(
                    f"_iter = [(row, {{}}) for row in _r.read_infile_columns("
                    f"{infile_stmt.path!r}, {input_stmt.items!r}, "
                    f"firstobs={infile_stmt.firstobs!r}, obs={infile_stmt.obs!r})]"
                )
            else:
                varspec = [(n, c) for (n, c) in input_stmt.vars]
                self.w(
                    f"_iter = [(row, {{}}) for row in _r.read_infile("
                    f"{infile_stmt.path!r}, {varspec!r}, dlm={infile_stmt.dlm!r}, "
                    f"dsd={infile_stmt.dsd!r}, firstobs={infile_stmt.firstobs!r}, "
                    f"obs={infile_stmt.obs!r})]"
                )
        else:
            self.w("_iter = _r.iter_once()")

        self.w("_rownum = 0")
        self.w("for _row, _flags in _iter:")
        self.indent += 1
        self.w("_rownum += 1")
        self.w("pdv['_n_'] = float(_rownum)")
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
        self.w("except _r._DataStop:")
        self.indent += 1
        self.w("break")
        self.indent -= 1
        if not has_explicit_output:
            self.w("for _dsname in _out_rows:")
            self.indent += 1
            self.w("_out_rows[_dsname].append(dict(pdv))")
            self.indent -= 1
        self.indent -= 1  # end for _row
        self.w("for _fh in _file_handles.values():")
        self.indent += 1
        self.w("_fh.close()")
        self.indent -= 1

        temp_array_vars = {e for arrstmt in arrays.values() if arrstmt.is_temporary for e in arrstmt.elements}
        auto_drop = (
            {f"first_{v}" for v in by_vars} | {f"last_{v}" for v in by_vars}
            | self.hidden_vars | temp_array_vars | {"_n_", "_iorc_"}
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
            src_expr = f"_r.get_proc_df(_DS, {name!r})"
        keep = opts.get("keep") or []
        drop = opts.get("drop") or []
        rename = opts.get("rename") or {}
        where = opts.get("where")
        wherecode = "None"
        if where is not None:
            wherecode = f"(lambda row: _r.truthy({self.gen_expr(where, varmap='row')}))"
        expr = (
            f"_r.apply_ds_opts({src_expr}, keep={keep!r} or None, "
            f"drop={drop!r} or None, rename={rename!r} or None, where={wherecode})"
        )
        firstobs = opts.get("firstobs")
        if isinstance(firstobs, (int, float)) or (isinstance(firstobs, str) and firstobs.isdigit()):
            expr = f"({expr}).iloc[{int(firstobs) - 1}:].reset_index(drop=True)"
        obs = opts.get("obs")
        if isinstance(obs, (int, float)) or (isinstance(obs, str) and str(obs).isdigit()):
            expr = f"({expr}).head({int(obs)})"
        return expr

    @staticmethod
    def _point_expr(tok: str) -> str:
        """Python expression for a POINT= option token: a numeric literal
        or a PDV variable reference (1-based SAS observation number)."""
        try:
            return repr(float(tok))
        except (TypeError, ValueError):
            return f"_r.nomiss_sum(pdv.get({tok.lower()!r}, _r.MISSING), 0.0)"

    def _gen_nested_set(self, s) -> None:
        """A SET statement nested inside IF/DO: single dataset, sequential
        cursor or POINT= random access, optional NOBS= size variable."""
        if len(s.datasets) != 1:
            raise CodegenError("nested SET supports a single dataset (use POINT= for random access)")
        (name, opts, db_info) = s.datasets[0]
        if opts.get("in_flag"):
            raise CodegenError("nested SET does not support IN=")
        base = self._gen_ds_opts_expr(name, opts, db_info)
        dfvar = self.newtmp("sdf")
        self.w(f"{dfvar} = {base}")
        nobs = opts.get("nobs")
        if isinstance(nobs, str) and nobs:
            self.w(f"pdv[{nobs.lower()!r}] = float(len({dfvar}))")
        key = opts.get("key")
        if key:
            # No persistent SAS index infrastructure: KEY= matches on
            # whichever of the target dataset's columns are already
            # present as keys in the current PDV. See keyed_lookup().
            self.w(f"pdv['_iorc_'] = 0.0 if _r.keyed_lookup({dfvar}, pdv, _row) else 1.0")
            return
        point = opts.get("point")
        endvar = opts.get("end")
        if point:
            self.w(f"_pidx = int({self._point_expr(point)}) - 1")
            self.w(f"if 0 <= _pidx < len({dfvar}):")
            self.indent += 1
            self.w(f"pdv.update({dfvar}.iloc[_pidx].to_dict())")
            self.indent -= 1
            self.w("else:")
            self.indent += 1
            if isinstance(endvar, str) and endvar:
                self.w(f"pdv[{endvar.lower()!r}] = True")
            self.w("raise _r._DataStop()")
            self.indent -= 1
        else:
            curvar = f"_setcur_{name}"
            self.hidden_vars.add(curvar)
            self.w(f"_cur = int(pdv.get({curvar!r}, 0.0))")
            self.w(f"if _cur < len({dfvar}):")
            self.indent += 1
            self.w(f"pdv.update({dfvar}.iloc[_cur].to_dict())")
            self.w(f"pdv[{curvar!r}] = float(_cur + 1)")
            self.indent -= 1
            self.w("else:")
            self.indent += 1
            if isinstance(endvar, str) and endvar:
                self.w(f"pdv[{endvar.lower()!r}] = True")
            self.w("raise _r._DataStop()")
            self.indent -= 1

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
    def gen_proc_fcmp(self, proc: A.ProcStep):
        """Emit one plain Python `def` per PROC FCMP FUNCTION, directly at
        module level (not wrapped in a callable _step_N()), and register
        each name so _gen_call() routes calls to it. Each function body
        reuses the ordinary DATA-step statement codegen (gen_stmt) by
        giving it a local dict literally named `pdv` -- every existing
        statement (IF/DO/SELECT/assignment/...) already targets that name,
        so nothing there needs to change for this to work. RETURN(expr)
        (FcmpReturnStmt) stores the result at pdv['__ret__'] and raises the
        same _RowReturn used by the ordinary DATA step RETURN, caught right
        here instead of by a row loop.

        `params` is a list of (name, is_array) pairs -- `is_array` marks a
        1-D `arr[*]`/`arr{*}` parameter. Such a parameter is passed as a
        plain Python list (the generated `def` takes it positionally, no
        pdv indirection); `arr{i}`/`arr[i]` references inside the body are
        rewritten straight to `arr[int(i)-1]` via self.fcmp_array_params,
        which is populated for the duration of this function's body only."""
        for clause in proc.clauses:
            if clause[0] != "function":
                continue
            _, fname, params, is_char, body, kind, outargs = clause
            if not fname:
                continue
            self.fcmp_functions.add(fname)
            if is_char:
                self.fcmp_char_functions.add(fname)
            if outargs:
                param_positions = {p: i for i, (p, _is_arr) in enumerate(params)}
                self.fcmp_outargs[fname] = [(param_positions[o], o) for o in outargs]
            array_param_names = {p for p, is_arr in params if is_arr}
            if array_param_names:
                self.fcmp_array_param_positions[fname] = {i for i, (_, is_arr) in enumerate(params) if is_arr}
            char_locals: set = set()
            for st in body:
                if isinstance(st, A.LengthStmt):
                    for (n, ischar, _length) in st.entries:
                        if ischar:
                            char_locals.add(n)

            self.w(f"def _fcmp_{fname}({', '.join(p for p, _ in params)}):")
            self.indent += 1
            self.w("pdv = {}")
            for n in sorted(char_locals):
                self.w(f"pdv[{n!r}] = ''")
            for p, is_arr in params:
                if not is_arr:
                    self.w(f"pdv[{p!r}] = {p}")
            self.fcmp_array_params = array_param_names
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
            self.fcmp_array_params = set()
            default = "''" if is_char else "_r.MISSING"
            if outargs:
                outarg_parts = ", ".join(f"pdv[{o!r}]" for o in outargs)
                self.w(f"return (pdv.get('__ret__', {default}), {outarg_parts})")
            else:
                self.w(f"return pdv.get('__ret__', {default})")
            self.indent -= 1
        self.w("")

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
        elif name == "contents":
            self._gen_proc_contents(proc)
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
        elif name == "report":
            self._gen_proc_report(proc)
        elif name == "tabulate":
            self._gen_proc_tabulate(proc)
        elif name == "sgplot":
            self._gen_proc_sgplot(proc)
        elif name == "compare":
            self._gen_proc_compare(proc)
        elif name == "ttest":
            self._gen_proc_ttest(proc)
        elif name == "anova":
            self._gen_proc_anova(proc)
        elif name == "standard":
            self._gen_proc_standard(proc)
        elif name == "npar1way":
            self._gen_proc_npar1way(proc)
        elif name == "princomp":
            self._gen_proc_princomp(proc)
        elif name == "cluster":
            self._gen_proc_cluster(proc)
        elif name == "surveyselect":
            self._gen_proc_surveyselect(proc)
        elif name == "arima":
            self._gen_proc_arima(proc)
        else:
            self.w(f"raise NotImplementedError({'PROC ' + name.upper() + ' is not supported by this compiler'!r})")
        self.w("_r.ods_proc_boundary()")
        self.indent -= 1
        return fname

    def _resolve_ds(self, proc: A.ProcStep) -> str:
        if "data" in proc.options and isinstance(proc.options["data"], str):
            return normalize_dsname(proc.options["data"])
        if self.last_ds_name:
            return self.last_ds_name
        raise CodegenError(f"PROC {proc.name.upper()} has no DATA= and no prior dataset to default to")

    def _proc_src(self, proc: A.ProcStep, dsname: str) -> str:
        """Python expression loading a PROC's DATA= dataset: real-database
        read-through when it names a libref registered via LIBNAME
        (SQLite file, server URL, or a directory of .sas7bdat/.csv files),
        else the in-memory _DS entry (via a tolerant lookup that warns
        and falls back instead of raising KeyError)."""
        raw = proc.options.get("data")
        if isinstance(raw, str) and "." in raw:
            lib, tbl = raw.split(".", 1)
            if lib.lower() in self.db_libs and lib.lower() != "work":
                return f"_r.db_read_table({lib.lower()!r}, {tbl.lower()!r})"
        return f"_r.get_proc_df(_DS, {dsname!r})"

    def _store_out(self, raw: str | None, flat: str, df_var: str):
        """Store a PROC OUT= result into _DS[flat] (and last_ds_name), with
        write-through via db_write_table when OUT= names a LIBNAME table."""
        self.w(f"_DS[{flat!r}] = {df_var}")
        if isinstance(raw, str) and "." in raw:
            lib, tbl = raw.split(".", 1)
            if lib.lower() in self.db_libs and lib.lower() != "work":
                self.w(f"_r.db_write_table({lib.lower()!r}, {tbl.lower()!r}, {df_var})")
        self.last_ds_name = flat

    def _clause(self, proc: A.ProcStep, key: str):
        for k, v in proc.clauses:
            if k == key:
                return v
        return None

    def _gen_proc_filters(self, proc: A.ProcStep):
        """Emit WHERE-statement and OBS=/FIRSTOBS= filtering lines for a
        PROC's already-loaded _df (dataset options flattened into PROC
        options by the parser, e.g. DATA=x(OBS=5) -> options['obs'])."""
        where_cond = self._clause(proc, "where")
        if where_cond is not None:
            self.w(f"_df = _r.apply_ds_opts(_df, where=(lambda row: _r.truthy({self.gen_expr(where_cond, varmap='row')})))")
        firstobs = proc.options.get("firstobs")
        if isinstance(firstobs, (int, float)) or (isinstance(firstobs, str) and firstobs.isdigit()):
            self.w(f"_df = _df.iloc[{int(firstobs) - 1}:].reset_index(drop=True)")
        obs = proc.options.get("obs")
        if isinstance(obs, (int, float)) or (isinstance(obs, str) and str(obs).isdigit()):
            self.w(f"_df = _df.head({int(obs)})")

    @staticmethod
    def _num_lit(v: float) -> str:
        if v == float("inf"):
            return "float('inf')"
        if v == float("-inf"):
            return "float('-inf')"
        return repr(float(v))

    def _gen_proc_format(self, proc: A.ProcStep):
        for clause in proc.clauses:
            (_, is_char, fmtname, entries, other_label) = clause
            key = ("$" if is_char else "") + fmtname
            if is_char:
                pairs = ", ".join(f"{v!r}: {lbl!r}" for v, lbl in entries)
                self.w(f"_r.USER_FORMATS[{key!r}] = {{'values': {{{pairs}}}, 'other': {other_label!r}}}")
            else:
                parts = []
                for item in entries:
                    if len(item) == 5:
                        lo, hi, lbl, lo_excl, hi_excl = item
                        parts.append(
                            f"({self._num_lit(lo)}, {self._num_lit(hi)}, {lbl!r}, {bool(lo_excl)!r}, {bool(hi_excl)!r})"
                        )
                    else:
                        lo, hi, lbl = item
                        parts.append(f"({self._num_lit(lo)}, {self._num_lit(hi)}, {lbl!r})")
                ranges = ", ".join(parts)
                self.w(f"_r.USER_FORMATS[{key!r}] = {{'ranges': [{ranges}], 'other': {other_label!r}}}")

    def _gen_proc_sql(self, proc: A.ProcStep):
        self.w("_con = duckdb.connect()")
        self.w("for _n, _d in list(_DS.items()):")
        self.indent += 1
        self.w("_con.register(_n, _d)")
        self.indent -= 1
        sqlite_libs = {
            lr: c for lr, c in self.db_libs.items()
            if "://" not in c and not os.path.isdir(c)
        }
        dir_libs = {
            lr: c for lr, c in self.db_libs.items()
            if "://" not in c and os.path.isdir(c)
        }
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
        for libref, conn in dir_libs.items():
            self.w(f"_r.sql_attach_dir(_con, {libref!r}, {conn!r})")
        url_libs = [lr for lr, c in self.db_libs.items() if "://" in c]
        if url_libs:
            # Server-backed libraries can't be ATTACHed: scan the SQL text
            # for libref.table references and stage those tables as views.
            # Each read is guarded -- a libref-shaped table alias in the
            # query must not break it.
            seen: set = set()
            for _, stmt in proc.clauses:
                for m in re.finditer(r"(?i)(?<![\w$])([A-Za-z_]\w*)\.([A-Za-z_]\w*)", stmt):
                    lib, tbl = m.group(1).lower(), m.group(2).lower()
                    if lib in url_libs and (lib, tbl) not in seen:
                        seen.add((lib, tbl))
                        reg = f"_saslib_{lib}_{tbl}"
                        self.w("try:")
                        self.indent += 1
                        self.w(f"_con.execute('CREATE SCHEMA IF NOT EXISTS \"{lib}\"')")
                        self.w(f"_con.register({reg!r}, _r.db_read_table({lib!r}, {tbl!r}))")
                        self.w(f"_con.execute('CREATE OR REPLACE VIEW \"{lib}\".\"{tbl}\" AS SELECT * FROM \"{reg}\"')")
                        self.indent -= 1
                        self.w("except Exception as _e:")
                        self.indent += 1
                        self.w(f"print(f'warning: could not stage {lib}.{tbl}: ' + str(_e))")
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

    def _gen_proc_contents(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self.w(f"print('Dataset: {dsname}  (NOBS=' + str(len(_df)) + ')')")
        self.w("print('Variables:')")
        self.w("for _i, _c in enumerate(_df.columns, start=1):")
        self.indent += 1
        self.w("print(f'  {_i}  {_c} ({_df[_c].dtype})')")
        self.indent -= 1

    def _gen_proc_print(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w("for _t in _TITLES:")
        self.indent += 1
        self.w("if _t: print(_t)")
        self.indent -= 1
        var_clause = self._clause(proc, "var")
        id_clause = self._clause(proc, "id")
        by_clause = self._clause(proc, "by")
        id_cols_all = [n for n, _ in id_clause] if id_clause else []
        by_cols = [n for n, _ in by_clause] if by_clause else []
        id_only_cols = []
        by_only_cols = []
        if var_clause:
            cols = [n for n, _ in var_clause]
            # An ID or BY variable is shown (as the row label, or the
            # "--- var=value ---" group header) even if it wasn't
            # requested via VAR -- both must survive this column
            # subsetting, so pull them along here and drop them again
            # after indexing/grouping below.
            id_only_cols = [c for c in id_cols_all if c not in cols]
            by_only_cols = [c for c in by_cols if c not in cols and c not in id_only_cols]
            keep_cols = cols + id_only_cols + by_only_cols
            self.w(f"_req = {cols!r}")
            self.w("_miss = [c for c in _req if c not in _df.columns]")
            self.w("if _miss: print(f\"warning: PROC PRINT VAR not found: {', '.join(_miss)}\")")
            self.w(f"_df = _df[[c for c in {keep_cols!r} if c in _df.columns]]")
        self.w(f"_fmts = dict(_FMT.get({dsname!r}, {{}}))")
        fmt_clause = self._clause(proc, "format")
        if fmt_clause:
            inline = {v: f for v, f in fmt_clause}
            self.w(f"_fmts.update({inline!r})")
            self.w(f"_FMT[{dsname!r}] = dict(_fmts)")
        if by_cols:
            self.w(f"_bycols = [c for c in {by_cols!r} if c in _df.columns]")
            self.w(f"_bymiss = [c for c in {by_cols!r} if c not in _df.columns]")
            self.w("if _bymiss: print(f\"warning: PROC PRINT BY not found: {', '.join(_bymiss)}\")")
            self.w("if _bycols: _df = _df.sort_values(by=_bycols, kind='mergesort').reset_index(drop=True)")
        sum_clause = self._clause(proc, "sum")
        sum_vars = []
        if isinstance(sum_clause, str):
            sum_vars = [t for t in re.split(r"[\s,;]+", sum_clause.strip().lower()) if t and t != "sum"]
        # Factor the per-table rendering into a helper so BY groups can
        # reuse it (each BY value gets its own table + subtotal).
        self.w("def _print_pf(_pf):")
        self.indent += 1
        if by_only_cols:
            self.w(f"_pf = _pf.drop(columns=[c for c in {by_only_cols!r} if c in _pf.columns])")
        self.w("for _c, _fmt in _fmts.items():")
        self.indent += 1
        self.w("if _c in _pf.columns:")
        self.indent += 1
        self.w("_pf[_c] = _pf[_c].map(lambda v: _r.apply_format(v, _fmt))")
        self.indent -= 1
        self.indent -= 1
        if not proc.options.get("noobs"):
            if id_cols_all:
                self.w(f"_idcols = [c for c in {id_cols_all!r} if c in _pf.columns]")
                self.w("if _idcols:")
                self.indent += 1
                self.w("_pf.index = _pf[_idcols].astype(str).agg(' '.join, axis=1)")
                self.w("_pf.index.name = None")
                if id_only_cols:
                    self.w(f"_pf = _pf.drop(columns=[c for c in {id_only_cols!r} if c in _pf.columns])")
                self.indent -= 1
                self.w("else:")
                self.indent += 1
                self.w("_pf.index = range(1, len(_pf) + 1)")
                self.w("_pf.index.name = 'Obs'")
                self.indent -= 1
            else:
                self.w("_pf.index = range(1, len(_pf) + 1)")
                self.w("_pf.index.name = 'Obs'")
        if sum_vars:
            self.w("_pf.loc['Total'] = ''")
            for _c in sum_vars:
                self.w(f"if {_c!r} in _pf.columns:")
                self.indent += 1
                self.w(f"_pf.loc['Total', {_c!r}] = pd.to_numeric(_pf[{_c!r}], errors='coerce').sum()")
                self.indent -= 1
            if proc.options.get("noobs"):
                # index (and its 'Total' label) is hidden with NOOBS, so
                # stamp the label into the first column instead
                self.w("if len(_pf) and str(_pf.iloc[-1, 0]) in ('', 'nan', 'NaN', 'None'):")
                self.indent += 1
                self.w("_pf.iloc[-1, 0] = 'Total'")
                self.indent -= 1
        self.w(f"_lbls = _LBL.get({dsname!r}, {{}})")
        self.w("if _lbls: _pf = _pf.rename(columns=_lbls)")
        if proc.options.get("noobs"):
            self.w("print(_pf.to_string(index=False))")
        else:
            self.w("print(_pf.to_string())")
        self.indent -= 1
        if by_cols:
            self.w("if _bycols:")
            self.indent += 1
            self.w("for _bkey, _bdf in _df.groupby(_bycols, dropna=False):")
            self.indent += 1
            self.w("_bkey = _bkey if isinstance(_bkey, tuple) else (_bkey,)")
            self.w("_blab = ', '.join(f'{c}={_r.sas_str(v)}' for c, v in zip(_bycols, _bkey))")
            self.w("print(f'--- {_blab} ---')")
            self.w("_print_pf(_bdf.copy())")
            self.indent -= 1
            self.indent -= 1
            self.w("else:")
            self.indent += 1
            self.w("_print_pf(_df.copy())")
            self.indent -= 1
        else:
            self.w("_print_pf(_df.copy())")
        self.w("for _f in _FOOTNOTES:")
        self.indent += 1
        self.w("if _f: print(_f)")
        self.indent -= 1

    def _gen_proc_sort(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        out = normalize_dsname(proc.options["out"]) if isinstance(proc.options.get("out"), str) else dsname
        by_clause = self._clause(proc, "by")
        if not by_clause:
            raise CodegenError("PROC SORT requires a BY statement")
        cols = [n for n, _ in by_clause]
        ascending = [not desc for _, desc in by_clause]
        self.w(f"_df = ({self._proc_src(proc, dsname)}).copy()")
        self._gen_proc_filters(proc)
        self.w(f"_df = _df.sort_values(by={cols!r}, ascending={ascending!r}, kind='mergesort')")
        dupout_raw = proc.options.get("dupout") if isinstance(proc.options.get("dupout"), str) else None
        if proc.options.get("nodupkey"):
            if dupout_raw:
                self.w(f"_dups = _df[_df.duplicated(subset={cols!r}, keep='first')]")
            self.w(f"_df = _df.drop_duplicates(subset={cols!r}, keep='first')")
        elif proc.options.get("nodup") or proc.options.get("noduprecs"):
            if dupout_raw:
                self.w("_dups = _df[_df.duplicated(keep='first')]")
            self.w("_df = _df.drop_duplicates(keep='first')")
        self.w("_df = _df.reset_index(drop=True)")
        if dupout_raw:
            self._store_out(dupout_raw, normalize_dsname(dupout_raw), "_dups")
        if out != dsname:
            self.w(f"if {dsname!r} in _FMT: _FMT[{out!r}] = _FMT[{dsname!r}]")
            self.w(f"if {dsname!r} in _LBL: _LBL[{out!r}] = _LBL[{dsname!r}]")
        self._store_out(
            proc.options.get("out") if isinstance(proc.options.get("out"), str) else None,
            out, "_df",
        )

    def _requested_stats(self, proc: A.ProcStep) -> list:
        requested = [s for s in _STAT_EXPR if proc.options.get(s) is True]
        return requested or list(_DEFAULT_MEANS_STATS)

    @staticmethod
    def _means_combos(proc: A.ProcStep, class_list: list) -> list:
        """CLASS-variable combinations from TYPES/WAYS statements; default
        is the single full-interaction combo (existing behavior)."""
        combos: list = []
        for k, raw in proc.clauses:
            if k == "types" and isinstance(raw, str):
                body = re.sub(r"(?i)^\s*types\b", "", raw).strip().rstrip(";")
                for tok in body.split():
                    combo = [v.lower() for v in tok.split("*") if v]
                    combo = [v for v in combo if v in class_list]
                    if combo and combo not in combos:
                        combos.append(combo)
            elif k == "ways" and isinstance(raw, str):
                body = re.sub(r"(?i)^\s*ways\b", "", raw).strip().rstrip(";")
                for tok in body.split():
                    try:
                        n = int(tok.rstrip(";"))
                    except ValueError:
                        continue
                    if n <= 0:
                        if [] not in combos:
                            combos.append([])
                    else:
                        for combo in itertools.combinations(class_list, min(n, len(class_list))):
                            if list(combo) not in combos:
                                combos.append(list(combo))
        return combos or [class_list]

    def _gen_proc_means(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        class_clause = self._clause(proc, "class") or self._clause(proc, "by")
        var_clause = self._clause(proc, "var")
        output_clause = self._clause(proc, "output")
        stat_names = self._requested_stats(proc)
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        if var_clause:
            var_list = [n for n, _ in var_clause]
        else:
            self.w("_var_list = [c for c in _df.columns if pd.api.types.is_numeric_dtype(_df[c])]")
            var_list = None
        vl = repr(var_list) if var_list is not None else "_var_list"
        class_list = [n for n, _ in class_clause] if class_clause else []
        combos = self._means_combos(proc, class_list) if class_list else [[]]

        self.w("_rows = []")
        for combo in combos:
            if combo:
                self.w(f"for _key, _grp in _df.groupby({combo!r}, dropna=False):")
                self.indent += 1
                self.w("_key = _key if isinstance(_key, tuple) else (_key,)")
                self.w(f"_row = dict(zip({combo!r}, _key))")
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
            self._store_out(output_clause.get("out_raw"), out, "pd.DataFrame(_rows)")

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
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        tables_raw = self._clause(proc, "tables")
        if not tables_raw:
            var_clause = self._clause(proc, "var") or []
            tables_raw = " ".join(n for n, _ in var_clause)
        table_part = tables_raw.split("/", 1)[0].strip() if tables_raw else ""
        table_opts = tables_raw.split("/", 1)[1] if tables_raw and "/" in tables_raw else ""
        want_chisq = bool(re.search(r"(?i)\bchisq\b", table_opts))
        want_measures = bool(re.search(r"(?i)\b(measures|relrisk|riskdiff)\b", table_opts))
        want_agree = bool(re.search(r"(?i)\bagree\b", table_opts))
        output_clause = self._clause(proc, "output")
        out = output_clause.get("out") if output_clause else None
        if out:
            self.w("_freq_rows = []")
        for req in table_part.split():
            req = req.strip()
            if not req:
                continue
            if "*" in req:
                v1, v2 = [x.strip() for x in req.split("*", 1)]
                self.w(f"print(pd.crosstab(_df[{v1!r}], _df[{v2!r}]))")
                if want_chisq:
                    self.w(f"_r.proc_freq_chisq(_df, {v1!r}, {v2!r})")
                if want_measures:
                    self.w(f"_r.proc_freq_measures(_df, {v1!r}, {v2!r})")
                if want_agree:
                    self.w(f"_r.proc_freq_agree(_df, {v1!r}, {v2!r})")
                if out:
                    self.w(f"_ct = pd.crosstab(_df[{v1!r}], _df[{v2!r}])")
                    self.w(f"_freq_rows.extend({{'{v1}': i, '{v2}': c, 'count': int(n), 'percent': 100.0 * n / max(len(_df), 1)}} for (i, c), n in _ct.stack().items())")
            else:
                self.w(f"print(_df[{req!r}].value_counts(dropna=False))")
                if want_chisq:
                    self.w(f"_r.proc_freq_chisq_oneway(_df, {req!r})")
                if out:
                    self.w(f"_vc = _df[{req!r}].value_counts(dropna=False)")
                    self.w(f"_freq_rows.extend({{'{req}': k, 'count': int(v), 'percent': 100.0 * v / max(len(_df), 1)}} for k, v in _vc.items())")
        if out:
            self._store_out(output_clause.get("out_raw"), out, "pd.DataFrame(_freq_rows)")

    def _gen_proc_transpose(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        out = normalize_dsname(proc.options["out"]) if isinstance(proc.options.get("out"), str) else f"{dsname}_transposed"
        by_clause = self._clause(proc, "by")
        var_clause = self._clause(proc, "var")
        id_clause = self._clause(proc, "id")
        by_vars = [n for n, _ in by_clause] if by_clause else []
        var_list = [n for n, _ in var_clause] if var_clause else None
        idvar = id_clause[0][0] if id_clause else None

        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
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
        stem = proc.options.get("prefix")
        stem = stem if isinstance(stem, str) else "col"
        delim = proc.options.get("delimiter")
        delim = delim if isinstance(delim, str) else ""
        suffix = proc.options.get("suffix")
        suffix = suffix if isinstance(suffix, str) else ""
        self.w(f"_row[{stem!r} + {delim!r} + str(_i + 1) + {suffix!r}] = _rec.get(_v)")
        self.indent -= 1
        self.w("_rows.append(_row)")
        self.indent -= 1
        self.indent -= 1
        self.indent -= 1
        self._store_out(
            proc.options.get("out") if isinstance(proc.options.get("out"), str) else None,
            out, "pd.DataFrame(_rows)",
        )

    def _gen_proc_import(self, proc: A.ProcStep):
        datafile = proc.options.get("datafile")
        out = proc.options.get("out")
        if not isinstance(datafile, str) or not isinstance(out, str):
            raise CodegenError("PROC IMPORT requires DATAFILE= and OUT=")
        raw_out = out
        out = normalize_dsname(out)
        dbms = str(proc.options.get("dbms", "csv")).lower()
        if dbms not in ("csv", "dlm", "tab"):
            raise CodegenError(f"PROC IMPORT: DBMS={dbms.upper()} is not supported (use CSV)")
        sep = "\t" if dbms == "tab" else ","
        self.w(f"_df = pd.read_csv({datafile!r}, sep={sep!r})")
        self.w("_df.columns = [str(c).strip().lower() for c in _df.columns]")
        self._store_out(raw_out, out, "_df")

    def _gen_proc_export(self, proc: A.ProcStep):
        outfile = proc.options.get("outfile")
        dsname = self._resolve_ds(proc)
        if not isinstance(outfile, str):
            raise CodegenError("PROC EXPORT requires OUTFILE=")
        dbms = str(proc.options.get("dbms", "csv")).lower()
        if dbms not in ("csv", "dlm", "tab"):
            raise CodegenError(f"PROC EXPORT: DBMS={dbms.upper()} is not supported (use CSV)")
        sep = "\t" if dbms == "tab" else ","
        self.w(f"({self._proc_src(proc, dsname)}).to_csv({outfile!r}, sep={sep!r}, index=False)")

    def _gen_proc_datasets(self, proc: A.ProcStep):
        for (kind, payload) in proc.clauses:
            if kind == "delete":
                for entry in payload:
                    flat, raw = entry if isinstance(entry, tuple) else (entry, entry)
                    self.w(f"_DS.pop({flat!r}, None)")
                    self.w(f"_FMT.pop({flat!r}, None)")
                    self.w(f"_LBL.pop({flat!r}, None)")
                    if isinstance(raw, str) and "." in raw:
                        lib, tbl = raw.split(".", 1)
                        if lib.lower() in self.db_libs and lib.lower() != "work":
                            self.w(f"_r.db_delete_table({lib.lower()!r}, {tbl.lower()!r})")
            elif kind == "change":
                for entry in payload:
                    if len(entry) == 4:
                        old, new, old_raw, new_raw = entry
                    else:
                        old, new = entry[:2]
                        old_raw = new_raw = None
                    self.w(f"if {old!r} in _DS: _DS[{new!r}] = _DS.pop({old!r})")
                    self.w(f"if {old!r} in _FMT: _FMT[{new!r}] = _FMT.pop({old!r})")
                    self.w(f"if {old!r} in _LBL: _LBL[{new!r}] = _LBL.pop({old!r})")
                    self.last_ds_name = new
                    if isinstance(new_raw, str) and "." in new_raw:
                        lib, new_tbl = new_raw.split(".", 1)
                        old_tbl = old_raw.split(".", 1)[1] if isinstance(old_raw, str) and "." in old_raw else None
                        if lib.lower() in self.db_libs and lib.lower() != "work" and old_tbl:
                            self.w(f"_r.db_rename_table({lib.lower()!r}, {old_tbl.lower()!r}, {new_tbl.lower()!r})")

    def _gen_proc_univariate(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        if var_clause:
            var_list = [n for n, _ in var_clause]
        else:
            self.w("_var_list = [c for c in _df.columns if pd.api.types.is_numeric_dtype(_df[c])]")
            var_list = None
        vl = repr(var_list) if var_list is not None else "_var_list"
        self.w("_uni_rows = []")
        self.w(f"for _v in {vl}:")
        self.indent += 1
        self.w("_s = _df[_v].dropna()")
        self.w("_urow = {'_varname_': _v, '_n_': len(_s),")
        self.indent += 1
        self.w("'_mean_': _s.mean() if len(_s) else float('nan'),")
        self.w("'_std_': _s.std() if len(_s) > 1 else float('nan'),")
        self.w("'_variance_': _s.var() if len(_s) > 1 else float('nan'),")
        self.w("'_min_': _s.min() if len(_s) else float('nan'),")
        self.w("'_max_': _s.max() if len(_s) else float('nan'),")
        self.w("'_median_': _s.median() if len(_s) else float('nan')}")
        self.indent -= 1
        self.w("_uni_rows.append(_urow)")
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
        self.w("_r.proc_univariate_normality(_s)")
        self.w("print()")
        self.indent -= 1
        output_clause = self._clause(proc, "output")
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            self._store_out(output_clause.get("out_raw"), out, "pd.DataFrame(_uni_rows)")

    @staticmethod
    def _parse_model_stmt(raw: str):
        m = re.match(r"^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*=\s*(.+)$", raw, re.S)
        if not m:
            raise CodegenError(f"could not parse MODEL statement: {raw!r}")
        y = m.group(1).lower()
        rhs = m.group(2)
        # Strip a trailing `/ options` clause (e.g. `model y = x1 x2 / vif;`)
        # before tokenizing predictors, so option keywords never leak into xs.
        rhs = rhs.split("/", 1)[0]
        xs = [tok.lower() for tok in re.split(r"[\s+]+", rhs.strip()) if tok and tok != "+"]
        return y, xs

    @staticmethod
    def _output_stat_dict(output_clause: dict) -> dict:
        out_stats: dict = {}
        for (statkw, var, names) in output_clause.get("stats", []):
            if var is None and isinstance(names, list):
                out_stats.setdefault(statkw, []).extend(names)
        return out_stats

    def _src_for_option(self, proc: A.ProcStep, key: str) -> tuple:
        """Like _proc_src but for a PROC option other than DATA= (e.g.
        PROC COMPARE's BASE=/COMPARE=). Returns (python_expr, flat_name)."""
        raw = proc.options.get(key)
        if not isinstance(raw, str):
            raise CodegenError(f"PROC {proc.name.upper()} requires {key.upper()}=")
        flat = normalize_dsname(raw)
        if "." in raw:
            lib, tbl = raw.split(".", 1)
            if lib.lower() in self.db_libs and lib.lower() != "work":
                return f"_r.db_read_table({lib.lower()!r}, {tbl.lower()!r})", flat
        return f"_DS[{flat!r}]", flat

    def _gen_proc_compare(self, proc: A.ProcStep):
        base_expr, _ = self._src_for_option(proc, "base")
        compare_expr, _ = self._src_for_option(proc, "compare")
        id_clause = self._clause(proc, "id")
        var_clause = self._clause(proc, "var")
        by_clause = self._clause(proc, "by")
        id_vars = [n for n, _ in id_clause] if id_clause else None
        var_list = [n for n, _ in var_clause] if var_clause else None
        by_vars = [n for n, _ in by_clause] if by_clause else None
        criterion_raw = proc.options.get("criterion")
        criterion = float(criterion_raw) if criterion_raw is not None else 0.0
        transform_clauses = [v for k, v in proc.clauses if k == "transform"]
        transforms: dict = {}
        for names, func_name in transform_clauses:
            for n in names:
                transforms[n] = func_name
        self.w(f"_base = {base_expr}")
        self.w(f"_compare = {compare_expr}")
        self.w(
            f"_cmp_diffs = _r.proc_compare_report(_base, _compare, "
            f"id_vars={id_vars!r}, var_list={var_list!r}, "
            f"by_vars={by_vars!r}, criterion={criterion!r}, "
            f"transforms={transforms!r})"
        )
        out_raw = proc.options.get("out")
        if isinstance(out_raw, str):
            out = normalize_dsname(out_raw)
            self._store_out(out_raw, out, "_cmp_diffs")

    def _gen_proc_corr(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        if var_clause:
            cols = [n for n, _ in var_clause]
        else:
            self.w("_cols = [c for c in _df.columns if pd.api.types.is_numeric_dtype(_df[c])]")
            cols = None
        cl = repr(cols) if cols is not None else "_cols"
        with_clause = self._clause(proc, "with")
        with_cols = [n for n, _ in with_clause] if with_clause else None
        if with_cols:
            self.w(f"_corr = _r.proc_corr_with_report(_df, {cl}, {with_cols!r})")
        else:
            self.w(f"_corr = _r.proc_corr_report(_df, {cl})")
        out = proc.options.get("out") or proc.options.get("outp")
        raw_out = out
        if isinstance(out, str):
            out = normalize_dsname(out)
            self.w("_corr_out = _corr.reset_index().rename(columns={'index': '_name_'})")
            self._store_out(raw_out, out, "_corr_out")

    def _gen_proc_ttest(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        class_clause = self._clause(proc, "class")
        paired_clause = self._clause(proc, "paired")
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        var_names = [n for n, _ in var_clause] if var_clause else []
        class_var = class_clause[0][0] if class_clause else None
        paired_pairs = paired_clause if paired_clause else []
        h0_raw = proc.options.get("h0", 0)
        try:
            h0 = float(h0_raw)
        except (TypeError, ValueError):
            h0 = 0.0
        self.w(
            f"_r.proc_ttest_report(_df, {var_names!r}, {class_var!r}, "
            f"{paired_pairs!r}, h0={h0!r})"
        )

    def _gen_proc_anova(self, proc: A.ProcStep):
        """One-way ANOVA only: exactly one CLASS variable and a MODEL
        statement whose right-hand side is that same variable."""
        dsname = self._resolve_ds(proc)
        class_clause = self._clause(proc, "class")
        if not class_clause:
            raise CodegenError("PROC ANOVA requires a CLASS statement")
        if len(class_clause) != 1:
            raise CodegenError(
                "PROC ANOVA: multi-way ANOVA (more than one CLASS variable) "
                "is not supported here; only one-way ANOVA is supported"
            )
        class_var = class_clause[0][0]
        model_raw = self._clause(proc, "model")
        if not model_raw:
            raise CodegenError("PROC ANOVA requires a MODEL statement")
        y, xs = self._parse_model_stmt(model_raw)
        if len(xs) != 1:
            raise CodegenError(
                "PROC ANOVA: multi-way ANOVA (more than one variable on the "
                "right-hand side of MODEL) is not supported here; only "
                "one-way ANOVA (MODEL y = class_var;) is supported"
            )
        if xs[0] != class_var:
            raise CodegenError(
                f"PROC ANOVA: MODEL right-hand side variable {xs[0]!r} must "
                f"match the CLASS variable {class_var!r} for one-way ANOVA"
            )
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w(f"_r.proc_anova_oneway_report(_df, {y!r}, {class_var!r})")

    def _gen_proc_npar1way(self, proc: A.ProcStep):
        """Wilcoxon rank-sum (2-level CLASS) / Kruskal-Wallis (k>2-level
        CLASS) only -- other NPAR1WAY test options (EDF, MEDIAN, SAVAGE,
        etc.) are not implemented."""
        dsname = self._resolve_ds(proc)
        class_clause = self._clause(proc, "class")
        if not class_clause or len(class_clause) != 1:
            raise CodegenError(
                "PROC NPAR1WAY requires a CLASS statement with exactly one variable"
            )
        class_var = class_clause[0][0]
        var_clause = self._clause(proc, "var")
        if not var_clause:
            raise CodegenError("PROC NPAR1WAY requires a VAR statement")
        var_names = [n for n, _ in var_clause]
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w(f"_r.proc_npar1way_report(_df, {var_names!r}, {class_var!r})")

    def _gen_proc_reg(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        model_raw = self._clause(proc, "model")
        if not model_raw:
            raise CodegenError("PROC REG requires a MODEL statement")
        y, xs = self._parse_model_stmt(model_raw)
        model_opts = model_raw.split("/", 1)[1] if "/" in model_raw else ""
        want_vif = bool(re.search(r"(?i)\bvif\b", model_opts))
        selection_m = re.search(r"(?i)\bselection\s*=\s*(\w+)", model_opts)
        selection = selection_m.group(1).lower() if selection_m else None
        if selection == "stepwise":
            raise CodegenError(
                "PROC REG: SELECTION=STEPWISE is not supported; use BACKWARD or FORWARD"
            )
        if selection not in (None, "backward", "forward"):
            raise CodegenError(
                f"PROC REG: unsupported SELECTION={selection.upper()}; "
                "use BACKWARD or FORWARD"
            )
        slstay_m = re.search(r"(?i)\bslstay\s*=\s*([\d.]+)", model_opts)
        slstay = float(slstay_m.group(1)) if slstay_m else 0.05
        slentry_m = re.search(r"(?i)\bslentry\s*=\s*([\d.]+)", model_opts)
        slentry = float(slentry_m.group(1)) if slentry_m else 0.05
        output_clause = self._clause(proc, "output")
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        out_stats_lit = "None"
        out = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            out_stats_lit = repr(self._output_stat_dict(output_clause))
        self.w(
            f"_scored = _r.proc_reg_fit(_df, {y!r}, {xs!r}, out_stats={out_stats_lit}, "
            f"vif={want_vif!r}, selection={selection!r}, slstay={slstay!r}, "
            f"slentry={slentry!r})"
        )
        if out:
            self._store_out(output_clause.get("out_raw"), out, "_scored")

    def _gen_proc_logistic(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        model_raw = self._clause(proc, "model")
        if not model_raw:
            raise CodegenError("PROC LOGISTIC requires a MODEL statement")
        y, xs = self._parse_model_stmt(model_raw)
        output_clause = self._clause(proc, "output")
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        out_stats_lit = "None"
        out = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            out_stats_lit = repr(self._output_stat_dict(output_clause))
        self.w(f"_scored = _r.proc_logistic_fit(_df, {y!r}, {xs!r}, out_stats={out_stats_lit})")
        if out:
            self._store_out(output_clause.get("out_raw"), out, "_scored")

    def _gen_proc_glm(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        model_raw = self._clause(proc, "model")
        if not model_raw:
            raise CodegenError("PROC GLM requires a MODEL statement")
        y, xs = self._parse_model_stmt(model_raw)
        class_clause = self._clause(proc, "class")
        class_vars = [n for n, _ in class_clause] if class_clause else []
        output_clause = self._clause(proc, "output")
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
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
            self._store_out(output_clause.get("out_raw"), out, "_scored")

    def _gen_proc_fastclus(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        if not var_clause:
            raise CodegenError("PROC FASTCLUS requires a VAR statement")
        var_list = [n for n, _ in var_clause]
        k = int(proc.options.get("maxclusters", 2))
        output_clause = self._clause(proc, "output")
        out = None
        out_raw = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            out_raw = output_clause.get("out_raw")
        elif isinstance(proc.options.get("out"), str):
            out = normalize_dsname(proc.options["out"])
            out_raw = proc.options["out"]
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w(f"_clustered = _r.proc_fastclus_fit(_df, {var_list!r}, {k})")
        if out:
            self._store_out(out_raw, out, "_clustered")

    def _gen_proc_princomp(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        if not var_clause:
            raise CodegenError("PROC PRINCOMP requires a VAR statement")
        var_list = [n for n, _ in var_clause]
        n_raw = proc.options.get("n")
        n = int(n_raw) if isinstance(n_raw, str) else None
        cov = bool(proc.options.get("cov"))
        std = bool(proc.options.get("std"))
        output_clause = self._clause(proc, "output")
        out = None
        out_raw = None
        if output_clause and output_clause.get("out"):
            out = output_clause["out"]
            out_raw = output_clause.get("out_raw")
        elif isinstance(proc.options.get("out"), str):
            out = normalize_dsname(proc.options["out"])
            out_raw = proc.options["out"]
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w(f"_scored = _r.proc_princomp_fit(_df, {var_list!r}, n={n!r}, use_cov={cov!r}, std_scores={std!r})")
        if out:
            self._store_out(out_raw, out, "_scored")

    def _gen_proc_cluster(self, proc: A.ProcStep):
        """Agglomerative/hierarchical clustering (scipy). Print-only, like
        PROC TTEST/ANOVA/NPAR1WAY -- no OUT=-like dataset here (real PROC
        CLUSTER's equivalent is the OUTTREE= tree-structure dataset, which
        is out of scope; see proc_cluster_report's docstring)."""
        dsname = self._resolve_ds(proc)
        var_clause = self._clause(proc, "var")
        if not var_clause:
            raise CodegenError("PROC CLUSTER requires a VAR statement")
        var_list = [n for n, _ in var_clause]
        method = str(proc.options.get("method", "average")).lower()
        if method not in _CLUSTER_METHODS:
            raise CodegenError(
                f"PROC CLUSTER: unrecognized METHOD= {method!r}; supported "
                f"methods are {sorted(_CLUSTER_METHODS)!r}"
            )
        id_clause = self._clause(proc, "id")
        id_var = id_clause[0][0] if id_clause else None
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w(
            f"_r.proc_cluster_report(_df, {var_list!r}, {method!r}, id_var={id_var!r})"
        )

    def _gen_proc_arima(self, proc: A.ProcStep):
        """PROC ARIMA, scoped to a single IDENTIFY/ESTIMATE/FORECAST block
        (see _parse_proc_arima's docstring for the full list of scope
        cuts: no differencing-in-VAR, no seasonal terms, no INPUT=
        transfer functions, no OUTLIER, FORECAST ID= parsed but not used
        for real date extrapolation -- forecast periods are always
        sequential integers 1..LEAD).

        IDENTIFY VAR= is required -- there is nothing to model without it.
        ESTIMATE is also required: with no ESTIMATE, real SAS defaults to
        p=0/d=0/q=0 (a degenerate white-noise model), and silently fitting
        that would be more confusing than useful, so this compiler asks
        for it explicitly instead of defaulting it.
        FORECAST is optional: without it, PROC ARIMA just fits and prints
        the model summary (print-only, like PROC TTEST/ANOVA/CLUSTER)."""
        dsname = self._resolve_ds(proc)
        identify = next((c[1] for c in proc.clauses if c[0] == "identify"), None)
        if not identify or not identify.get("var"):
            raise CodegenError("PROC ARIMA requires an IDENTIFY statement with VAR=")
        estimate = next((c[1] for c in proc.clauses if c[0] == "estimate"), None)
        if not estimate:
            raise CodegenError("PROC ARIMA requires an ESTIMATE statement with P=/D=/Q=")
        forecast = next((c[1] for c in proc.clauses if c[0] == "forecast"), None)

        var = identify["var"]
        order = (estimate.get("p", 0), estimate.get("d", 0), estimate.get("q", 0))
        lead = forecast.get("lead") if forecast else None

        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w(f"_fcst = _r.proc_arima_fit(_df, {var!r}, order={order!r}, lead={lead!r})")
        if forecast and forecast.get("out"):
            self._store_out(forecast.get("out_raw"), forecast["out"], "_fcst")

    # ---- PROC REPORT ----
    _REPORT_STAT_WORDS = {"sum", "mean", "n", "min", "max", "std", "median"}
    _REPORT_STAT_METHOD = {
        "sum": "sum", "mean": "mean", "n": "count",
        "min": "min", "max": "max", "std": "std", "median": "median",
    }

    @classmethod
    def _classify_report_define(cls, mods: list) -> tuple:
        """Return (usage, stat) for a DEFINE var / mod1 mod2 ...; clause.
        usage is 'group', 'analysis', or 'display' (SAS's default)."""
        modset = set(mods)
        if "group" in modset:
            return ("group", None)
        if "display" in modset:
            return ("display", None)
        stat = next((m for m in mods if m in cls._REPORT_STAT_WORDS), None)
        if "analysis" in modset or stat:
            return ("analysis", stat or "sum")
        return ("display", None)

    def _gen_compute_blocks(self, compute_blocks) -> None:
        """Emit per-row COMPUTE-block execution over _rows (list of dicts):
        each row becomes the PDV, the block's statements run via the normal
        statement generator, and results merge back into the row (DELETE
        inside a block drops the row)."""
        self.w("_lag = _r.new_lag_state()")
        self.w("_hashes = {}")
        self.w("_kept = []")
        self.w("for _rrow in _rows:")
        self.indent += 1
        self.w("pdv = dict(_rrow)")
        self.w("try:")
        self.indent += 1
        for _, body in compute_blocks:
            for st in body:
                self.gen_stmt(st)
        self.indent -= 1
        self.w("except _r._RowDelete:")
        self.indent += 1
        self.w("continue")
        self.indent -= 1
        self.w("except _r._RowReturn:")
        self.indent += 1
        self.w("pass")
        self.indent -= 1
        self.w("_rrow.update(pdv)")
        self.w("_kept.append(_rrow)")
        self.indent -= 1
        self.w("_rows = _kept")

    def _report_breaks(self, proc: A.ProcStep) -> tuple:
        """Return (rbreak_summarize, [break_vars]) from BREAK/RBREAK
        ... / SUMMARIZE statements (parsed as raw-text clauses)."""
        rbreak, breaks = False, []
        for k, raw in proc.clauses:
            if not isinstance(raw, str):
                continue
            low = raw.lower()
            if k == "rbreak" and "summarize" in low:
                rbreak = True
            elif k == "break" and "summarize" in low:
                m = re.search(r"after\s+([a-z_]\w*)", low)
                if m:
                    breaks.append(m.group(1))
        return rbreak, breaks

    def _gen_proc_report(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        column_clause = self._clause(proc, "column")
        if not column_clause:
            raise CodegenError("PROC REPORT requires a COLUMN statement")
        define_map = {}
        for k, v in proc.clauses:
            if k == "define":
                var, mods = v
                define_map[var] = mods

        group_vars, analysis_vars, display_vars = [], [], []
        for col in column_clause:
            usage, stat = self._classify_report_define(define_map.get(col, []))
            if usage == "group":
                group_vars.append(col)
            elif usage == "analysis":
                analysis_vars.append((col, stat))
            else:
                display_vars.append(col)

        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        rbreak, breaks = self._report_breaks(proc)
        breaks = [b for b in breaks if b in group_vars]
        compute_blocks = [v for k, v in proc.clauses if k == "compute"]
        if group_vars and analysis_vars:
            self.w("_rows = []")
            self.w(f"for _key, _g in _df.groupby({group_vars!r}, dropna=False):")
            self.indent += 1
            self.w("_key = _key if isinstance(_key, tuple) else (_key,)")
            self.w(f"_row = dict(zip({group_vars!r}, _key))")
            for var, stat in analysis_vars:
                method = self._REPORT_STAT_METHOD[stat]
                self.w(f"_row[{var!r}] = _g[{var!r}].{method}()")
            self.w("_rows.append(_row)")
            for b in breaks:
                self.w(f"_sub = {{{b!r}: _key[{group_vars!r}.index({b!r})]}}")
                for g2 in group_vars:
                    if g2 != b:
                        self.w(f"_sub[{g2!r}] = ''")
                for var, stat in analysis_vars:
                    method = self._REPORT_STAT_METHOD[stat]
                    self.w(f"_sub[{var!r}] = _g[{var!r}].{method}()")
                self.w("_rows.append(_sub)")
            self.indent -= 1
            self.w("_report_df = pd.DataFrame(_rows)")
            if compute_blocks:
                self._gen_compute_blocks(compute_blocks)
                self.w("_report_df = pd.DataFrame(_rows)")
            if rbreak:
                self.w(f"_tot = {{{group_vars[0]!r}: 'Total'}}")
                for g2 in group_vars[1:]:
                    self.w(f"_tot[{g2!r}] = ''")
                for var, stat in analysis_vars:
                    method = self._REPORT_STAT_METHOD[stat]
                    self.w(f"_tot[{var!r}] = _df[{var!r}].{method}()")
                self.w("_report_df = pd.concat([_report_df, pd.DataFrame([_tot])], ignore_index=True)")
            self.w("print(_report_df.to_string(index=False))")
        else:
            self.w(f"_report_df = _df[{column_clause!r}]")
            if compute_blocks:
                self.w("_rows = _report_df.to_dict('records')")
                self._gen_compute_blocks(compute_blocks)
                self.w("_report_df = pd.DataFrame(_rows)")
            if rbreak:
                self.w("_numcols = [c for c in _report_df.columns if pd.api.types.is_numeric_dtype(_report_df[c])]")
                self.w("_tot = {c: _report_df[c].sum() for c in _numcols}")
                self.w("for c in _report_df.columns:")
                self.indent += 1
                self.w("_tot.setdefault(c, 'Total' if c == _report_df.columns[0] else '')")
                self.indent -= 1
                self.w("_report_df = pd.concat([_report_df, pd.DataFrame([_tot])], ignore_index=True)")
            self.w("_pf = _report_df.copy()")
            self.w("_pf.index = range(1, len(_pf) + 1)")
            self.w("_pf.index.name = 'Obs'")
            self.w("print(_pf.to_string())")

    # ---- PROC TABULATE ----
    _TABULATE_STATS = {"sum": "sum", "mean": "mean", "n": "count"}

    @classmethod
    def _split_tabulate_axis(cls, text: str) -> list:
        """Split a TABLE axis on '*' but keep parenthesized stat groups
        ('sales*(sum mean)') together as single parts."""
        parts, buf, depth = [], "", 0
        for c in text:
            if c == "(":
                depth += 1
            elif c == ")":
                depth = max(0, depth - 1)
            if c == "*" and depth == 0:
                if buf.strip():
                    parts.append(buf.strip().lower())
                buf = ""
            else:
                buf += c
        if buf.strip():
            parts.append(buf.strip().lower())
        return parts

    @classmethod
    def _parse_tabulate_axis(cls, text: str) -> tuple:
        """Parse one side of a TABLE row, col statement: a plain class var
        ('rowvar'), or class-var(s) chained with an analysis var and stat
        keyword(s) via '*' ('colvar*analysisvar*mean', leading-stat
        'colvar*mean*analysisvar', or multi-stat 'colvar*(sum mean)').
        Returns (class_vars, analysis_var_or_None, stats_list). Only this
        shape is supported -- no nested groupings within one axis, no
        PCTN/other TABULATE-specific statistics."""
        parts = cls._split_tabulate_axis(text)
        stats: list = []
        rest = []
        for p in parts:
            if p.startswith("(") and p.endswith(")"):
                for tok in p[1:-1].split():
                    if tok in cls._TABULATE_STATS and tok not in stats:
                        stats.append(tok)
            else:
                rest.append(p)
        parts = rest
        if parts and parts[-1] in cls._TABULATE_STATS:
            stat = parts.pop()
            if stat not in stats:
                stats.append(stat)
        elif len(parts) >= 2 and parts[-2] in cls._TABULATE_STATS:
            # leading-stat form: class*stat*analysis
            stat = parts.pop(-2)
            if stat not in stats:
                stats.append(stat)
        analysis_var = None
        if stats and parts:
            analysis_var = parts.pop()
        return parts, analysis_var, stats

    def _gen_proc_tabulate(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        table_raw = self._clause(proc, "table")
        if not table_raw:
            raise CodegenError("PROC TABULATE requires a TABLE statement")
        if "," in table_raw:
            row_text, col_text = table_raw.split(",", 1)
        else:
            row_text, col_text = table_raw, ""
        row_classes, row_var, row_stats = self._parse_tabulate_axis(row_text)
        col_classes, col_var, col_stats = self._parse_tabulate_axis(col_text)
        analysis_var = row_var or col_var
        stats = row_stats or col_stats or ["sum"]
        if not analysis_var:
            var_clause = self._clause(proc, "var")
            analysis_var = var_clause[0][0] if var_clause else None
        if not analysis_var:
            raise CodegenError(
                "PROC TABULATE: could not determine the analysis variable "
                "(add a VAR statement, or var*stat on one TABLE axis)"
            )

        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w("_tab_outs = []")
        for stat in stats:
            aggfunc = self._TABULATE_STATS.get(stat, "sum")
            self.w(f"print('--- {stat.upper()} ---')")
            self.w(
                f"_piv = pd.pivot_table(_df, values={analysis_var!r}, "
                f"index={row_classes!r} or None, columns={col_classes!r} or None, "
                f"aggfunc={aggfunc!r})"
            )
            self.w("_piv.columns.name = None")
            self.w("print(_piv.to_string())")
            self.w(f"_t = _piv.reset_index(); _t['_stat_'] = {stat!r}; _tab_outs.append(_t)")
        out = proc.options.get("out")
        if isinstance(out, str):
            out_name = normalize_dsname(out)
            self.w("_piv_out = pd.concat(_tab_outs, ignore_index=True)")
            self._store_out(out, out_name, "_piv_out")

    # ---- PROC SGPLOT ----
    _SGPLOT_KV_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)")
    _SGPLOT_KINDS = ("scatter", "series", "vbar", "hbar", "histogram", "density", "refline")

    def _parse_sgplot_stmt(self, ckw: str, raw: str) -> dict:
        kv = {k.lower(): v.lower() for k, v in self._SGPLOT_KV_RE.findall(raw)}
        if ckw in ("scatter", "series"):
            if "x" not in kv or "y" not in kv:
                raise CodegenError(f"PROC SGPLOT {ckw.upper()} requires X= and Y=")
            return {"kind": ckw, "x": kv["x"], "y": kv["y"]}
        if ckw in ("vbar", "hbar"):
            body = raw.split("/", 1)[0]
            m = re.search(r"(?:vbar|hbar)\s+([A-Za-z_]\w*)", body, re.I)
            if not m:
                raise CodegenError(f"PROC SGPLOT {ckw.upper()} requires a category variable")
            return {"kind": ckw, "category": m.group(1).lower(), "response": kv.get("response")}
        if ckw in ("histogram", "density"):
            m = re.search(r"(?:histogram|density)\s+([A-Za-z_]\w*)", raw, re.I)
            if not m:
                raise CodegenError(f"PROC SGPLOT {ckw.upper()} requires a variable")
            return {"kind": ckw, "var": m.group(1).lower()}
        if ckw == "refline":
            body = raw.split("/", 1)[0]
            vals = [float(v) for v in re.findall(r"-?\d+\.?\d*(?:[eE][+-]?\d+)?", body)]
            return {"kind": ckw, "values": vals}
        raise CodegenError(f"unrecognized PROC SGPLOT statement: {ckw!r}")

    def _gen_proc_sgplot(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        plots = [
            self._parse_sgplot_stmt(ckw, raw)
            for ckw, raw in proc.clauses
            if ckw in self._SGPLOT_KINDS
        ]
        if not plots:
            raise CodegenError(
                "PROC SGPLOT requires at least one SCATTER/SERIES/VBAR/HBAR/HISTOGRAM/DENSITY/REFLINE statement"
            )
        self.sgplot_counter += 1
        out_path = proc.options.get("out")
        if not isinstance(out_path, str):
            out_path = f"sgplot_{self.sgplot_counter}.png"
        title = proc.options.get("title") if isinstance(proc.options.get("title"), str) else None
        self.w(f"_df = {self._proc_src(proc, dsname)}")
        self._gen_proc_filters(proc)
        self.w(f"_r.proc_sgplot_render(_df, {plots!r}, {out_path!r}, title={title!r})")

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

        self.w(f"_df = ({self._proc_src(proc, dsname)}).copy()")
        self._gen_proc_filters(proc)
        groups = proc.options.get("groups")
        ngroups = None
        if isinstance(groups, (int, float)) or (isinstance(groups, str) and str(groups).isdigit()):
            ngroups = int(groups)
        if by_clause:
            by_list = [n for n, _ in by_clause]
            self.w(f"_rank_src = _df.groupby({by_list!r}, dropna=False)[{var_list!r}]")
        else:
            self.w(f"_rank_src = _df[{var_list!r}]")
        if ngroups:
            # GROUPS=n: 0-based ntile buckets from the average ranks
            self.w(f"_ranked = _rank_src.rank(method='average', ascending={ascending!r}, na_option='keep')")
            self.w(f"_ranked = (((_ranked - 1) * {ngroups} // _ranked.count()).clip(upper={ngroups - 1}).astype('Int64'))")
        else:
            self.w(f"_ranked = _rank_src.rank(method='average', ascending={ascending!r}, na_option='keep')")
        for v, rn in zip(var_list, rank_names):
            self.w(f"_df[{rn!r}] = _ranked[{v!r}]")
        self._store_out(
            proc.options.get("out") if isinstance(proc.options.get("out"), str) else None,
            out, "_df",
        )

    def _gen_proc_standard(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        out = normalize_dsname(proc.options["out"]) if isinstance(proc.options.get("out"), str) else dsname
        var_clause = self._clause(proc, "var")
        if not var_clause:
            raise CodegenError("PROC STANDARD requires a VAR statement")
        var_list = [n for n, _ in var_clause]
        target_mean = float(proc.options.get("mean", 0))
        target_std = float(proc.options.get("std", 1))
        replace = bool(proc.options.get("replace"))

        self.w(f"_df = ({self._proc_src(proc, dsname)}).copy()")
        self._gen_proc_filters(proc)
        self.w(f"_df = _r.proc_standardize(_df, {var_list!r}, {target_mean!r}, {target_std!r}, {replace!r})")
        self._store_out(
            proc.options.get("out") if isinstance(proc.options.get("out"), str) else None,
            out, "_df",
        )

    def _gen_proc_surveyselect(self, proc: A.ProcStep):
        dsname = self._resolve_ds(proc)
        out_raw = proc.options.get("out") if isinstance(proc.options.get("out"), str) else None
        if not out_raw:
            raise CodegenError("PROC SURVEYSELECT requires OUT=")
        out = normalize_dsname(out_raw)

        method = proc.options.get("method")
        method = str(method).lower() if method is not None and method is not True else "srs"
        if method != "srs":
            raise CodegenError(f"PROC SURVEYSELECT METHOD={method.upper()} is not supported (only METHOD=SRS)")

        n_opt = proc.options.get("n")
        samprate_opt = proc.options.get("samprate")
        if n_opt is None and samprate_opt is None:
            raise CodegenError("PROC SURVEYSELECT requires exactly one of N= or SAMPRATE=")
        if n_opt is not None and samprate_opt is not None:
            raise CodegenError("PROC SURVEYSELECT accepts only one of N= or SAMPRATE=, not both")
        n_val = int(float(n_opt)) if n_opt is not None else None
        samprate_val = float(samprate_opt) if samprate_opt is not None else None

        seed_opt = proc.options.get("seed")
        seed_val = int(float(seed_opt)) if seed_opt is not None else None

        strata_clause = self._clause(proc, "strata")
        strata_list = [n for n, _ in strata_clause] if strata_clause else None

        self.w(f"_df = ({self._proc_src(proc, dsname)}).copy()")
        self._gen_proc_filters(proc)
        self.w(
            f"_df = _r.proc_surveyselect(_df, n={n_val!r}, samprate={samprate_val!r}, "
            f"seed={seed_val!r}, strata={strata_list!r})"
        )
        self._store_out(out_raw, out, "_df")

    def _opt_src(self, raw: str | None, flat: str) -> str:
        """Like _proc_src but for BASE=/DATA= style options carrying a raw
        dotted name plus its normalized flat name."""
        if isinstance(raw, str) and "." in raw:
            lib, tbl = raw.split(".", 1)
            if lib.lower() in self.db_libs and lib.lower() != "work":
                return f"_r.db_read_table({lib.lower()!r}, {tbl.lower()!r})"
        return f"_DS[{flat!r}]"

    def _gen_proc_append(self, proc: A.ProcStep):
        base_raw = proc.options.get("base") if isinstance(proc.options.get("base"), str) else None
        data_raw = proc.options.get("data") if isinstance(proc.options.get("data"), str) else None
        base = normalize_dsname(base_raw) if base_raw else None
        data = normalize_dsname(data_raw) if data_raw else None
        if not base or not data:
            raise CodegenError("PROC APPEND requires BASE= and DATA=")
        self.w(f"_DS[{base!r}] = pd.concat([{self._opt_src(base_raw, base)}, {self._opt_src(data_raw, data)}], ignore_index=True)")
        if isinstance(base_raw, str) and "." in base_raw:
            lib, tbl = base_raw.split(".", 1)
            if lib.lower() in self.db_libs and lib.lower() != "work":
                self.w(f"_r.db_write_table({lib.lower()!r}, {tbl.lower()!r}, _DS[{base!r}])")
        self.last_ds_name = base


def generate(prog: A.Program, nested: bool = False) -> str:
    return CodeGen().generate(prog, nested=nested)
