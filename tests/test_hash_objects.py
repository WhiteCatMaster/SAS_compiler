from tests.test_compiler import run_sas


def test_hash_find_success_populates_data_vars():
    src = """
    data lookup;
      input id product $ price;
      datalines;
    1 Widget 9.99
    2 Gadget 19.99
    ;
    run;
    data orders;
      input id qty;
      datalines;
    1 5
    2 3
    ;
    run;
    data out;
      length product $20;
      if _n_ = 1 then do;
        declare hash h(dataset: "lookup");
        h.definekey("id");
        h.definedata("product", "price");
        h.definedone();
      end;
      set orders;
      rc = h.find(key: id);
      total = qty * price;
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("id")
    assert df.loc[1.0, "product"] == "Widget"
    assert df.loc[1.0, "rc"] == 0.0
    assert df.loc[1.0, "total"] == 5 * 9.99
    assert df.loc[2.0, "product"] == "Gadget"
    assert df.loc[2.0, "total"] == 3 * 19.99


def test_hash_find_miss_returns_nonzero_and_leaves_data_vars():
    src = """
    data lookup;
      input id product $ price;
      datalines;
    1 Widget 9.99
    ;
    run;
    data orders;
      input id;
      datalines;
    1
    99
    ;
    run;
    data out;
      length product $20;
      if _n_ = 1 then do;
        declare hash h(dataset: "lookup");
        h.definekey("id");
        h.definedata("product", "price");
        h.definedone();
      end;
      product = "UNKNOWN";
      price = 0;
      set orders;
      rc = h.find(key: id);
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("id")
    assert df.loc[1.0, "rc"] == 0.0
    assert df.loc[1.0, "product"] == "Widget"
    assert df.loc[99.0, "rc"] == 1.0
    # miss: data vars keep whatever the PDV already held (the defaults set
    # before .find(), since a real dataset row never overwrote them)
    assert df.loc[99.0, "product"] == "UNKNOWN"
    assert df.loc[99.0, "price"] == 0.0


def test_hash_find_implicit_key_from_current_pdv():
    """h.find(); with no explicit key: arg reads the CURRENT pdv value(s)
    of the defined key variable(s) directly."""
    src = """
    data lookup;
      input id price;
      datalines;
    1 100
    2 200
    ;
    run;
    data orders;
      input id;
      datalines;
    1
    2
    ;
    run;
    data out;
      if _n_ = 1 then do;
        declare hash h(dataset: "lookup");
        h.definekey("id");
        h.definedata("price");
        h.definedone();
      end;
      set orders;
      rc = h.find();
    run;
    """
    ds = run_sas(src)
    df = ds["out"].set_index("id")
    assert df.loc[1.0, "price"] == 100.0
    assert df.loc[2.0, "price"] == 200.0


def test_hash_add_and_remove_without_source_dataset():
    src = """
    data out;
      length name $10;
      if _n_ = 1 then do;
        declare hash h();
        h.definekey("id");
        h.definedata("name");
      end;
      id = 1;
      name = "Alice";
      rc_add = h.add();
      found_id = 1;
      rc_find1 = h.find(key: found_id);
      h.remove(key: found_id);
      rc_find2 = h.find(key: found_id);
    run;
    """
    ds = run_sas(src)
    row = ds["out"].iloc[0]
    assert row["rc_add"] == 0.0
    assert row["rc_find1"] == 0.0
    assert row["rc_find2"] == 1.0
