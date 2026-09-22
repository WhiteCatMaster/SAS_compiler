"""Tests for PROC DISCRIM (linear discriminant analysis / classification
via scikit-learn's LinearDiscriminantAnalysis)."""
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


def _two_class_src(extra_stmts: str = "") -> str:
    # Two well-separated clusters in (x, y) space: grp A near the
    # origin, grp B far away -- LDA should resubstitute near-perfectly.
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
    proc discrim data=pts out=scored;
      class grp;
      var x y;
      {extra_stmts}
    run;
    """


def test_discrim_two_class_high_resubstitution_accuracy_and_out_columns():
    src = _two_class_src()
    ds = run_sas(src)
    df = ds["scored"]
    assert len(df) == 16
    assert "_INTO_" in df.columns
    assert "prob_A" in df.columns
    assert "prob_B" in df.columns

    # Well-separated classes -> resubstitution predictions should match
    # the true class for (almost) every row.
    accuracy = (df["_INTO_"] == df["grp"]).mean()
    assert accuracy > 0.9

    # Posterior probabilities should sum to ~1 per row.
    prob_sums = df["prob_A"] + df["prob_B"]
    assert (prob_sums.round(6) == 1.0).all()


def test_discrim_three_class():
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
    proc discrim data=pts3 out=scored3;
      class grp;
      var x y;
    run;
    """
    ds = run_sas(src)
    df = ds["scored3"]
    assert len(df) == 15
    for level in ("A", "B", "C"):
        assert f"prob_{level}" in df.columns
    prob_sum = df["prob_A"] + df["prob_B"] + df["prob_C"]
    assert (prob_sum.round(6) == 1.0).all()
    accuracy = (df["_INTO_"] == df["grp"]).mean()
    assert accuracy > 0.9


def test_discrim_requires_class_statement():
    src = """
    data pts;
      input x y;
      datalines;
    1 2
    2 1
    ;
    run;
    proc discrim data=pts out=scored;
      var x y;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_discrim_requires_var_statement():
    src = """
    data pts;
      input x y grp $;
      datalines;
    1 2 A
    2 1 B
    ;
    run;
    proc discrim data=pts out=scored;
      class grp;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_discrim_rejects_multiple_class_variables():
    src = """
    data pts;
      input x y grp $ grp2 $;
      datalines;
    1 2 A A1
    2 1 B B1
    ;
    run;
    proc discrim data=pts out=scored;
      class grp grp2;
      var x y;
    run;
    """
    with pytest.raises(CodegenError):
        run_sas(src)


def test_discrim_single_class_level_raises_clear_error():
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
    proc discrim data=pts out=scored;
      class grp;
      var x y;
    run;
    """
    with pytest.raises(RuntimeError):
        run_sas(src)
