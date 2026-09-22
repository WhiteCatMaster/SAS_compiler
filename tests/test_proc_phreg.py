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


def _synthetic_survival_datalines():
    """Genuine survival data with a real relationship: higher x1 raises
    the hazard (shortens time-to-event), built from an exponential
    time-to-event model with independent exponential censoring, using a
    fixed seed for reproducibility.

    `censor` is coded 1 = event occurred, 0 = censored, so `censor(0)`
    in the MODEL statement marks 0 as the censored value."""
    rng = np.random.default_rng(42)
    n = 200
    x1 = rng.normal(size=n)
    rate = np.exp(0.9 * x1)  # true coefficient on x1 is positive
    true_time = rng.exponential(1.0 / rate)
    cens_time = rng.exponential(2.0, size=n)
    time = np.minimum(true_time, cens_time)
    event = (true_time <= cens_time).astype(int)
    lines = [f"{t:.4f} {x:.4f} {e}" for t, x, e in zip(time, x1, event)]
    return "\n".join(lines)


def test_phreg_basic_fit_hazard_ratio_and_coefficient_sign(capsys):
    src = f"""
    data one;
      input survtime x1 censor;
      datalines;
{_synthetic_survival_datalines()}
    ;
    run;
    proc phreg data=one;
      model survtime*censor(0) = x1;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    assert "Hazard Ratio Estimates" in out
    # The synthetic data was built so higher x1 -> higher hazard, i.e. a
    # positive fitted log-hazard-ratio coefficient for x1, and therefore
    # a hazard ratio (exp(coef)) greater than 1.
    hr_line = next(line for line in out.splitlines() if line.strip().startswith("x1:"))
    hr = float(hr_line.split(":")[1].strip())
    assert hr > 1.0


def test_phreg_malformed_model_statement_raises_codegen_error():
    src = """
    data one;
      input y x1;
      datalines;
    1 2
    3 4
    ;
    run;
    proc phreg data=one;
      model y = x1;
    run;
    """
    with pytest.raises(CodegenError, match="timevar\\*censorvar"):
        run_sas(src)


def test_phreg_no_variation_in_status_raises_clear_error():
    src = """
    data one;
      input survtime x1 censor;
      datalines;
    1 1 0
    2 2 0
    3 3 0
    4 4 0
    5 5 0
    ;
    run;
    proc phreg data=one;
      model survtime*censor(0) = x1;
    run;
    """
    with pytest.raises(RuntimeError, match="no variation"):
        run_sas(src)


def test_phreg_too_few_observations_raises_clear_error():
    src = """
    data one;
      input survtime x1 x2 censor;
      datalines;
    1 1 5 1
    2 2 4 0
    ;
    run;
    proc phreg data=one;
      model survtime*censor(0) = x1 x2;
    run;
    """
    with pytest.raises(RuntimeError, match="too few"):
        run_sas(src)
