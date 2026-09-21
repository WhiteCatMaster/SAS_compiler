"""Tests for PROC GLM and PROC FASTCLUS (added alongside PROC REG/LOGISTIC)."""
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


# ---------------- PROC GLM ----------------
def test_glm_class_variable_exact_group_means_zero_noise():
    # Noise-free data: score is exactly the group mean, so OLS with a
    # dummy-encoded CLASS variable must reproduce the group means exactly.
    src = """
    data src;
      input grp $ score;
      datalines;
    A 10
    A 10
    A 10
    B 20
    B 20
    B 20
    ;
    run;
    proc glm data=src;
      class grp;
      model score = grp;
      output out=scored p=predicted r=resid;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert df["predicted"].round(6).tolist() == [10.0, 10.0, 10.0, 20.0, 20.0, 20.0]
    assert all(abs(r) < 1e-6 for r in df["resid"])


def test_glm_dummy_encoding_matches_direct_statsmodels_call():
    # Cross-check the compiler's dummy-encoding/OLS pipeline against calling
    # statsmodels directly with a hand-built design matrix on the same data.
    import statsmodels.api as sm

    df = pd.DataFrame({
        "y": [5.0, 7.0, 6.0, 12.0, 14.0, 13.0, 20.0, 22.0, 21.0],
        "x": [1.0, 2.0, 3.0, 1.0, 2.0, 3.0, 1.0, 2.0, 3.0],
        "grp": ["A", "A", "A", "B", "B", "B", "C", "C", "C"],
    })

    result = _r.proc_glm_fit(df, "y", ["x", "grp"], class_vars=["grp"],
                              out_stats={"p": ["pred"]})

    # Hand-build the same design matrix (drop_first dummy encoding) and fit
    # directly with statsmodels to get an independent expected prediction.
    dummies = pd.get_dummies(df["grp"], drop_first=True, dtype=float)
    X = sm.add_constant(pd.concat([df[["x"]], dummies], axis=1))
    expected_model = sm.OLS(df["y"], X).fit()
    expected_pred = expected_model.predict(X)

    assert result["pred"].round(6).tolist() == expected_pred.round(6).tolist()


def test_glm_requires_model_statement():
    from sas_compiler.codegen import CodegenError

    src = """
    data src;
      x = 1;
    run;
    proc glm data=src;
      class x;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


# ---------------- PROC FASTCLUS ----------------
def test_fastclus_separates_two_obvious_clusters():
    src = """
    data points;
      input x y;
      datalines;
    1 1
    1.2 0.9
    0.9 1.1
    10 10
    10.2 9.8
    9.9 10.1
    ;
    run;
    proc fastclus data=points maxclusters=2;
      var x y;
      output out=clustered;
    run;
    """
    ds = run_sas(src)
    df = ds["clustered"]
    assert len(df) == 6
    assert set(df["cluster"].unique()) == {1, 2}

    low_cluster = df[df["x"] < 5]["cluster"]
    high_cluster = df[df["x"] > 5]["cluster"]
    # every low-value row shares one label, every high-value row shares the
    # other, and the two groups are labeled differently
    assert low_cluster.nunique() == 1
    assert high_cluster.nunique() == 1
    assert low_cluster.iloc[0] != high_cluster.iloc[0]


def test_fastclus_cluster_means_are_correct():
    df = pd.DataFrame({
        "x": [1.0, 1.0, 1.0, 9.0, 9.0, 9.0],
        "y": [1.0, 1.0, 1.0, 9.0, 9.0, 9.0],
    })
    result = _r.proc_fastclus_fit(df, ["x", "y"], k=2)
    means = result.groupby("cluster")[["x", "y"]].mean()
    means_sorted = means.sort_values("x")
    assert means_sorted.iloc[0].tolist() == [1.0, 1.0]
    assert means_sorted.iloc[1].tolist() == [9.0, 9.0]


def test_fastclus_default_k_is_two():
    src = """
    data points;
      input x;
      datalines;
    1
    1.1
    0.9
    10
    10.1
    9.9
    ;
    run;
    proc fastclus data=points;
      var x;
      output out=clustered;
    run;
    """
    ds = run_sas(src)
    assert set(ds["clustered"]["cluster"].unique()) == {1, 2}


def test_fastclus_requires_var_statement():
    from sas_compiler.codegen import CodegenError

    src = """
    data src;
      x = 1;
    run;
    proc fastclus data=src maxclusters=2;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
