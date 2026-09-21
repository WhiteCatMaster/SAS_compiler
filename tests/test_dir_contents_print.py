import pandas as pd
import pytest

from sas_compiler import compile_source
from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse, ParseError
from sas_compiler.codegen import generate


def run_print(source: str, capsys) -> str:
    """Compile + execute SAS that only prints; return captured stdout."""
    code = compile_source(source)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return capsys.readouterr().out


def test_bom_first_statement_not_dropped():
    src = "﻿libname mylib \"/tmp\";\nproc print data=mylib.t;\nrun;\n"
    prog = parse(MacroProcessor().expand(src))
    assert prog.steps[0].__class__.__name__ == "LibnameStmt"
    assert prog.steps[0].libref == "mylib"


def test_date_literal_parses_to_sas_number():
    prog = parse("proc print data=x; where d > '01Jan2010'd; run;")
    cond = prog.steps[0].clauses[-1][1]
    assert cond.right.value == 18263.0


def test_date_literal_two_digit_year_and_case():
    prog = parse("data a; x = '1jan60'd; run;")
    assign = prog.steps[0].statements[0]
    assert assign.expr.value == 0.0  # 1960-01-01 is day zero


def test_non_date_string_with_d_stays_string():
    # 'US' followed by keyword AND: no date parsing
    prog = parse("proc print data=x; where c = 'US' and s > 1; run;")
    cond = prog.steps[0].clauses[-1][1]
    assert cond.left.right.value == "US"
    # space between quote and d: not a SAS date literal
    prog = parse("data a; x = '01Jan2010' d; run;")
    assert prog.steps[0].statements[0].expr.value == "01Jan2010"


def test_bare_data_option_is_clear_error():
    with pytest.raises(ParseError, match="DATA="):
        parse("proc print data foo; run;")


def test_dir_libname_reads_csv(tmp_path):
    from sas_compiler import runtime as r

    df = pd.DataFrame({"a": [1.0, 2.0], "b": ["x", "y"]})
    df.to_csv(tmp_path / "t.csv", index=False)
    r.libname("d", str(tmp_path))
    try:
        got = r.db_read_table("d", "t")
    finally:
        r.libname_clear("d")
    assert list(got.columns) == ["a", "b"]
    assert len(got) == 2


def test_dir_libname_missing_table_errors(tmp_path):
    from sas_compiler import runtime as r

    r.libname("d", str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="no table"):
            r.db_read_table("d", "nope")
    finally:
        r.libname_clear("d")


def test_proc_contents_runs(tmp_path, capsys):
    pd.DataFrame({"a": [1.0], "b": ["x"]}).to_csv(tmp_path / "t.csv", index=False)
    out = run_print(
        f'libname d "{tmp_path}";\nproc contents data=d.t;\nrun;\n', capsys
    )
    assert "NOBS=1" in out
    assert "a (" in out and "b (" in out


def test_proc_print_where_obs_noobs_sum(tmp_path, capsys):
    pd.DataFrame({"name": ["a", "b", "c"], "sal": [10.0, 20.0, 30.0]}).to_csv(
        tmp_path / "t.csv", index=False
    )
    base = f'libname d "{tmp_path}";\n'
    out = run_print(base + "proc print data=d.t;\nwhere sal > 15;\nrun;\n", capsys)
    assert "b" in out and "c" in out
    assert "\n3 " not in out  # only 2 rows pass the filter
    out = run_print(base + "proc print data=d.t (obs=2);\nrun;\n", capsys)
    assert "a" in out and "b" in out and "c" not in out
    out = run_print(base + "proc print data=d.t noobs;\nvar name sal;\nsum sal;\nrun;\n", capsys)
    assert "Obs" not in out
    assert "Total" in out and "60" in out


def test_proc_print_work_dataset_still_prints(capsys):
    # a plain work dataset still prints (no libref involved)
    out = run_print(
        "data w; x = 1; output; x = 2; output; run;\nproc print data=w;\nrun;\n",
        capsys,
    )
    assert "Obs" in out and "2" in out


def test_proc_out_writes_through_to_dir_lib(tmp_path, capsys):
    from sas_compiler import runtime as r

    src = (
        f'libname d "{tmp_path}";\n'
        "data w; v = 1; output; v = 2; output; run;\n"
        "proc sort data=w out=d.srt;\n  by descending v;\nrun;\n"
        "proc print data=d.srt;\nrun;\n"
    )
    out = run_print(src, capsys)
    assert "2" in out
    back = r.db_read_table("d", "srt")
    assert list(back["v"]) == [2.0, 1.0]
    r.libname_clear("d")


def test_proc_append_dir_lib_base(tmp_path, capsys):
    from sas_compiler import runtime as r

    src = (
        f'libname d "{tmp_path}";\n'
        "data d.base;\n  input n;\n  datalines;\n1\n2\n;\nrun;\n"
        "data new; n = 3; run;\n"
        "proc append base=d.base data=new;\nrun;\n"
        "proc print data=d.base;\nrun;\n"
    )
    out = run_print(src, capsys)
    assert "3" in out
    assert len(r.db_read_table("d", "base")) == 3
    r.libname_clear("d")


def test_proc_where_and_obs_generalize(tmp_path, capsys):
    pd.DataFrame({"g": ["a", "a", "b", "b"], "v": [1.0, 2.0, 3.0, 4.0]}).to_csv(
        tmp_path / "t.csv", index=False
    )
    base = f'libname d "{tmp_path}";\n'
    # WHERE inside PROC MEANS filters before aggregating (a: mean 1.5)
    out = run_print(
        base + "proc means data=d.t;\nvar v;\nwhere g = 'a';\nrun;\n", capsys
    )
    assert "1.5" in out and "3.5" not in out
    # OBS= inside PROC FREQ limits input rows (first 2 rows: a x2)
    out = run_print(
        base + "proc freq data=d.t (obs=2);\ntables g;\nrun;\n", capsys
    )
    assert "2" in out
    # WHERE inside PROC SORT filters before sorting
    out = run_print(
        base + "proc sort data=d.t out=s;\nby v;\nwhere v > 2;\nrun;\n"
        "proc print data=s;\nrun;\n",
        capsys,
    )
    assert "3.0" in out and "4.0" in out and "1.0" not in out


def test_proc_sql_queries_dir_lib(tmp_path, capsys):
    pd.DataFrame({"g": ["a", "a", "b"], "v": [1.0, 2.0, 3.0]}).to_csv(
        tmp_path / "t.csv", index=False
    )
    out = run_print(
        f'libname d "{tmp_path}";\n'
        "proc sql;\n"
        "  select g, avg(v) as m from d.t group by g;\n"
        "quit;\n",
        capsys,
    )
    assert "1.5" in out and "3.0" in out
