"""Tests for PROC HPSPLIT (decision tree classification via scikit-learn's
DecisionTreeClassifier)."""
import pytest

from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse
from sas_compiler.codegen import generate, CodegenError
from sas_compiler import runtime as _r


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


def _two_class_src(extra_proc_opts: str = "", extra_stmts: str = "") -> str:
    # Two well-separated clusters in (x, y) space: grp A near the
    # origin, grp B far away -- a decision tree should resubstitute
    # near-perfectly.
    return f"""
    data pts;
      input x y grp $;
      datalines;
    1 2 A
    2 1 A
    1 1 A
    2 2 A
    1.5 1.5 A
    0.5 1 A
    2.5 2 A
    1 0.5 A
    20 21 B
    21 20 B
    20 20 B
    21 21 B
    20.5 20.5 B
    19.5 20 B
    21.5 21 B
    20 19.5 B
    ;
    run;
    proc hpsplit data=pts out=scored {extra_proc_opts};
      class grp;
      model grp = x y;
      {extra_stmts}
    run;
    """


def test_hpsplit_two_class_high_resubstitution_accuracy_and_out_columns():
    src = _two_class_src()
    ds = run_sas(src)
    df = ds["scored"]
    assert len(df) == 16
    assert "_INTO_" in df.columns

    # Well-separated classes -> resubstitution predictions should match
    # the true class for (almost) every row.
    accuracy = (df["_INTO_"] == df["grp"]).mean()
    assert accuracy > 0.9

    # Predicted classes should only ever be one of the observed levels.
    assert set(df["_INTO_"].unique()) <= {"A", "B"}


def test_hpsplit_output_out_form():
    src = """
    data pts;
      input x y grp $;
      datalines;
    1 2 A
    2 1 A
    1.5 1.5 A
    20 21 B
    21 20 B
    20.5 20.5 B
    ;
    run;
    proc hpsplit data=pts;
      class grp;
      model grp = x y;
      output out=scored2;
    run;
    """
    ds = run_sas(src)
    df = ds["scored2"]
    assert len(df) == 6
    assert "_INTO_" in df.columns
    assert set(df["_INTO_"].unique()) <= {"A", "B"}


def test_hpsplit_maxdepth_constrains_tree_depth():
    # A shallow MAXDEPTH=1 stump can't perfectly separate 3 well-clustered
    # classes arranged so a single split can't isolate all three -- so a
    # depth-1 tree should do meaningfully worse than an unconstrained one.
    src = """
    data pts3;
      input x y grp $;
      datalines;
    1 1 A
    1 2 A
    2 1 A
    1.5 1.5 A
    2 2 A
    20 1 B
    21 2 B
    20 2 B
    21 1 B
    20.5 1.5 B
    10 20 C
    11 21 C
    10 21 C
    11 20 C
    10.5 20.5 C
    ;
    run;
    proc hpsplit data=pts3 maxdepth=1 out=shallow;
      class grp;
      model grp = x y;
    run;
    proc hpsplit data=pts3 out=deep;
      class grp;
      model grp = x y;
    run;
    """
    ds = run_sas(src)
    shallow_acc = (ds["shallow"]["_INTO_"] == ds["shallow"]["grp"]).mean()
    deep_acc = (ds["deep"]["_INTO_"] == ds["deep"]["grp"]).mean()
    assert deep_acc > 0.9
    assert shallow_acc < deep_acc


def test_hpsplit_requires_class_statement():
    src = """
    data pts;
      input x y grp $;
      datalines;
    1 2 A
    2 1 B
    ;
    run;
    proc hpsplit data=pts out=scored;
      model grp = x y;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_hpsplit_requires_model_statement():
    src = """
    data pts;
      input x y grp $;
      datalines;
    1 2 A
    2 1 B
    ;
    run;
    proc hpsplit data=pts out=scored;
      class grp;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_hpsplit_model_response_must_match_class_variable():
    src = """
    data pts;
      input x y grp $ other $;
      datalines;
    1 2 A A1
    2 1 B B1
    ;
    run;
    proc hpsplit data=pts out=scored;
      class grp;
      model other = x y;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_hpsplit_rejects_multiple_class_variables():
    src = """
    data pts;
      input x y grp $ grp2 $;
      datalines;
    1 2 A A1
    2 1 B B1
    ;
    run;
    proc hpsplit data=pts out=scored;
      class grp grp2;
      model grp = x y;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_hpsplit_single_class_level_raises_clear_error():
    src = """
    data pts;
      input x y grp $;
      datalines;
    1 2 A
    2 1 A
    1.5 1.5 A
    2.5 2 A
    ;
    run;
    proc hpsplit data=pts out=scored;
      class grp;
      model grp = x y;
    run;
    """
    with pytest.raises(RuntimeError):
        run_sas(src)
