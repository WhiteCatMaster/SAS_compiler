/* CALL EXECUTE: a classic idiom is generating one small block of SAS
   code per row of a driver dataset. Each queued snippet runs right
   after this DATA _NULL_ step finishes, sharing the same WORK library,
   so the PROC PRINT below can see datasets it never mentions by name
   at compile time. */
data regions;
  input region $ target;
  datalines;
east 100
west 150
north 90
;
run;

data _null_;
  set regions;
  call execute(cats(
    'data work.', region, '_report; ',
    'set work.regions(where=(region="', region, '")); ',
    'pct_of_target = target / ', target, ' * 100; ',
    'run;'
  ));
run;

data all_reports;
  set work.east_report work.west_report work.north_report;
run;

proc print data=all_reports;
run;
