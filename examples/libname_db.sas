/* Connects to a real SQLite database via LIBNAME, reads from it,
   writes results back to it, and queries it directly with PROC SQL. */
libname classdb "examples/data/class.db";

data work.high_scorers;
  set classdb.students;
  if score >= 80;
run;

proc print data=work.high_scorers; run;

data classdb.passing;
  set classdb.students;
  passed = (score >= 75);
run;

proc sql;
  select name, score
  from classdb.students
  where score > 70
  order by score desc;
quit;
