import pytest
from statsmodels.duration.survfunc import survdiff

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


# Hand-computable KM curve:
#   times/status (1=event, 0=censored): 2/1, 4/1, 4/0, 5/1, 6/1, 6/0
#   at risk=6, event at t=2 -> S=5/6=0.833333
#   at risk=5, event at t=4 (one censored also at t=4, doesn't affect this
#     step's survival drop, just future at-risk count) -> S = 5/6 * 4/5 = 0.666667
#   at risk=3 (5 - 1 event - 1 censored), event at t=5 -> S = 0.666667 * 2/3 = 0.444444
#   at risk=2, event at t=6 (one censored also at t=6) -> S = 0.444444 * 1/2 = 0.222222
DATA_SIMPLE = """
data surv;
  input t c;
  datalines;
5 1
6 0
6 1
2 1
4 1
4 0
;
run;
"""


def test_lifetest_basic_km_curve_matches_hand_computation(capsys):
    src = DATA_SIMPLE + """
    proc lifetest data=surv;
      time t*c(0);
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    assert "The LIFETEST Procedure" in out
    assert "Surv prob" in out

    # Pull the printed survival probabilities in Time order.
    lines = out.splitlines()
    data_lines = [
        l for l in lines
        if l.strip() and l.strip()[0].isdigit() and "Time" not in l
    ]
    probs = [float(l.split()[1]) for l in data_lines]
    times = [float(l.split()[0]) for l in data_lines]

    assert times == [2.0, 4.0, 5.0, 6.0]
    expected = [5 / 6, (5 / 6) * (4 / 5), (5 / 6) * (4 / 5) * (2 / 3),
                (5 / 6) * (4 / 5) * (2 / 3) * (1 / 2)]
    for got, want in zip(probs, expected):
        assert got == pytest.approx(want, abs=1e-6)

    # Monotonic non-increasing.
    for a, b in zip(probs, probs[1:]):
        assert b <= a

    assert "Total observations: 6" in out
    assert "Events: 4" in out
    assert "Censored: 2" in out
    # Median survival: first time where S <= 0.5, which is t=5 (S=0.444444).
    assert "Median Survival Time: 5.0000" in out


def test_lifetest_strata_logrank_matches_independent_survdiff(capsys):
    # Group A: events cluster early (t=1..4, one censored at t=5).
    # Group B: events cluster late (t=6..9, one censored at t=10).
    src = """
    data surv;
      input t c grp $;
      datalines;
    1 1 A
    2 1 A
    3 1 A
    4 1 A
    5 0 A
    6 1 B
    7 1 B
    8 1 B
    9 1 B
    10 0 B
    ;
    run;
    proc lifetest data=surv;
      time t*c(0);
      strata grp;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    assert "--- grp=A ---" in out
    assert "--- grp=B ---" in out
    assert "Log-Rank Test" in out

    times = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    status = [1, 1, 1, 1, 0, 1, 1, 1, 1, 0]
    groups = ["A"] * 5 + ["B"] * 5
    expected_stat, expected_pvalue = survdiff(times, status, groups)

    line = next(l for l in out.splitlines() if l.startswith("Chi-Square"))
    parts = line.replace("Chi-Square:", "").replace("p-value:", "").split()
    got_stat, got_pvalue = float(parts[0]), float(parts[1])

    assert got_stat == pytest.approx(expected_stat, abs=1e-4)
    assert got_pvalue == pytest.approx(expected_pvalue, abs=1e-4)
    # Sanity: survival is clearly different between the two groups (log-rank
    # p-value should be small/significant).
    assert got_pvalue < 0.05


def test_lifetest_missing_time_statement_raises_clear_error():
    src = DATA_SIMPLE + """
    proc lifetest data=surv;
    run;
    """
    expanded = MacroProcessor().expand(src)
    prog = parse(expanded)
    with pytest.raises(CodegenError, match="TIME statement"):
        generate(prog)
