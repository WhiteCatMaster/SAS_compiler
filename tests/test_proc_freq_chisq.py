import pandas as pd
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


def test_freq_chisq_two_way_matches_scipy(capsys):
    # a and b are clearly dependent: a="X" always pairs with b="P",
    # a="Y" always pairs with b="Q" (with one exception to avoid a
    # perfectly degenerate/zero cell).
    src = """
    data one;
      input a $ b $;
      datalines;
    X P
    X P
    X P
    X Q
    Y Q
    Y Q
    Y Q
    Y P
    ;
    run;
    proc freq data=one;
      tables a*b / chisq;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    df = pd.DataFrame(
        {
            "a": ["X", "X", "X", "X", "Y", "Y", "Y", "Y"],
            "b": ["P", "P", "P", "Q", "Q", "Q", "Q", "P"],
        }
    )
    ct = pd.crosstab(df["a"], df["b"])
    chi2, p, dof, _ = _stats.chi2_contingency(ct, correction=False)

    # Crosstab is printed first.
    assert "col_0" in out or "b" in out
    # Chi-square report follows with the expected statistic/DF/p-value.
    assert "Chi-Square" in out
    assert str(dof) in out
    assert f"{chi2:.4f}" in out
    assert f"{p:.4f}" in out
    # Known ballpark for this table: chi2 = 2.0, DF = 1
    assert dof == 1
    assert "2.0000" in out


def test_freq_chisq_oneway_matches_scipy(capsys):
    # Clearly non-uniform one-way distribution across three levels:
    # A appears 6 times, B 3 times, C 1 time (10 observations, 3 levels).
    src = """
    data one;
      input g $;
      datalines;
    A
    A
    A
    A
    A
    A
    B
    B
    B
    C
    ;
    run;
    proc freq data=one;
      tables g / chisq;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    observed = [6, 3, 1]
    expected = [10 / 3, 10 / 3, 10 / 3]
    chi2, p = _stats.chisquare(observed, f_exp=expected)
    dof = len(observed) - 1

    # Frequency table is printed first, chi-square report follows.
    assert "Chi-Square Goodness-of-Fit Test" in out
    assert str(dof) in out
    assert f"{chi2:.4f}" in out
    assert f"{p:.4f}" in out


def test_freq_chisq_oneway_single_level_warns(capsys):
    # A single distinct level cannot be tested against equal proportions;
    # expect a SAS-style warning instead of a crash.
    src = """
    data one;
      input g $;
      datalines;
    A
    A
    A
    ;
    run;
    proc freq data=one;
      tables g / chisq;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Chi-Square Goodness-of-Fit Test" in out
    assert "WARNING" in out
    # No Statistic/DF/Value/Prob line should follow the warning.
    assert "Statistic " not in out


def test_freq_chisq_independent_table_high_pvalue(capsys):
    # Roughly independent/balanced table -> small chi-square, large p-value.
    src = """
    data one;
      input a $ b $;
      datalines;
    X P
    X Q
    Y P
    Y Q
    ;
    run;
    proc freq data=one;
      tables a*b / chisq;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    df = pd.DataFrame(
        {"a": ["X", "X", "Y", "Y"], "b": ["P", "Q", "P", "Q"]}
    )
    ct = pd.crosstab(df["a"], df["b"])
    chi2, p, dof, _ = _stats.chi2_contingency(ct, correction=False)

    assert f"{chi2:.4f}" in out
    assert f"{p:.4f}" in out
    assert p > 0.9
