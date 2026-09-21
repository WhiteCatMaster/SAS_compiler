"""Tests for SET ... KEY= keyed lookups and HASH .OUTPUT()."""
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


def test_set_key_matches_and_populates_row():
    src = """
    data customers;
      input custid name $ city $;
      datalines;
    1 Alice NYC
    2 Bob LA
    3 Carol SF
    ;
    run;
    data orders;
      input custid amount;
      datalines;
    1 100
    2 250
    ;
    run;
    data joined;
      set orders;
      set customers key=custid_idx;
      if _iorc_ ne 0 then do;
        matched = 0;
        name = "UNKNOWN";
      end;
      else matched = 1;
    run;
    """
    ds = run_sas(src)
    df = ds["joined"].set_index("custid")
    assert df.loc[1.0, "name"] == "Alice"
    assert df.loc[1.0, "city"] == "NYC"
    assert df.loc[1.0, "matched"] == 1.0
    assert df.loc[2.0, "name"] == "Bob"
    assert df.loc[2.0, "matched"] == 1.0


def test_set_key_miss_sets_iorc_and_leaves_stale_values():
    src = """
    data customers;
      input custid name $ city $;
      datalines;
    1 Alice NYC
    2 Bob LA
    ;
    run;
    data orders;
      input custid amount;
      datalines;
    1 100
    4 999
    ;
    run;
    data joined;
      set orders;
      set customers key=custid_idx;
      if _iorc_ ne 0 then do;
        matched = 0;
        name = "UNKNOWN";
      end;
      else matched = 1;
    run;
    """
    ds = run_sas(src)
    df = ds["joined"].set_index("custid")
    # id=1 matches
    assert df.loc[1.0, "matched"] == 1.0
    assert df.loc[1.0, "name"] == "Alice"
    # id=4 doesn't match: matched=0, name explicitly overwritten to UNKNOWN,
    # but "city" (never touched by the ELSE branch) keeps its stale prior value
    assert df.loc[4.0, "matched"] == 0.0
    assert df.loc[4.0, "name"] == "UNKNOWN"
    assert df.loc[4.0, "city"] == "NYC"  # stale from the id=1 row, not overwritten
    # _iorc_ and _n_ are automatic vars, not written to the output dataset
    assert "_iorc_" not in ds["joined"].columns
    assert "_n_" not in ds["joined"].columns


def test_hash_output_from_add_calls():
    src = """
    data _null_;
      if _n_ = 1 then do;
        declare hash h();
        h.definekey("id");
        h.definedata("id", "val");
      end;
      id = 1; val = "a"; h.add();
      id = 2; val = "b"; h.add();
      id = 3; val = "c"; h.add();
      if _n_ = 1 then h.output(dataset: "dumped");
    run;
    """
    ds = run_sas(src)
    df = ds["dumped"].set_index("id")
    assert df.loc[1.0, "val"] == "a"
    assert df.loc[2.0, "val"] == "b"
    assert df.loc[3.0, "val"] == "c"
    assert len(df) == 3


def test_hash_output_from_source_dataset():
    src = """
    data lookup;
      input id val $;
      datalines;
    10 x
    20 y
    ;
    run;
    data _null_;
      if _n_ = 1 then do;
        declare hash h(dataset: "lookup");
        h.definekey("id");
        h.definedata("val");
        h.definedone();
        h.output(dataset: "roundtrip");
      end;
    run;
    """
    ds = run_sas(src)
    df = ds["roundtrip"].set_index("id")
    assert df.loc[10.0, "val"] == "x"
    assert df.loc[20.0, "val"] == "y"


def test_hash_output_requires_dataset_arg():
    import pytest
    from sas_compiler.codegen import CodegenError

    src = """
    data _null_;
      if _n_ = 1 then do;
        declare hash h();
        h.definekey("id");
        h.definedata("id");
      end;
      id = 1; h.add();
      h.output();
    run;
    """
    with pytest.raises(CodegenError, match="dataset"):
        run_sas(src)
