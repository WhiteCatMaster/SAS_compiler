/* Reads examples/data/people.csv, filters it, writes the result back out. */
proc import datafile="examples/data/people.csv" out=people dbms=csv replace;
  getnames=yes;
run;

data adults;
  set people;
  if age >= 18;
run;

proc print data=adults; run;

proc export data=adults outfile="examples/data/adults_out.csv" dbms=csv replace;
run;
