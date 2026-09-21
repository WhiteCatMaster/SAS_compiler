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

/* Summarized report: group by region, sum amount */
proc report data=sales;
  column region amount;
  define region / group;
  define amount / analysis sum;
run;

/* Plain listing restricted to the COLUMN statement's variables */
proc report data=sales;
  column region product amount;
run;
