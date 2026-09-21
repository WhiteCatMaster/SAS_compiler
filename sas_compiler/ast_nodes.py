"""AST node definitions for the SAS DATA step / PROC step language."""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------- expressions ----------------
class Expr:
    pass


@dataclass
class Num(Expr):
    value: float


@dataclass
class Str(Expr):
    value: str


@dataclass
class Missing(Expr):
    pass


@dataclass
class Var(Expr):
    name: str


@dataclass
class ArrayRef(Expr):
    name: str
    index: Expr


@dataclass
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class UnaryOp(Expr):
    op: str
    operand: Expr


@dataclass
class Call(Expr):
    name: str
    args: list


@dataclass
class DotVar(Expr):
    """first.var / last.var by-group markers."""
    kind: str  # 'first' or 'last'
    var: str


@dataclass
class HashMethodCall(Expr):
    """<hashname>.<method>(args) -- a DATA step HASH object method call,
    usable either as an expression (e.g. `rc = h.find();`) or, wrapped in
    ExprStmt, as a standalone statement (e.g. `h.add();`)."""
    hashname: str
    method: str
    args: list  # [(argname_or_None, Expr)]


# ---------------- statements ----------------
class Stmt:
    pass


@dataclass
class Assign(Stmt):
    target: Expr
    expr: Expr


@dataclass
class If(Stmt):
    cond: Expr
    then: list
    orelse: list


@dataclass
class DoBlock(Stmt):
    kind: str  # 'block' | 'iterative' | 'while' | 'until'
    var: str | None
    start: Expr | None
    stop: Expr | None
    by: Expr | None
    cond: Expr | None
    body: list
    over_array: str | None = None


@dataclass
class Output(Stmt):
    dataset: str | None


@dataclass
class SetStmt(Stmt):
    datasets: list  # [(name, options_dict)]


@dataclass
class MergeStmt(Stmt):
    datasets: list
    by: list


@dataclass
class ByStmt(Stmt):
    vars: list  # list of (name, descending: bool)


@dataclass
class ArrayStmt(Stmt):
    name: str
    dim: int | None
    elements: list
    is_char: bool
    length: int | None
    init_values: list = field(default_factory=list)
    lo_bound: int = 1
    is_temporary: bool = False


@dataclass
class RetainStmt(Stmt):
    entries: list  # [(name, initial_or_None)]


@dataclass
class DropStmt(Stmt):
    vars: list


@dataclass
class KeepStmt(Stmt):
    vars: list


@dataclass
class LengthStmt(Stmt):
    entries: list  # [(name, is_char, length)]


@dataclass
class FormatStmt(Stmt):
    entries: list  # [(var, fmt)]


@dataclass
class LabelStmt(Stmt):
    entries: list  # [(var, label)]


@dataclass
class PutStmt(Stmt):
    args: list


@dataclass
class CallStmt(Stmt):
    name: str
    args: list


@dataclass
class WhereStmt(Stmt):
    cond: Expr


@dataclass
class InputStmt(Stmt):
    vars: list  # [(name, is_char)]


@dataclass
class DatalinesStmt(Stmt):
    lines: list


@dataclass
class RawStmt(Stmt):
    text: str


@dataclass
class DeleteStmt(Stmt):
    pass


@dataclass
class ReturnStmt(Stmt):
    pass


@dataclass
class ExprStmt(Stmt):
    """An expression used as a standalone statement (currently only
    produced for a bare HASH object method call, e.g. `h.add();`)."""
    expr: Expr


@dataclass
class SelectStmt(Stmt):
    """SELECT [expr]; WHEN (v1, ...) stmt; ... [OTHERWISE stmt;] END;"""
    select_expr: Expr | None  # None means bare SELECT (WHEN holds conditions)
    whens: list  # [(conds: list[Expr], body: list)]
    otherwise: list = field(default_factory=list)


@dataclass
class StopStmt(Stmt):
    pass


@dataclass
class LeaveStmt(Stmt):
    pass


@dataclass
class ContinueStmt(Stmt):
    pass


@dataclass
class UpdateStmt(Stmt):
    datasets: list
    by: list = field(default_factory=list)


@dataclass
class InfileStmt(Stmt):
    """INFILE "path" [DLM=..] [DSD] [FIRSTOBS=n] [OBS=n] [TRUNCOVER/MISSOVER]."""
    path: str
    dlm: str | None = None
    dsd: bool = False
    firstobs: int = 1
    obs: int | None = None
    truncover: bool = False


@dataclass
class FileStmt(Stmt):
    """FILE "path" [MOD] [DLM=..] -- redirects PUT output for the DATA step.
    The reserved filerefs LOG/PRINT write to stdout."""
    path: str
    mod: bool = False
    dlm: str | None = None


@dataclass
class AbortStmt(Stmt):
    message: str | None = None


@dataclass
class DeclareHashStmt(Stmt):
    """declare hash <name>(args); -- args e.g. [("dataset", Str("lookup"))]."""
    hashname: str
    args: list  # [(argname_or_None, Expr)]


@dataclass
class DeclareHiterStmt(Stmt):
    """declare hiter <itername>("hashname"); -- hash iterator object."""
    itername: str
    hashname: str


@dataclass
class TitleStmt(Stmt):
    """TITLE/FOOTNOTE statement: printed atop/below PROC PRINT output."""
    text: str
    kind: str  # 'title' or 'footnote'


# ---------------- top-level steps ----------------
@dataclass
class DataStep:
    outputs: list  # [(dataset_name, options_dict)]
    statements: list
    is_null: bool = False


@dataclass
class ProcStep:
    name: str
    options: dict
    clauses: list  # list of (clause_name, raw_text or structured)
    body_statements: list = field(default_factory=list)


@dataclass
class LibnameStmt:
    libref: str
    conn: str | None  # None means "libname libref clear;"


@dataclass
class OdsStmt:
    """ODS HTML FILE="path"; / ODS HTML CLOSE; / bare ODS HTML;"""
    action: str  # 'open' or 'close'
    destination: str  # 'html'
    path: str | None = None


@dataclass
class Program:
    steps: list
