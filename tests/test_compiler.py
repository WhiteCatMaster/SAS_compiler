import math

import pandas as pd
import pytest

from sas_compiler.macro import MacroProcessor, MacroError
from sas_compiler.parser import parse
from sas_compiler.codegen import generate, CodegenError
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


def test_file_statement_two_files_route_put_per_statement(tmp_path):
    # A FILE statement is a real per-statement effect: every PUT after it
    # (until the next FILE) goes to *that* fileref, not "last FILE wins".
    p1 = tmp_path / "one.txt"
    p2 = tmp_path / "two.txt"
    src = f"""
    data _null_;
      file "{p1}";
      put "one";
      file "{p2}";
      put "two";
    run;
    """
    run_sas(src)
    assert p1.read_text() == "one\n"
    assert p2.read_text() == "two\n"


def test_file_statement_inside_if_do_redirects_some_rows(capsys):
    # A FILE statement nested inside IF/DO must switch the PUT target
    # following normal control flow, leaving rows that never hit it on
    # stdout (the default target before any FILE statement has run).
    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        src = f"""
        data _null_;
          input x;
          if x = 2 then do;
            file "{path}";
            put x;
          end;
          else do;
            file print;
            put x;
          end;
          datalines;
        1
        2
        3
        ;
        run;
        """
        run_sas(src)
        captured = capsys.readouterr()
        assert "1" in captured.out.split()
        assert "3" in captured.out.split()
        assert "2" not in captured.out.split()
        with open(path) as f:
            file_content = f.read()
        assert file_content == "2\n"
    finally:
        os.remove(path)


def test_file_statement_reexecuted_in_loop_does_not_truncate(tmp_path):
    # Re-executing FILE "same path" (e.g. inside a DO loop) must reuse the
    # already-open handle rather than reopening/truncating it each time.
    p = tmp_path / "loop.txt"
    src = f"""
    data _null_;
      do i = 1 to 5;
        file "{p}";
        put i;
      end;
    run;
    """
    run_sas(src)
    lines = p.read_text().splitlines()
    assert lines == ["1", "2", "3", "4", "5"]


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


def test_proc_means_explicit_stat_keywords():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    4
    5
    ;
    run;
    proc means data=src n mean median p25 p75 range noprint;
      var x;
      output out=stats;
    run;
    """
    ds = run_sas(src)
    row = ds["stats"].iloc[0]
    assert row["x_n"] == 5.0
    assert row["x_median"] == 3.0
    assert row["x_range"] == 4.0
    assert row["x_p25"] == 2.0
    assert row["x_p75"] == 4.0


def test_proc_means_lclm_uclm_confidence_interval():
    from scipy import stats as scipy_stats

    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    4
    5
    ;
    run;
    proc means data=src mean lclm uclm noprint;
      var x;
      output out=stats;
    run;
    """
    ds = run_sas(src)
    row = ds["stats"].iloc[0]
    n = 5
    mean = 3.0
    std = pd.Series([1, 2, 3, 4, 5]).std()
    se = std / n ** 0.5
    margin = scipy_stats.t.ppf(0.975, n - 1) * se
    assert row["x_mean"] == pytest.approx(mean)
    assert row["x_lclm"] == pytest.approx(mean - margin)
    assert row["x_uclm"] == pytest.approx(mean + margin)


def test_proc_means_lclm_uclm_by_class_group():
    from scipy import stats as scipy_stats

    src = """
    data src;
      input g $ x;
      datalines;
    a 1
    a 2
    a 3
    a 4
    b 10
    b 20
    b 30
    ;
    run;
    proc means data=src mean lclm uclm noprint;
      class g;
      var x;
      output out=stats;
    run;
    """
    ds = run_sas(src)
    out = ds["stats"].set_index("g")

    def expected_bounds(values):
        s = pd.Series(values, dtype=float)
        n = len(s)
        mean = s.mean()
        se = s.std() / n ** 0.5
        margin = scipy_stats.t.ppf(0.975, n - 1) * se
        return mean - margin, mean + margin

    a_lo, a_hi = expected_bounds([1, 2, 3, 4])
    b_lo, b_hi = expected_bounds([10, 20, 30])
    assert out.loc["a", "x_lclm"] == pytest.approx(a_lo)
    assert out.loc["a", "x_uclm"] == pytest.approx(a_hi)
    assert out.loc["b", "x_lclm"] == pytest.approx(b_lo)
    assert out.loc["b", "x_uclm"] == pytest.approx(b_hi)


def test_proc_datasets_delete_and_change():
    src = """
    data temp1 temp2 keep_me;
      x = 1;
    run;
    proc datasets library=work nolist;
      delete temp1 temp2;
    run;
    quit;
    data renamed;
      x = 1;
    run;
    proc datasets library=work nolist;
      change renamed=final;
    run;
    quit;
    """
    ds = run_sas(src)
    assert "temp1" not in ds
    assert "temp2" not in ds
    assert "keep_me" in ds
    assert "renamed" not in ds
    assert "final" in ds


def test_label_statement_used_as_print_header(capsys):
    src = """
    data out;
      x = 1;
      y = 2;
      label x = "First Value" y = "Second Value";
    run;
    proc print data=out;
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["x"].tolist() == [1.0]  # underlying column name unaffected
    captured = capsys.readouterr().out
    assert "First Value" in captured
    assert "Second Value" in captured


def test_include_splices_macro_library(tmp_path):
    lib = tmp_path / "lib.sas"
    lib.write_text("%macro double(x);\n  (&x * 2)\n%mend double;\n")
    src = f"""
    %include "{lib}";
    data out;
      x = %double(21);
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["x"].tolist() == [42.0]


def test_proc_univariate_output(capsys):
    src = """
    data src;
      input x;
      datalines;
    1
    2
    2
    3
    4
    5
    100
    ;
    run;
    proc univariate data=src;
      var x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Variable: x" in out
    assert "N                7" in out
    assert "Mode             2.0" in out
    assert "50%  3.0" in out


def test_proc_univariate_normality_matches_scipy(capsys):
    import re

    from scipy import stats as _stats

    vals = [2, 4, 4, 4, 5, 5, 7, 9]
    src = """
    data src;
      input x;
      datalines;
    2
    4
    4
    4
    5
    5
    7
    9
    ;
    run;
    proc univariate data=src;
      var x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Tests for Normality" in out

    s = pd.Series(vals, dtype=float)
    exp_w, exp_wp = _stats.shapiro(s)
    exp_d, exp_dp = _stats.kstest(
        s, _stats.norm(loc=s.mean(), scale=s.std(ddof=1)).cdf
    )

    m = re.search(r"Shapiro-Wilk\s+W=([\-0-9.]+)\s+Pr < W=([\-0-9.]+)", out)
    assert m, out
    assert float(m.group(1)) == pytest.approx(exp_w, abs=1e-4)
    assert float(m.group(2)) == pytest.approx(exp_wp, abs=1e-4)

    m = re.search(r"Kolmogorov-Smirnov\s+D=([\-0-9.]+)\s+Pr > D=([\-0-9.]+)", out)
    assert m, out
    assert float(m.group(1)) == pytest.approx(exp_d, abs=1e-4)
    assert float(m.group(2)) == pytest.approx(exp_dp, abs=1e-4)


def test_proc_univariate_normality_small_n_not_computed(capsys):
    src = """
    data src;
      input x;
      datalines;
    1
    2
    ;
    run;
    proc univariate data=src;
      var x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Shapiro-Wilk       not computed (N < 3)" in out
    assert "Kolmogorov-Smirnov D=" in out or "Kolmogorov-Smirnov not computed" in out


def test_proc_univariate_normality_constant_column_not_computed(capsys):
    src = """
    data src;
      input x;
      datalines;
    5
    5
    5
    5
    ;
    run;
    proc univariate data=src;
      var x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Kolmogorov-Smirnov not computed (zero variance)" in out


def test_temporary_array_lookup():
    src = """
    data src;
      input grp;
      datalines;
    1
    2
    3
    ;
    run;
    data out;
      array codes{3} _temporary_ (100, 200, 300);
      set src;
      matched = codes{grp};
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["matched"].tolist() == [100.0, 200.0, 300.0]
    assert not any(c.startswith("__tmp_") for c in ds["out"].columns)


def test_proc_rank_descending_with_ties():
    src = """
    data src;
      input name $ score;
      datalines;
    Alice 90
    Bob 70
    Carol 85
    Dave 70
    ;
    run;
    proc rank data=src out=ranked descending;
      var score;
      ranks rank_score;
    run;
    """
    ds = run_sas(src)
    df = ds["ranked"].set_index("name")
    assert df.loc["Alice", "rank_score"] == 1.0
    assert df.loc["Carol", "rank_score"] == 2.0
    assert df.loc["Bob", "rank_score"] == 3.5
    assert df.loc["Dave", "rank_score"] == 3.5


def test_proc_corr_matrix():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 4
    3 6
    4 8
    5 10
    ;
    run;
    proc corr data=src;
      var x y;
    run;
    """
    ds = run_sas(src)
    # proc_corr_report doesn't register a dataset unless OUT= is given
    assert "src" in ds


def test_proc_reg_fits_and_scores():
    src = """
    data src;
      input x y;
      datalines;
    1 2
    2 4
    3 6
    4 8
    5 10
    ;
    run;
    proc reg data=src;
      model y = x;
      output out=scored p=predicted r=resid;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert df["predicted"].round(4).tolist() == [2.0, 4.0, 6.0, 8.0, 10.0]
    assert all(abs(r) < 1e-6 for r in df["resid"])


def test_proc_reg_model_slash_options_do_not_pollute_predictors():
    # Regression test: MODEL y = x1 x2 / vif; used to mis-tokenize '/' and
    # 'vif' into the predictor list (xs == ['x1', 'x2', '/', 'vif']),
    # corrupting the fit. It must now use only x1/x2 as predictors.
    src = """
    data src;
      input x1 x2 y;
      datalines;
    1 5 2
    2 4 4
    3 3 6
    4 2 8
    5 1 10
    ;
    run;
    proc reg data=src;
      model y = x1 x2 / vif;
      output out=scored p=predicted r=resid;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    # y = 2*x1 exactly (x2 is irrelevant but must still be accepted as a
    # real predictor, not corrupted by bogus '/'/'vif' tokens).
    assert df["predicted"].round(4).tolist() == [2.0, 4.0, 6.0, 8.0, 10.0]
    assert all(abs(r) < 1e-6 for r in df["resid"])


def test_proc_reg_vif_reports_high_for_collinear_and_low_for_independent(capsys):
    import numpy as np
    import statsmodels.api as sm
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    rng = np.random.RandomState(0)
    n = 200
    x1 = rng.normal(size=n)
    x2 = x1 + rng.normal(scale=1e-6, size=n)  # near-perfectly collinear with x1
    x3 = rng.normal(size=n)  # independent
    y = 3 * x1 + 2 * x3 + rng.normal(scale=0.1, size=n)

    df = pd.DataFrame({"x1": x1, "x2": x2, "x3": x3, "y": y})
    lines = "\n".join(f"{a} {b} {c} {d}" for a, b, c, d in zip(x1, x2, x3, y))
    src = f"""
    data src;
      input x1 x2 x3 y;
      datalines;
{lines}
    ;
    run;
    proc reg data=src;
      model y = x1 x2 x3 / vif;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Variance Inflation Factor" in out

    # Cross-check against an independently built design matrix/statsmodels call.
    X = sm.add_constant(df[["x1", "x2", "x3"]])
    vif_x1 = variance_inflation_factor(X.values, 1)
    vif_x2 = variance_inflation_factor(X.values, 2)
    vif_x3 = variance_inflation_factor(X.values, 3)
    assert vif_x1 > 1000  # collinear pair -> huge VIF
    assert vif_x2 > 1000
    assert vif_x3 < 2  # independent predictor -> VIF near 1

    # The printed VIF table's x3 row should show a small, near-1 VIF value.
    # (model.summary() above also has an "x3" coefficient row, so only look
    # at lines after the "Variance Inflation Factor" heading.)
    vif_section = out.split("Variance Inflation Factor", 1)[1]
    x3_line = [ln for ln in vif_section.splitlines() if ln.strip().startswith("x3")][0]
    printed_vif_x3 = float(x3_line.split()[-1])
    assert abs(printed_vif_x3 - vif_x3) < 1e-3


def _reg_selection_datalines():
    import numpy as np

    rng = np.random.RandomState(0)
    n = 200
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    x3 = rng.normal(size=n)  # pure noise, unrelated to y
    x4 = rng.normal(size=n)  # pure noise, unrelated to y
    y = 3 * x1 - 2 * x2 + rng.normal(scale=0.2, size=n)
    lines = "\n".join(
        f"{a} {b} {c} {d} {e}" for a, b, c, d, e in zip(x1, x2, x3, x4, y)
    )
    return lines


def test_proc_reg_selection_backward_drops_noise_predictors(capsys):
    lines = _reg_selection_datalines()
    src = f"""
    data src;
      input x1 x2 x3 x4 y;
      datalines;
{lines}
    ;
    run;
    proc reg data=src;
      model y = x1 x2 x3 x4 / selection=backward slstay=0.05;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "removed" in out
    # The final summary (last occurrence, after any step-removal logs)
    # should retain x1/x2 and drop the noise predictors x3/x4.
    summary = out.rsplit("OLS Regression Results", 1)[1]
    assert "x1" in summary
    assert "x2" in summary
    assert "x3" not in summary
    assert "x4" not in summary


def test_proc_reg_selection_forward_converges_to_sensible_model(capsys):
    lines = _reg_selection_datalines()
    src = f"""
    data src;
      input x1 x2 x3 x4 y;
      datalines;
{lines}
    ;
    run;
    proc reg data=src;
      model y = x1 x2 x3 x4 / selection=forward slentry=0.05;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "added" in out
    summary = out.rsplit("OLS Regression Results", 1)[1]
    assert "x1" in summary
    assert "x2" in summary
    assert "x3" not in summary
    assert "x4" not in summary


def test_proc_reg_selection_stepwise_is_unsupported():
    src = """
    data src;
      input x1 x2 y;
      datalines;
    1 5 2
    2 4 4
    3 3 6
    4 2 8
    5 1 10
    ;
    run;
    proc reg data=src;
      model y = x1 x2 / selection=stepwise;
    run;
    """
    expanded = MacroProcessor().expand(src)
    prog = parse(expanded)
    with pytest.raises(CodegenError, match=r"(?i)selection=stepwise"):
        generate(prog)


def test_proc_logistic_fits_and_scores():
    src = """
    data src;
      input hours pass;
      datalines;
    1 0
    2 0
    3 0
    4 1
    5 0
    6 1
    7 1
    8 0
    9 1
    10 1
    ;
    run;
    proc logistic data=src;
      model pass = hours;
      output out=scored p=predicted_prob;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert len(df) == 10
    assert df["predicted_prob"].between(0, 1).all()
    # predicted probability should be monotonically increasing with hours
    assert df.sort_values("hours")["predicted_prob"].is_monotonic_increasing


def test_proc_logistic_reports_high_c_statistic_for_separable_data(capsys):
    from sklearn.metrics import roc_auc_score

    src = """
    data src;
      input hours pass;
      datalines;
    1 0
    1.5 0
    2 0
    2.5 0
    3 0
    8 1
    8.5 1
    9 1
    9.5 1
    10 1
    ;
    run;
    proc logistic data=src;
      model pass = hours;
      output out=scored p=predicted_prob;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    out = capsys.readouterr().out
    assert "Association of Predicted Probabilities and Observed Responses" in out

    expected_c = roc_auc_score(df["pass"], df["predicted_prob"])
    assert expected_c > 0.95  # clearly separable outcome -> c near 1

    c_line = [ln for ln in out.splitlines() if ln.strip().startswith("c ")][0]
    printed_c = float(c_line.split()[-1])
    assert abs(printed_c - expected_c) < 1e-3


def test_proc_logistic_reports_c_statistic_near_half_for_weak_relationship(capsys):
    import numpy as np

    rng = np.random.RandomState(0)
    n = 200
    hours = rng.normal(size=n)
    # outcome essentially independent of the predictor
    pass_ = rng.randint(0, 2, size=n)
    df = pd.DataFrame({"hours": hours, "pass": pass_})
    lines = "\n".join(f"{h} {p}" for h, p in zip(hours, pass_))
    src = f"""
    data src;
      input hours pass;
      datalines;
{lines}
    ;
    run;
    proc logistic data=src;
      model pass = hours;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    c_line = [ln for ln in out.splitlines() if ln.strip().startswith("c ")][0]
    printed_c = float(c_line.split()[-1])
    assert 0.35 < printed_c < 0.65  # near-random relationship -> c close to 0.5


def test_proc_logistic_single_outcome_level_reports_clear_message_not_crash(capsys):
    src = """
    data src;
      input hours pass;
      datalines;
    1 0
    2 0
    3 0
    4 0
    5 0
    ;
    run;
    proc logistic data=src;
      model pass = hours;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "c statistic undefined" in out


def test_proc_logistic_class_variable_categorical_predictor(capsys):
    # A genuine categorical predictor (region name) must be dummy-encoded
    # via CLASS rather than coerced to numeric (which would silently give
    # NaN coefficients / a crash under the old pd.to_numeric-everything
    # behavior).
    src = """
    data src;
      input region $ outcome;
      datalines;
    East 0
    East 0
    East 1
    East 1
    West 0
    West 1
    West 1
    West 1
    South 0
    South 0
    South 0
    South 1
    ;
    run;
    proc logistic data=src;
      class region;
      model outcome = region;
      output out=scored p=predicted_prob;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert len(df) == 12
    assert df["predicted_prob"].between(0, 1).all()
    assert df["predicted_prob"].notna().all()

    out = capsys.readouterr().out
    assert "region_South" in out
    assert "region_West" in out


def test_proc_logistic_class_variable_mixed_with_numeric_predictor(capsys):
    # CLASS categorical predictor combined with an ordinary numeric
    # predictor in the same MODEL statement.
    src = """
    data src;
      input region $ hours outcome;
      datalines;
    East 1 0
    East 2 0
    East 8 1
    East 9 1
    West 1 0
    West 2 1
    West 8 1
    West 9 1
    South 1 0
    South 2 0
    South 8 0
    South 9 1
    ;
    run;
    proc logistic data=src;
      class region;
      model outcome = hours region;
      output out=scored p=predicted_prob;
    run;
    """
    ds = run_sas(src)
    df = ds["scored"]
    assert len(df) == 12
    assert df["predicted_prob"].between(0, 1).all()
    assert df["predicted_prob"].notna().all()

    out = capsys.readouterr().out
    assert "hours" in out
    assert "region_South" in out
    assert "region_West" in out


def _seed_sqlite(path, rows):
    import sqlite3
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE students (id INTEGER, name TEXT, score REAL)")
    con.executemany("INSERT INTO students VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def test_libname_read_through(tmp_path):
    db_path = tmp_path / "class.db"
    _seed_sqlite(db_path, [(1, "Alice", 90), (2, "Bob", 70), (3, "Carol", 85)])
    src = f"""
    libname classdb "{db_path}";
    data high;
      set classdb.students;
      if score >= 80;
    run;
    """
    ds = run_sas(src)
    assert sorted(ds["high"]["name"].tolist()) == ["Alice", "Carol"]


def test_libname_write_through(tmp_path):
    import sqlite3
    db_path = tmp_path / "class.db"
    _seed_sqlite(db_path, [(1, "Alice", 90), (2, "Bob", 70)])
    src = f"""
    libname classdb "{db_path}";
    data classdb.passing;
      set classdb.students;
      passed = (score >= 75);
    run;
    """
    run_sas(src)
    con = sqlite3.connect(str(db_path))
    rows = con.execute("SELECT name, passed FROM passing ORDER BY name").fetchall()
    con.close()
    assert rows == [("Alice", 1), ("Bob", 0)]


def test_proc_sql_attach_sqlite(tmp_path, capsys):
    db_path = tmp_path / "class.db"
    _seed_sqlite(db_path, [(1, "Alice", 90), (2, "Bob", 70), (3, "Carol", 85)])
    src = f"""
    libname classdb "{db_path}";
    proc sql;
      select name, score from classdb.students where score > 70 order by score desc;
    quit;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "Alice" in out
    assert "Carol" in out
    assert "Bob" not in out


def test_zero_row_output_preserves_columns():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    ;
    run;
    data out;
      set src;
      y = x * 2;
      if x > 0 then delete;
    run;
    """
    ds = run_sas(src)
    # every row was deleted, but the schema should still reflect the
    # variables that would have existed (regression: used to lose all columns)
    assert list(ds["out"].columns) == ["x", "y"]
    assert len(ds["out"]) == 0


def test_retain_dash_range_expansion():
    src = """
    data out;
      retain h1-h3 0;
      h1 + 1;
      h2 + 2;
      h3 + 3;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["h1"] == 1.0
    assert row["h2"] == 2.0
    assert row["h3"] == 3.0


def test_title_footnote_numbered_slots(capsys):
    src = """
    data a; x = 1; run;
    title1 'Orion Star Sales Staff';
    title2 'Salary report';
    title3 'September2026';
    footnote1 'Confidential';
    title2 'TEST';
    proc print data=a; run;
    title;
    footnote;
    proc print data=a; run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    first_print_pos = out.index("Obs")
    before = out[:first_print_pos]
    after = out[first_print_pos:]
    # title1 survives, title3 is cancelled by setting title2, title2 becomes TEST
    assert "Orion Star Sales Staff" in before
    assert "TEST" in before
    assert "September2026" not in before
    assert "Confidential" in after
    # after `title;`/`footnote;` clear everything, nothing prints again
    assert "Orion Star Sales Staff" not in after.split("Confidential", 1)[1]
    assert "TEST" not in after.split("Confidential", 1)[1]


def test_proc_print_id_not_in_var_list(capsys):
    src = """
    data a;
      input id name $ score;
      datalines;
    1 Alice 90
    2 Bob 80
    ;
    run;
    proc print data=a;
      id id;
      var name score;
    run;
    """
    ds = run_sas(src)
    # the underlying dataset is unaffected -- only PROC PRINT's *display*
    # treats id specially (as the row label instead of a data column)
    assert list(ds["a"].columns) == ["id", "name", "score"]
    out = capsys.readouterr().out
    # the id values (1.0/2.0) become the row label; "id" isn't a second,
    # duplicate data column in the printed table
    assert "1.0" in out and "2.0" in out
    header_line = out.splitlines()[0]
    assert "id" not in header_line.split()


def test_proc_print_by_not_in_var_list_groups_correctly(capsys):
    src = """
    data a;
      input grp $ x;
      datalines;
    a 1
    a 2
    b 5
    ;
    run;
    proc print data=a;
      by grp;
      var x;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "grp=a" in out
    assert "grp=b" in out
    # regression: a stray for/else bug used to print a third, ungrouped
    # table after the two BY-group tables
    assert out.count("Obs") == 2


def test_mmddyy_width_six_drops_separators():
    from sas_compiler.runtime import apply_format
    import datetime
    d = (datetime.date(1993, 11, 21) - datetime.date(1960, 1, 1)).days
    assert apply_format(float(d), "mmddyy8.") == "11/21/93"
    assert apply_format(float(d), "mmddyy6.") == "112193"
    assert apply_format(float(d), "mmddyy10.") == "11/21/1993"


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


# ---------------- edge cases ----------------
def test_data_step_zero_output_rows():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    ;
    run;
    data out;
      set src;
      if x > 100;
    run;
    """
    ds = run_sas(src)
    # NOTE: when a DATA step's output has zero rows, finalize_dataset()
    # currently has no rows to infer column names from, so the result is
    # an empty DataFrame with NO columns (not zero rows of the expected
    # shape) -- see the matching "Known limitations" bullet in README.md.
    assert len(ds["out"]) == 0


def test_all_missing_column_through_proc_means():
    src = """
    data src;
      input x y;
      datalines;
    . 1
    . 2
    . 3
    ;
    run;
    proc means data=src;
      var x y;
      output out=stats mean=avgx avgy;
    run;
    """
    ds = run_sas(src)
    row = ds["stats"].iloc[0]
    assert math.isnan(row["avgx"])
    assert row["avgy"] == 2.0


def test_drop_and_keep_together():
    src = """
    data out;
      x = 1;
      y = 2;
      z = 3;
      keep x y;
      drop y;
    run;
    """
    ds = run_sas(src)
    assert list(ds["out"].columns) == ["x"]


def test_retain_combined_with_array():
    src = """
    data src;
      input x;
      datalines;
    1
    2
    3
    ;
    run;
    data out;
      set src;
      array hist{3} h1-h3;
      retain h1 0 h2 0 h3 0;
      do i = 3 to 2 by -1;
        hist{i} = hist{i - 1};
      end;
      hist{1} = x;
      drop i;
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["h1"].tolist() == [1.0, 2.0, 3.0]
    assert ds["out"]["h2"].tolist() == [0.0, 1.0, 2.0]
    assert ds["out"]["h3"].tolist() == [0.0, 0.0, 1.0]


def test_proc_print_format_and_label_together(capsys):
    src = """
    data out;
      salary = 55000;
      format salary dollar12.2;
      label salary = "Annual Salary";
    run;
    proc print data=out;
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["salary"].tolist() == [55000.0]
    captured = capsys.readouterr().out
    assert "Annual Salary" in captured
    assert "$55,000.00" in captured


def test_proc_sort_mixed_ascending_descending():
    src = """
    data src;
      input grp $ x;
      datalines;
    a 1
    a 3
    b 2
    b 4
    ;
    run;
    proc sort data=src out=sorted;
      by grp descending x;
    run;
    """
    ds = run_sas(src)
    df = ds["sorted"]
    assert df["grp"].tolist() == ["a", "a", "b", "b"]
    assert df["x"].tolist() == [3.0, 1.0, 4.0, 2.0]


def test_merge_with_where_dataset_option():
    src = """
    data a;
      input id x;
      datalines;
    1 10
    2 20
    3 30
    ;
    run;
    data b;
      input id y;
      datalines;
    1 100
    2 200
    3 300
    ;
    run;
    data m;
      merge a(where=(x >= 20)) b;
      by id;
    run;
    """
    ds = run_sas(src)
    df = ds["m"].set_index("id")
    assert math.isnan(df.loc[1.0, "x"])
    assert df.loc[1.0, "y"] == 100.0
    assert df.loc[2.0, "x"] == 20.0
    assert df.loc[2.0, "y"] == 200.0
    assert df.loc[3.0, "x"] == 30.0
    assert df.loc[3.0, "y"] == 300.0
