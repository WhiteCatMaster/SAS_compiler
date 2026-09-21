/* Demonstrates FILE/PUT (writing a delimited text file) and
   INFILE/INPUT (reading one back) round-tripping through /tmp. */
data _null_;
  input name $ score bonus;
  file "/tmp/sasc_demo_in.txt" dlm="," dsd;
  put name score bonus;
  datalines;
Alice 90 5
Bob 80 3
Carol 95 7
;
run;

data scores;
  infile "/tmp/sasc_demo_in.txt" dlm="," dsd;
  input name $ score bonus;
run;

proc print data=scores; run;
