"""Tests for PROC PRINCOMP (added alongside PROC FASTCLUS)."""
import pandas as pd
import pytest

from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse
from sas_compiler.codegen import generate
from sas_compiler import runtime as _r


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


def _points_src(extra_opts: str = "") -> str:
    return f"""
    data points;
      input x y z;
      datalines;
    1 2 5
    2 1 6
    3 4 7
    4 3 8
    5 6 9
    6 5 10
    7 8 12
    8 7 13
    ;
    run;
    proc princomp data=points {extra_opts};
      var x y z;
      output out=scores;
    run;
    """


def test_princomp_out_has_prin_columns_and_correlation_eigenvalues_sum_to_nvars():
    src = _points_src()
    ds = run_sas(src)
    df = ds["scores"]
    assert len(df) == 8
    assert "Prin1" in df.columns
    assert "Prin2" in df.columns
    assert "Prin3" in df.columns

    # Cross-check: sum of eigenvalues of the correlation matrix equals the
    # number of standardized variables (each has variance 1) -- a known
    # PCA-on-correlation-matrix property.
    from sklearn.decomposition import PCA

    clean = df[["x", "y", "z"]]
    scaled = ((clean - clean.mean()) / clean.std(ddof=1)).to_numpy()
    pca = PCA(n_components=3)
    pca.fit(scaled)
    assert pca.explained_variance_.sum() == pytest.approx(3.0, abs=1e-6)


def test_princomp_n_option_limits_component_columns():
    src = _points_src("n=2")
    ds = run_sas(src)
    df = ds["scores"]
    assert "Prin1" in df.columns
    assert "Prin2" in df.columns
    assert "Prin3" not in df.columns


def test_princomp_cov_changes_eigenvalues_on_differently_scaled_data():
    src = """
    data src;
      input a b;
      datalines;
    1 1000
    2 3000
    3 2000
    4 6000
    5 4000
    6 9000
    7 5000
    8 11000
    ;
    run;
    proc princomp data=src out=corrscores;
      var a b;
    run;
    proc princomp data=src cov out=covscores;
      var a b;
    run;
    """
    ds = run_sas(src)
    corr_result = _r.proc_princomp_fit(ds["src"], ["a", "b"], use_cov=False)
    cov_result = _r.proc_princomp_fit(ds["src"], ["a", "b"], use_cov=True)
    assert "Prin1" in ds["corrscores"].columns
    assert "Prin1" in ds["covscores"].columns
    # The two fitting modes should not produce the same score columns given
    # the very different scales of a vs b.
    assert not corr_result["Prin1"].round(6).tolist() == cov_result["Prin1"].round(6).tolist()


def test_princomp_requires_var_statement():
    from sas_compiler.codegen import CodegenError

    src = """
    data src;
      x = 1;
    run;
    proc princomp data=src out=scores;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
