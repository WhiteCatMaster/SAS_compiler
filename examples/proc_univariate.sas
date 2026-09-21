data src;
  input x;
  datalines;
1
2
2
3
4
5
100
;
run;

proc univariate data=src;
  var x;
run;
