"""Tests for CALL EXECUTE: queues raw SAS source at DATA step runtime,
which is compiled and run immediately after the current step finishes
(before the next step), sharing the same _DS/_FMT/_LBL WORK library as
the rest of the program -- matching real SAS's CALL EXECUTE semantics."""
import sas_compiler.runtime as sr
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


def setup_function(_fn):
    # The execute queue is process-global runtime state; make sure a
    # failed/aborted previous test never leaks into the next one.
    sr._EXECUTE_QUEUE.clear()


def test_call_execute_creates_dataset():
    src = """
    data _null_;
      call execute('data work.gen; x = 42; run;');
    run;
    """
    ds = run_sas(src)
    assert "gen" in ds
    assert ds["gen"]["x"].iloc[0] == 42.0


def test_call_execute_runs_after_current_step_before_next():
    # The queued snippet must be visible to a PROC/DATA step that comes
    # right after the DATA _NULL_ that queued it in the ORIGINAL program,
    # even though that snippet was never part of the ahead-of-time compile.
    src = """
    data _null_;
      call execute('data work.later; y = 7; run;');
    run;

    data combined;
      set work.later;
      z = y + 1;
    run;
    """
    ds = run_sas(src)
    assert "later" in ds
    assert "combined" in ds
    assert ds["combined"]["z"].iloc[0] == 8.0


def test_call_execute_multiple_rows_each_queue_own_snippet():
    src = """
    data driver;
      input id name $;
      datalines;
    1 alpha
    2 beta
    3 gamma
    ;
    run;

    data _null_;
      set driver;
      call execute(cats('data work.', name, '; id2=', id, '; val=', id*10, '; run;'));
    run;

    data combined;
      set work.alpha work.beta work.gamma;
    run;
    """
    ds = run_sas(src)
    for name in ("alpha", "beta", "gamma"):
        assert name in ds
    combined = ds["combined"].sort_values("id2").reset_index(drop=True)
    assert list(combined["id2"]) == [1.0, 2.0, 3.0]
    assert list(combined["val"]) == [10.0, 20.0, 30.0]


def test_call_execute_queued_code_sees_earlier_datasets():
    # Queued CALL EXECUTE code shares the SAME WORK library, so it can
    # read a dataset that already exists from earlier in the program.
    src = """
    data base;
      total = 100;
    run;

    data _null_;
      call execute('data work.derived; set work.base; total2 = total * 2; run;');
    run;
    """
    ds = run_sas(src)
    assert "derived" in ds
    assert ds["derived"]["total2"].iloc[0] == 200.0


def test_call_execute_queue_drained_and_empty_afterward():
    src = """
    data _null_;
      call execute('data work.gen2; a = 1; run;');
    run;
    """
    run_sas(src)
    assert sr._EXECUTE_QUEUE == []
