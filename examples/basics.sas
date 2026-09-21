data work.people;
  input name $ age salary;
  datalines;
Alice 30 55000
Bob 45 72000
Carol 25 48000
Dave 52 91000
;
run;

data raise;
  set people;
  if age > 40 then bonus = salary * 0.10;
  else bonus = salary * 0.05;
  new_salary = salary + bonus;
  category = "junior";
  if age >= 40 then category = "senior";
  drop age;
run;

proc sort data=raise out=raise_sorted;
  by descending new_salary;
run;

proc print data=raise_sorted;
run;

proc means data=raise;
  var salary bonus;
  output out=stats mean=avg_salary avg_bonus std=sd_salary sd_bonus;
run;

proc print data=stats;
run;
