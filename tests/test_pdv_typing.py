"""Regression tests for dtype-based PDV character defaulting.

Background: a DATA step variable's default value (before it's ever
explicitly assigned) is decided by a static heuristic in codegen.py
(LENGTH/ARRAY $ declarations, string-valued RETAIN, or a string-literal/
char-function assignment found anywhere in the step). That heuristic
misses the most common real case: a variable that comes from a SET/
MERGE/UPDATE source dataset is genuinely character (the actual pandas
column dtype says so), but the compiler has no *static* way to know
that.

Plain SET can't actually expose this gap: it refreshes every source
column from the current row on every iteration, so a SET-sourced
column always holds real string data by the time anything reads it.
The gap only shows up with MERGE (or UPDATE), where a source dataset
can be *absent* for a given BY-group -- if that happens on the very
first group processed, the variable has never been assigned by name
and never been refreshed from that source either, so before the fix
it fell through the plain `pdv.get(name, MISSING)` default and came
back NaN instead of blank.
"""
import math

from sas_compiler import compile_source


def run_sas(source: str) -> dict:
    code = compile_source(source)
    g = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


def test_merge_char_column_defaults_to_blank_when_absent_in_first_group():
    # dataset `a` (the source of the char column `name`) has no row for
    # id=1, which is processed *before* id=2 (the only id `a` has) -- so
    # `name` is never assigned by name and never refreshed from `a` for
    # that first iteration.
    src = """
    data a;
      input id name $;
      datalines;
    2 Bob
    ;
    run;
    data b;
      input id score;
      datalines;
    1 70
    2 85
    ;
    run;
    data out;
      merge a b;
      by id;
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("id")
    assert df.loc[1.0, "name"] == ""
    assert df.loc[2.0, "name"] == "Bob"
    assert df.loc[1.0, "score"] == 70.0


def test_length_declared_numeric_wins_over_source_dtype():
    # `code` is an object-dtype (character) column in dataset `a`, but
    # this DATA step explicitly declares it numeric via LENGTH -- that
    # static declaration must win over the dtype-based guess, so the
    # id=1 row (where `a` has no matching record) should stay
    # numeric-missing (NaN), not get seeded blank.
    src = """
    data a;
      input id code $;
      datalines;
    2 X
    ;
    run;
    data b;
      input id score;
      datalines;
    1 70
    2 85
    ;
    run;
    data out;
      length code 8;
      merge a b;
      by id;
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("id")
    assert math.isnan(df.loc[1.0, "code"])
    assert df.loc[2.0, "code"] == "X"


def test_update_char_column_defaults_to_blank_when_absent_in_first_group():
    # Same gap, but via UPDATE (master/transaction) instead of MERGE.
    src = """
    data master;
      input id label $;
      datalines;
    2 Existing
    ;
    run;
    data trans;
      input id amount;
      datalines;
    1 5
    2 10
    ;
    run;
    data out;
      update master trans;
      by id;
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("id")
    assert df.loc[1.0, "label"] == ""
    assert df.loc[2.0, "label"] == "Existing"


def test_existing_static_string_literal_heuristic_still_works():
    # Pre-existing behavior (no SET/MERGE involved at all): a variable
    # only ever assigned a string literal in one branch, read in a
    # DATA step with no input dataset, should still default to blank
    # rather than NaN on the branch that skips the assignment.
    src = """
    data out;
      x = 1;
      if x > 100 then note = "big";
      flag = (note = "");
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["note"] == ""
    assert row["flag"] == 1.0
