%macro make_scores;
data scores;
  input student $ subject $ score;
  datalines;
Alice Math 90
Alice Sci 80
Bob Math 70
Bob Sci 60
Bob Hist 50
Carol Math 100
;
run;
%mend make_scores;
%make_scores;

proc sort data=scores out=scores_sorted;
  by student;
run;

data totals;
  set scores_sorted;
  by student;
  retain total 0;
  retain n 0;
  if first.student then do;
    total = 0;
    n = 0;
  end;
  total = total + score;
  n + 1;
  if last.student then do;
    avg = total / n;
    output;
  end;
  keep student total n avg;
run;

proc print data=totals;
run;

data arraydemo;
  array vals{5} v1-v5;
  do i = 1 to 5;
    vals{i} = i * i;
  end;
  total = 0;
  do i = 1 to 5;
    total + vals{i};
  end;
  drop i;
run;

proc print data=arraydemo;
run;

%let threshold = 75;
data flagged;
  set scores;
  if score >= &threshold then flag = "PASS";
  else flag = "FAIL";
run;

proc print data=flagged;
run;

proc sql;
  create table high_scores as
  select student, avg(score) as avg_score
  from scores
  group by student
  having avg(score) > 70
  order by avg_score desc;
quit;

proc print data=high_scores;
run;
