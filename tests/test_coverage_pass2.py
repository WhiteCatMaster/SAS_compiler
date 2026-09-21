import pandas as pd

from sas_compiler import compile_source
from sas_compiler.parser import parse


def run_print(source: str, capsys) -> str:
    code = compile_source(source)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return capsys.readouterr().out, g["_DS"]


def test_time_and_datetime_literals():
    stmts = parse(
        "data a; t='12:34:56't; dt='01JAN2010:01:02:03'dt; d='01JAN2010'd; run;"
    ).steps[0].statements
    assert stmts[0].expr.value == 12 * 3600 + 34 * 60 + 56
    assert stmts[1].expr.value == 18263 * 86400 + 3723
    assert stmts[2].expr.value == 18263.0


def test_time_functions_run(capsys):
    out, ds = run_print(
        "data a;\n"
        "  t = hms(12, 34, 56);\n"
        "  h = hour('01JAN2010:06:00:00'dt);\n"
        "  p = datepart('01JAN2010:06:00:00'dt);\n"
        "  s = put(t, time8.);\n"
        "run;\n"
        "proc print data=a;\nrun;\n",
        capsys,
    )
    row = ds["a"].iloc[0]
    assert row["t"] == 45296.0
    assert row["h"] == 6.0
    assert row["p"] == 18263.0
    assert "12:34:56" in out


def test_rank_groups(capsys):
    out, ds = run_print(
        "data v; input x; datalines;\n1\n2\n3\n4\n;\nrun;\n"
        "proc rank data=v out=r groups=2;\nvar x;\nranks b;\nrun;\n",
        capsys,
    )
    assert list(ds["r"]["b"].astype(int)) == [0, 0, 1, 1]


def test_univariate_output_out(capsys):
    out, ds = run_print(
        "data v; input x; datalines;\n1\n2\n3\n4\n;\nrun;\n"
        "proc univariate data=v;\nvar x;\noutput out=u;\nrun;\n",
        capsys,
    )
    row = ds["u"].iloc[0]
    assert row["_mean_"] == 2.5 and row["_n_"] == 4 and row["_max_"] == 4.0


def test_freq_output_out(capsys):
    out, ds = run_print(
        "data v; input g $; datalines;\na\na\nb\n;\nrun;\n"
        "proc freq data=v;\ntables g;\noutput out=f;\nrun;\n",
        capsys,
    )
    f = ds["f"]
    assert sorted(f["count"]) == [1, 2]
    assert abs(f["percent"].sum() - 100.0) < 1e-9


def test_title_and_footnote(capsys):
    out, _ = run_print(
        "data w; x = 1; run;\n"
        'title "My Report";\n'
        "proc print data=w;\nrun;\n"
        'footnote "the end";\n'
        "proc print data=w;\nrun;\n",
        capsys,
    )
    assert out.index("My Report") < out.index("Obs")
    assert out.rstrip().endswith("the end")


def test_datasets_delete_and_rename_dir_lib(tmp_path, capsys):
    from sas_compiler import runtime as r

    src = (
        f'libname d "{tmp_path}";\n'
        "data d.k; v = 1; run;\n"
        "proc datasets;\n  change d.k=d.r;\nrun;\n"
        "proc datasets;\n  delete d.r;\nrun;\n"
    )
    run_print(src, capsys)
    assert list(tmp_path.iterdir()) == []
    r.libname_clear("d")


def test_dif_function(capsys):
    out, ds = run_print(
        "data v; input x; datalines;\n10\n12\n15\n;\nrun;\n"
        "data d; set v; dx = dif(x); run;\n",
        capsys,
    )
    dx = ds["d"]["dx"].tolist()
    assert pd.isna(dx[0]) and dx[1] == 2.0 and dx[2] == 3.0


def test_sgplot_new_kinds(tmp_path, capsys):
    out_png = tmp_path / "p.png"
    out, _ = run_print(
        "data v; input x; datalines;\n1\n2\n3\n4\n;\nrun;\n"
        f'proc sgplot data=v out="{out_png}";\n'
        "  hbar x;\n  density x;\n  refline 2;\nrun;\n",
        capsys,
    )
    assert out_png.exists() and "saved" in out


def test_print_id_statement(capsys):
    out, _ = run_print(
        "data v; input id $ x; datalines;\na 1\nb 2\n;\nrun;\n"
        "proc print data=v;\n  id id;\nrun;\n",
        capsys,
    )
    assert "Obs" not in out
    assert "a" in out and "b" in out


def test_transpose_prefix_suffix(capsys):
    out, ds = run_print(
        "data v; input x; datalines;\n1\n2\n;\nrun;\n"
        "proc transpose data=v out=t prefix=Q suffix=_s;\nvar x;\nrun;\n",
        capsys,
    )
    assert list(ds["t"].columns) == ["_name_", "Q1_s", "Q2_s"]


def test_symputx_and_new_string_funcs(capsys):
    from sas_compiler import runtime as r

    run_print("data _null_;\n  call symputx('who', '  ada  ');\nrun;\n", capsys)
    assert r.MACRO_VARS.get("who") == "ada"
    _, ds2 = run_print(
        "data w;\n"
        "  a = translate('abc', 'XY', 'ab');\n"
        "  b = verify('abc', 'ab');\n"
        "  c = prxmatch('/^b/', 'abc');\n"
        "run;\n",
        capsys,
    )
    row = ds2["w"].iloc[0]
    assert row["a"] == "XYc" and row["b"] == 3.0 and row["c"] == 0.0


def test_quarter_intervals(capsys):
    out, ds = run_print(
        "data w;\n"
        "  q = intck('qtr', '01JAN2010'd, '01APR2010'd);\n"
        "  d = intnx('qtr', '15FEB2010'd, 1);\n"
        "run;\n",
        capsys,
    )
    row = ds["w"].iloc[0]
    assert row["q"] == 1.0
    from datetime import date

    assert row["d"] == float((date(2010, 4, 1) - date(1960, 1, 1)).days)


def test_sort_dupout(capsys):
    out, ds = run_print(
        "data v; input x; datalines;\n1\n1\n2\n;\nrun;\n"
        "proc sort data=v out=s dupout=d nodupkey;\nby x;\nrun;\n",
        capsys,
    )
    assert len(ds["s"]) == 2 and len(ds["d"]) == 1 and ds["d"]["x"].iloc[0] == 1.0


def test_tabulate_leading_stat(capsys):
    out, _ = run_print(
        "data s; input region $ sales; datalines;\nE 10\nE 20\nW 30\n;\nrun;\n"
        "proc tabulate data=s;\n  class region;\n  var sales;\n"
        "  table region, mean*sales;\nrun;\n",
        capsys,
    )
    assert "15" in out and "30" in out


def test_report_break_rbreak(capsys):
    out, _ = run_print(
        "data s; input region $ sales; datalines;\nE 10\nE 20\nW 30\n;\nrun;\n"
        "proc report data=s;\n"
        "  column region sales;\n"
        "  define region / group;\n"
        "  define sales / analysis sum;\n"
        "  break after region / summarize;\n"
        "  rbreak after / summarize;\n"
        "run;\n",
        capsys,
    )
    assert "Total" in out and "60" in out


def test_report_compute_block(capsys):
    out, _ = run_print(
        "data s; input region $ sales; datalines;\nE 10\nE 20\nW 5\n;\nrun;\n"
        "proc report data=s;\n"
        "  column region sales double;\n"
        "  define region / group;\n"
        "  define sales / analysis sum;\n"
        "  define double / computed;\n"
        "  compute double;\n"
        "    double = sales * 2;\n"
        "    if sales < 10 then delete;\n"
        "  endcomp;\n"
        "run;\n",
        capsys,
    )
    assert "60" in out and "W" not in out


def test_corr_with_statement(capsys):
    out, _ = run_print(
        "data s; input a b; datalines;\n1 2\n2 4\n3 6\n;\nrun;\n"
        "proc corr data=s;\n  var a b;\n  with a;\nrun;\n",
        capsys,
    )
    assert "With Variables: a" in out and "1.0000" in out


def test_string_six_pack(capsys):
    _, ds = run_print(
        "data w;\n"
        "  a = compbl('x  y');\n"
        "  b = reverse('abc');\n"
        "  c = quote('hi');\n"
        "  d = dequote('\"hi\"');\n"
        "  e = findc('abc', 'b');\n"
        "  f = findw('the cat sat', 'cat');\n"
        "run;\n",
        capsys,
    )
    row = ds["w"].iloc[0]
    assert row["a"] == "x y"
    assert row["b"] == "cba"
    assert row["c"] == '"hi"'
    assert row["d"] == "hi"
    assert row["e"] == 2.0
    assert row["f"] == 5.0


def test_means_types_ways(capsys):
    src = (
        "data s; input a $ b $ v; datalines;\n"
        "x p 1\nx q 2\ny p 3\ny q 4\n;\nrun;\n"
        "proc means data=s;\n  class a b;\n  var v;\n  types a;\nrun;\n"
    )
    out, _ = run_print(src, capsys)
    assert "1.5" in out and "3.5" in out
    out, ds = run_print(
        "data s; input a $ b $ v; datalines;\n"
        "x p 1\nx q 2\ny p 3\ny q 4\n;\nrun;\n"
        "proc means data=s;\n  class a b;\n  var v;\n  ways 0 1;\n"
        "  output out=m;\nrun;\n",
        capsys,
    )
    # overall (1) + a (2) + b (2) = 5 rows
    assert len(ds["m"]) == 5


def test_freq_multiway_out(capsys):
    _, ds = run_print(
        "data s; input a $ b $; datalines;\nx p\nx q\ny p\n;\nrun;\n"
        "proc freq data=s;\n  tables a*b;\n  output out=f;\nrun;\n",
        capsys,
    )
    assert ds["f"]["count"].sum() == 3


def test_tabulate_multi_stat(capsys):
    out, ds = run_print(
        "data s; input g $ v; datalines;\na 1\na 3\nb 2\n;\nrun;\n"
        "proc tabulate data=s out=t;\n  class g;\n  var v;\n"
        "  table g, v*(sum mean);\nrun;\n",
        capsys,
    )
    assert "SUM" in out and "MEAN" in out
    assert set(ds["t"]["_stat_"]) == {"sum", "mean"}


def test_hash_multidata_hiter(capsys):
    _, ds = run_print(
        "data l; input id v; datalines;\n1 10\n1 20\n2 30\n;\nrun;\n"
        "data walk;\n"
        '  declare hash h(dataset: "l", multidata: "Y");\n'
        '  h.definekey("id");\n'
        '  h.definedata("v");\n'
        "  h.definedone();\n"
        '  declare hiter hi("h");\n'
        "  rc = hi.first();\n"
        "  do while (rc = 0);\n"
        "    output;\n"
        "    rc = hi.next();\n"
        "  end;\n"
        "  stop;\n"
        "run;\n",
        capsys,
    )
    assert ds["walk"]["v"].tolist() == [10.0, 20.0, 30.0]


def test_nested_set_point_nobs(capsys):
    _, ds = run_print(
        "data s; input x; datalines;\n10\n20\n30\n;\nrun;\n"
        "data rev;\n"
        "  do p = 3 to 1 by -1;\n"
        "    set s point=p nobs=n;\n"
        "    output;\n"
        "  end;\n"
        "  stop;\n"
        "run;\n",
        capsys,
    )
    assert ds["rev"]["x"].tolist() == [30.0, 20.0, 10.0]
    assert ds["rev"]["n"].tolist() == [3.0, 3.0, 3.0]


def test_sql_url_lib(tmp_path, capsys):
    import sqlite3

    db = tmp_path / "srv.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE items (id INTEGER, val REAL)")
    con.executemany("INSERT INTO items VALUES (?,?)", [(1, 10.0), (2, 20.0)])
    con.commit()
    con.close()
    out, _ = run_print(
        f'libname srv "sqlite:///{db}";\n'
        "proc sql;\n"
        "  select sum(val) as s from srv.items;\n"
        "quit;\n",
        capsys,
    )
    assert "30" in out
