data customers;
  input id custname $ region $;
  datalines;
1 Acme East
2 Globex West
3 Initech East
4 Umbrella South
;
run;

data orders;
  input id amount;
  datalines;
1 100
2 250
3 75
1 50
5 999
;
run;

proc sort data=customers out=customers_s; by id; run;
proc sort data=orders out=orders_s; by id; run;

data merged;
  merge customers_s(in=a) orders_s(in=b);
  by id;
  if a and b;
  matched = "both";
run;

proc print data=merged; run;

data east_only;
  set customers(where=(region = "East"));
run;

proc print data=east_only; run;

data filtered;
  set orders;
  if amount < 100 then delete;
  pct = amount / 1000;
run;

proc print data=filtered; run;

proc freq data=customers;
  tables region;
run;

data loopdemo;
  x = 0;
  do while (x < 5);
    x + 1;
  end;
  y = 10;
  do until (y <= 0);
    y = y - 3;
  end;
run;

proc print data=loopdemo; run;
