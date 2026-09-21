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


def test_by_group_splits_report(capsys):
    src = """
    data old;
      input grp $ id x;
      datalines;
    a 1 10
    a 2 20
    b 1 30
    b 2 40
    ;
    run;

    data new;
      input grp $ id x;
      datalines;
    a 1 10
    a 2 99
    b 1 30
    b 2 40
    ;
    run;

    proc compare base=old compare=new;
      by grp;
      id id;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    a_section = out.split("--- grp=a ---", 1)[1].split("--- grp=b ---", 1)[0]
    b_section = out.split("--- grp=b ---", 1)[1]
    assert "id=2.0" in a_section and "20.0" in a_section and "99.0" in a_section
    assert "id=2.0" not in b_section
    assert "No unequal values" in b_section


def test_criterion_absorbs_small_differences():
    src = """
    data old;
      input id x;
      datalines;
    1 10.00005
    2 10.0
    ;
    run;

    data new;
      input id x;
      datalines;
    1 10.00010
    2 10.01
    ;
    run;

    proc compare base=old compare=new criterion=0.0001 out=diffs;
      id id;
    run;
    """
    ds = run_sas(src)
    diffs = ds["diffs"]
    assert list(diffs["_id_"]) == ["id=2.0"]


def test_criterion_still_flags_larger_differences(capsys):
    src = """
    data old; x = 10.0; run;
    data new; x = 10.01; run;

    proc compare base=old compare=new criterion=0.0001;
    run;
    """
    run_sas(src)
    out = capsys.readouterr().out
    assert "No unequal values" not in out
    assert "10.0" in out and "10.01" in out


def test_by_and_criterion_combined():
    src = """
    data old;
      input grp $ id x;
      datalines;
    a 1 10.0000
    a 2 20.0000
    b 1 30.0000
    ;
    run;

    data new;
      input grp $ id x;
      datalines;
    a 1 10.00005
    a 2 20.5000
    b 1 30.0000
    ;
    run;

    proc compare base=old compare=new criterion=0.001 out=diffs;
      by grp;
      id id;
    run;
    """
    ds = run_sas(src)
    diffs = ds["diffs"]
    assert len(diffs) == 1
    row = diffs.iloc[0]
    assert row["grp"] == "a"
    assert row["_id_"] == "id=2.0"
