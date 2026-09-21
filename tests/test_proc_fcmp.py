from sas_compiler import compile_source


def run_sas(source: str) -> dict:
    """Compile and execute SAS source, returning the _DS dict of datasets."""
    code = compile_source(source)
    g: dict = {}
    exec(compile(code, "<test>", "exec"), g)
    return g["_DS"]


def test_fcmp_numeric_function():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function double_it(x);
        return(x * 2);
      endsub;
    run;
    data out;
      y = double_it(5);
      z = double_it(3) + 1;
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["y"] == 10.0
    assert row["z"] == 7.0


def test_fcmp_character_function_with_if_else():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function classify(score) $;
        length result $10;
        if score >= 90 then result = "A";
        else if score >= 80 then result = "B";
        else result = "C";
        return(result);
      endsub;
    run;
    data src;
      input score;
      datalines;
    95
    85
    50
    ;
    run;
    data out;
      set src;
      grade = classify(score);
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["grade"].tolist() == ["A", "B", "C"]


def test_fcmp_function_called_from_data_step_with_set():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function double_it(x);
        return(x * 2);
      endsub;
    run;
    data src;
      input x;
      datalines;
    1
    2
    3
    ;
    run;
    data out;
      set src;
      y = double_it(x) + 1;
    run;
    """
    ds = run_sas(src)
    assert ds["out"]["y"].tolist() == [3.0, 5.0, 7.0]


def test_fcmp_multiple_functions_and_cmplib_option_is_ignored():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function add_one(x);
        return(x + 1);
      endsub;
      function square(x);
        return(x * x);
      endsub;
    run;
    options cmplib=work.funcs;
    data out;
      a = add_one(4);
      b = square(4);
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["a"] == 5.0
    assert row["b"] == 16.0


def test_fcmp_two_argument_function():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function add_two(x, y);
        return(x + y);
      endsub;
    run;
    data out;
      z = add_two(3, 4);
    run;
    """
    ds = run_sas(src)
    assert ds["out"].iloc[0]["z"] == 7.0
