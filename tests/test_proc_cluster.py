import pytest

from sas_compiler.macro import MacroProcessor
from sas_compiler.parser import parse
from sas_compiler.codegen import generate, CodegenError


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    code = generate(prog)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


def test_cluster_two_obvious_groups_shows_small_then_large_distance(capsys):
    src = """
    data points;
      input x y;
      datalines;
    1 1
    1.2 0.9
    0.9 1.1
    10 10
    10.2 9.8
    9.9 10.1
    ;
    run;
    proc cluster data=points method=average;
      var x y;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    assert "The CLUSTER Procedure" in out
    assert "Clustering Method: AVERAGE" in out
    assert "Cluster History" in out

    # Parse the Distance column out of each Cluster History data row.
    lines = [l for l in out.splitlines() if l.strip() and l.strip()[0].isdigit()]
    distances = [float(l.split()[-1]) for l in lines]
    assert len(distances) == 5  # N=6 observations -> 5 merges
    # Within-group merges happen early and are small; the final merge
    # (joining the two well-separated groups) is much larger.
    assert distances[-1] > distances[0] * 5


def test_cluster_id_statement_uses_id_values_as_labels(capsys):
    src = """
    data points;
      input name $ x y;
      datalines;
    alpha 1 1
    bravo 1.1 0.9
    charlie 10 10
    delta 10.1 9.9
    ;
    run;
    proc cluster data=points method=average;
      var x y;
      id name;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    for name in ("alpha", "bravo", "charlie", "delta"):
        assert name in out
    # Bare row numbers should not stand in for the ID values.
    assert "1 + 2" not in out


def test_cluster_method_ward_runs_and_reports(capsys):
    src = """
    data points;
      input x y;
      datalines;
    1 1
    1.1 0.9
    9 9
    9.1 8.9
    ;
    run;
    proc cluster data=points method=ward;
      var x y;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out

    assert "Clustering Method: WARD" in out
    assert "Cluster History" in out
    lines = [l for l in out.splitlines() if l.strip() and l.strip()[0].isdigit()]
    assert len(lines) == 3  # N=4 -> 3 merges


def test_cluster_unrecognized_method_raises(capsys):
    src = """
    data points;
      input x y;
      datalines;
    1 1
    2 2
    ;
    run;
    proc cluster data=points method=bogus;
      var x y;
    run;
    """
    with pytest.raises(CodegenError, match="unrecognized METHOD"):
        run_sas(src)


def test_cluster_requires_var_statement(capsys):
    src = """
    data points;
      input x y;
      datalines;
    1 1
    2 2
    ;
    run;
    proc cluster data=points;
    run;
    """
    with pytest.raises(CodegenError, match="VAR statement"):
        run_sas(src)
