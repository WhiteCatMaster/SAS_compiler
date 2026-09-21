/* Demonstrates ODS HTML: PROC output between FILE= and CLOSE is
   captured into a formatted report file instead of the console. */
data students;
  input name $ score;
  datalines;
Alice 90
Bob 70
Carol 85
;
run;

ods html file="/tmp/sasc_ods_report.html";

proc print data=students;
run;

proc means data=students;
  var score;
run;

ods html close;

data _null_;
  put "Report written to /tmp/sasc_ods_report.html";
run;
