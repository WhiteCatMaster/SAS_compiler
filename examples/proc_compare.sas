/* PROC COMPARE: identical datasets vs. datasets with differences,
   aligned by an ID variable. */
data old_run;
  input id x y $;
  datalines;
1 10 aa
2 20 bb
3 30 cc
;
run;

data new_run;
  input id x y $;
  datalines;
1 10 aa
2 25 bb
3 30 zz
;
run;

proc compare base=old_run compare=new_run;
  id id;
run;

proc compare base=old_run compare=old_run;
run;
