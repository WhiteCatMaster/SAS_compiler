import pytest
from scipy import stats as _stats

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


def test_npar1way_two_group_matches_mannwhitneyu(capsys):
    a = [1, 2, 3, 4]
    b = [5, 6, 7, 8]
    src = """
    data one;
      input grp $ y;
      datalines;
    A 1
    A 2
    A 3
    A 4
    B 5
    B 6
    B 7
    B 8
    ;
    run;
    proc npar1way data=one wilcoxon;
      class grp;
      var y;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    stat, p = _stats.mannwhitneyu(a, b, alternative="two-sided")
    assert f"{stat:.4f}" in out
    assert f"{p:.4f}" in out
    assert "The NPAR1WAY Procedure" in out
    assert "Wilcoxon Scores (Rank Sums)" in out
    assert "Wilcoxon Two-Sample Test" in out


def test_npar1way_three_group_matches_kruskal(capsys):
    a = [1, 2, 3, 4]
    b = [5, 6, 7, 8]
    c = [2, 4, 6, 10]
    src = """
    data one;
      input grp $ y;
      datalines;
    A 1
    A 2
    A 3
    A 4
    B 5
    B 6
    B 7
    B 8
    C 2
    C 4
    C 6
    C 10
    ;
    run;
    proc npar1way data=one;
      class grp;
      var y;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    chi2, p = _stats.kruskal(a, b, c)
    assert f"{chi2:.4f}" in out
    assert f"{p:.4f}" in out
    assert "Kruskal-Wallis Test" in out
    assert "Chi-Square" in out
    assert "Pr > Chi-Square" in out


def test_npar1way_requires_at_least_two_levels(capsys):
    src = """
    data one;
      input grp $ y;
      datalines;
    A 1
    A 2
    A 3
    ;
    run;
    proc npar1way data=one;
      class grp;
      var y;
    run;
    """
    with pytest.raises(ValueError, match="at least 2"):
        run_sas(src)


def test_npar1way_requires_var_statement(capsys):
    src = """
    data one;
      input grp $ y;
      datalines;
    A 1
    B 2
    ;
    run;
    proc npar1way data=one;
      class grp;
    run;
    """
    with pytest.raises(CodegenError, match="VAR statement"):
        run_sas(src)


def test_npar1way_requires_class_statement(capsys):
    src = """
    data one;
      input grp $ y;
      datalines;
    A 1
    B 2
    ;
    run;
    proc npar1way data=one;
      var y;
    run;
    """
    with pytest.raises(CodegenError, match="CLASS statement"):
        run_sas(src)


def test_npar1way_multiple_vars_print_separate_blocks(capsys):
    src = """
    data one;
      input grp $ y z;
      datalines;
    A 1 10
    A 2 20
    A 3 30
    A 4 40
    B 5 15
    B 6 25
    B 7 35
    B 8 45
    ;
    run;
    proc npar1way data=one;
      class grp;
      var y z;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    assert out.count("Variable: y") == 1
    assert out.count("Variable: z") == 1
    assert out.count("Wilcoxon Two-Sample Test") == 2
