/* Demonstrates the expanded %SYSFUNC date/format functions and the
   %QSCAN/%QSUBSTR/%QUPCASE quoted-text macro functions. */

%let start_date = %sysfunc(mdy(1,15,2024));
%let end_date = %sysfunc(mdy(6,15,2024));

%put Months between start and end: %sysfunc(intck(month,&start_date,&end_date));
%put Year of start date: %sysfunc(year(&start_date));
%put Month of start date: %sysfunc(month(&start_date));
%put Day of start date: %sysfunc(day(&start_date));

%let one_month_later = %sysfunc(intnx(month,&start_date,1));
%put One month after start: %sysfunc(month(&one_month_later))/%sysfunc(day(&one_month_later));

%put Formatted amount: %sysfunc(putn(1234.5,comma8.1));

%put %qscan(East-West-North,2,-);
%put %qsubstr(classroom,1,5);
%put %qupcase(machine learning);

data checkpoint;
  days_between = %sysfunc(intck(day,&start_date,&end_date));
  start = &start_date;
  stop = &end_date;
run;

proc print data=checkpoint; run;
