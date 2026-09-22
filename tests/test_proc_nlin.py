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


# y = 2.0 * exp(0.5 * x) + small noise, generated once with
# numpy.random.default_rng(42) and baked in as literals so the test doesn't
# depend on numpy's RNG implementation staying stable across versions.
_EXP_X = [
    0.0000, 0.1034, 0.2069, 0.3103, 0.4138, 0.5172, 0.6207, 0.7241, 0.8276,
    0.9310, 1.0345, 1.1379, 1.2414, 1.3448, 1.4483, 1.5517, 1.6552, 1.7586,
    1.8621, 1.9655, 2.0690, 2.1724, 2.2759, 2.3793, 2.4828, 2.5862, 2.6897,
    2.7931, 2.8966, 3.0000,
]
_EXP_Y = [
    2.0457, 1.9502, 2.3305, 2.4768, 2.1671, 2.3950, 2.7470, 2.8252, 3.0226,
    3.0577, 3.4867, 3.6495, 3.7303, 4.0870, 4.1960, 4.2160, 4.6309, 4.6746,
    5.2060, 5.3361, 5.5996, 5.8239, 6.4240, 6.5487, 6.8565, 7.2353, 7.7549,
    8.1373, 8.5735, 9.0280,
]

_EXP_DATALINES = "\n".join(f"{x:.4f} {y:.4f}" for x, y in zip(_EXP_X, _EXP_Y))

_EXP_DATA = f"""
data work.expdata;
  input x y;
  datalines;
{_EXP_DATALINES}
;
run;
"""


def test_nlin_exponential_recovers_true_parameters(capsys):
    src = _EXP_DATA + """
    proc nlin data=work.expdata;
      parms b0=1 b1=1;
      model y = b0 * exp(b1 * x);
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "The NLIN Procedure" in out
    assert "b0" in out and "b1" in out

    import re
    m_b0 = re.search(r"b0\s+([\-\d.]+)", out)
    m_b1 = re.search(r"b1\s+([\-\d.]+)", out)
    assert m_b0 and m_b1
    b0_est = float(m_b0.group(1))
    b1_est = float(m_b1.group(1))
    assert b0_est == pytest.approx(2.0, abs=0.2)
    assert b1_est == pytest.approx(0.5, abs=0.05)

    m_r2 = re.search(r"R-Square:\s+([\d.]+)", out)
    assert m_r2 and float(m_r2.group(1)) > 0.9


def test_nlin_sigmoid_form_also_converges(capsys):
    """A different nonlinear form (logistic/sigmoid), proving the
    expression-parsing approach generalizes beyond one hardcoded shape."""
    import numpy as np

    rng = np.random.default_rng(1)
    x = np.linspace(-5, 5, 60)
    b0_true, b1_true, b2_true = 10.0, 1.2, 0.0
    y = b0_true / (1 + np.exp(-b1_true * (x - b2_true))) + rng.normal(0, 0.2, size=len(x))

    lines = ["data work.sig;", "input x y;", "datalines;"]
    for xi, yi in zip(x, y):
        lines.append(f"{xi:.6f} {yi:.6f}")
    lines += [";", "run;"]
    src = "\n".join(lines) + """
    proc nlin data=work.sig;
      parms b0=5 b1=1 b2=0;
      model y = b0 / (1 + exp(-b1 * (x - b2)));
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "The NLIN Procedure" in out

    import re
    m_b0 = re.search(r"b0\s+([\-\d.]+)", out)
    m_b1 = re.search(r"b1\s+([\-\d.]+)", out)
    assert m_b0 and m_b1
    assert float(m_b0.group(1)) == pytest.approx(10.0, abs=1.0)
    assert float(m_b1.group(1)) == pytest.approx(1.2, abs=0.3)


def test_nlin_requires_parms(capsys):
    src = _EXP_DATA + """
    proc nlin data=work.expdata;
      model y = b0 * exp(b1 * x);
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_nlin_requires_model(capsys):
    src = _EXP_DATA + """
    proc nlin data=work.expdata;
      parms b0=1 b1=1;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)
