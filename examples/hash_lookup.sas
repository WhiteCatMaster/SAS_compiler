/* DATA step HASH object: enrich orders with product info from a lookup
   table, without an explicit MERGE/SORT step. */
data lookup;
  input id product $ price;
  datalines;
1 Widget 9.99
2 Gadget 19.99
3 Gizmo 29.99
;
run;

data orders;
  input id qty;
  datalines;
1 5
2 3
99 1
;
run;

data enriched;
  length product $20;
  if _n_ = 1 then do;
    declare hash h(dataset: "lookup");
    h.definekey("id");
    h.definedata("product", "price");
    h.definedone();
  end;
  set orders;
  rc = h.find(key: id);
  if rc ne 0 then do;
    product = "UNKNOWN";
    price = 0;
  end;
  total = qty * price;
  drop rc;
run;

proc print data=enriched; run;
