data temp1 temp2 keep_me;
  x = 1;
run;

proc datasets library=work nolist;
  delete temp1 temp2;
run;
quit;

data renamed;
  x = 1;
  y = 2;
  label x = "First Value" y = "Second Value";
run;

proc datasets library=work nolist;
  change renamed=final;
run;
quit;

proc print data=final; run;
