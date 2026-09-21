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
class Program:
    steps: list
