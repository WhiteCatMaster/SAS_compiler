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


def test_anova_oneway_matches_scipy(capsys):
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
    proc anova data=one;
      class grp;
      model y = grp;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    f_val, p_val = _stats.f_oneway(a, b, c)
    assert f"{f_val:.4f}" in out
    assert f"{p_val:.4f}" in out
    assert "The ANOVA Procedure" in out
    assert "Class Level Information" in out
    assert "grp" in out
    assert "A B C" in out
    assert "R-Square" in out
    assert "Coeff Var" in out
    assert "Root MSE" in out


def test_anova_model_must_match_class_variable(capsys):
    src = """
    data one;
      input grp $ other $ y;
      datalines;
    A x 1
    A x 2
    B y 5
    B y 6
    ;
    run;
    proc anova data=one;
      class grp;
      model y = other;
    run;
    """
    with pytest.raises(CodegenError, match="must match the CLASS variable"):
        run_sas(src)


def test_anova_too_many_class_variables(capsys):
    src = """
    data one;
      input grp $ grp2 $ y;
      datalines;
    A x 1
    A x 2
    B y 5
    B y 6
    ;
    run;
    proc anova data=one;
      class grp grp2;
      model y = grp;
    run;
    """
    with pytest.raises(CodegenError, match="multi-way ANOVA"):
        run_sas(src)


def test_anova_too_many_model_righthand_vars(capsys):
    src = """
    data one;
      input grp $ x y;
      datalines;
    A 1 1
    A 2 2
    B 1 5
    B 2 6
    ;
    run;
    proc anova data=one;
      class grp;
      model y = grp x;
    run;
    """
    with pytest.raises(CodegenError, match="multi-way ANOVA"):
        run_sas(src)


def test_anova_requires_at_least_two_levels(capsys):
    src = """
    data one;
      input grp $ y;
      datalines;
    A 1
    A 2
    A 3
    ;
    run;
    proc anova data=one;
      class grp;
      model y = grp;
    run;
    """
    with pytest.raises(ValueError, match="at least 2"):
        run_sas(src)
