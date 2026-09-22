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


def test_infile_line_pointer_jump_skips_middle_line(tmp_path):
    """#n jumps directly to the nth physical line of the current record,
    skipping (for reading purposes) any lines in between -- but those
    skipped lines are still consumed as part of the record, so the next
    record starts on the first genuinely unconsumed line."""
    p = tmp_path / "lineptr.txt"
    # Record 1: lines 1-3 ("Alice", "SKIP1", "12345")
    # Record 2: lines 4-6 ("Bob  ", "SKIP2", "67890")
    p.write_text("Alice\nSKIP1\n12345\nBob  \nSKIP2\n67890\n")
    src = f"""
    data out;
      infile "{p}";
      input #1 a $ 1-5 #3 c 1-5;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    assert list(df["a"]) == ["Alice", "Bob"]
    assert list(df["c"]) == [12345.0, 67890.0]


def test_infile_line_pointer_leaves_column_pointer_unchanged(tmp_path):
    """Design choice: a bare `#n` does NOT reset the column pointer (`col`)
    -- it stays wherever the previous item left it, matching real SAS
    (only `/` and a fresh INPUT statement reset the column pointer to 1).
    This pins that choice down: `a 5.` leaves col at 5 (0-indexed), then
    `#3` jumps only the *line* pointer to the record's 3rd line, and the
    following `b 2.` (an implicit width continuation, no explicit @/range)
    must therefore read columns 6-7 of that line, not 1-2. If `#n` instead
    reset col to 0, `b` would read "AA" (non-numeric -> MISSING) rather
    than the "67" that sits at columns 6-7."""
    p = tmp_path / "colptr.txt"
    # Record: line1 "12345", line2 "SKIP2" (unread), line3 "AAAAA67"
    p.write_text("12345\nSKIP2\nAAAAA67\n")
    src = f"""
    data out;
      infile "{p}";
      input a 5. #3 b 2.;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    assert list(df["a"]) == [12345.0]
    assert list(df["b"]) == [67.0]


def test_infile_line_pointer_combined_with_newline(tmp_path):
    """#n combined with `/` in the same INPUT statement: `/` advances one
    line at a time (consumed as part of the record), and a later `#n` can
    still jump to an absolute line of the same record measured from its
    first physical line, including a line already passed by `/`."""
    p = tmp_path / "combo.txt"
    # Single record spanning 2 physical lines: line1 "Head1", line2 "Mid12".
    # `/` advances to line2 (the highest line touched), so the record
    # consumes both lines and there is nothing left for a second record.
    p.write_text("Head1\nMid12\n")
    src = f"""
    data out;
      infile "{p}";
      input a $ 1-5 / b $ 1-5 #1 c $ 1-5;
    run;
    """
    ds = run_sas(src)
    df = ds["out"]
    # a reads line 1; `/` advances to line 2 and reads b; #1 jumps back to
    # line 1 (the record's 1st line) and reads c from there.
    assert list(df["a"]) == ["Head1"]
    assert list(df["b"]) == ["Mid12"]
    assert list(df["c"]) == ["Head1"]


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
