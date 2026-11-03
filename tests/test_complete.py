from provider_directory.cli import build_parser
from provider_directory.complete import (
    dow_overlay_set,
    is_ymd_sql,
    rebuild_complete,
    service_date_sql,
    yoy_change_sql,
)
from provider_directory.schema import PD_PROVIDER_PHASE5_COLUMNS, TABLES, ddl_statements
from provider_directory.settings import PRIOR_WINDOW_END, PRIOR_WINDOW_START
from provider_directory.transforms import (
    activity_specialty_percentile,
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
    assert wrvu_yoy_change_pct(26.23, 0.01) is None
    assert wrvu_yoy_change_pct(1_000_000, 1) == 9999.99
    assert specialty_percentile(10, 40) == 25.0
    assert specialty_percentile(1, 1) == 100.0
    assert activity_specialty_percentile(80.0, 20.0) == 50.0
    assert activity_specialty_percentile(90.0, None) == 90.0
    assert activity_specialty_percentile(None, 10.0) == 10.0
    assert activity_specialty_percentile(None, None) is None
    assert referral_display_name(last_name="Smith", first_name="Sean") == "Smith, Sean"


def test_schema_includes_phase5():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_provider_referral" in sql
    assert "pd_stg_visit_date" in sql
    assert "pd_stg_npi_wrvu_prior" in sql
    assert "wrvu_yoy_change_pct DECIMAL(12,2)" in sql
    names = {name for name, _def in PD_PROVIDER_PHASE5_COLUMNS}
    assert "visits_percent_monday" in names
    assert "visits_specialty_percentile" in names
    assert "activity_specialty_percentile" in names
    assert "pd_provider_referral" in TABLES
    assert "pd_stg_npi_visits_percentile" in TABLES
    assert "visits_specialty_percentile DECIMAL(5,1)" in sql


def test_phase5_sql_stays_on_dash_and_pat_dt_dates():
    source = open(rebuild_complete.__code__.co_filename, encoding="utf-8").read()
    assert "dash_physician_referrals_to_rendering" in source
    assert "service_end_date" in source
    assert "FROM az.procd_dt" not in source
    assert "pd_stg_npi_visits_percentile" in source
    assert "visits_specialty_percentile" in source
    assert "activity_specialty_percentile" in source
    date_sql = service_date_sql("t.service_end_date")
    assert "%" not in date_sql
    assert "%" not in is_ymd_sql("t.service_end_date")
    overlay = dow_overlay_set("x", "p")
    formatted = f"UPDATE t SET {overlay} WHERE MOD(npi, 16) = %s"
    formatted % (3,)
    assert "LEAST(" in yoy_change_sql("p.wrvu_total", "w.prior")
    assert "%" not in yoy_change_sql("p.wrvu_total", "w.prior")


def test_cli_phase5():
    args = build_parser().parse_args(["phase5"])
    assert args.cmd == "phase5"


def test_pipeline_exports_phase5():
    from provider_directory.pipeline import run_phase4, run_phase5, run_phase6

    assert callable(run_phase4)
    assert callable(run_phase5)
    assert callable(run_phase6)


def test_referral_rank_applies_exclusions_and_edges_are_cached():
    import inspect

    from provider_directory.cli import build_parser
    from provider_directory.complete import (
        _fill_referral_edges,
        _rank_referrals,
        rebuild_referrals,
        referral_exclusion_sql,
    )
    from provider_directory.pipeline import run_phase5
    from provider_directory.schema import PHASE5_CACHED_STAGING_TABLES, PHASE5_STAGING_TABLES
    from provider_directory.settings import MAX_REFERRAL_PEERS

    assert MAX_REFERRAL_PEERS == 5
    excluded = referral_exclusion_sql("me", "peer")
    assert "peer.primary_organization_id = me.primary_organization_id" in excluded
    assert "DIAGNOSTIC RADIOLOGY" in excluded and "PATHOLOG" in excluded and "ANESTHE" in excluded
    assert "NURSE PRACTITIONER" not in excluded
    rank = inspect.getsource(_rank_referrals)
    assert "INNER JOIN {mart}.pd_provider me ON me.npi = e.npi" in rank
    assert "AND NOT {excluded}" in rank
    assert "pd_stg_referral_edge" in PHASE5_CACHED_STAGING_TABLES
    assert "pd_stg_referral_edge" not in PHASE5_STAGING_TABLES
    fill = inspect.getsource(_fill_referral_edges)
    assert "referral edges reused" in fill and "TRUNCATE TABLE {mart}.pd_stg_referral_edge" in fill
    only = inspect.getsource(rebuild_referrals)
    assert "TRUNCATE TABLE {mart}.pd_provider_referral" in only and "reuse_cached=not rebuild_edges" in only
    assert "referrals_only" in inspect.getsource(run_phase5)
    args = build_parser().parse_args(["phase5", "--referrals-only", "--rebuild-edges"])
    assert args.referrals_only and args.rebuild_edges


def test_benchmark_peers_are_in_state_with_volume_and_referrals_use_either_org_key():
    import inspect

    import pytest

    from provider_directory.complete import _fill_specialty_stats, _overlay_benchmarks, benchmark_peer_where, referral_exclusion_sql
    from provider_directory.settings import BENCHMARK_MIN_VISITS

    where = benchmark_peer_where("`az_pd`", "AZ")
    assert f">= {BENCHMARK_MIN_VISITS}" in where and "pr.state = 'AZ'" in where and "pd_provider_practice pr" in where
    with pytest.raises(ValueError):
        benchmark_peer_where("`az_pd`", "az_cms")
    for fn in (_fill_specialty_stats, _overlay_benchmarks):
        src = inspect.getsource(fn)
        assert "benchmark_peer_where" in src and "AND {peers}" in src
    excluded = referral_exclusion_sql("me", "peer")
    assert "peer.billing_organization_id = me.billing_organization_id" in excluded
    assert "ANESTHE" in excluded


def test_peer_specialty_prefixes_role_for_generic_descriptions():
    import inspect

    from provider_directory.complete import _rank_referrals, peer_specialty_sql

    sql = peer_specialty_sql("peer")
    assert "'MEDICAL', 'SURGICAL'" in sql and "CONCAT(" in sql and "specialty_classification" in sql
    assert "peer_specialty_sql" in inspect.getsource(_rank_referrals)
