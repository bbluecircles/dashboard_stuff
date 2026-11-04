from provider_directory.cli import build_parser
from provider_directory.locations import (
    cluster_key_sql,
    po_box_sql,
    practice_name_sql,
    rebuild_locations,
    street_key_sql,
)
from provider_directory.schema import PD_PROVIDER_PHASE3_COLUMNS, TABLES, ddl_statements
from provider_directory.transforms import (
    PERSON_NAME_REGEXP,
    city_without_state,
    cluster_key,
    is_po_box,
    normalize_street,
    pick_practice_name,
    rank_clusters,
)


def test_normalize_street_strips_suite_and_directionals():
    assert normalize_street("1325 S Colorado Blvd Ste 206") == "1325 S COLORADO BLVD"
    assert normalize_street("400 North Stephanie Street Suite 310") == "400 N STEPHANIE ST"
    assert normalize_street("PO Box 123") is None


def test_po_box_and_city_zip():
    assert is_po_box("P.O. Box 88") is True
    assert is_po_box("4126 N Holland Sylvania Rd") is False
    assert city_without_state("Toledo, OH") == "Toledo"
    assert city_without_state("Phoenix") == "Phoenix"


def test_cluster_key_prefers_street_zip_over_geo():
    key = cluster_key(
        sl_code=111,
        street="100 Main St Ste 2",
        zip_code="85016-1234",
        latitude=33.5,
        longitude=-112.1,
    )
    assert key.startswith("a:100 MAIN ST|85016")
    geo = cluster_key(sl_code=111, street=None, zip_code=None, latitude=33.4484, longitude=-112.074)
    assert geo.startswith("g:33.4484,-112.0740")
    raw = cluster_key(sl_code=999, street="PO Box 1", zip_code=None, latitude=0, longitude=0)
    assert raw == "s:999"


def test_pick_practice_name_dba_first_never_unknown():
    type2 = pick_practice_name(
        dba_name="VALLEY HEART TYPE-2-ENTITY",
        sl_name="VALLEY HEART TYPE-2-ENTITY",
    )
    assert type2 == "VALLEY HEART"
    # A clinician's own name as the DBA keeps the name with ' Office' appended.
    person = pick_practice_name(
        dba_name="SMITH, SEAN",
        street="1 Main St",
        city="Mesa",
    )
    assert person == "SMITH, SEAN Office"
    mayo = pick_practice_name(
        dba_name="Mayo Clinic Scottsdale",
    )
    assert mayo == "Mayo Clinic Scottsdale"
    unknown = pick_practice_name(
        dba_name="Unknown",
        sl_name="UNKNOWN",
        common_name="",
        street="13400 E Shea Blvd",
        city="Scottsdale",
    )
    assert unknown == "13400 E Shea Blvd, Scottsdale"
    clone = pick_practice_name(
        dba_name="ROBETORYE, RYAN",
        sl_name="ROBETORYE, RYAN",
        street="13400 E Shea Blvd",
        city="Scottsdale",
    )
    assert clone == "ROBETORYE, RYAN Office"
    own = pick_practice_name(dba_name="SMITH, SEAN", sl_name="SMITH, SEAN")
    assert own == "SMITH, SEAN Office"
    assert pick_practice_name(dba_name="", street="1 Main St", city="Mesa") == "1 Main St, Mesa"


def test_practice_name_sql_guards_unknown_and_drops_facility_npi():
    from provider_directory.transforms import clean_name_sql, work_type_case_sql

    name_sql = practice_name_sql()
    assert "sl_hospital_system_name" not in name_sql
    assert "provider_facility_npi" not in name_sql
    assert "'UNKNOWN'" in name_sql and "'UNKNOWN GROUP PRACTICE'" in name_sql
    assert name_sql.index("sl.sl_dba_name") < name_sql.index("sl.sl_name") < name_sql.index("sl.sl_common_name")
    assert "TYPE-2-ENTITY" in name_sql
    assert "npi_type = '2'" not in name_sql
    assert "'UNKNOWN'" in clean_name_sql("x.pos_type_name")
    wt = work_type_case_sql("sl")
    assert "'UNKNOWN'" in wt and "im_specialty_rollup" in wt
    assert "pos_type_code" not in wt and "pos_type_name" not in wt
    # pymysql %s formatting must survive: no bare % in the generated SQL
    for sql in (name_sql, wt):
        assert "%" not in sql.replace("%%", "")


def test_work_type_and_rank_clusters():
    ranked = rank_clusters(
        [
            {"cluster_key": "a:B", "visits": 10, "sl_code": 2},
            {"cluster_key": "a:A", "visits": 10, "sl_code": 1},
            {"cluster_key": "a:C", "visits": 3, "sl_code": 9},
        ],
        visits_total=23,
        max_sites=2,
    )
    assert [row["site_rank"] for row in ranked] == [1, 2]
    assert ranked[0]["sl_code"] == 1
    assert ranked[0]["visit_share_pct"] == 43.48
    assert len(ranked) == 2


def test_schema_includes_practice_mart():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_provider_practice" in sql
    assert "pd_stg_visit_site" in sql
    assert "provider_practices_total" in sql
    assert "pd_provider_practice" in TABLES
    names = {name for name, _def in PD_PROVIDER_PHASE3_COLUMNS}
    assert "provider_practices_total" in names


def test_phase3_sql_stays_on_staging_and_mart():
    source = open(rebuild_locations.__code__.co_filename, encoding="utf-8").read()
    assert "pd_stg_visit" in source
    assert "pd_stg_window_claim" in source
    assert "DROP TABLE IF EXISTS" not in source or "drop_phase3_staging" in source
    assert "pd_stg_window_claim" in source
    assert "TRUNCATE TABLE" in source
    assert "FROM az.pat_dt" not in source
    assert "cms_pdc_clinician" in source
    assert "provider_facility_npi" not in source
    assert "claims_sl" in source
    street = street_key_sql("sl.street")
    assert "STE|SUITE" in street
    assert "CHAR(37)" in po_box_sql("sl.street")
    assert "%" not in po_box_sql("sl.street")
    "SELECT 1 WHERE x = %s AND NOT ({})".format(po_box_sql("sl.street")) % (0,)
    name_sql = practice_name_sql()
    assert "sl.street" in name_sql
    assert PERSON_NAME_REGEXP in name_sql or "^[^,]+," in name_sql
    assert name_sql.count("' Office')") == 3
    key = cluster_key_sql("sl.street", "sl.zip_code", "sl.latitude", "sl.longitude", "sl.sl_code")
    assert "a:" in key and "g:" in key and "s:" in key


def test_cli_phase3():
    args = build_parser().parse_args(["phase3"])
    assert args.cmd == "phase3"
