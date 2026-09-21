data src;
  input name $ score;
  datalines;
Alice 90
Bob 70
Carol 85
Dave 70
;
run;

proc rank data=src out=ranked descending;
  var score;
  ranks rank_score;
run;

proc print data=ranked; run;
