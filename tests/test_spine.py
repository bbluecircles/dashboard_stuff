import inspect

from provider_directory.spine import (
    rebuild_spine,
    upsert_spine,
    upsert_spine_insert_sql,
    upsert_spine_update_sql,
)


_ACTIVITY = (
    "visits_total",
    "wrvu_total",
    "open_payments",
    "panel_size",
    "in_system_provider",
    "mips_final_score",
)


def test_upsert_insert_only_missing_type1():
    sql = upsert_spine_insert_sql(mart_db="az_pd", claims_db="az", lookup_db="azal")
    assert "TRUNCATE TABLE" not in sql
    assert "LEFT JOIN `az_pd`.pd_provider dest" in sql
    assert "WHERE dest.npi IS NULL" in sql
    assert "npi_type = '1'" in sql
    assert "`az`.physician" in sql
    assert "`azal`.npi_spec_grp" in sql


def test_upsert_update_is_identity_only():
    sql = upsert_spine_update_sql(mart_db="az_pd")
    assert "TRUNCATE TABLE" not in sql
    assert "dest.first_name = COALESCE(src.first_name, dest.first_name)" in sql
    assert "primary_specialty_code" in sql
    for col in _ACTIVITY:
        assert col not in sql


def test_upsert_spine_function_never_truncates():
    source = inspect.getsource(upsert_spine)
    assert "TRUNCATE TABLE" not in source
    assert "pat_dt" not in source
    assert inspect.getsource(rebuild_spine).count("TRUNCATE TABLE") == 1
