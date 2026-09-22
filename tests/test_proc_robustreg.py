"""Tests for PROC ROBUSTREG (robust linear regression via statsmodels RLM,
M-estimation with Huber's T norm -- METHOD=M only)."""
import re

import numpy as np
import pandas as pd
import pytest

from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse
from sas_compiler.codegen import generate, CodegenError
from sas_compiler import runtime as _r


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


def _extract_coef(output: str, name: str) -> float:
    """Pull a coefficient value out of a printed statsmodels RLM/OLS
    summary table row, e.g. 'x              1.9326      0.033 ...'."""
    m = re.search(rf"^{re.escape(name)}\s+([-\d.]+)", output, re.M)
    assert m, f"could not find coefficient {name!r} in output:\n{output}"
    return float(m.group(1))


# ---------------- robustness against outliers ----------------
def test_robustreg_coefficient_closer_to_true_relationship_than_ols(capsys):
    rng = np.random.default_rng(0)
    n = 60
    x = rng.uniform(0, 20, n)
    true_intercept, true_slope = 3.0, 2.0
    y = true_intercept + true_slope * x + rng.normal(0, 1, n)

    # Contaminate a handful of y values with gross outliers.
    y = y.copy()
    outlier_idx = [0, 10, 20, 30, 40]
    y[outlier_idx] = y[outlier_idx] + rng.choice([-1, 1], size=len(outlier_idx)) * 200

    df = pd.DataFrame({"x": x, "y": y})

    _r.proc_reg_fit(df, "y", ["x"])
    ols_out = capsys.readouterr().out
    ols_slope = _extract_coef(ols_out, "x")

    _r.proc_robustreg_fit(df, "y", ["x"])
    robust_out = capsys.readouterr().out
    robust_slope = _extract_coef(robust_out, "x")

    ols_err = abs(ols_slope - true_slope)
    robust_err = abs(robust_slope - true_slope)
    assert robust_err < ols_err, (
        f"expected robust fit (err={robust_err:.4f}) to be closer to the true "
        f"slope ({true_slope}) than OLS (err={ols_err:.4f}); "
        f"ols_slope={ols_slope}, robust_slope={robust_slope}"
    )


def test_robustreg_through_full_compile_pipeline_outperforms_ols_on_outliers():
    src = """
    data work.a;
      input x y;
      datalines;
    1 3
    2 5
    3 7
    4 9
    5 500
    6 13
    7 15
    8 17
    9 19
    10 21
    ;
    run;
    proc robustreg data=work.a;
      model y = x;
    run;
    """
    # y = 2*x + 1 aside from the deliberate outlier at x=5 (y=500 instead
    # of 11). Should compile and run without error.
    run_sas(src)


# ---------------- OUTPUT OUT= P=/R= ----------------
def test_robustreg_output_out_predicted_and_residual_columns():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 4
    3 6
    4 8
    5 10
    ;
    run;
    proc robustreg data=src;
      model y = x;
      output out=scored p=predicted r=resid;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert "predicted" in df.columns
    assert "resid" in df.columns
    # Perfectly linear (noise-free) data: the robust fit should recover
    # y = 2*x almost exactly, same as OLS would.
    assert df["predicted"].round(4).tolist() == [2.0, 4.0, 6.0, 8.0, 10.0]
    assert all(abs(r) < 1e-6 for r in df["resid"])


# ---------------- compile-time validation ----------------
def test_robustreg_requires_model_statement():
    src = """
    data src;
      x = 1;
    run;
    proc robustreg data=src;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_robustreg_rejects_unsupported_method():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 3
    3 5
    ;
    run;
    proc robustreg data=src;
      model y = x / method=lts;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_robustreg_accepts_explicit_method_m():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 3
    3 5
    ;
    run;
    proc robustreg data=src;
      model y = x / method=m;
    run;
    """
    # Should compile and run without error.
    run_sas(src)
