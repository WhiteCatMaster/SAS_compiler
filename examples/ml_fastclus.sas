data points;
  input x y;
  datalines;
1 1
1.2 0.9
0.9 1.1
10 10
10.2 9.8
9.9 10.1
;
run;

proc fastclus data=points maxclusters=2;
  var x y;
  output out=clustered;
run;

proc print data=clustered; run;
