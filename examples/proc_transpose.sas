data wide;
  input id x y z;
  datalines;
1 10 20 30
2 40 50 60
;
run;

proc transpose data=wide out=long;
  by id;
  var x y z;
run;

proc print data=long; run;

data sales;
  input region $ quarter $ amount;
  datalines;
East Q1 100
East Q2 150
West Q1 200
West Q2 250
;
run;

proc transpose data=sales out=sales_wide;
  by region;
  id quarter;
  var amount;
run;

proc print data=sales_wide; run;
