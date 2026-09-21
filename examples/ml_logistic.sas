data students;
  input hours pass;
  datalines;
1 0
2 0
3 0
4 1
5 0
6 1
7 1
8 0
9 1
10 1
;
run;

proc logistic data=students;
  model pass = hours;
  output out=scored p=predicted_prob;
run;

proc print data=scored; run;
