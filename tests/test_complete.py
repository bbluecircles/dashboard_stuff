from provider_directory.cli import build_parser
from provider_directory.complete import (
    dow_overlay_set,
    is_ymd_sql,
    rebuild_complete,
    service_date_sql,
)
from provider_directory.schema import PD_PROVIDER_PHASE5_COLUMNS, TABLES, ddl_statements
from provider_directory.settings import PRIOR_WINDOW_END, PRIOR_WINDOW_START
from provider_directory.transforms import (
    dow_percentages,
    referral_display_name,
    specialty_percentile,
    wrvu_yoy_change_pct,
)


def test_prior_window_is_previous_year():
    assert PRIOR_WINDOW_START == 202208
    assert PRIOR_WINDOW_END == 202307


def test_dow_percentages_use_mariadb_dayofweek():
    mix = dow_percentages({2: 50, 3: 25, 4: 25, 1: 0, 5: 0, 6: 0, 7: 0})
    assert mix["visits_percent_monday"] == 50.0
    assert mix["visits_percent_tuesday"] == 25.0
    assert mix["visits_percent_sunday"] == 0.0


def test_wrvu_yoy_and_percentile():
    assert wrvu_yoy_change_pct(120, 100) == 20.0
    assert wrvu_yoy_change_pct(80, 100) == -20.0
    assert wrvu_yoy_change_pct(10, 0) is None
    assert specialty_percentile(10, 40) == 25.0
    assert specialty_percentile(1, 1) == 100.0
    assert referral_display_name(last_name="Smith", first_name="Sean") == "Smith, Sean"


def test_schema_includes_phase5():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_provider_referral" in sql
    assert "pd_stg_visit_date" in sql
    assert "pd_stg_npi_wrvu_prior" in sql
    assert "wrvu_specialty_percentile" in sql
    names = {name for name, _def in PD_PROVIDER_PHASE5_COLUMNS}
    assert "visits_percent_monday" in names
    assert "pd_provider_referral" in TABLES


def test_phase5_sql_stays_on_dash_and_pat_dt_dates():
    source = open(rebuild_complete.__code__.co_filename, encoding="utf-8").read()
    assert "dash_physician_referrals_to_rendering" in source
    assert "service_end_date" in source
    assert "FROM az.procd_dt" not in source
    date_sql = service_date_sql("t.service_end_date")
    assert "%" not in date_sql
    assert "%" not in is_ymd_sql("t.service_end_date")
    overlay = dow_overlay_set("x", "p")
    formatted = f"UPDATE t SET {overlay} WHERE MOD(npi, 16) = %s"
    formatted % (3,)
    f"SELECT {date_sql} FROM t WHERE npi = %s" % (1,)


def test_cli_phase5():
    args = build_parser().parse_args(["phase5"])
    assert args.cmd == "phase5"


def test_pipeline_exports_phase5():
    from provider_directory.pipeline import run_phase4, run_phase5

    assert callable(run_phase4)
    assert callable(run_phase5)
