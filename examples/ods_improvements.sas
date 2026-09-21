/* Demonstrates real HTML <table> rendering (a 3+ column PROC PRINT is
   detected and rendered as a table, not a plain <pre> block), the RTF
   destination, and having both open at the same time. */
data students;
  input name $ score bonus;
  datalines;
Alice 90 5
Bob 80 3
Carol 95 7
;
run;

ods html file="/tmp/ods_improvements_report.html";
ods rtf file="/tmp/ods_improvements_report.rtf";

proc print data=students;
run;

proc means data=students;
  var score bonus;
run;

ods html close;
ods rtf close;
