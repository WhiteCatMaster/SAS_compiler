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


def test_fcmp_array_parameter_sum():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function arr_sum(vals[*], n);
        total = 0;
        do i = 1 to n;
          total = total + vals{i};
        end;
        return(total);
      endsub;
    run;
    data out;
      array nums{4} (10, 20, 30, 40);
      total = arr_sum(nums, 4);
    run;
    """
    ds = run_sas(src)
    assert ds["out"].iloc[0]["total"] == 100.0


def test_fcmp_array_parameter_find_max():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function arr_max(vals[*], n);
        m = vals{1};
        do i = 2 to n;
          if vals{i} > m then m = vals{i};
        end;
        return(m);
      endsub;
    run;
    data out;
      array nums{5} (3, 9, 2, 7, 1);
      biggest = arr_max(nums, 5);
    run;
    """
    ds = run_sas(src)
    assert ds["out"].iloc[0]["biggest"] == 9.0


def test_fcmp_array_parameter_dim():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function arr_len(vals[*]);
        return(dim(vals));
      endsub;
    run;
    data out;
      array nums{4} (1, 2, 3, 4);
      n = arr_len(nums);
    run;
    """
    ds = run_sas(src)
    assert ds["out"].iloc[0]["n"] == 4.0


def test_fcmp_array_parameter_is_pass_by_value():
    """Mutating the array parameter inside the function must not affect the
    caller's SAS array afterwards -- PROC FCMP array params are copied in,
    not passed by reference (documented scope cut)."""
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function zero_out(vals[*], n);
        do i = 1 to n;
          vals{i} = 0;
        end;
        return(1);
      endsub;
    run;
    data out;
      array nums{3} (1, 2, 3);
      ignore = zero_out(nums, 3);
      a = nums{1};
      b = nums{2};
      c = nums{3};
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert (row["a"], row["b"], row["c"]) == (1.0, 2.0, 3.0)


def test_fcmp_array_parameter_call_site_rejects_non_array():
    import pytest
    from sas_compiler import compile_source
    from sas_compiler.codegen import CodegenError

    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      function arr_sum(vals[*], n);
        return(vals{1});
      endsub;
    run;
    data out;
      x = 5;
      total = arr_sum(x, 1);
    run;
    """
    with pytest.raises(CodegenError):
        compile_source(src)


def test_fcmp_subroutine_outargs_updates_caller_variable():
    """A SUBROUTINE with OUTARGS is pass-by-reference for the declared
    parameter(s): CALLing it must write the computed value(s) back into
    the caller's own variable(s), named at the call site -- not the
    routine's internal parameter name."""
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      subroutine addone(x, y);
        outargs y;
        y = x + 1;
      endsub;
    run;
    data out;
      x = 5;
      call addone(x, result);
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["x"] == 5.0
    assert row["result"] == 6.0


def test_fcmp_subroutine_multiple_outargs():
    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      subroutine divmod(a, b, q, r);
        outargs q, r;
        q = int(a / b);
        r = mod(a, b);
      endsub;
    run;
    data out;
      call divmod(17, 5, quotient, remainder);
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["quotient"] == 3.0
    assert row["remainder"] == 2.0


def test_fcmp_plain_call_actually_executes():
    """CALLing a PROC FCMP routine (SUBROUTINE or FUNCTION, no OUTARGS) as
    a statement used to be a silent no-op (the generated code fell through
    to `pass # unsupported: call ...`). It must now actually run the
    routine's body -- observed here through a runtime side effect
    (CALL SYMPUTX) that a no-op could never produce."""
    from sas_compiler import runtime as r

    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      subroutine set_flag(v);
        call symputx("fcmp_call_stmt_flag", v);
      endsub;
    run;
    data _null_;
      call set_flag(42);
    run;
    """
    run_sas(src)
    assert r.MACRO_VARS.get("fcmp_call_stmt_flag") == "42"


def test_fcmp_outargs_subroutine_rejected_as_expression():
    import pytest
    from sas_compiler import compile_source
    from sas_compiler.codegen import CodegenError

    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      subroutine addone(x, y);
        outargs y;
        y = x + 1;
      endsub;
    run;
    data out;
      x = 5;
      z = addone(x, result);
    run;
    """
    with pytest.raises(CodegenError):
        compile_source(src)


def test_fcmp_outargs_call_site_rejects_non_bare_variable():
    import pytest
    from sas_compiler import compile_source
    from sas_compiler.codegen import CodegenError

    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      subroutine addone(x, y);
        outargs y;
        y = x + 1;
      endsub;
    run;
    data out;
      x = 5;
      call addone(x, x + 1);
    run;
    """
    with pytest.raises(CodegenError):
        compile_source(src)


def test_fcmp_outargs_name_must_match_declared_parameter():
    import pytest
    from sas_compiler.parser import ParseError, parse

    src = """
    proc fcmp outlib=work.funcs.myfuncs;
      subroutine addone(x, y);
        outargs z;
        y = x + 1;
      endsub;
    run;
    data out;
      x = 5;
      call addone(x, result);
    run;
    """
    with pytest.raises(ParseError):
        parse(src)
