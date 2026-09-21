proc fcmp outlib=work.funcs.myfuncs;
  function double_it(x);
    return(x * 2);
  endsub;

  function classify(score) $;
    length result $10;
    if score >= 90 then result = "A";
    else if score >= 80 then result = "B";
    else result = "C";
    return(result);
  endsub;
run;

data students;
  input name $ score;
  datalines;
Alice 95
Bob 85
Carol 50
;
run;

data out;
  set students;
  bonus_points = double_it(5);
  grade = classify(score);
run;

proc print data=out; run;
