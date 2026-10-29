from provider_directory.analytics import (
    is_hcpcs_sql,
    payer_mix_update_sql,
    period_in_sql,
    rebuild_analytics,
    top_payer_update_sql,
    work_rvu_sql,
    work_type_case_sql,
)
from provider_directory.cli import build_parser
from provider_directory.schema import PD_PROVIDER_PHASE4_COLUMNS, TABLES, ddl_statements
from provider_directory.settings import (
    MAX_TOP_PAYERS,
    PAYOR_CLASSES,
    PAYOR_COMMERCIAL,
    PAYOR_MIX_CODES,
    PAYOR_MIX_COLUMNS,
)
from provider_directory.transforms import (
    looks_like_person_name,
    payer_mix_percents,
    physician_work_rvu,
    polish_work_type,
    top_commercial_payers,
)


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
    assert "pd_provider_group_practice" in sql
    assert "pd_provider_hospital_affiliation" in sql
    assert "wrvu_at_site" in sql
    names = {name for name, _def in PD_PROVIDER_PHASE4_COLUMNS}
    assert "visits_percent_medicaid" in names
    assert "pd_stg_npi_wrvu" in TABLES


def test_work_rvu_prefers_plausible_pfs_columns():
    assert physician_work_rvu(work_rvu=1.46) == 1.46
    assert physician_work_rvu(
        work_rvu=0.001,
        non_facility_total=5.0,
        non_fac_pe_rvu=3.0,
        mp_rvu=0.5,
    ) == 1.5
    assert physician_work_rvu(work_rvu=0.001, nf_total_rvu=4.6) == 4.6
    assert physician_work_rvu(work_rvu=0.03) == 0.03
    assert looks_like_person_name("ROBETORYE, RYAN") is True
    assert looks_like_person_name("MAYO CLINIC HOSPITAL") is False


def test_phase4_sql_stays_on_mart_and_dash():
    source = open(rebuild_analytics.__code__.co_filename, encoding="utf-8").read()
    assert "dash_physician_payor_all" in source
    assert "physician_primary_affiliation" in source
    assert "rebuild_org_lists" in source
    assert "WORK_RVU" in source
    assert "NON_FACILITY_TOTAL" in source
    assert "ROUND(SUM(pr.WORK_RVU)" not in source
    assert "FROM az.pat_dt" not in source
    assert "pd_stg_visit" in source
    assert "is_payor_code = 5" not in source or "PAYOR_OTHER" in source
    # Blended az_cms: payer mix comes from pat_dt × payor lookup, visit grain,
    # one scan per period/encounter bucket, accumulated with ON DUPLICATE KEY.
    assert 'payer_source == "pat_dt"' in source
    assert "encounter_payor_code" in source
    assert "py.is_payor_code" in source
    assert "MAX(t.encounter_rendering_physician_code) AS rendering_npi" in source
    assert "claim_count = {mart}.pd_stg_npi_payor.claim_count + VALUES(claim_count)" in source
    periods = period_in_sql(202308, 202407)
    assert "202308" in periods and "202407" in periods and "202408" not in periods
    case_sql = work_type_case_sql("sl")
    formatted = f"UPDATE t SET w = {case_sql} WHERE MOD(npi, 16) = %s"
    formatted % (3,)
    assert "Urgent Care" in case_sql
    wrvu = work_rvu_sql("pr")
    assert "NON_FACILITY_TOTAL" in wrvu
    assert "CHAR_LENGTH(TRIM(px)) = 5" in is_hcpcs_sql("px")
    f"SELECT {wrvu} FROM t WHERE npi = %s" % (1,)


def test_cli_phase4():
    args = build_parser().parse_args(["phase4"])
    assert args.cmd == "phase4"
    assert args.org_lists_only is False
    lists = build_parser().parse_args(["phase4", "--org-lists-only"])
    assert lists.org_lists_only is True


def test_payer_mix_covers_classes_1_to_7_except_other():
    # 4 is HMO, 6 is Medicare Advantage, 7 is Medicare Other; 5 Other is outside the denominator.
    mix = payer_mix_percents({1: 10, 2: 10, 3: 20, 4: 10, 5: 900, 6: 40, 7: 10})
    assert mix["visits_percent_medicare_traditional"] == 10.0
    assert mix["visits_percent_medicaid"] == 10.0
    assert mix["visits_percent_third_party"] == 20.0
    assert mix["visits_percent_hmo"] == 10.0
    assert mix["visits_percent_medicare_advantage"] == 40.0
    assert mix["visits_percent_medicare_other"] == 10.0
    assert set(mix) == {col for _code, col in PAYOR_MIX_COLUMNS}


def test_payer_class_constants():
    assert PAYOR_CLASSES[4] == "HMO"
    assert PAYOR_CLASSES[6] == "Medicare Advantage"
    assert PAYOR_CLASSES[7] == "Medicare Other"
    assert 5 not in PAYOR_MIX_CODES
    assert PAYOR_MIX_CODES == (1, 2, 3, 4, 6, 7)
    assert dict(PAYOR_MIX_COLUMNS)[6] == "visits_percent_medicare_advantage"
    assert dict(PAYOR_MIX_COLUMNS)[4] == "visits_percent_hmo"


def test_payer_update_sql_uses_every_class_and_five_top_payers():
    mix = payer_mix_update_sql("`az_pd`")
    assert "is_payor_code IN (1, 2, 3, 4, 6, 7)" in mix
    assert "p.visits_percent_medicare_advantage = ROUND(100.0 * m.c6" in mix
    assert "p.visits_percent_hmo = ROUND(100.0 * m.c4" in mix
    assert "p.visits_percent_medicare_other = ROUND(100.0 * m.c7" in mix
    assert "is_payor_code = 5" not in mix
    top = top_payer_update_sql("`az_pd`")
    assert "WHERE rk <= 5" in top
    assert "p.top_payer_name_5 = t.n5" in top
    assert "p.top_payer_percent_5 = t.p5" in top
    assert f"is_payor_code = {PAYOR_COMMERCIAL}" in top
    assert MAX_TOP_PAYERS == 5
    cols = dict(PD_PROVIDER_PHASE4_COLUMNS)
    for name in ("visits_percent_hmo", "visits_percent_medicare_other", "top_payer_name_5", "top_payer_percent_5"):
        assert name in cols
    sql = "\n".join(ddl_statements("az_pd"))
    assert "visits_percent_medicare_other" in sql
    assert "top_payer_name_5" in sql


def test_phase4_payers_only_flag_and_entry():
    import inspect

    from provider_directory.analytics import rebuild_payer_columns
    from provider_directory.pipeline import run_phase4

    args = build_parser().parse_args(["phase4", "--payers-only"])
    assert args.payers_only is True
    assert build_parser().parse_args(["phase4"]).payers_only is False
    source = inspect.getsource(rebuild_payer_columns)
    assert "pd_stg_npi_payor" in source
    assert "migrate_phase4_columns" in source
    assert "rebuild_group_summary" in source
    assert "drop_phase4_staging" not in source
    assert "payers_only" in inspect.getsource(run_phase4)
