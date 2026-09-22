"""Tests for PROC PLS (partial least squares regression via scikit-learn's
PLSRegression)."""
import numpy as np
import pandas as pd
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


def _correlated_datalines() -> str:
    """y = 3 + 2*x1 - 1.5*x3 + noise, with x2 correlated with x1 and x4
    correlated with x3 -- a genuine linear-ish relationship plus collinear
    predictors, the setting PLS is designed for. Generated once (fixed
    seed 42) and pasted as literal datalines so the test has no runtime
    dependency on numpy's RNG stream."""
    rng = np.random.default_rng(42)
    n = 40
    x1 = rng.uniform(0, 10, n)
    x2 = x1 * 0.8 + rng.normal(0, 1, n)
    x3 = rng.uniform(0, 10, n)
    x4 = x3 * 0.9 + rng.normal(0, 1, n)
    y = 3 + 2 * x1 - 1.5 * x3 + rng.normal(0, 0.5, n)
    lines = [
        f"{y[i]:.4f} {x1[i]:.4f} {x2[i]:.4f} {x3[i]:.4f} {x4[i]:.4f}"
        for i in range(n)
    ]
    return "\n".join(lines)


def _pls_src(extra_proc_opts: str = "") -> str:
    return f"""
    data src;
      input y x1 x2 x3 x4;
      datalines;
{_correlated_datalines()}
    ;
    run;
    proc pls data=src out=scores {extra_proc_opts};
      model y = x1 x2 x3 x4;
    run;
    """


def test_pls_out_has_predicted_and_factor_columns_with_good_fit():
    src = _pls_src("nfac=2")
    ds = run_sas(src)
    df = ds["scores"]
    assert len(df) == 40
    assert "Predicted" in df.columns
    assert "Factor1" in df.columns
    assert "Factor2" in df.columns
    assert "Factor3" not in df.columns

    from sklearn.metrics import r2_score

    r2 = r2_score(df["y"], df["Predicted"])
    assert r2 > 0.8, f"expected a strong fit given the true linear relationship, got R^2={r2}"


def test_pls_explicit_nfac_controls_component_count():
    src = _pls_src("nfac=1")
    ds = run_sas(src)
    df = ds["scores"]
    assert "Factor1" in df.columns
    assert "Factor2" not in df.columns


def test_pls_default_nfac_when_omitted():
    # No NFAC= given: default resolves to min(2, len(xs)) = 2 here.
    src = _pls_src()
    ds = run_sas(src)
    df = ds["scores"]
    assert "Factor1" in df.columns
    assert "Factor2" in df.columns
    assert "Factor3" not in df.columns


def test_pls_requires_model_statement():
    src = """
    data src;
      y = 1; x1 = 2;
    run;
    proc pls data=src out=scores;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
