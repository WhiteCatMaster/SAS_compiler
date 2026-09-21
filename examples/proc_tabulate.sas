data sales;
  input region $ product $ amount;
  datalines;
East Widget 100
East Widget 150
East Gadget 200
West Widget 300
West Gadget 400
West Gadget 100
;
run;

/* region (rows) x product (cols), cell = sum of amount */
proc tabulate data=sales;
  class region product;
  var amount;
  table region, product*amount*sum;
run;

/* same, cell = mean of amount, plus an OUT= dataset of the pivot */
proc tabulate data=sales out=avg_by_region_product;
  class region product;
  var amount;
  table region, product*amount*mean;
run;

proc print data=avg_by_region_product; run;
