import math

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


def test_proc_standard_default_zscore():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    4
    5
    ;
    run;
    proc standard data=src out=stdized mean=0 std=1;
      var x;
    run;
    """
    ds = run_sas(src)
    col = ds["stdized"]["x"]
    assert col.mean() == pytest.approx(0.0, abs=1e-9)
    assert col.std() == pytest.approx(1.0, abs=1e-9)


def test_proc_standard_custom_mean_std():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    4
    5
    ;
    run;
    proc standard data=src out=stdized mean=100 std=10;
      var x;
    run;
    """
    ds = run_sas(src)
    col = ds["stdized"]["x"]
    assert col.mean() == pytest.approx(100.0, abs=1e-9)
    assert col.std() == pytest.approx(10.0, abs=1e-9)


def test_proc_standard_replace_fills_missing_with_original_mean():
    src = """
    data src;
      input x y;
      datalines;
    1 10
    2 .
    3 30
    ;
    run;
    proc standard data=src out=stdized mean=0 std=1 replace;
      var x y;
    run;
    """
    ds = run_sas(src)
    out = ds["stdized"]
    # y's original (pre-standardization) mean is (10+30)/2 = 20, which
    # standardizes to 0 under MEAN=0 STD=1 -- so the previously-missing
    # row's standardized y should now be non-missing and equal to 0.
    assert not math.isnan(out["y"].iloc[1])
    assert out["y"].iloc[1] == pytest.approx(0.0, abs=1e-9)
    # x had no missing values, so it should be unaffected by REPLACE.
    assert out["x"].mean() == pytest.approx(0.0, abs=1e-9)


def test_proc_standard_missing_stays_missing_without_replace():
    src = """
    data src;
      input x y;
      datalines;
    1 10
    2 .
    3 30
    ;
    run;
    proc standard data=src out=stdized mean=0 std=1;
      var x y;
    run;
    """
    ds = run_sas(src)
    out = ds["stdized"]
    assert math.isnan(out["y"].iloc[1])


def test_proc_standard_zero_variance_column_is_all_missing():
    src = """
    data src;
      input x c;
      datalines;
    1 5
    2 5
    3 5
    ;
    run;
    proc standard data=src out=stdized;
      var x c;
    run;
    """
    ds = run_sas(src)
    out = ds["stdized"]
    assert out["c"].isna().all()
    # x still standardizes normally.
    assert out["x"].mean() == pytest.approx(0.0, abs=1e-9)


def test_proc_standard_out_defaults_to_input_dataset():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    ;
    run;
    proc standard data=src mean=0 std=1;
      var x;
    run;
    """
    ds = run_sas(src)
    assert ds["src"]["x"].mean() == pytest.approx(0.0, abs=1e-9)


def test_proc_standard_requires_var():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    ;
    run;
    proc standard data=src out=stdized;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
