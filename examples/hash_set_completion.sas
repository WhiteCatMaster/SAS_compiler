/* SET ... KEY= : keyed lookup without a separate HASH object. */
data customers;
  input custid name $ city $;
  datalines;
1 Alice NYC
2 Bob LA
3 Carol SF
;
run;

data orders;
  input custid amount;
  datalines;
1 100
2 250
4 999
;
run;

data joined;
  set orders;
  set customers key=custid_idx;
  if _iorc_ ne 0 then do;
    matched = 0;
    name = "UNKNOWN";
  end;
  else matched = 1;
run;

proc print data=joined; run;

/* HASH .OUTPUT(): dump a hash table's contents back out as a dataset. */
data _null_;
  if _n_ = 1 then do;
    declare hash h();
    h.definekey("id");
    h.definedata("id", "val");
  end;
  id = 1; val = "a"; h.add();
  id = 2; val = "b"; h.add();
  id = 3; val = "c"; h.add();
  if _n_ = 1 then h.output(dataset: "dumped");
run;

proc print data=dumped; run;
