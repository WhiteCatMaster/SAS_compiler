/* End-to-end smoke test combining several subsystems in one pipeline:
   a %MACRO-driven parameter, an ARRAY-built DATA step, PROC FORMAT,
   PROC MEANS with explicit stats and OUTPUT OUT=, and a PROC SQL
   GROUP BY summary. */
%let n_students = 5;

data students;
  array base_score{5} (65, 70, 78, 85, 92);
  array bonus{5} (5, 3, 8, 2, 6);
  do i = 1 to &n_students;
    student_id = i;
    score = base_score{i} + bonus{i};
    output;
  end;
  keep student_id score;
run;

proc format;
  value gradefmt
    low-69 = "F"
    70-79 = "C"
    80-89 = "B"
    90-high = "A";
run;

data graded;
  set students;
  format score gradefmt.;
  grade_label = put(score, gradefmt.);
run;

proc print data=graded;
run;

proc means data=graded n mean std min max;
  var score;
  output out=summary_stats mean=avg_score;
run;

proc print data=summary_stats;
run;

proc sql;
  select grade_label, count(*) as n
  from graded
  group by grade_label
  order by grade_label;
quit;
