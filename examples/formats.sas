data money;
  input name $ salary bonus_pct;
  format salary dollar12.2 bonus_pct percent8.1;
  label_txt = put(salary, comma10.);
  datalines;
Alice 55000 0.10
Bob 72000.5 0.05
;
run;

proc print data=money;
run;

proc sort data=money out=money_sorted;
  by salary;
run;

proc print data=money_sorted;
run;
