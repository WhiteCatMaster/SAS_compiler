"""Tests for PROC MIXED, scoped down to a single random-intercept linear
mixed-effects model (statsmodels MixedLM via the formula API):

    proc mixed data=in;
      model y = x1 x2;
      random intercept / subject=subjectvar;
    run;

No random slopes, no multiple RANDOM statements, no REPEATED, no CLASS,
no OUTPUT OUT=/LSMEANS."""
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
    """Pull a coefficient value out of a printed MixedLM summary table row,
    e.g. 'x1            1.982    0.027 74.292 0.000  1.930  2.034'."""
    m = re.search(rf"^{re.escape(name)}\s+([-\d.]+)", output, re.M)
    assert m, f"could not find coefficient {name!r} in output:\n{output}"
    return float(m.group(1))


def _make_grouped_data(seed=42, n_subjects=25, per=10, b0=5.0, b1=2.0, subj_sd=3.0):
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_subjects):
        subject_effect = rng.normal(0, subj_sd)
        for _ in range(per):
            x1 = rng.uniform(0, 10)
            y = b0 + b1 * x1 + subject_effect + rng.normal(0, 1)
            rows.append((f"S{s}", x1, y))
    return pd.DataFrame(rows, columns=["subj", "x1", "y"])


# ---------------- basic fit: correctness + recognizable output ----------------
def test_mixed_random_intercept_recovers_fixed_effect_coefficient(capsys):
    df = _make_grouped_data(seed=42, b0=5.0, b1=2.0, subj_sd=3.0)

    result = _r.proc_mixed_fit(df, "y", ["x1"], "subj")
    assert result is None  # print-only, no OUTPUT OUT=/LSMEANS support

    out = capsys.readouterr().out
    x1_coef = _extract_coef(out, "x1")
    assert x1_coef == pytest.approx(2.0, abs=0.1)


def test_mixed_summary_contains_random_effects_variance_component(capsys):
    df = _make_grouped_data(seed=7)

    _r.proc_mixed_fit(df, "y", ["x1"], "subj")
    out = capsys.readouterr().out

    assert "Mixed Linear Model" in out
    assert "Group Var" in out  # random-intercept variance component
    assert "No. Groups" in out


def test_mixed_through_full_compile_pipeline():
    df = _make_grouped_data(seed=3, n_subjects=15, per=6)
    datalines = "\n".join(
        f"{r.subj} {r.x1:.4f} {r.y:.4f}" for r in df.itertuples()
    )
    src = f"""
    data work.a;
      input subj $ x1 y;
      datalines;
{datalines}
    ;
    run;
    proc mixed data=work.a;
      model y = x1;
      random intercept / subject=subj;
    run;
    """
    # Should compile and run without error; PROC MIXED is print-only so
    # there is no OUT= dataset to inspect -- just confirm the pipeline runs.
    run_sas(src)


# ---------------- compile-time validation ----------------
def test_mixed_requires_random_statement():
    src = """
    data src;
      input subj x1 y;
      datalines;
    1 1 2
    1 2 3
    2 1 4
    2 2 5
    ;
    run;
    proc mixed data=src;
      model y = x1;
    run;
    """
    with pytest.raises(CodegenError, match="RANDOM"):
        run_sas(src)


def test_mixed_requires_model_statement():
    src = """
    data src;
      input subj x1 y;
      datalines;
    1 1 2
    1 2 3
    2 1 4
    2 2 5
    ;
    run;
    proc mixed data=src;
      random intercept / subject=subj;
    run;
    """
    with pytest.raises(CodegenError, match="MODEL"):
        run_sas(src)


def test_mixed_rejects_random_slope_not_bare_intercept():
    # RANDOM x1 / SUBJECT=subj; is a random-slope model, which is out of
    # scope -- the parser must not silently misinterpret this as the
    # supported RANDOM INTERCEPT shape. It should be skipped (no "random"
    # clause recorded), so codegen raises the same clear "RANDOM statement
    # required" error rather than mishandling it.
    src = """
    data src;
      input subj x1 y;
      datalines;
    1 1 2
    1 2 3
    2 1 4
    2 2 5
    ;
    run;
    proc mixed data=src;
      model y = x1;
      random x1 / subject=subj;
    run;
    """
    with pytest.raises(CodegenError, match="RANDOM"):
        run_sas(src)


# ---------------- runtime-level degenerate-input guards ----------------
def test_mixed_fit_rejects_too_few_groups():
    df = pd.DataFrame({
        "subj": ["A", "A", "A", "A"],
        "x1": [1, 2, 3, 4],
        "y": [2, 4, 6, 8],
    })
    with pytest.raises(RuntimeError):
        _r.proc_mixed_fit(df, "y", ["x1"], "subj")


def test_mixed_intercept_only_no_fixed_predictors(capsys):
    # model y = ; -- valid: intercept-only fixed effects plus a random
    # intercept. Real SAS allows this; support it here too.
    df = _make_grouped_data(seed=99, n_subjects=20, per=8, b1=0.0)

    result = _r.proc_mixed_fit(df, "y", [], "subj")
    assert result is None

    out = capsys.readouterr().out
    assert "Mixed Linear Model" in out
