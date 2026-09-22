import pytest

from sas_compiler.codegen import CodegenError

from tests.test_compiler import run_sas


def test_2d_array_cell_read_write_matches_flat_mapping():
    ds = run_sas("""
        data grid;
            array g{3,4} g1-g12;
            do i = 1 to 3;
                do j = 1 to 4;
                    g{i,j} = i*10 + j;
                end;
            end;
            output;
        run;
    """)
    row = ds["grid"].iloc[0]
    # row-major: g{i,j} -> flat index (i-1)*4 + (j-1) -> g1..g12
    assert row["g1"] == 11 and row["g4"] == 14
    assert row["g5"] == 21 and row["g8"] == 24
    assert row["g9"] == 31 and row["g12"] == 34


def test_2d_array_nested_do_loop_sum():
    ds = run_sas("""
        data out;
            array g{3,4} g1-g12;
            do i = 1 to 3;
                do j = 1 to 4;
                    g{i,j} = i*10 + j;
                end;
            end;
            total = 0;
            do i = 1 to 3;
                do j = 1 to 4;
                    total + g{i,j};
                end;
            end;
            output;
        run;
    """)
    assert ds["out"].iloc[0]["total"] == 270


def test_dim_of_2d_array():
    ds = run_sas("""
        data out;
            array g{3,4} g1-g12;
            nd1 = dim(g, 1);
            nd2 = dim(g, 2);
            ntot = dim(g);
            output;
        run;
    """)
    row = ds["out"].iloc[0]
    assert row["nd1"] == 3
    assert row["nd2"] == 4
    assert row["ntot"] == 12


def test_explicit_bounds_2d_array():
    ds = run_sas("""
        data out;
            array b{2020:2022, 1:2} b1-b6;
            b{2020,1} = 100;
            b{2021,2} = 200;
            b{2022,1} = 300;
            lo1 = lbound(b, 1);
            hi1 = hbound(b, 1);
            lo2 = lbound(b, 2);
            hi2 = hbound(b, 2);
            output;
        run;
    """)
    row = ds["out"].iloc[0]
    assert row["b1"] == 100  # (2020,1) -> offset 0
    assert row["b4"] == 200  # (2021,2) -> offset (2021-2020)*2 + (2-1) = 3
    assert row["b5"] == 300  # (2022,1) -> offset (2022-2020)*2 + (1-1) = 4
    assert row["lo1"] == 2020 and row["hi1"] == 2022
    assert row["lo2"] == 1 and row["hi2"] == 2


def test_3d_array_offset_mapping():
    ds = run_sas("""
        data out;
            array c{2,2,2} c1-c8;
            do i = 1 to 2;
                do j = 1 to 2;
                    do k = 1 to 2;
                        c{i,j,k} = i*100 + j*10 + k;
                    end;
                end;
            end;
            output;
        run;
    """)
    row = ds["out"].iloc[0]
    assert row["c1"] == 111  # (1,1,1) -> offset 0
    assert row["c8"] == 222  # (2,2,2) -> offset 7


def test_hbound_lbound_multidim_without_dim_arg_raises():
    with pytest.raises(CodegenError):
        run_sas("""
            data out;
                array g{3,4} g1-g12;
                x = hbound(g);
                output;
            run;
        """)


def test_do_over_multidim_iterates_flat_row_major():
    ds = run_sas("""
        data out;
            array g{2,3} g1-g6 (1 2 3 4 5 6);
            total = 0;
            do over g;
                total + g;
            end;
            output;
        run;
    """)
    assert ds["out"].iloc[0]["total"] == 21


def test_2d_array_nested_per_row_initializer():
    ds = run_sas("""
        data out;
            array g{2,3} g1-g6 (1,2,3) (4,5,6);
            output;
        run;
    """)
    row = ds["out"].iloc[0]
    assert [row["g1"], row["g2"], row["g3"], row["g4"], row["g5"], row["g6"]] == [1, 2, 3, 4, 5, 6]


def test_2d_array_nested_per_row_initializer_matches_flat_form():
    flat = run_sas("""
        data out;
            array g{2,3} g1-g6 (1 2 3 4 5 6);
            output;
        run;
    """)["out"].iloc[0]
    nested = run_sas("""
        data out;
            array g{2,3} g1-g6 (1,2,3) (4,5,6);
            output;
        run;
    """)["out"].iloc[0]
    for col in ["g1", "g2", "g3", "g4", "g5", "g6"]:
        assert flat[col] == nested[col]


def test_3d_array_nested_per_row_initializer():
    ds = run_sas("""
        data out;
            array c{2,2,2} c1-c8 ((1,2)(3,4)) ((5,6)(7,8));
            output;
        run;
    """)
    row = ds["out"].iloc[0]
    assert [row[f"c{i}"] for i in range(1, 9)] == [1, 2, 3, 4, 5, 6, 7, 8]
