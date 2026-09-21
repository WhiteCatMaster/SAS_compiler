data bounded;
  array yr{2020:2023} y2020-y2023 (10, 20, 30, 40);
  do year = 2020 to 2023;
    total + yr{year};
  end;
  lo = lbound(yr);
  hi = hbound(yr);
  n = dim(yr);
  drop year;
run;

proc print data=bounded; run;
