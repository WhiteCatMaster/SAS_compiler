data arrinit;
  array nums{5} (10, 20, 30, 40, 50);
  array names{3} $10 ("Alice", "Bob", "Carol");
  total = 0;
  do i = 1 to dim(nums);
    total + nums{i};
  end;
  drop i;
run;

proc print data=arrinit; run;
