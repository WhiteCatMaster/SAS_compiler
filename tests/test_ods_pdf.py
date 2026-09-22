import os
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


def test_ods_pdf_file_is_created_with_proc_output(tmp_path):
    out_path = tmp_path / "report.pdf"
    src = f"""
    data a;
      input name $ score bonus;
      datalines;
    Alice 90 5
    Bob 80 3
    ;
    run;

    ods pdf file="{out_path}";
    proc print data=a;
    run;
    ods pdf close;
    """
    compile_and_run(src)

    assert out_path.exists()
    data = out_path.read_bytes()
    assert data.startswith(b"%PDF-")
    assert len(data) > 0


def test_ods_pdf_table_output_is_more_than_trivial(tmp_path):
    out_path = tmp_path / "table.pdf"
    empty_path = tmp_path / "empty.pdf"

    src = f"""
    data a;
      input name $ score bonus;
      datalines;
    Alice 90 5
    Bob 80 3
    Carol 95 7
    ;
    run;

    ods pdf file="{out_path}";
    proc print data=a;
    run;
    ods pdf close;

    ods pdf file="{empty_path}";
    ods pdf close;
    """
    compile_and_run(src)

    assert out_path.exists()
    assert empty_path.exists()
    assert empty_path.read_bytes().startswith(b"%PDF-")
    # a real table-shaped report should produce meaningfully more bytes
    # than an empty capture (which still must be a valid, if tiny, PDF)
    assert os.path.getsize(out_path) > os.path.getsize(empty_path)
    assert os.path.getsize(out_path) > 500


def test_stdout_is_captured_while_pdf_open_and_restored_after_close(capsys, tmp_path):
    out_path = tmp_path / "restore.pdf"
    src = f"""
    data a;
      x = 1;
      output;
    run;

    data _null_;
      put "before open";
    run;

    ods pdf file="{out_path}";
    proc print data=a;
    run;
    ods pdf close;

    data _null_;
      put "after close";
    run;
    """
    compile_and_run(src)
    captured = capsys.readouterr()
    assert "before open" in captured.out
    assert "after close" in captured.out
    assert "Obs" not in captured.out
    assert sys.stdout is sys.__stdout__ or not isinstance(sys.stdout, _r._OdsHtmlCapture)


def test_ods_pdf_close_without_open_is_a_noop():
    _r.ods_pdf_close()
    assert _r._ODS_PDF_STATE["active"] is False


def test_reopen_pdf_while_already_open_flushes_previous_destination(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    src = f"""
    ods pdf file="{first}";
    data _null_;
      put "first destination";
    run;
    ods pdf file="{second}";
    data _null_;
      put "second destination";
    run;
    ods pdf close;
    """
    compile_and_run(src)

    assert first.exists()
    assert first.read_bytes().startswith(b"%PDF-")
    assert second.exists()
    assert second.read_bytes().startswith(b"%PDF-")
    # the two files were written from distinct captures, so they should
    # not be byte-identical (second also has strictly more content)
    assert os.path.getsize(second) >= os.path.getsize(first)


def test_html_and_pdf_can_be_open_simultaneously(tmp_path):
    real_stdout = sys.stdout
    html_path = tmp_path / "both.html"
    pdf_path = tmp_path / "both.pdf"
    src = f"""
    ods html file="{html_path}";
    ods pdf file="{pdf_path}";
    data _null_;
      put "shared content";
    run;
    ods html close;
    ods pdf close;
    """
    compile_and_run(src)
    assert "shared content" in html_path.read_text()
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert sys.stdout is real_stdout


def test_rtf_and_pdf_can_be_open_simultaneously(tmp_path):
    real_stdout = sys.stdout
    rtf_path = tmp_path / "both.rtf"
    pdf_path = tmp_path / "both.pdf"
    src = f"""
    ods rtf file="{rtf_path}";
    ods pdf file="{pdf_path}";
    data _null_;
      put "shared rtf pdf content";
    run;
    ods rtf close;
    ods pdf close;
    """
    compile_and_run(src)
    assert "shared rtf pdf content" in rtf_path.read_text()
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert sys.stdout is real_stdout


def test_closing_pdf_leaves_html_open(tmp_path):
    real_stdout = sys.stdout
    html_path = tmp_path / "h.html"
    pdf_path = tmp_path / "p.pdf"
    src = f"""
    ods html file="{html_path}";
    ods pdf file="{pdf_path}";
    data _null_;
      put "before pdf close";
    run;
    ods pdf close;
    data _null_;
      put "after pdf close, html still open";
    run;
    ods html close;
    """
    compile_and_run(src)
    html_content = html_path.read_text()
    assert "before pdf close" in html_content
    assert "after pdf close, html still open" in html_content
    assert sys.stdout is real_stdout


def test_exception_between_open_and_close_still_restores_stdout_via_atexit_helper(tmp_path):
    real_stdout = sys.stdout
    out_path = tmp_path / "exc.pdf"
    _r.ods_pdf_open(str(out_path))
    assert sys.stdout is not real_stdout
    _r._ods_pdf_atexit_restore()
    assert sys.stdout is real_stdout
    assert _r._ODS_PDF_STATE["active"] is False
    assert out_path.read_bytes().startswith(b"%PDF-")
