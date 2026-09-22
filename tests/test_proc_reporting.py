import pandas as pd
import pytest

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


SALES_DATA = """
data sales;
  input region $ product $ amount;
  datalines;
East Widget 100
East Widget 150
East Gadget 200
West Widget 300
West Gadget 400
West Gadget 100
;
run;
"""


# ---------------- PROC REPORT ----------------
def test_proc_report_group_and_analysis_sum(capsys):
    src = SALES_DATA + """
    proc report data=sales;
      column region amount;
      define region / group;
      define amount / analysis sum;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "East" in out and "450" in out
    assert "West" in out and "800" in out


def test_proc_report_analysis_default_stat_is_sum(capsys):
    # "analysis" alone (no explicit stat keyword) defaults to SUM, per SAS.
    src = SALES_DATA + """
    proc report data=sales;
      column region amount;
      define region / group;
      define amount / analysis;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "450" in out
    assert "800" in out


def test_proc_report_no_group_is_plain_listing(capsys):
    src = SALES_DATA + """
    proc report data=sales;
      column region product amount;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    # all 6 detail rows should appear untouched (no aggregation)
    assert out.count("Widget") + out.count("Gadget") == 6
    assert "100" in out and "150" in out and "400" in out


def test_proc_report_requires_column_statement():
    import pytest
    from sas_compiler.codegen import CodegenError

    src = SALES_DATA + "proc report data=sales; run;"
    with pytest.raises(CodegenError):
        run_sas(src)


# ---------------- PROC TABULATE ----------------
def test_proc_tabulate_two_way_sum(capsys):
    src = SALES_DATA + """
    proc tabulate data=sales;
      class region product;
      var amount;
      table region, product*amount*sum;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    # East/Gadget=200, East/Widget=250, West/Gadget=500, West/Widget=300
    assert "200" in out
    assert "250" in out
    assert "500" in out
    assert "300" in out


def test_proc_tabulate_mean_and_out_dataset():
    src = SALES_DATA + """
    proc tabulate data=sales out=avgtab;
      class region product;
      var amount;
      table region, product*amount*mean;
    run;
    """
    ds = run_sas(src)
    df = ds["avgtab"].set_index("region")
    assert df.loc["East", "Gadget"] == 200.0
    assert df.loc["East", "Widget"] == 125.0
    assert df.loc["West", "Gadget"] == 250.0
    assert df.loc["West", "Widget"] == 300.0


def test_proc_tabulate_single_axis_no_comma(capsys):
    src = """
    data sales;
      input region $ amount;
      datalines;
    East 100
    East 150
    West 300
    ;
    run;
    proc tabulate data=sales;
      class region;
      var amount;
      table region*amount*sum;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "250" in out  # East total
    assert "300" in out  # West total


def test_proc_tabulate_var_statement_supplies_analysis_var(capsys):
    # no var*stat on either axis -> analysis var comes from the VAR statement,
    # default stat is sum
    src = SALES_DATA + """
    proc tabulate data=sales;
      class region;
      var amount;
      table region;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "450" in out
    assert "800" in out


def test_proc_tabulate_requires_table_statement():
    import pytest
    from sas_compiler.codegen import CodegenError

    src = SALES_DATA + """
    proc tabulate data=sales;
      class region;
      var amount;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_proc_tabulate_multi_stat_min_max_median(capsys):
    # region amounts: East = 100, 150, 200 -> min 100, max 200, median 150
    #                 West = 300, 400, 100 -> min 100, max 400, median 300
    src = SALES_DATA + """
    proc tabulate data=sales;
      class region;
      var amount;
      table region, amount*(min max median);
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "--- MIN ---" in out
    assert "--- MAX ---" in out
    assert "--- MEDIAN ---" in out
    # East
    assert "100" in out
    assert "150" in out
    assert "200" in out
    # West
    assert "300" in out
    assert "400" in out


def test_proc_tabulate_std_var_out_dataset():
    src = SALES_DATA + """
    proc tabulate data=sales out=stdtab;
      class region;
      var amount;
      table region, amount*(std var);
    run;
    """
    ds = run_sas(src)
    df = ds["stdtab"]

    # cross-check against pandas' own groupby std/var on the same raw data
    raw = pd.DataFrame(
        {
            "region": ["East", "East", "East", "West", "West", "West"],
            "amount": [100, 150, 200, 300, 400, 100],
        }
    )
    expected_std = raw.groupby("region")["amount"].std()
    expected_var = raw.groupby("region")["amount"].var()

    std_rows = df[df["_stat_"] == "std"].set_index("region")
    var_rows = df[df["_stat_"] == "var"].set_index("region")
    assert std_rows.loc["East", "amount"] == pytest.approx(expected_std["East"])
    assert std_rows.loc["West", "amount"] == pytest.approx(expected_std["West"])
    assert var_rows.loc["East", "amount"] == pytest.approx(expected_var["East"])
    assert var_rows.loc["West", "amount"] == pytest.approx(expected_var["West"])


def test_proc_tabulate_pctsum_grand_total(capsys):
    # East sum=450, West sum=800, grand total=1250 -> East=36%, West=64%
    src = SALES_DATA + """
    proc tabulate data=sales out=pcttab;
      class region;
      var amount;
      table region, amount*pctsum;
    run;
    """
    ds = run_sas(src)
    out = capsys.readouterr().out
    assert "--- PCTSUM ---" in out
    df = ds["pcttab"].set_index("region")
    assert df.loc["East", "amount"] == pytest.approx(36.0)
    assert df.loc["West", "amount"] == pytest.approx(64.0)
    # structural correctness: percentages across all cells sum to ~100
    assert df["amount"].sum() == pytest.approx(100.0)
