from provider_directory.analytics import period_in_sql, rebuild_analytics, work_type_case_sql
from provider_directory.cli import build_parser
from provider_directory.schema import PD_PROVIDER_PHASE4_COLUMNS, TABLES, ddl_statements
from provider_directory.transforms import payer_mix_percents, polish_work_type, top_commercial_payers


def test_polish_work_type_pos_and_rollup():
    assert polish_work_type(pos_type_code=11) == "Office"
    assert polish_work_type(pos_type_code=23) == "Emergency Department"
    assert polish_work_type(im_specialty_rollup="Office Specialty") == "Single Specialty Group"
    assert polish_work_type(im_specialty_rollup="General Acute Care Hospital") == "Short Term Acute Care Hospital"
    assert polish_work_type(im_specialty_rollup="Urgent Care Center") == "Urgent Care"


def test_payer_mix_excludes_other_from_denominator():
    mix = payer_mix_percents({1: 50, 2: 25, 3: 25, 4: 0, 5: 900})
    assert mix["visits_percent_medicare_traditional"] == 50.0
    assert mix["visits_percent_medicaid"] == 25.0
    assert mix["visits_percent_third_party"] == 25.0
    assert mix["visits_percent_medicare_advantage"] == 0.0


def test_top_commercial_is_share_of_commercial_only():
    top = top_commercial_payers({"Aetna": 70, "BCBS": 20, "UHC": 10, "": 99}, n=3)
    assert [name for name, _pct in top] == ["Aetna", "BCBS", "UHC"]
    assert top[0][1] == 70.0


def test_schema_includes_phase4():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "wrvu_total" in sql
    assert "top_payer_name_1" in sql
    assert "primary_organization_npi" in sql
    assert "pd_stg_npi_payor" in sql
    assert "wrvu_at_site" in sql
    names = {name for name, _def in PD_PROVIDER_PHASE4_COLUMNS}
    assert "visits_percent_medicaid" in names
    assert "pd_stg_npi_wrvu" in TABLES


def test_phase4_sql_stays_on_mart_and_dash():
    source = open(rebuild_analytics.__code__.co_filename, encoding="utf-8").read()
    assert "dash_physician_payor_all" in source
    assert "physician_primary_affiliation" in source
    assert "WORK_RVU" in source
    assert "FROM az.pat_dt" not in source
    assert "pd_stg_visit" in source
    assert "is_payor_code = 5" not in source or "PAYOR_OTHER" in source
    periods = period_in_sql(202308, 202407)
    assert "202308" in periods and "202407" in periods and "202408" not in periods
    case_sql = work_type_case_sql("sl")
    formatted = f"UPDATE t SET w = {case_sql} WHERE MOD(npi, 16) = %s"
    formatted % (3,)
    assert "Urgent Care" in case_sql


def test_cli_phase4():
    args = build_parser().parse_args(["phase4"])
    assert args.cmd == "phase4"
