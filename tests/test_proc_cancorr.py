"""Tests for PROC CANCORR (canonical correlation analysis via
scikit-learn's cross_decomposition.CCA)."""
import re

import numpy as np
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
    """Two VAR-side variables (x1, x2) and two WITH-side variables
    (y1, y2), with y1/y2 close copies of x1/x2 plus a bit of noise -- a
    setting where the first canonical correlation should be strong.
    Generated once (fixed seed 7) and pasted as literal datalines so the
    test has no runtime dependency on numpy's RNG stream."""
    rng = np.random.default_rng(7)
    n = 40
    x1 = rng.uniform(0, 10, n)
    x2 = rng.uniform(0, 10, n)
    y1 = x1 + rng.normal(0, 0.3, n)
    y2 = x2 * 0.5 + rng.normal(0, 1.5, n)
    lines = [
        f"{x1[i]:.4f} {x2[i]:.4f} {y1[i]:.4f} {y2[i]:.4f}"
        for i in range(n)
    ]
    return "\n".join(lines)


def _cancorr_src(extra_proc_opts: str = "", extra_stmts: str = "") -> str:
    return f"""
    data src;
      input x1 x2 y1 y2;
      datalines;
{_correlated_datalines()}
    ;
    run;
    proc cancorr data=src {extra_proc_opts};
      var x1 x2;
      with y1 y2;
      {extra_stmts}
    run;
    """


def test_cancorr_prints_correlations_within_valid_range_and_strong_first_pair():
    src = _cancorr_src(extra_stmts="output out=scores;")
    ds, captured = None, None
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ds = run_sas(src)
    captured = buf.getvalue()

    assert "The CANCORR Procedure" in captured
    assert "Canonical Correlation" in captured

    coeffs = [float(v) for v in re.findall(r"Can\d+\s+(-?\d+\.\d+)", captured)]
    assert len(coeffs) == 2
    for c in coeffs:
        assert -1.0 <= c <= 1.0
    # First canonical pair should be strongly correlated given y1 ~= x1.
    assert abs(coeffs[0]) > 0.8
    # Canonical correlations are conventionally reported in decreasing
    # magnitude.
    assert abs(coeffs[0]) >= abs(coeffs[1])


def test_cancorr_out_has_can_columns_with_expected_row_count():
    src = _cancorr_src("out=scores")
    ds = run_sas(src)
    df = ds["scores"]
    assert len(df) == 40
    assert "Can1" in df.columns
    assert "Can2" in df.columns
    assert "Can3" not in df.columns


def test_cancorr_output_out_form_also_works():
    src = _cancorr_src(extra_stmts="output out=scores2;")
    ds = run_sas(src)
    df = ds["scores2"]
    assert len(df) == 40
    assert "Can1" in df.columns
    assert "Can2" in df.columns


def test_cancorr_requires_var_statement():
    src = """
    data src;
      x1 = 1; y1 = 2;
    run;
    proc cancorr data=src out=scores;
      with y1;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_cancorr_requires_with_statement():
    src = """
    data src;
      x1 = 1; y1 = 2;
    run;
    proc cancorr data=src out=scores;
      var x1;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
