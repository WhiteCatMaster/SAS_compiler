data grades;
  input name $ score;
  datalines;
Alice 95
Bob 72
Carol 58
Dave 88
;
run;

data lettered;
  set grades;
  length grade $ 1;
  select (score);
    when (95, 100) grade = "A";
    when (88) do;
      grade = "B";
    end;
    otherwise grade = "C";
  end;
run;

proc print data=lettered; run;

data bare;
  set grades;
  length band $ 4;
  select;
    when (score >= 90) band = "high";
    when (score >= 70) band = "mid";
    otherwise band = "low";
  end;
run;

proc print data=bare; run;
