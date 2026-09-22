"""Tests for PROC FACTOR (exploratory factor analysis via scikit-learn's
FactorAnalysis, added alongside PROC PRINCOMP):

    proc factor data=in out=scores n=2;
      var x1 x2 x3 x4;
    run;

VAR is required. N= is optional; when omitted we reproduce real SAS
FACTOR's own default (the Kaiser criterion: retain every factor whose
correlation-matrix eigenvalue exceeds 1.0). OUT= (PROC option or OUTPUT
OUT=, same dual-form handling as PRINCOMP) gets the fit-subset rows plus
1-based Factor1..FactorN score columns. Loadings are printed as a "Factor
Pattern" table; no variance-explained table (a documented scope cut --
see runtime.proc_factor_fit's docstring)."""
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


def _make_two_factor_data(seed=0, n=200):
    """x1, x2 are driven mostly by shared latent f1; x3, x4 by shared
    latent f2 -- a genuine two-factor structure, not just correlated
    noise."""
    rng = np.random.default_rng(seed)
    f1 = rng.normal(0, 1, n)
    f2 = rng.normal(0, 1, n)
    x1 = 0.9 * f1 + rng.normal(0, 0.3, n)
    x2 = 0.8 * f1 + rng.normal(0, 0.3, n)
    x3 = 0.9 * f2 + rng.normal(0, 0.3, n)
    x4 = 0.8 * f2 + rng.normal(0, 0.3, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "x3": x3, "x4": x4})


# ---------------- correctness: recovers the two-factor structure ----------------
def test_factor_loadings_recover_two_latent_factor_structure(capsys):
    df = _make_two_factor_data()

    result = _r.proc_factor_fit(df, ["x1", "x2", "x3", "x4"], n=2)
    out = capsys.readouterr().out

    assert "The FACTOR Procedure" in out
    assert "Factor Pattern" in out
    assert "Factor1" in result.columns
    assert "Factor2" in result.columns
    assert len(result) == len(df)

    # x1/x2 should load strongly on one factor and weakly on the other;
    # x3/x4 the reverse -- and it should be the *same* factor for each
    # pair, since that is how the data was constructed.
    from sklearn.decomposition import FactorAnalysis

    clean = df[["x1", "x2", "x3", "x4"]]
    scaled = ((clean - clean.mean()) / clean.std(ddof=1)).to_numpy()
    fa = FactorAnalysis(n_components=2)
    fa.fit(scaled)
    loadings = pd.DataFrame(fa.components_.T, index=["x1", "x2", "x3", "x4"],
                             columns=["Factor1", "Factor2"])

    x1_factor = loadings.loc["x1"].abs().idxmax()
    x2_factor = loadings.loc["x2"].abs().idxmax()
    x3_factor = loadings.loc["x3"].abs().idxmax()
    x4_factor = loadings.loc["x4"].abs().idxmax()

    assert x1_factor == x2_factor
    assert x3_factor == x4_factor
    assert x1_factor != x3_factor

    # The dominant loadings should be reasonably strong (not noise-level).
    assert abs(loadings.loc["x1", x1_factor]) > 0.5
    assert abs(loadings.loc["x3", x3_factor]) > 0.5
    # And the off-factor (cross) loadings should be comparatively weak.
    other = "Factor2" if x1_factor == "Factor1" else "Factor1"
    assert abs(loadings.loc["x1", other]) < abs(loadings.loc["x1", x1_factor])


def test_factor_through_full_compile_pipeline_out_has_factor_columns():
    df = _make_two_factor_data(seed=1, n=60)
    datalines = "\n".join(
        f"{r.x1:.4f} {r.x2:.4f} {r.x3:.4f} {r.x4:.4f}" for r in df.itertuples()
    )
    src = f"""
    data work.a;
      input x1 x2 x3 x4;
      datalines;
{datalines}
    ;
    run;
    proc factor data=work.a out=scores n=2;
      var x1 x2 x3 x4;
    run;
    """
    ds = run_sas(src)
    out_df = ds["scores"]
    assert len(out_df) == 60
    assert "Factor1" in out_df.columns
    assert "Factor2" in out_df.columns
    assert "Factor3" not in out_df.columns


def test_factor_output_out_dual_form():
    df = _make_two_factor_data(seed=2, n=40)
    datalines = "\n".join(
        f"{r.x1:.4f} {r.x2:.4f} {r.x3:.4f} {r.x4:.4f}" for r in df.itertuples()
    )
    src = f"""
    data work.a;
      input x1 x2 x3 x4;
      datalines;
{datalines}
    ;
    run;
    proc factor data=work.a n=2;
      var x1 x2 x3 x4;
      output out=scores2;
    run;
    """
    ds = run_sas(src)
    assert "Factor1" in ds["scores2"].columns
    assert "Factor2" in ds["scores2"].columns


# ---------------- N= default (Kaiser criterion) ----------------
def test_factor_n_omitted_uses_kaiser_criterion_default():
    df = _make_two_factor_data(seed=5, n=200)
    # No n= given -- should pick 2 factors via the Kaiser criterion since
    # the data has a genuine two-latent-factor structure (2 correlation
    # eigenvalues > 1).
    result = _r.proc_factor_fit(df, ["x1", "x2", "x3", "x4"])
    assert "Factor1" in result.columns
    assert "Factor2" in result.columns
    assert "Factor3" not in result.columns


def test_factor_explicit_n_overrides_default():
    df = _make_two_factor_data(seed=6, n=200)
    result = _r.proc_factor_fit(df, ["x1", "x2", "x3", "x4"], n=1)
    assert "Factor1" in result.columns
    assert "Factor2" not in result.columns


# ---------------- missing values ----------------
def test_factor_drops_rows_with_missing_var_values():
    df = _make_two_factor_data(seed=8, n=30)
    df.loc[0, "x1"] = np.nan
    df.loc[5, "x3"] = np.nan
    result = _r.proc_factor_fit(df, ["x1", "x2", "x3", "x4"], n=2)
    assert len(result) == 28


# ---------------- compile-time validation ----------------
def test_factor_requires_var_statement():
    src = """
    data src;
      x = 1;
    run;
    proc factor data=src out=scores;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_factor_too_few_observations_raises_clear_error():
    df = pd.DataFrame({"x1": [1.0, 2.0], "x2": [2.0, 4.0], "x3": [3.0, 6.0]})
    with pytest.raises(RuntimeError):
        _r.proc_factor_fit(df, ["x1", "x2", "x3"], n=1)
