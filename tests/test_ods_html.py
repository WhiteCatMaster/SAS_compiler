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


def test_ods_html_file_is_created_with_proc_output(tmp_path):
    out_path = tmp_path / "report.html"
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

    assert out_path.exists()
    content = out_path.read_text()
    assert "<html>" in content
    assert "<pre" in content
    assert "1.0" in content
    assert "2.0" in content


def test_stdout_is_captured_while_open_and_restored_after_close(capsys):
    src = """
    data a;
      x = 1;
      output;
    run;

    data _null_;
      put "before open";
    run;

    ods html file="/tmp/test_ods_html_restore.html";
    proc print data=a;
    run;
    ods html close;

    data _null_;
      put "after close";
    run;
    """
    compile_and_run(src)
    captured = capsys.readouterr()
    assert "before open" in captured.out
    assert "after close" in captured.out
    # the PROC PRINT output (an "Obs" header, distinctive to that report)
    # must NOT have reached the real stdout -- it was captured instead
    assert "Obs" not in captured.out
    # and stdout must be the real object again, not left redirected
    assert sys.stdout is sys.__stdout__ or not isinstance(sys.stdout, _r._OdsHtmlCapture)


def test_ods_html_close_without_open_is_a_noop():
    # should not raise
    _r.ods_html_close()
    assert _r._ODS_HTML_STATE["active"] is False


def test_reopen_while_already_open_flushes_previous_destination(tmp_path):
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    src = f"""
    ods html file="{first}";
    data _null_;
      put "first destination";
    run;
    ods html file="{second}";
    data _null_;
      put "second destination";
    run;
    ods html close;
    """
    compile_and_run(src)

    assert first.exists()
    assert "first destination" in first.read_text()
    assert second.exists()
    assert "second destination" in second.read_text()
    # the second destination should not contain the first's content
    assert "first destination" not in second.read_text()


def test_bare_ods_html_with_no_file_is_noop(capsys):
    src = """
    ods html;
    data _null_;
      put "still on stdout";
    run;
    """
    compile_and_run(src)
    assert "still on stdout" in capsys.readouterr().out


def test_unsupported_ods_destination_is_ignored_not_an_error(capsys):
    src = """
    ods listing close;
    ods pdf file="/tmp/should_be_ignored.pdf";
    data _null_;
      put "ods pdf/listing are not implemented, just ignored";
    run;
    ods pdf close;
    """
    compile_and_run(src)
    assert "ods pdf/listing are not implemented, just ignored" in capsys.readouterr().out


def test_exception_between_open_and_close_still_restores_stdout_via_atexit_helper():
    # simulate what atexit would do if a step raised between open/close
    real_stdout = sys.stdout
    _r.ods_html_open("/tmp/test_ods_html_exception.html")
    assert sys.stdout is not real_stdout
    _r._ods_html_atexit_restore()
    assert sys.stdout is real_stdout
    assert _r._ODS_HTML_STATE["active"] is False
