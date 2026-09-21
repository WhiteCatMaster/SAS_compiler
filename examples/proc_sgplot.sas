data students;
  input hours score region $;
  datalines;
1 60 East
2 65 East
3 70 West
4 75 West
5 80 East
6 85 West
7 90 East
8 95 West
;
run;

/* scatter: raw relationship between two numeric variables */
proc sgplot data=students out="examples/data/sgplot_scatter.png";
  scatter x=hours y=score;
run;

/* series: same data as a sorted line plot */
proc sgplot data=students out="examples/data/sgplot_series.png";
  series x=hours y=score;
run;

/* vbar: frequency count per category (no RESPONSE=) */
proc sgplot data=students out="examples/data/sgplot_vbar_freq.png";
  vbar region;
run;

/* vbar with RESPONSE=: sum of score per category */
proc sgplot data=students out="examples/data/sgplot_vbar_response.png";
  vbar region / response=score;
run;

/* histogram: distribution of one numeric variable */
proc sgplot data=students out="examples/data/sgplot_histogram.png" title="Score distribution";
  histogram score;
run;

/* multiple plot statements overlay onto the same figure */
proc sgplot data=students out="examples/data/sgplot_overlay.png";
  scatter x=hours y=score;
  series x=hours y=score;
run;
