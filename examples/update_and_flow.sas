data master;
  input id balance;
  datalines;
1 100
2 200
3 300
;
run;

data trans;
  input id balance;
  datalines;
2 250
3 .
;
run;

proc sort data=master out=master_s; by id; run;
proc sort data=trans out=trans_s; by id; run;

data updated;
  update master_s trans_s;
  by id;
run;

proc print data=updated; run;

data loopctl;
  x = 0;
  do i = 1 to 10;
    x + 1;
    if x >= 3 then leave;
  end;
  y = 0;
  do j = 1 to 5;
    if j = 2 then continue;
    y + 10;
  end;
run;

proc print data=loopctl; run;

data stopped;
  input v;
  datalines;
1
2
3
4
;
run;

data early;
  set stopped;
  if v >= 3 then stop;
  w = v * 10;
run;

proc print data=early; run;
