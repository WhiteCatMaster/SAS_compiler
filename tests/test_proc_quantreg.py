"""Tests for PROC QUANTREG (quantile regression via statsmodels QuantReg,
a single QUANTILE= value only)."""
import re

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


def _extract_coef(output: str, name: str) -> float:
    """Pull a coefficient value out of a printed statsmodels QuantReg
    summary table row, e.g. 'x              1.9326      0.033 ...'."""
    m = re.search(rf"^{re.escape(name)}\s+([-\d.]+)", output, re.M)
    assert m, f"could not find coefficient {name!r} in output:\n{output}"
    return float(m.group(1))


_LINEAR_DATA = """
data src;
  input x y;
  datalines;
1 3
2 5
3 7
4 9
5 11
6 13
7 15
8 17
9 19
10 21
;
run;
"""


# ---------------- default quantile (0.5, the median) ----------------
def test_quantreg_default_quantile_is_median(capsys):
    src = _LINEAR_DATA + """
    proc quantreg data=src;
      model y = x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "The QUANTREG Procedure" in out
    assert "quantile = 0.5" in out
    # y = 2*x + 1, noise-free: the median fit should recover it almost exactly.
    slope = _extract_coef(out, "x")
    assert slope == pytest.approx(2.0, abs=0.05)


# ---------------- explicit QUANTILE= ----------------
def test_quantreg_explicit_quantile_value(capsys):
    src = _LINEAR_DATA + """
    proc quantreg data=src quantile=0.9;
      model y = x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "The QUANTREG Procedure" in out
    assert "quantile = 0.9" in out
    # Still noise-free perfectly linear data, so any quantile recovers the
    # same line.
    slope = _extract_coef(out, "x")
    assert slope == pytest.approx(2.0, abs=0.05)


# ---------------- OUTPUT OUT= P=/R= ----------------
def test_quantreg_output_out_predicted_and_residual_columns():
    src = _LINEAR_DATA + """
    proc quantreg data=src;
      model y = x;
      output out=scored p=predicted r=resid;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert "predicted" in df.columns
    assert "resid" in df.columns
    assert df["predicted"].round(4).tolist() == [3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0, 17.0, 19.0, 21.0]
    assert all(abs(r) < 1e-6 for r in df["resid"])


def test_quantreg_output_p_only():
    src = _LINEAR_DATA + """
    proc quantreg data=src;
      model y = x;
      output out=scored p=predicted;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert "predicted" in df.columns
    assert "resid" not in df.columns


# ---------------- compile-time validation ----------------
def test_quantreg_requires_model_statement():
    src = """
    data src;
      x = 1;
    run;
    proc quantreg data=src;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_quantreg_rejects_multiple_quantile_values():
    src = _LINEAR_DATA + """
    proc quantreg data=src quantile=0.25 0.5 0.75;
      model y = x;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_quantreg_rejects_out_of_range_quantile():
    src = _LINEAR_DATA + """
    proc quantreg data=src quantile=1.5;
      model y = x;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
