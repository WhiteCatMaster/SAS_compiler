data students;
  input hours group $ score;
  datalines;
1 A 65
2 A 68
3 A 75
4 B 78
5 B 82
6 B 85
7 C 90
8 C 92
9 C 95
10 C 98
;
run;

proc glm data=students;
  class group;
  model score = hours group;
  output out=scored p=predicted r=resid;
run;

proc print data=scored; run;
