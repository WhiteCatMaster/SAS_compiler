data students;
  input hours_studied prior_score exam_score;
  datalines;
1 60 65
2 62 68
3 70 75
4 72 78
5 78 82
6 80 85
7 85 90
8 88 92
9 90 95
10 95 98
;
run;

proc corr data=students;
  var hours_studied prior_score exam_score;
run;

proc reg data=students;
  model exam_score = hours_studied prior_score;
  output out=scored p=predicted r=resid;
run;

proc print data=scored; run;
