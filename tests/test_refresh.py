from provider_directory.cli import build_parser
from provider_directory.schema import MART_INDEXES, TABLES, ddl_statements
from provider_directory.window import (
    add_months,
    iter_period_codes,
    prior_window,
    slide_diff,
    usable_window,
)


def test_add_months_wraps_year():
    assert add_months(202412, 1) == 202501
    assert add_months(202501, -1) == 202412
    assert add_months(202308, 12) == 202408


def test_usable_window_applies_two_month_lag():
    assert usable_window(202409, lag_months=2, length=12) == (202308, 202407)
    assert usable_window(202410, lag_months=2, length=12) == (202309, 202408)


def test_slide_diff_one_month_forward():
    drop, add = slide_diff(202308, 202407, 202309, 202408)
    assert drop == [202308]
    assert add == [202408]
    assert prior_window(202309, 202408) == (202209, 202308)
    assert iter_period_codes(202407, 202408) == [202407, 202408]


def test_schema_has_refresh_state_and_period_indexes():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_refresh_state" in TABLES
    assert "pd_refresh_state" in sql
    assert "KEY idx_period (period_code)" in sql
    assert "KEY idx_active_visits (active_provider, visits_total)" in sql
    assert "KEY idx_spec_visits (primary_specialty_code, visits_total)" in sql
    names = {f"{table}.{index}" for table, index, _cols in MART_INDEXES}
    assert "pd_stg_window_claim.idx_period" in names
    assert "pd_provider.idx_active_visits" in names
    assert all(not table.startswith("az.") for table, _i, _c in MART_INDEXES)


def test_cli_phase6_flags():
    args = build_parser().parse_args(["phase6"])
    assert args.cmd == "phase6"
    assert args.slide is False
    args = build_parser().parse_args(["phase6", "--slide", "--skip-staging-indexes"])
    assert args.slide is True
    assert args.skip_staging_indexes is True


def test_pipeline_exports_phase6():
    from provider_directory.activity import slide_activity
    from provider_directory.pipeline import run_phase6
    from provider_directory.refresh import rebuild_refresh, resolve_window

    assert callable(run_phase6)
    assert callable(rebuild_refresh)
    assert callable(slide_activity)
    assert callable(resolve_window)


def test_slide_activity_keeps_window_claim():
    import inspect

    from provider_directory.activity import rebuild_activity, slide_activity

    slide_src = inspect.getsource(slide_activity)
    assert "drop_phase2_derived" in slide_src
    assert "drop_staging_tables(conn, mart_db)" not in slide_src
    full_src = inspect.getsource(rebuild_activity)
    assert "drop_staging_tables(conn, mart_db)" in full_src


def test_phase6_does_not_alter_claims_db():
    from provider_directory.refresh import rebuild_refresh
    from provider_directory.schema import ensure_indexes

    source = open(rebuild_refresh.__code__.co_filename, encoding="utf-8").read()
    assert "ALTER TABLE" not in source
    idx_source = open(ensure_indexes.__code__.co_filename, encoding="utf-8").read()
    assert "az.pat_dt" not in idx_source
    assert "ADD INDEX" in idx_source
