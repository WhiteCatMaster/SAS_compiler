"""Canary: core parser contracts other edits must not break.

If any of these fail, a concurrent edit has reverted or corrupted
parser support — restore the corresponding block in sas_compiler/parser.py.
"""

from sas_compiler.parser import (
    parse,
    parse_sas_date_literal,
    parse_sas_time_literal,
    parse_sas_datetime_literal,
)


def test_canary_date_time_datetime_helpers_exist():
    assert parse_sas_date_literal("01JAN2010") == 18263.0
    assert parse_sas_time_literal("12:34") == 12 * 3600 + 34 * 60
    assert parse_sas_datetime_literal("01JAN2010:01:00") == 18263 * 86400 + 3600


def test_canary_suffix_literals_parse():
    stmts = parse("data a; d='01JAN2010'd; t='01:02:03't; dt='2JAN60:00:00:01'dt; run;").steps[0].statements
    assert [s.expr.value for s in stmts] == [18263.0, 3723.0, 86401.0]


def test_canary_title_parses():
    step = parse('title "Hi"; data a; x = 1; run;').steps[0]
    assert step.__class__.__name__ == "TitleStmt" and step.text == "Hi"


def test_canary_datasets_dotted_names():
    clauses = parse("proc datasets; change d.k=d.r; delete d.x; run;").steps[0].clauses
    assert clauses[0] == ("change", [("d_k", "d_r", "d.k", "d.r")])
    assert clauses[1] == ("delete", [("d_x", "d.x")])


def test_canary_output_clause_keeps_raw():
    clauses = parse("proc means data=v; output out=d.o; run;").steps[0].clauses
    out = dict(clauses)["output"]
    assert out["out"] == "d_o" and out["out_raw"] == "d.o"
