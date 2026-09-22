"""Tests for PROC GENMOD (generalized linear models: Poisson/Gamma/Binomial/
Gaussian regression via statsmodels GLM, with configurable DIST=/LINK=)."""
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
    """Pull a coefficient value out of a printed statsmodels GLM/OLS
    summary table row, e.g. 'x              0.3761      0.139 ...'."""
    m = re.search(rf"^{re.escape(name)}\s+([-\d.]+)", output, re.M)
    assert m, f"could not find coefficient {name!r} in output:\n{output}"
    return float(m.group(1))


# ---------------- DIST=POISSON ----------------
def test_genmod_poisson_log_link_recovers_sign_of_true_relationship(capsys):
    rng = np.random.default_rng(42)
    n = 300
    x = rng.uniform(-2, 2, n)
    b0, b1 = 0.5, 0.8  # positive relationship between x and log(mean count)
    lam = np.exp(b0 + b1 * x)
    y = rng.poisson(lam)
    df = pd.DataFrame({"x": x, "y": y})

    result = _r.proc_genmod_fit(df, "y", ["x"], dist="poisson", link="log")
    assert result is None  # print-only, no OUTPUT OUT= support

    out = capsys.readouterr().out
    assert "Poisson" in out
    x_coef = _extract_coef(out, "x")
    assert x_coef > 0  # sign matches the true positive relationship


def test_genmod_poisson_through_full_compile_pipeline():
    src = """
    data work.a;
      input x y;
      datalines;
    1 2
    2 5
    3 4
    4 9
    5 11
    ;
    run;
    proc genmod data=work.a;
      model y = x / dist=poisson link=log;
    run;
    """
    # Should compile and run without error; PROC GENMOD is print-only so
    # there is no OUT= dataset to inspect -- just confirm the pipeline runs.
    run_sas(src)


# ---------------- DIST=NORMAL (default) cross-check against PROC REG ----------------
def test_genmod_normal_identity_matches_proc_reg_ols(capsys):
    rng = np.random.default_rng(7)
    n = 50
    x = rng.uniform(0, 10, n)
    y = 3 + 2 * x + rng.normal(0, 1, n)
    df = pd.DataFrame({"x": x, "y": y})

    _r.proc_reg_fit(df, "y", ["x"])
    reg_out = capsys.readouterr().out
    reg_x_coef = _extract_coef(reg_out, "x")
    reg_const = _extract_coef(reg_out, "const")

    # link=None -> GLM uses Gaussian's own default (identity) link, exactly
    # as omitting LINK= does in real SAS and in statsmodels.
    _r.proc_genmod_fit(df, "y", ["x"], dist="normal", link=None)
    genmod_out = capsys.readouterr().out
    genmod_x_coef = _extract_coef(genmod_out, "x")
    genmod_const = _extract_coef(genmod_out, "const")

    assert genmod_x_coef == pytest.approx(reg_x_coef, abs=1e-3)
    assert genmod_const == pytest.approx(reg_const, abs=1e-3)


# ---------------- compile-time validation ----------------
def test_genmod_requires_model_statement():
    src = """
    data src;
      x = 1;
    run;
    proc genmod data=src;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_genmod_rejects_unsupported_dist():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 3
    3 5
    ;
    run;
    proc genmod data=src;
      model y = x / dist=weibull;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_genmod_rejects_unsupported_link():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 3
    3 5
    ;
    run;
    proc genmod data=src;
      model y = x / dist=poisson link=probit;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_genmod_rejects_power_link():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 3
    3 5
    ;
    run;
    proc genmod data=src;
      model y = x / dist=gamma link=power(-1);
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
