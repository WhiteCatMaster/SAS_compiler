import pytest

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


def test_repeated_id_values_matched_pairwise(capsys):
    # BASE has three rows for id=1 (x=10,20,30), COMPARE has two (x=10,99).
    # SAS matches repeated ID occurrences pairwise in encounter order:
    # 1st-vs-1st (10 vs 10, equal), 2nd-vs-2nd (20 vs 99, differs), and
    # the extra 3rd BASE row is unmatched -- not silently dropped, and
    # not incorrectly compared against anything on the COMPARE side.
    src = """
    data a;
      input id x;
      datalines;
    1 10
    1 20
    1 30
    ;
    run;
    data b;
      input id x;
      datalines;
    1 10
    1 99
    ;
    run;
    """
    code = compile_source(src + "proc compare base=a compare=b out=diffs; id id; run;")
    g: dict = {}
    exec(compile(code, "<test>", "exec"), g)
    out = capsys.readouterr().out
    assert "Observations in BASE but not in COMPARE: 1" in out
    assert "Observations compared: 2" in out
    assert "Observations with all compared values equal: 1" in out

    diffs = g["_DS"]["diffs"]
    assert len(diffs) == 1
    row = diffs.iloc[0]
    assert row["_var_"] == "x"
    assert row["_base_"] == 20.0
    assert row["_compare_"] == 99.0


def test_transform_abs_flips_diff_to_match(capsys):
    # x=4 vs x=-4 differ under plain comparison, but TRANSFORM x=ABS
    # applies abs() to both sides first, making them equal.
    src = """
    data a; x = 4; run;
    data b; x = -4; run;
    """
    out_no_transform = run_print(src + "proc compare base=a compare=b; run;", capsys)
    assert "No unequal values" not in out_no_transform

    out_transformed = run_print(
        src + "proc compare base=a compare=b; transform x = abs; run;", capsys
    )
    assert "No unequal values were found" in out_transformed


def test_transform_log_applies_before_comparison():
    # TRANSFORM x=LOG should apply math.log to both BASE and COMPARE
    # before comparing, so the diff records carry the log-transformed
    # values rather than the raw ones.
    import math

    src = """
    data a; x = 100; run;
    data b; x = 50; run;
    proc compare base=a compare=b out=diffs; transform x = log; run;
    """
    ds = run_sas(src)
    diffs = ds["diffs"]
    assert len(diffs) == 1
    row = diffs.iloc[0]
    assert row["_base_"] == pytest.approx(math.log(100))
    assert row["_compare_"] == pytest.approx(math.log(50))
