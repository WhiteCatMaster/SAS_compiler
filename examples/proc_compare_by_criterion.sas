/* PROC COMPARE with BY-group processing and a CRITERION= fuzzy
   tolerance for numeric comparisons. */
data old_readings;
  input site $ day x;
  datalines;
a 1 10.0001
a 2 20.00
b 1 30.00
b 2 40.00
;
run;

data new_readings;
  input site $ day x;
  datalines;
a 1 10.0002
a 2 20.05
b 1 30.00
b 2 41.00
;
run;

/* Without CRITERION=, tiny rounding differences would be reported. */
proc compare base=old_readings compare=new_readings criterion=0.001;
  by site;
  id day;
run;
