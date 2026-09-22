import pandas as pd
from scipy import stats as _stats
from sklearn.metrics import cohen_kappa_score
from statsmodels.stats.contingency_tables import Table2x2

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


def test_freq_measures_two_way_matches_statsmodels(capsys):
    # Clean 2x2 table: X pairs mostly with P, Y mostly with Q.
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
      tables a*b / measures;
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
    t = Table2x2(ct.to_numpy().astype(float))
    odds_ratio = float(t.oddsratio)
    or_lcl, or_ucl = (float(x) for x in t.oddsratio_confint())
    risk_ratio = float(t.riskratio)
    rr_lcl, rr_ucl = (float(x) for x in t.riskratio_confint())

    assert "Odds Ratio" in out
    assert "Relative Risk" in out
    assert f"{odds_ratio:.4f}" in out
    assert f"{or_lcl:.4f}" in out
    assert f"{or_ucl:.4f}" in out
    assert f"{risk_ratio:.4f}" in out
    assert f"{rr_lcl:.4f}" in out
    assert f"{rr_ucl:.4f}" in out


def test_freq_measures_non_2x2_warns(capsys):
    # A 2x3 table cannot support odds ratio / relative risk measures;
    # expect a SAS-style warning instead of a crash, and no OR/RR output.
    src = """
    data one;
      input a $ b $;
      datalines;
    X P
    X Q
    X R
    Y P
    Y Q
    Y R
    ;
    run;
    proc freq data=one;
      tables a*b / measures;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "Odds Ratio" not in out
    # The header names "Relative Risk" up front (as real SAS does), but the
    # actual Relative Risk (Column 1) value row must not appear.
    assert "Relative Risk (Column 1)" not in out


def test_freq_agree_high_kappa_matches_sklearn(capsys):
    # Two raters (rater1, rater2) scoring the same set of categories
    # (Low/Medium/High); they agree on 7 of 9 cases -> high kappa.
    src = """
    data one;
      input rater1 $ rater2 $;
      datalines;
    Low Low
    Low Low
    Low Medium
    Medium Medium
    Medium Medium
    Medium Low
    High High
    High High
    High High
    ;
    run;
    proc freq data=one;
      tables rater1*rater2 / agree;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    df = pd.DataFrame(
        {
            "rater1": ["Low", "Low", "Low", "Medium", "Medium", "Medium",
                       "High", "High", "High"],
            "rater2": ["Low", "Low", "Medium", "Medium", "Medium", "Low",
                       "High", "High", "High"],
        }
    )
    expected_kappa = cohen_kappa_score(df["rater1"], df["rater2"])
    expected_pct = (df["rater1"] == df["rater2"]).mean() * 100.0

    assert "Simple Kappa Coefficient" in out
    assert "Kappa" in out
    assert f"{expected_kappa:.4f}" in out
    assert f"{expected_pct:.1f}" in out
    assert expected_kappa > 0.5


def test_freq_agree_low_kappa_near_random(capsys):
    # rater2 is essentially a derangement of rater1 over the same three
    # categories (same marginals, near-random pairing) -> kappa near zero.
    src = """
    data one;
      input rater1 $ rater2 $;
      datalines;
    A B
    A C
    A B
    B C
    B A
    B C
    C A
    C B
    C A
    ;
    run;
    proc freq data=one;
      tables rater1*rater2 / agree;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    df = pd.DataFrame(
        {
            "rater1": ["A", "A", "A", "B", "B", "B", "C", "C", "C"],
            "rater2": ["B", "C", "B", "C", "A", "C", "A", "B", "A"],
        }
    )
    expected_kappa = cohen_kappa_score(df["rater1"], df["rater2"])

    assert "Simple Kappa Coefficient" in out
    assert f"{expected_kappa:.4f}" in out
    # No agreement at all in this construction -> kappa should be low.
    assert expected_kappa < 0.3


def test_freq_agree_non_square_warns(capsys):
    # a has 3 levels, b has 2 levels: not a square, same-category table,
    # so kappa cannot be computed; expect a SAS-style warning, no crash.
    src = """
    data one;
      input a $ b $;
      datalines;
    X P
    X Q
    Y P
    Y Q
    Z P
    Z Q
    ;
    run;
    proc freq data=one;
      tables a*b / agree;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Simple Kappa Coefficient" in out
    assert "WARNING" in out
    assert "Percent Agreement" not in out
    assert "Statistic                       Value" not in out
