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


# A genuine trend + seasonal (period=12) + small-noise series, generated
# once with numpy.random.default_rng(42) as:
#   trend = linspace(50, 100, 48)
#   seasonal = 10 * sin(2*pi*t/12)
#   noise = rng.normal(0, 0.5, 48)
#   y = trend + seasonal + noise
# and baked in as a literal so the test doesn't depend on numpy's RNG
# implementation staying stable across versions.
_PERIOD = 12
_TS_VALUES = [
    50.1524, 55.5438, 61.1631, 63.6618, 61.9401, 59.6681, 56.4469, 52.2887,
    49.8420, 49.1479, 52.4177, 57.0910, 62.7990, 69.3934, 73.7876, 75.5278,
    75.8659, 72.6057, 69.5882, 65.1878, 62.5239, 62.0000, 65.3553, 69.3908,
    75.3178, 81.4197, 86.5860, 88.9061, 88.6539, 86.0665, 82.9857, 77.7755,
    75.1262, 74.6995, 77.8179, 82.7985, 88.2409, 93.9416, 98.6735, 101.8147,
    101.5851, 98.8886, 94.3481, 90.8608, 88.2066, 87.9817, 90.7116, 95.1118,
]

_TS_DATALINES = "\n".join(f"{v:.4f}" for v in _TS_VALUES)

DATA_TS = f"""
data src;
  input y;
  datalines;
{_TS_DATALINES}
;
run;
"""


def test_timeseries_basic_decomp_recovers_seasonality_and_out_columns():
    src = DATA_TS + """
    proc timeseries data=src period=12 out=decomp;
      var y;
      decomp;
    run;
    """
    ds = run_sas(src)
    out = ds["decomp"]
    assert len(out) == len(_TS_VALUES)
    for col in ("observed", "trend", "seasonal", "residual"):
        assert col in out.columns

    seasonal = out["seasonal"]
    # A genuine correctness property of additive seasonal decomposition:
    # the recovered seasonal component repeats exactly every PERIOD.
    for i in range(0, len(seasonal) - _PERIOD):
        a, b = seasonal.iloc[i], seasonal.iloc[i + _PERIOD]
        if a == a and b == b:  # skip NaN edges
            assert a == pytest.approx(b, abs=1e-6)

    # Trend/residual are NaN at the series edges (expected
    # seasonal_decompose behavior), but populated in the interior.
    assert out["trend"].iloc[_PERIOD:-_PERIOD].notna().all()


def test_timeseries_missing_decomp_raises():
    src = DATA_TS + """
    proc timeseries data=src period=12 out=decomp;
      var y;
    run;
    """
    with pytest.raises(CodegenError, match="DECOMP"):
        run_sas(src)


def test_timeseries_no_var_raises():
    src = DATA_TS + """
    proc timeseries data=src period=12 out=decomp;
      decomp;
    run;
    """
    with pytest.raises(CodegenError, match="VAR"):
        run_sas(src)


def test_timeseries_multiple_var_raises():
    src = DATA_TS + """
    proc timeseries data=src period=12 out=decomp;
      var y y;
      decomp;
    run;
    """
    with pytest.raises(CodegenError, match="VAR"):
        run_sas(src)


def test_timeseries_too_short_series_raises():
    src = """
    data short;
      input y;
      datalines;
    1
    2
    3
    4
    5
    ;
    run;
    proc timeseries data=short period=12 out=decomp;
      var y;
      decomp;
    run;
    """
    with pytest.raises(ValueError, match="not enough observations"):
        run_sas(src)
