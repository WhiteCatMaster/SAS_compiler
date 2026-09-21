data src;
  input grp;
  datalines;
1
2
3
;
run;

data lookup_demo;
  array codes{3} _temporary_ (100, 200, 300);
  set src;
  matched = codes{grp};
run;

proc print data=lookup_demo; run;
