import pytest
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


def test_specialty_stats_include_cohort_average_per_procedure():
    import inspect

    from provider_directory.complete import _fill_specialty_stats, _overlay_benchmarks
    from provider_directory.schema import PD_PROVIDER_PHASE5_COLUMNS, ddl_statements

    fill = inspect.getsource(_fill_specialty_stats)
    assert "ROUND(SUM(wrvu_total) / NULLIF(SUM(wrvu_procedure_count), 0), 3)" in fill
    assert "avg_wrvu_per_procedure" in fill
    assert "wrvu_state_specialty_average_per_procedure" in inspect.getsource(_overlay_benchmarks)
    assert "wrvu_state_specialty_average_per_procedure" in dict(PD_PROVIDER_PHASE5_COLUMNS)
    assert "avg_wrvu_per_procedure DECIMAL(10,3)" in "\n".join(ddl_statements("az_pd"))


def test_specialty_benchmarks_of_averages_per_procedure():
    import inspect

    from provider_directory.complete import _fill_specialty_stats, _overlay_benchmarks
    from provider_directory.schema import PD_PROVIDER_PHASE5_COLUMNS, PHASE5_STAGING_TABLES, ddl_statements

    fill = inspect.getsource(_fill_specialty_stats)
    assert "ORDER BY wrvu_average, npi" in fill and "median_wrvu_per_procedure" in fill
    over = inspect.getsource(_overlay_benchmarks)
    assert "pd_stg_npi_avg_percentile" in over and "p.wrvu_average_specialty_percentile = avgp.pct" in over
    cols = dict(PD_PROVIDER_PHASE5_COLUMNS)
    for c in ("wrvu_state_specialty_median_per_procedure", "wrvu_state_specialty_p25_per_procedure", "wrvu_state_specialty_p75_per_procedure", "wrvu_average_specialty_percentile"):
        assert c in cols, c
    assert "pd_stg_npi_avg_percentile" in PHASE5_STAGING_TABLES
    assert "median_wrvu_per_procedure DECIMAL(10,3)" in "\n".join(ddl_statements("az_pd"))


def test_claim_referral_edges_come_from_window_claims_behind_the_setting():
    import inspect
    import subprocess
    import sys

    from provider_directory.complete import _fill_claim_referral_edges, _fill_referral_edges, claim_referral_edge_sql
    from provider_directory.settings import REFERRAL_IN, REFERRAL_OUT, REFERRAL_SOURCE, REFERRAL_SOURCES

    assert REFERRAL_SOURCES == ("implied", "claims") and REFERRAL_SOURCE in REFERRAL_SOURCES
    sql_in = claim_referral_edge_sql("`az_pd`", "0, 4", REFERRAL_IN)
    sql_out = claim_referral_edge_sql("`az_pd`", "0, 4", REFERRAL_OUT)
    for sql in (sql_in, sql_out):
        assert "FROM `az_pd`.pd_stg_window_claim c" in sql
        assert "COUNT(DISTINCT c.pat_id)" in sql and "COUNT(DISTINCT c.encounter_id)" in sql
        assert "INNER JOIN `az_pd`.pd_provider refr ON refr.npi = c.referring_physician_code" in sql
        assert "c.rendering_physician_code <> c.referring_physician_code" in sql
        assert "dash_physician_referrals" not in sql and "pat_dt" not in sql
        sql % (3,)
    assert "GROUP BY c.rendering_physician_code, c.referring_physician_code" in sql_in
    assert "GROUP BY c.referring_physician_code, c.rendering_physician_code" in sql_out
    for sql in (sql_in, sql_out):
        # split by patient so distinct counts add up exactly across buckets
        assert "MOD(IFNULL(c.pat_id, 0), 4) = %s" in sql and "ON DUPLICATE KEY UPDATE" in sql
    assert "for bucket in range(CLAIM_ID_BUCKETS)" in inspect.getsource(_fill_claim_referral_edges)
    with pytest.raises(ValueError):
        claim_referral_edge_sql("`az_pd`", "0, 4", "sideways")
    fill = inspect.getsource(_fill_referral_edges)
    assert 'if source == "claims":' in fill and "_fill_claim_referral_edges" in fill
    assert "Phase2Required" in inspect.getsource(_fill_claim_referral_edges)
    bad = subprocess.run(
        [sys.executable, "-c", "import provider_directory.settings"],
        env={"PD_REFERRAL_SOURCE": "sideways", "PATH": ""},
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0 and "PD_REFERRAL_SOURCE" in bad.stderr


def test_prior_year_rvu_uses_the_same_per_clinician_floor():
    import inspect

    from provider_directory.complete import _fill_prior_wrvu
    from provider_directory.models import ProviderSpine

    src = inspect.getsource(_fill_prior_wrvu)
    assert "INNER JOIN {mart}.pd_provider p ON p.npi = l.npi" in src
    assert 'rvu_line_filter_sql("p", "pr")' in src
    assert "wrvu_filter_applied" in ProviderSpine.model_fields
