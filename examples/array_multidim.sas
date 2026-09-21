data seating;
  /* 3 rows x 4 seats/row, filled with a running seat number */
  array chart{3,4} seat1-seat12;
  do row = 1 to dim(chart, 1);
    do col = 1 to dim(chart, 2);
      chart{row,col} = (row - 1) * dim(chart, 2) + col;
    end;
  end;
  total_seats = dim(chart);
  drop row col;
run;

data sales;
  /* dual-key lookup: sales by (region, quarter), non-default lower bounds */
  array rev{1:2, 2023:2024} rev1-rev4;
  rev{1,2023} = 1000;
  rev{1,2024} = 1500;
  rev{2,2023} = 2000;
  rev{2,2024} = 2600;

  grand_total = 0;
  do r = lbound(rev,1) to hbound(rev,1);
    do y = lbound(rev,2) to hbound(rev,2);
      grand_total + rev{r,y};
    end;
  end;
  drop r y;
run;

proc print data=seating; run;
proc print data=sales; run;
