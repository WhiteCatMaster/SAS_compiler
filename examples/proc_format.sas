proc format;
  value agegrp
    low-17 = "Minor"
    18-64 = "Adult"
    65-high = "Senior";
  value $gender
    "M" = "Male"
    "F" = "Female"
    other = "Unknown";
run;

data people;
  input name $ age gender $;
  format age agegrp. gender $gender.;
  datalines;
Alice 15 F
Bob 30 M
Carol 70 X
;
run;

proc print data=people;
run;
