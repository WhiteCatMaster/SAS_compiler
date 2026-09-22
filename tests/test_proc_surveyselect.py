import pytest

from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse
from sas_compiler.codegen import generate, CodegenError


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


DATA_20 = """
data src;
  input x;
  datalines;
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
;
run;
"""


def test_surveyselect_n_exact_row_count():
    src = DATA_20 + """
    proc surveyselect data=src out=sample method=srs n=10 seed=123;
    run;
    """
    ds = run_sas(src)
    assert len(ds["sample"]) == 10
    assert list(ds["sample"].columns) == ["x"]


def test_surveyselect_samprate_approximate_row_count():
    src = DATA_20 + """
    proc surveyselect data=src out=sample method=srs samprate=0.3 seed=123;
    run;
    """
    ds = run_sas(src)
    assert len(ds["sample"]) == round(0.3 * 20)


def test_surveyselect_seed_reproducibility():
    src1 = DATA_20 + """
    proc surveyselect data=src out=sample method=srs n=10 seed=42;
    run;
    """
    src2 = DATA_20 + """
    proc surveyselect data=src out=sample method=srs n=10 seed=42;
    run;
    """
    ds1 = run_sas(src1)
    ds2 = run_sas(src2)
    assert list(ds1["sample"]["x"]) == list(ds2["sample"]["x"])


def test_surveyselect_strata_per_group_counts_and_clamping():
    src = """
    data src;
      input region $ x;
      datalines;
    A 1
    A 2
    A 3
    A 4
    A 5
    A 6
    A 7
    A 8
    A 9
    A 10
    B 1
    B 2
    B 3
    ;
    run;
    proc surveyselect data=src out=sample method=srs n=5 seed=7;
      strata region;
    run;
    """
    ds = run_sas(src)
    out = ds["sample"]
    counts = out.groupby("region")["x"].count()
    # stratum A has 10 rows, N=5 requested -> exactly 5
    assert counts["A"] == 5
    # stratum B has only 3 rows, N=5 requested -> clamped to all 3, no error
    assert counts["B"] == 3
    assert len(out) == 8


def test_surveyselect_missing_out_raises():
    src = DATA_20 + """
    proc surveyselect data=src method=srs n=10;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_surveyselect_neither_n_nor_samprate_raises():
    src = DATA_20 + """
    proc surveyselect data=src out=sample method=srs;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_surveyselect_both_n_and_samprate_raises():
    src = DATA_20 + """
    proc surveyselect data=src out=sample method=srs n=10 samprate=0.5;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_surveyselect_unsupported_method_raises():
    src = DATA_20 + """
    proc surveyselect data=src out=sample method=systematic n=10;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
