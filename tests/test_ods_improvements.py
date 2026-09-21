import sys

from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse
from sas_compiler.codegen import generate
from sas_compiler import runtime as _r


def compile_and_run(source: str):
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g: dict = {}
    exec(compile(code, "<test>", "exec"), g)
    return g


def test_html_renders_real_table_for_three_plus_column_output(tmp_path):
    out_path = tmp_path / "report.html"
    src = f"""
    data a;
      input name $ score bonus;
      datalines;
    Alice 90 5
    Bob 80 3
    ;
    run;

    ods html file="{out_path}";
    proc print data=a;
    run;
    ods html close;
    """
    compile_and_run(src)
    content = out_path.read_text()
    assert "<table>" in content
    assert "<tr>" in content
    assert "<th>" in content
    assert "Alice" in content
    assert "<td>90.0</td>" in content


def test_html_falls_back_to_pre_for_narrow_output(tmp_path):
    # a single-VAR PROC PRINT (Obs + one column = 2 fields) stays <pre>,
    # matching the pre-existing test_ods_html.py expectations
    out_path = tmp_path / "narrow.html"
    src = f"""
    data a;
      x = 1;
      output;
      x = 2;
      output;
    run;

    ods html file="{out_path}";
    proc print data=a;
    run;
    ods html close;
    """
    compile_and_run(src)
    content = out_path.read_text()
    assert "<pre>" in content
    assert "<table>" not in content


def test_multiple_procs_render_as_separate_tables(tmp_path):
    out_path = tmp_path / "multi.html"
    src = f"""
    data a;
      input name $ score bonus;
      datalines;
    Alice 90 5
    Bob 80 3
    Carol 95 7
    ;
    run;

    ods html file="{out_path}";
    proc print data=a;
    run;
    proc means data=a;
      var score bonus;
    run;
    ods html close;
    """
    compile_and_run(src)
    content = out_path.read_text()
    assert content.count("<table>") == 2
    assert "score_mean" in content


def test_rtf_file_is_created_and_starts_with_rtf_header(tmp_path):
    out_path = tmp_path / "report.rtf"
    src = f"""
    data a;
      x = 1;
      output;
    run;

    ods rtf file="{out_path}";
    proc print data=a;
    run;
    ods rtf close;
    """
    compile_and_run(src)
    content = out_path.read_text()
    assert content.startswith("{\\rtf1")
    assert content.rstrip().endswith("}")
    assert "1.0" in content


def test_html_and_rtf_can_be_open_simultaneously(tmp_path):
    real_stdout = sys.stdout
    html_path = tmp_path / "both.html"
    rtf_path = tmp_path / "both.rtf"
    src = f"""
    ods html file="{html_path}";
    ods rtf file="{rtf_path}";
    data _null_;
      put "shared content";
    run;
    ods html close;
    ods rtf close;
    """
    compile_and_run(src)
    assert "shared content" in html_path.read_text()
    assert "shared content" in rtf_path.read_text()
    assert sys.stdout is real_stdout


def test_closing_one_destination_leaves_the_other_open(tmp_path):
    real_stdout = sys.stdout
    html_path = tmp_path / "h.html"
    rtf_path = tmp_path / "r.rtf"
    src = f"""
    ods html file="{html_path}";
    ods rtf file="{rtf_path}";
    data _null_;
      put "before html close";
    run;
    ods html close;
    data _null_;
      put "after html close, rtf still open";
    run;
    ods rtf close;
    """
    compile_and_run(src)
    html_content = html_path.read_text()
    rtf_content = rtf_path.read_text()
    assert "before html close" in html_content
    assert "after html close" not in html_content
    assert "before html close" in rtf_content
    assert "after html close, rtf still open" in rtf_content
    assert sys.stdout is real_stdout


def test_ods_proc_boundary_is_noop_when_nothing_open(capsys):
    # must never affect ordinary (non-ODS) program output
    src = """
    data a;
      x = 1;
      output;
    run;
    proc print data=a;
    run;
    """
    compile_and_run(src)
    out = capsys.readouterr().out
    assert not out.endswith("\n\n\n")


def test_rtf_close_without_open_is_a_noop():
    _r.ods_rtf_close()
    assert _r._ODS_RTF_STATE["active"] is False
