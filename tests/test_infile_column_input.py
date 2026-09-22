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


def test_infile_plain_list_input_still_works(tmp_path):
    """Backward-compat: plain list input (no pointer/column controls) must
    keep taking the existing _r.read_infile code path."""
    p = tmp_path / "list.txt"
    p.write_text("1 abc\n2 def\n")
    src = f"""
    data out;
      infile "{p}";
      input a b $;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    assert list(df["a"]) == [1.0, 2.0]
    assert list(df["b"]) == ["abc", "def"]


def test_infile_at_absolute_pointer(tmp_path):
    p = tmp_path / "abs.txt"
    # columns:  123456789012
    p.write_text("John      25\nJane      31\n")
    src = f"""
    data out;
      infile "{p}";
      input @1 name $ 10. @11 age 2.;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    assert list(df["name"]) == ["John", "Jane"]
    assert list(df["age"]) == [25.0, 31.0]


def test_infile_relative_pointer(tmp_path):
    p = tmp_path / "rel.txt"
    p.write_text("AB12CD\nEF34GH\n")
    src = f"""
    data out;
      infile "{p}";
      input x $2. +2 y $2.;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    # x reads cols 1-2, +2 skips "12"/"34", y reads cols 5-6
    assert list(df["x"]) == ["AB", "EF"]
    assert list(df["y"]) == ["CD", "GH"]


def test_infile_line_hold_multiline_record(tmp_path):
    p = tmp_path / "multi.txt"
    p.write_text("Alice\n030\nBob\n045\n")
    src = f"""
    data out;
      infile "{p}";
      input name $ 5. / age 3.;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    assert list(df["name"]) == ["Alice", "Bob"]
    assert list(df["age"]) == [30.0, 45.0]


def test_infile_column_range_numeric_and_char(tmp_path):
    p = tmp_path / "range.txt"
    # cols:      1234567890
    p.write_text("Smith  042\nJones  017\n")
    src = f"""
    data out;
      infile "{p}";
      input name $ 1-7 age 8-10;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    assert list(df["name"]) == ["Smith", "Jones"]
    assert list(df["age"]) == [42.0, 17.0]


def test_infile_width_informat_with_decimals(tmp_path):
    p = tmp_path / "dec.txt"
    p.write_text("012345\n067890\n")
    src = f"""
    data out;
      infile "{p}";
      input amt 6.2;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    # No literal '.' in the raw text -> implied 2 decimals scaling.
    assert list(df["amt"]) == [123.45, 678.90]
