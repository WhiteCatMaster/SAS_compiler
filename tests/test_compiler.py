import math

import pandas as pd
import pytest

from sas_compiler.macro import MacroProcessor, MacroError
from sas_compiler.parser import parse
from sas_compiler.codegen import generate
from sas_compiler.cli import main as cli_main


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


# ---------------- macro processor ----------------
def test_macro_let_and_resolution():
    out = MacroProcessor().expand("%let x = 5; value is &x.")
    assert out.strip() == "value is 5"


def test_macro_do_loop():
    out = MacroProcessor().expand("%do i = 1 %to 3; [&i] %end;")
    assert out.split() == ["[1]", "[2]", "[3]"]


def test_macro_if_else():
    src = "%let n=10; %if &n > 5 %then %let r=big; %else %let r=small; &r"
    out = MacroProcessor().expand(src)
    assert out.strip() == "big"


def test_macro_untaken_branch_not_executed():
    src = "%let n=1; %if &n > 5 %then %do; %let side=yes; %end; got=&side."
    out = MacroProcessor().expand(src)
    assert "got=." in out or out.strip().endswith("got=")


def test_macro_definition_and_call_with_keyword_args():
    src = """
    %macro add(a, b=10);
    total is %eval(&a + &b)
    %mend add;
    %add(5)
    %add(5, b=1)
    """
    out = MacroProcessor().expand(src)
    assert "total is 15" in out
    assert "total is 6" in out


def test_macro_functions():
    mp = MacroProcessor()
    assert mp.expand("%upcase(abc)").strip() == "ABC"
    assert mp.expand("%substr(hello,2,3)").strip() == "ell"
    assert mp.expand("%length(hello)").strip() == "5"
    assert mp.expand("%scan(a-b-c,2,-)").strip() == "b"


# ---------------- DATA step ----------------
def test_basic_assignment_and_if_else():
    src = """
    data work.people;
      input name $ age;
      datalines;
    Alice 30
    Bob 45
    ;
    run;
    data out;
      set people;
      if age >= 40 then bucket = "old";
      else bucket = "young";
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("name")
    assert df.loc["Alice", "bucket"] == "young"
    assert df.loc["Bob", "bucket"] == "old"


def test_retain_and_sum_statement():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    4
    ;
    run;
    data out;
      set src;
      running + x;
    run;
    """
    ds = run_sas(src)
    assert list(ds["out"]["running"]) == [1.0, 3.0, 6.0, 10.0]


def test_array_and_do_loop():
    src = """
    data out;
      array vals{5} v1-v5;
      do i = 1 to 5;
        vals{i} = i * 2;
      end;
      drop i;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert [row["v1"], row["v2"], row["v3"], row["v4"], row["v5"]] == [2.0, 4.0, 6.0, 8.0, 10.0]


def test_by_group_first_last():
    src = """
    data src;
      input grp $ x;
      datalines;
    a 1
    a 2
    b 5
    ;
    run;
    data out;
      set src;
      by grp;
      retain total 0;
      if first.grp then total = 0;
      total = total + x;
      if last.grp then output;
      keep grp total;
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("grp")
    assert df.loc["a", "total"] == 3.0
    assert df.loc["b", "total"] == 5.0
    assert "first_grp" not in ds["out"].columns
    assert "last_grp" not in ds["out"].columns


def test_merge_by_key():
    src = """
    data a;
      input id x;
      datalines;
    1 10
    2 20
    ;
    run;
    data b;
      input id y;
      datalines;
    1 100
    3 300
    ;
    run;
    data m;
      merge a(in=ina) b(in=inb);
      by id;
      matched = ina and inb;
    run;
    """
    ds = run_sas(src)
    df = ds["m"].set_index("id")
    assert df.loc[1.0, "matched"] == True  # noqa: E712
    assert df.loc[2.0, "matched"] == False  # noqa: E712
    assert df.loc[3.0, "matched"] == False  # noqa: E712


def test_where_dataset_option():
    src = """
    data src;
      input id region $;
      datalines;
    1 East
    2 West
    3 East
    ;
    run;
    data out;
      set src(where=(region = "East"));
    run;
    """
    ds = run_sas(src)
    assert sorted(ds["out"]["id"].tolist()) == [1.0, 3.0]


def test_subsetting_if_delete():
    src = """
    data src;
      input x;
      datalines;
    5
    150
    20
    ;
    run;
    data out;
      set src;
      if x < 100 then delete;
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["x"].tolist() == [150.0]


def test_missing_value_propagation():
    src = """
    data out;
      x = .;
      y = x + 1;
      z = 5 / 0;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert math.isnan(row["y"])
    assert math.isnan(row["z"])


def test_string_functions():
    src = """
    data out;
      s = "  Hello World  ";
      up = upcase(s);
      trimmed = strip(s);
      sub = substr(s, 3, 5);
      cc = cats("a", "b", "c");
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["up"].strip() == "HELLO WORLD"
    assert row["trimmed"] == "Hello World"
    assert row["cc"] == "abc"


def test_do_while_until():
    src = """
    data out;
      x = 0;
      do while (x < 5);
        x + 1;
      end;
      y = 10;
      do until (y <= 0);
        y = y - 3;
      end;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["x"] == 5.0
    assert row["y"] == -2.0


# ---------------- PROC steps ----------------
def test_proc_sort():
    src = """
    data src;
      input x;
      datalines;
    3
    1
    2
    ;
    run;
    proc sort data=src out=sorted;
      by x;
    run;
    """
    ds = run_sas(src)
    assert ds["sorted"]["x"].tolist() == [1.0, 2.0, 3.0]


def test_proc_means_output():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    4
    ;
    run;
    proc means data=src;
      var x;
      output out=stats mean=avgx std=sdx;
    run;
    """
    ds = run_sas(src)
    row = ds["stats"].iloc[0]
    assert row["avgx"] == 2.5


def test_proc_sql_group_by():
    src = """
    data src;
      input grp $ x;
      datalines;
    a 1
    a 3
    b 5
    ;
    run;
    proc sql;
      create table out as
      select grp, sum(x) as total
      from src
      group by grp
      order by grp;
    quit;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("grp")
    assert df.loc["a", "total"] == 4
    assert df.loc["b", "total"] == 5


def test_format_statement_and_put_with_format(capsys):
    src = """
    data money;
      input salary bonus_pct;
      format salary dollar12.2 bonus_pct percent8.1;
      txt = put(salary, comma10.);
      datalines;
    55000 0.1
    ;
    run;
    proc print data=money;
    run;
    """
    ds = run_sas(src)
    row = ds["money"].iloc[0]
    assert row["salary"] == 55000.0
    assert row["txt"] == "55,000"
    captured = capsys.readouterr()
    assert "$55,000.00" in captured.out
    assert "10.0%" in captured.out


def test_format_propagates_through_sort():
    src = """
    data money;
      input salary;
      format salary dollar10.2;
      datalines;
    200
    100
    ;
    run;
    proc sort data=money out=sorted; by salary; run;
    """
    run_sas(src)  # just verify it compiles/executes without error
    expanded = MacroProcessor().expand(src)
    code = generate(parse(expanded))
    assert "_FMT['sorted'] = _FMT['money']" in code


def test_array_initializer_and_dim():
    src = """
    data out;
      array nums{5} (10, 20, 30, 40, 50);
      array names{3} $10 ("Alice", "Bob", "Carol");
      total = 0;
      do i = 1 to dim(nums);
        total + nums{i};
      end;
      drop i;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["total"] == 150.0
    assert [row["nums1"], row["nums2"], row["nums3"], row["nums4"], row["nums5"]] == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert [row["names1"], row["names2"], row["names3"]] == ["Alice", "Bob", "Carol"]


def test_do_over():
    src = """
    data out;
      array vals{4} v1-v4 (1, 2, 3, 4);
      do over vals;
        vals = vals * 10;
      end;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert [row["v1"], row["v2"], row["v3"], row["v4"]] == [10.0, 20.0, 30.0, 40.0]
    assert not any(c.startswith("__ovidx") for c in ds["out"].columns)


def test_array_explicit_bounds():
    src = """
    data out;
      array yr{2020:2023} y2020-y2023 (10, 20, 30, 40);
      do year = 2020 to 2023;
        total + yr{year};
      end;
      lo = lbound(yr);
      hi = hbound(yr);
      n = dim(yr);
      drop year;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["total"] == 100.0
    assert row["lo"] == 2020.0
    assert row["hi"] == 2023.0
    assert row["n"] == 4.0


def test_proc_format_value_lists(capsys):
    src = """
    proc format;
      value agegrp
        low-17 = "Minor"
        18-64 = "Adult"
        65-high = "Senior";
      value $gender
        "M" = "Male"
        "F" = "Female"
        other = "Unknown";
    run;
    data people;
      input name $ age gender $;
      format age agegrp. gender $gender.;
      datalines;
    Alice 15 F
    Bob 30 M
    Carol 70 X
    ;
    run;
    proc print data=people;
    run;
    """
    ds = run_sas(src)
    assert ds["people"]["age"].tolist() == [15.0, 30.0, 70.0]  # underlying value unchanged
    captured = capsys.readouterr().out
    assert "Minor" in captured
    assert "Adult" in captured
    assert "Senior" in captured
    assert "Female" in captured
    assert "Unknown" in captured


def test_proc_transpose_default():
    src = """
    data wide;
      input id x y;
      datalines;
    1 10 20
    ;
    run;
    proc transpose data=wide out=long;
      by id;
      var x y;
    run;
    """
    ds = run_sas(src)
    df = ds["long"]
    assert df["_name_"].tolist() == ["x", "y"]
    assert df["col1"].tolist() == [10.0, 20.0]


def test_proc_transpose_with_id():
    src = """
    data sales;
      input region $ quarter $ amount;
      datalines;
    East Q1 100
    East Q2 150
    West Q1 200
    ;
    run;
    proc transpose data=sales out=wide;
      by region;
      id quarter;
      var amount;
    run;
    """
    ds = run_sas(src)
    df = ds["wide"].set_index("region")
    assert df.loc["East", "Q1"] == 100.0
    assert df.loc["East", "Q2"] == 150.0
    assert df.loc["West", "Q1"] == 200.0


def test_unterminated_macro_do_raises_with_location():
    with pytest.raises(MacroError, match=r"line \d+, col \d+: unterminated %do block"):
        MacroProcessor().expand("%macro foo;\n%do i = 1 %to 3;\n%put &i;\n%mend foo;\n%foo;")


def test_cli_check_reports_ok(tmp_path, capsys):
    f = tmp_path / "ok.sas"
    f.write_text("data x; y = 1; run;")
    rc = cli_main([str(f), "--check"])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_cli_check_reports_error_cleanly(tmp_path, capsys):
    f = tmp_path / "bad.sas"
    f.write_text("%macro foo;\n%do i = 1 %to 3;\n%put &i;\n")
    rc = cli_main([str(f), "--check"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "line" in err


def test_proc_import_export_csv(tmp_path):
    csv_in = tmp_path / "in.csv"
    csv_in.write_text("name,age\nAlice,30\nBob,15\n")
    csv_out = tmp_path / "out.csv"
    src = f"""
    proc import datafile="{csv_in}" out=people dbms=csv replace;
    run;
    data adults;
      set people;
      if age >= 18;
    run;
    proc export data=adults outfile="{csv_out}" dbms=csv replace;
    run;
    """
    ds = run_sas(src)
    assert ds["people"]["name"].tolist() == ["Alice", "Bob"]
    assert ds["adults"]["name"].tolist() == ["Alice"]
    assert csv_out.exists()
    assert csv_out.read_text().strip().splitlines() == ["name,age", "Alice,30"]


def test_macro_driven_data_step():
    src = """
    %let cutoff = 50;
    data src;
      input x;
      datalines;
    10
    60
    90
    ;
    run;
    data out;
      set src;
      if x > &cutoff then flag = "HIGH";
      else flag = "LOW";
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["flag"].tolist() == ["LOW", "HIGH", "HIGH"]
