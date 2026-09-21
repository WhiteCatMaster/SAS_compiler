"""Extra macro-language coverage: nested control flow stress tests and
additional macro functions (%QSCAN/%QSUBSTR/%QUPCASE, expanded %SYSFUNC)."""
import datetime

from sas_compiler.macro import MacroProcessor


# ---------------- nested control flow stress tests ----------------
def test_do_loop_nested_inside_if_then_else():
    src = """
    %let flag = 1;
    %if &flag = 1 %then %do;
      %do i = 1 %to 3;
        [T&i]
      %end;
    %end;
    %else %do;
      %do i = 1 %to 3;
        [F&i]
      %end;
    %end;
    """
    out = MacroProcessor().expand(src)
    assert out.split() == ["[T1]", "[T2]", "[T3]"]


def test_do_loop_nested_inside_else_branch():
    src = """
    %let flag = 0;
    %if &flag = 1 %then %do;
      %do i = 1 %to 3;
        [T&i]
      %end;
    %end;
    %else %do;
      %do i = 1 %to 2;
        [F&i]
      %end;
    %end;
    """
    out = MacroProcessor().expand(src)
    assert out.split() == ["[F1]", "[F2]"]


def test_do_while_with_compound_and_condition():
    src = """
    %let x = 0;
    %let y = 10;
    %do %while(&x < 5 and &y > 0);
      [&x]
      %let x = %eval(&x + 1);
      %let y = %eval(&y - 2);
    %end;
    """
    out = MacroProcessor().expand(src)
    # x goes 0,1,2,3,4 then loop stops because x=5 fails x<5 (y would also
    # eventually stop it, but x<5 is the binding constraint here)
    assert out.split() == ["[0]", "[1]", "[2]", "[3]", "[4]"]


def test_do_until_with_compound_or_condition():
    src = """
    %let x = 0;
    %let done = 0;
    %do %until(&x >= 3 or &done = 1);
      [&x]
      %let x = %eval(&x + 1);
    %end;
    """
    out = MacroProcessor().expand(src)
    # until-loop executes body at least once, checks after: x becomes
    # 1,2,3 -- loop exits once x>=3 evaluates true after appending [2]->x=3
    assert out.split() == ["[0]", "[1]", "[2]"]


def test_deeply_nested_macro_calls():
    src = """
    %macro inner(x);
    (&x*2)
    %mend inner;

    %macro middle(x);
    [%inner(&x)]
    %mend middle;

    %macro outer(x);
    {%middle(&x)}
    %mend outer;

    %outer(5)
    """
    out = MacroProcessor().expand(src)
    assert "".join(out.split()) == "{[(5*2)]}"


def test_if_condition_from_eval_result():
    src = """
    %let a = 3;
    %let b = 4;
    %if %eval(&a + &b) = 7 %then %put SEVEN;
    %else %put NOT_SEVEN;
    """
    MacroProcessor().expand(src)  # %put writes to stdout; just must not raise


def test_if_condition_from_eval_result_captures_branch(capsys):
    src = """
    %let a = 3;
    %let b = 4;
    %if %eval(&a + &b) = 7 %then %put SEVEN;
    %else %put NOT_SEVEN;
    """
    MacroProcessor().expand(src)
    out = capsys.readouterr().out
    assert "SEVEN" in out
    assert "NOT_SEVEN" not in out


def test_if_condition_from_sysfunc_result():
    src = """
    %if %sysfunc(year(0)) = 1960 %then %put YEAR_OK;
    %else %put YEAR_BAD;
    """
    import sys
    from io import StringIO
    old = sys.stdout
    sys.stdout = buf = StringIO()
    try:
        MacroProcessor().expand(src)
    finally:
        sys.stdout = old
    assert "YEAR_OK" in buf.getvalue()


def test_nested_if_inside_do_loop():
    src = """
    %do i = 1 %to 4;
      %if %eval(&i / 2 * 2) = &i %then %do;
        [even &i]
      %end;
      %else %do;
        [odd &i]
      %end;
    %end;
    """
    out = MacroProcessor().expand(src)
    assert out.split() == ["[odd", "1]", "[even", "2]", "[odd", "3]", "[even", "4]"]


# ---------------- %QSCAN / %QSUBSTR / %QUPCASE ----------------
def test_qscan_basic():
    out = MacroProcessor().expand("%qscan(a-b-c,2,-)")
    assert out.strip() == "b"


def test_qsubstr_basic():
    out = MacroProcessor().expand("%qsubstr(hello,2,3)")
    assert out.strip() == "ell"


def test_qupcase_basic():
    out = MacroProcessor().expand("%qupcase(abc)")
    assert out.strip() == "ABC"


def test_qscan_matches_scan_for_same_input():
    mp1 = MacroProcessor()
    mp2 = MacroProcessor()
    a = mp1.expand("%scan(one two three,2)")
    b = mp2.expand("%qscan(one two three,2)")
    assert a == b == " two"[1:] or a.strip() == b.strip() == "two"


# ---------------- expanded %SYSFUNC allowlist ----------------
def test_sysfunc_mdy():
    out = MacroProcessor().expand("%sysfunc(mdy(1,1,1960))")
    assert out.strip() == "0"


def test_sysfunc_mdy_nonzero():
    out = MacroProcessor().expand("%sysfunc(mdy(3,15,1960))")
    expected = (datetime.date(1960, 3, 15) - datetime.date(1960, 1, 1)).days
    assert out.strip() == str(expected)


def test_sysfunc_year_month_day():
    sasdate = (datetime.date(2024, 6, 15) - datetime.date(1960, 1, 1)).days
    mp = MacroProcessor()
    assert mp.expand(f"%sysfunc(year({sasdate}))").strip() == "2024"
    mp = MacroProcessor()
    assert mp.expand(f"%sysfunc(month({sasdate}))").strip() == "6"
    mp = MacroProcessor()
    assert mp.expand(f"%sysfunc(day({sasdate}))").strip() == "15"


def test_sysfunc_intck_day_month_year():
    d1 = (datetime.date(2024, 1, 1) - datetime.date(1960, 1, 1)).days
    d2 = (datetime.date(2024, 3, 1) - datetime.date(1960, 1, 1)).days
    mp = MacroProcessor()
    out = mp.expand(f"%sysfunc(intck(month,{d1},{d2}))")
    assert out.strip() == "2"


def test_sysfunc_intnx_month_advances_date():
    d1 = (datetime.date(2024, 1, 15) - datetime.date(1960, 1, 1)).days
    mp = MacroProcessor()
    out = mp.expand(f"%sysfunc(intnx(month,{d1},1))")
    result_days = int(out.strip())
    result_date = datetime.date(1960, 1, 1) + datetime.timedelta(days=result_days)
    assert result_date.year == 2024
    assert result_date.month == 2


def test_sysfunc_putn_comma_format():
    out = MacroProcessor().expand("%sysfunc(putn(1234.5,comma8.1))")
    assert "1,234.5" in out.strip()


def test_sysfunc_inputn_basic():
    out = MacroProcessor().expand("%sysfunc(inputn(42.5,8.))")
    assert out.strip() == "42.5"


def test_macro_functions_composed_with_sysfunc_in_condition():
    src = """
    %let start = 0;
    %let stop  = %sysfunc(mdy(1,1,1961));
    %if %sysfunc(intck(year,&start,&stop)) = 1 %then %put ONE_YEAR;
    %else %put NOT_ONE_YEAR;
    """
    import sys
    from io import StringIO
    old = sys.stdout
    sys.stdout = buf = StringIO()
    try:
        MacroProcessor().expand(src)
    finally:
        sys.stdout = old
    assert "ONE_YEAR" in buf.getvalue()
