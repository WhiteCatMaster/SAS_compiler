import os

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


_DATA = """
data students;
  input hours score region $;
  datalines;
1 60 East
2 65 East
3 70 West
4 75 West
5 80 East
6 85 West
7 90 East
8 95 West
;
run;
"""


def _assert_real_png(path):
    assert os.path.exists(path)
    assert os.path.getsize(path) > 500  # a real rendered PNG, not a stub


def test_scatter_creates_png(tmp_path):
    out = tmp_path / "scatter.png"
    src = _DATA + f'proc sgplot data=students out="{out}"; scatter x=hours y=score; run;\n'
    run_sas(src)
    _assert_real_png(out)


def test_series_creates_png(tmp_path):
    out = tmp_path / "series.png"
    src = _DATA + f'proc sgplot data=students out="{out}"; series x=hours y=score; run;\n'
    run_sas(src)
    _assert_real_png(out)


def test_vbar_frequency_creates_png(tmp_path):
    out = tmp_path / "vbar_freq.png"
    src = _DATA + f'proc sgplot data=students out="{out}"; vbar region; run;\n'
    run_sas(src)
    _assert_real_png(out)


def test_vbar_response_creates_png(tmp_path):
    out = tmp_path / "vbar_response.png"
    src = _DATA + f'proc sgplot data=students out="{out}"; vbar region / response=score; run;\n'
    run_sas(src)
    _assert_real_png(out)


def test_histogram_creates_png(tmp_path):
    out = tmp_path / "hist.png"
    src = _DATA + f'proc sgplot data=students out="{out}"; histogram score; run;\n'
    run_sas(src)
    _assert_real_png(out)


def test_overlay_multiple_plots_creates_one_png(tmp_path):
    out = tmp_path / "overlay.png"
    src = (
        _DATA
        + f'proc sgplot data=students out="{out}"; scatter x=hours y=score; series x=hours y=score; run;\n'
    )
    run_sas(src)
    _assert_real_png(out)


def test_default_sequential_filenames(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = (
        _DATA
        + "proc sgplot data=students; scatter x=hours y=score; run;\n"
        + "proc sgplot data=students; scatter x=hours y=score; run;\n"
    )
    run_sas(src)
    _assert_real_png(tmp_path / "sgplot_1.png")
    _assert_real_png(tmp_path / "sgplot_2.png")


def test_title_option_does_not_error(tmp_path):
    out = tmp_path / "titled.png"
    src = _DATA + f'proc sgplot data=students out="{out}" title="My Chart"; histogram score; run;\n'
    run_sas(src)
    _assert_real_png(out)
