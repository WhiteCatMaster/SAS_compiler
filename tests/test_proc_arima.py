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


# A genuinely autocorrelated AR(1)-ish series (phi=0.7, N(0,1) innovations,
# shifted to a mean of 50), generated once with numpy.random.default_rng(42)
# and baked in as a literal so the test doesn't depend on numpy's RNG
# implementation staying stable across versions.
_AR1_VALUES = [
    50.0000, 50.3047, 49.1733, 50.1718, 51.0608, 48.7915, 47.8519, 48.6242,
    48.7207, 49.0877, 48.5083, 49.8352, 50.6624, 50.5297, 51.4981, 51.5162,
    50.2020, 50.5102, 49.3982, 50.4572, 50.2701, 50.0042, 49.3220, 50.7480,
    50.3690, 49.8300, 49.5289, 50.2025, 50.5072, 50.7678, 50.9683, 52.8194,
    51.5672, 50.5848, 49.5956, 50.3329, 51.3620, 50.8394, 49.7475, 48.9987,
    49.9497, 50.7081, 51.0388, 50.0616, 50.2753, 50.3094, 50.4353, 51.1761,
    51.0469, 51.4117, 51.0558, 51.0282, 51.3510, 49.4886, 49.3223, 49.0552,
    48.6998, 48.8147, 50.6652, 49.5998, 50.6882, 48.7988, 48.8243, 49.3398,
    50.1241, 50.7981, 51.3520, 50.5977, 49.9560, 50.8272, 50.3877, 48.9957,
    48.1637, 47.7952, 48.9538, 49.4101, 50.2775, 49.7670, 49.9955, 50.6224,
]

_AR1_DATALINES = "\n".join(f"{v:.4f}" for v in _AR1_VALUES)

DATA_AR1 = f"""
data src;
  input y;
  datalines;
{_AR1_DATALINES}
;
run;
"""


def test_arima_basic_fit_and_forecast():
    src = DATA_AR1 + """
    proc arima data=src;
      identify var=y;
      estimate p=1 d=0 q=0;
      forecast lead=10 out=fcst;
    run;
    """
    ds = run_sas(src)
    out = ds["fcst"]
    assert len(out) == 10
    assert list(out["period"]) == list(range(1, 11))
    for col in ("FORECAST", "L95", "U95"):
        assert col in out.columns
        assert out[col].apply(lambda v: v == v and abs(v) != float("inf")).all()
    # Confidence interval should bracket the forecast at every period.
    assert (out["L95"] <= out["FORECAST"]).all()
    assert (out["FORECAST"] <= out["U95"]).all()


def test_arima_no_forecast_block_is_print_only(capsys):
    src = DATA_AR1 + """
    proc arima data=src;
      identify var=y;
      estimate p=1 d=0 q=0;
    run;
    """
    ds = run_sas(src)
    assert "fcst" not in ds
    captured = capsys.readouterr()
    assert "The ARIMA Procedure" in captured.out


def test_arima_missing_identify_raises():
    src = DATA_AR1 + """
    proc arima data=src;
      estimate p=1 d=0 q=0;
      forecast lead=5 out=fcst;
    run;
    """
    with pytest.raises(CodegenError, match="IDENTIFY"):
        run_sas(src)


def test_arima_missing_estimate_raises():
    src = DATA_AR1 + """
    proc arima data=src;
      identify var=y;
      forecast lead=5 out=fcst;
    run;
    """
    with pytest.raises(CodegenError, match="ESTIMATE"):
        run_sas(src)


def test_arima_missing_var_column_raises():
    src = DATA_AR1 + """
    proc arima data=src;
      identify var=not_a_column;
      estimate p=1 d=0 q=0;
      forecast lead=5 out=fcst;
    run;
    """
    with pytest.raises(ValueError, match="not a column"):
        run_sas(src)
