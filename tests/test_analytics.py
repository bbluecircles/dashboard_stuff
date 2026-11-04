import pytest

from provider_directory.analytics import (
    is_hcpcs_sql,
    payer_mix_update_sql,
    period_in_sql,
    rebuild_analytics,
    top_payer_update_sql,
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
    strict_work_rvu_sql,
    top_commercial_payers,
)


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
    # strict: practice-expense totals and tiny placeholder work values never price a line
    assert physician_work_rvu(work_rvu=0.001, nf_total_rvu=4.6) is None
    assert physician_work_rvu(work_rvu=0.03) is None
    assert physician_work_rvu(work_rvu=0.001, facility_total=3.0, facility_pe_rvu=1.0, mp_rvu=0.2) == 1.8
    assert looks_like_person_name("ROBETORYE, RYAN") is True
    assert looks_like_person_name("MAYO CLINIC HOSPITAL") is False


def test_phase4_sql_stays_on_mart_and_dash():
    source = open(rebuild_analytics.__code__.co_filename, encoding="utf-8").read()
    assert "dash_physician_payor_all" in source
    assert "overlay_billing_organization" in source
    assert "physician_primary_affiliation" in open(rebuild_analytics.__code__.co_filename, encoding="utf-8").read()
    assert "rebuild_org_lists" in source
    assert "strict_work_rvu_sql" in source
    assert "NON_FACILITY_TOTAL" in strict_work_rvu_sql("pr")
    assert "nf_total_rvu" not in strict_work_rvu_sql("pr")
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
    assert "im_specialty_rollup" in case_sql and "pos_type_code" not in case_sql
    wrvu = strict_work_rvu_sql("pr")
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


def test_ref_server_settings_and_connection_are_separate(monkeypatch):
    import inspect

    from provider_directory import db, settings

    assert settings.REF_DB == "master_ref"
    assert settings.REF_PHYSICIAN_TABLE == "111_physician"
    assert settings.REF_GROUP_PRACTICE_TABLE == "cms_group_practice"
    monkeypatch.setenv("DB_USER", "claims")
    monkeypatch.setenv("DB_PASSWORD", "pw")
    monkeypatch.delenv("REF_DB_HOST", raising=False)
    same = db.ref_db_config()
    assert same["same_server"] is True and same["user"] == "claims"
    monkeypatch.setenv("REF_DB_HOST", "ref.example")
    monkeypatch.setenv("REF_DB_PORT", "3307")
    monkeypatch.setenv("REF_DB_USER", "ref")
    monkeypatch.setenv("REF_DB_PASSWORD", "refpw")
    other = db.ref_db_config()
    assert other == {"host": "ref.example", "port": 3307, "user": "ref", "password": "refpw", "same_server": False}
    monkeypatch.delenv("REF_DB_USER")
    monkeypatch.delenv("DB_USER")
    with pytest.raises(db.ConfigError):
        db.ref_db_config()
    source = inspect.getsource(db.get_ref_connection)
    assert "READ UNCOMMITTED" in source and "max_statement_time" in source and "autocommit=True" in source
    assert "READ UNCOMMITTED, " not in source  # isolation level and timeout are separate statements
    assert build_parser().parse_args(["ref-check"]).func.__name__ == "_cmd_ref_check"


def test_phase4_name_polish_rewrites_blank_unknown_and_person_names_for_any_npi_type():
    import inspect

    source = inspect.getsource(rebuild_analytics)
    assert "UPPER(TRIM(pr.name)) IN {EMPTY_NAME_SQL_LIST}" in source
    assert "pr.name IS NULL" in source
    assert "IFNULL(pr.npi_type, '') = '1'" not in source
    assert "provider_facility_npi" not in source


def test_rvu_prices_every_line_and_rvu_only_flag():
    import inspect

    from provider_directory.analytics import _rebuild_rvu, line_scan_sql, priced_line_filter_sql, rebuild_rvu_only
    from provider_directory.complete import _fill_prior_wrvu, prior_line_scan_sql
    from provider_directory.pipeline import run_phase4
    from provider_directory.schema import PHASE4_STAGING_TABLES, PHASE5_CACHED_STAGING_TABLES, PHASE5_STAGING_TABLES, ddl_statements

    scan = line_scan_sql("`az_pd`", "`az_cms`")
    assert "INSERT INTO `az_pd`.pd_stg_npi_px_line" in scan
    assert "COALESCE(NULLIF(TRIM(t.work_procd_code), ''), NULLIF(TRIM(t.procd_code), ''))" in scan
    assert "t.rendering_physician_code" in scan and "IFNULL(t.sl_code, 0)" in scan
    assert "MOD(IFNULL(t.pat_id, 0), 4) = %s" in scan
    assert "encounter_work_procd_code" not in scan
    priced = priced_line_filter_sql("l.code", "pr")
    assert "REGEXP '^[0-9]{4}F$'" in priced and "REGEXP '^C[0-9]{4}$'" in priced and "CHAR_LENGTH(TRIM(l.code)) = 5" in priced
    assert build_parser().parse_args(["phase4", "--rvu-only", "--rescan-lines"]).rescan_lines is True
    body = inspect.getsource(_rebuild_rvu)
    assert "SUM(l.line_count * {wrvu_expr})" in body and "SUM(l.line_count)" in body
    assert "phase4 rvu lines reused" in body
    assert "pd_stg_visit v" not in body
    assert "ROUND(w.total_wrvu / NULLIF(w.procedure_count, 0), 3)" in body
    prior = prior_line_scan_sql("`az_pd`", "`az_cms`")
    assert "pd_stg_npi_px_line_prior" in prior and "work_procd_code" in prior
    assert "prior rvu lines reused" in inspect.getsource(_fill_prior_wrvu)
    assert "pd_stg_npi_px_line" in PHASE4_STAGING_TABLES
    assert "pd_stg_npi_px_line_prior" in PHASE5_CACHED_STAGING_TABLES
    assert "pd_stg_npi_wrvu_prior" in PHASE5_STAGING_TABLES and "pd_stg_npi_wrvu_prior" not in PHASE5_CACHED_STAGING_TABLES
    assert "pd_stg_npi_px_line_prior" in "\n".join(ddl_statements("az_pd"))
    assert "rvu_only" in inspect.getsource(run_phase4)
    assert "pd_stg_npi_payor" not in inspect.getsource(rebuild_rvu_only)
    assert build_parser().parse_args(["phase4", "--rvu-only"]).rvu_only is True


def test_service_type_is_the_rollup_and_pos_mix_keys_on_it():
    wt = work_type_case_sql("sl")
    assert "sl.im_specialty_rollup" in wt and "POS" not in wt
    from provider_directory.extras import pos_mix_bucket_sql

    mix = pos_mix_bucket_sql("sl")
    assert "'urgent'" in mix and "'emergency'" in mix and "THEN 'ed'" in mix
    assert "Emergency Department" not in mix
    f"SELECT {mix} FROM t WHERE npi = %s" % (1,)
