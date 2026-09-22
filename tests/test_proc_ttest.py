import pytest
from scipy import stats as _stats

from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse
from sas_compiler.codegen import generate


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


# ---------------- one-sample ----------------
def test_ttest_one_sample_matches_scipy(capsys):
    data = [1, 2, 3, 4, 5]
    src = """
    data one;
      input x;
      datalines;
    1
    2
    3
    4
    5
    ;
    run;
    proc ttest data=one h0=2;
      var x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    t, p = _stats.ttest_1samp(data, 2)
    assert f"{t:.4f}" in out
    assert f"{p:.4f}" in out
    assert "N          5" in out
    assert "Mean       3.0000" in out
    # Known ballpark: t = 1.4142, Pr > |t| = 0.2302
    assert "1.4142" in out
    assert "0.2302" in out


def test_ttest_one_sample_default_h0_is_zero(capsys):
    src = """
    data one;
      input x;
      datalines;
    1
    2
    3
    ;
    run;
    proc ttest data=one;
      var x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "H0: Mean = 0.0" in out


# ---------------- two-sample (CLASS) ----------------
def test_ttest_two_sample_class_pooled_and_satterthwaite(capsys):
    src = """
    data two;
      input grp $ y;
      datalines;
    A 1
    A 2
    A 3
    B 4
    B 5
    B 9
    ;
    run;
    proc ttest data=two;
      class grp;
      var y;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    a = [1, 2, 3]
    b = [4, 5, 9]
    t_eq, p_eq = _stats.ttest_ind(a, b, equal_var=True)
    t_un, p_un = _stats.ttest_ind(a, b, equal_var=False)

    assert "Pooled" in out and "Satterthwaite" in out
    assert f"t={t_eq:.4f}" in out
    assert f"t={t_un:.4f}" in out
    assert f"{p_eq:.4f}" in out
    assert f"{p_un:.4f}" in out
    # Known ballpark values
    assert "-2.4495" in out
    assert "0.0705" in out
    assert "0.1064" in out


def test_ttest_class_requires_exactly_two_levels(capsys):
    src = """
    data bad;
      input grp $ y;
      datalines;
    A 1
    B 2
    C 3
    ;
    run;
    proc ttest data=bad;
      class grp;
      var y;
    run;
    """
    with pytest.raises(ValueError, match="exactly 2"):
        run_sas(src)


# ---------------- paired ----------------
def test_ttest_paired_matches_scipy(capsys):
    a = [1, 2, 3, 4, 5]
    b = [2, 3, 5, 4, 7]
    src = """
    data three;
      input a b;
      datalines;
    1 2
    2 3
    3 5
    4 4
    5 7
    ;
    run;
    proc ttest data=three;
      paired a*b;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    t, p = _stats.ttest_rel(a, b)
    assert "Difference: a - b" in out
    assert f"{t:.4f}" in out
    assert f"{p:.4f}" in out
    # Known ballpark: t = -3.2071, Pr > |t| = 0.0327
    assert "-3.2071" in out
    assert "0.0327" in out


def test_ttest_paired_multiple_pairs(capsys):
    src = """
    data multi;
      input a b c d;
      datalines;
    1 2 5 5
    2 3 6 5
    3 5 7 6
    4 4 8 7
    5 7 9 8
    ;
    run;
    proc ttest data=multi;
      paired a*b c*d;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Difference: a - b" in out
    assert "Difference: c - d" in out
