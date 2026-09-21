data doubled;
  array vals{4} v1-v4 (1, 2, 3, 4);
  do over vals;
    vals = vals * 10;
  end;
run;

proc print data=doubled; run;
