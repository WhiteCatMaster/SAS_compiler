from sas_compiler import compile_source


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    code = compile_source(source)
    g: dict = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


def run_print(source: str, capsys) -> str:
    code = compile_source(source)
    g: dict = {}
    exec(compile(code, "<test>", "exec"), g)
    return capsys.readouterr().out


IDENTICAL_SRC = """
data a;
  input id x y $;
  datalines;
1 10 aa
2 20 bb
;
run;
data b;
  input id x y $;
  datalines;
1 10 aa
2 20 bb
;
run;
"""

DIFFERING_SRC = """
data a;
  input id x y $;
  datalines;
1 10 aa
2 20 bb
3 30 cc
;
run;
data b;
  input id x y $;
  datalines;
1 10 aa
2 25 bb
3 30 zz
;
run;
"""


def test_identical_datasets_reports_no_unequal_values(capsys):
    out = run_print(IDENTICAL_SRC + "proc compare base=a compare=b; run;", capsys)
    assert "No unequal values were found" in out
    assert "Base dataset:     2 observations" in out
    assert "Compare dataset:  2 observations" in out


def test_differing_datasets_flag_correct_variables_and_ids(capsys):
    out = run_print(DIFFERING_SRC + "proc compare base=a compare=b; id id; run;", capsys)
    assert "No unequal values were found" not in out
    # x differs only for id=2, y differs only for id=3
    assert "x" in out and "y" in out
    assert "id=2" in out
    assert "id=3" in out
    assert "Observations compared: 3" in out
    assert "Observations with all compared values equal: 1" in out


def test_differing_datasets_positional_alignment_no_id(capsys):
    out = run_print(DIFFERING_SRC + "proc compare base=a compare=b; run;", capsys)
    assert "Observations compared: 3" in out
    assert "Observations with all compared values equal: 1" in out


def test_var_statement_restricts_compared_variables(capsys):
    out = run_print(DIFFERING_SRC + "proc compare base=a compare=b; id id; var x; run;", capsys)
    # only x is compared; y's difference (id=3) must not be reported
    assert "id=2" in out
    assert "id=3" not in out


def test_columns_only_in_one_dataset_are_flagged(capsys):
    src = """
    data a; input id x; datalines;
    1 10
    ;
    run;
    data b; input id x z; datalines;
    1 10 99
    ;
    run;
    """
    out = run_print(src + "proc compare base=a compare=b; id id; run;", capsys)
    assert "Variables in COMPARE but not in BASE: z" in out


def test_row_count_mismatch_without_id_is_flagged(capsys):
    src = """
    data a; input x; datalines;
    1
    2
    ;
    run;
    data b; input x; datalines;
    1
    2
    3
    ;
    run;
    """
    out = run_print(src + "proc compare base=a compare=b; run;", capsys)
    assert "Observations in COMPARE but not in BASE: 1" in out


def test_out_dataset_contains_long_form_differences():
    src = DIFFERING_SRC + "proc compare base=a compare=b out=diffs; id id; run;"
    ds = run_sas(src)
    diffs = ds["diffs"]
    assert set(diffs.columns) == {"_id_", "_var_", "_base_", "_compare_"}
    assert len(diffs) == 2
    assert set(diffs["_var_"]) == {"x", "y"}
