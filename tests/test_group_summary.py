import inspect

from provider_directory.activity import rebuild_activity, slide_activity
from provider_directory.analytics import rebuild_analytics
from provider_directory.group_summary import (
    GROUP_SUMMARY_COLUMNS,
    group_summary_insert_sql,
    rebuild_group_summary,
)
from provider_directory.lookup import GROUP_SCOPES
from provider_directory.mart import overlay_cms
from provider_directory.refresh import rebuild_refresh
from provider_directory.schema import TABLES, ddl_statements


def test_schema_has_group_summary_table_and_indexes():
    assert "pd_group_practice" in TABLES
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_group_practice" in sql
    assert "PRIMARY KEY (member_scope, organization_id)" in sql
    for idx in ("idx_scope_visits", "idx_scope_name", "idx_scope_parent", "idx_scope_providers"):
        assert idx in sql
    for col in GROUP_SUMMARY_COLUMNS:
        assert col in sql


def test_group_summary_insert_fills_each_scope_from_pd_provider_only():
    for scope in GROUP_SCOPES:
        sql = group_summary_insert_sql("az_pd", scope)
        assert "INSERT INTO `az_pd`.pd_group_practice (member_scope," in sql
        assert f"SELECT '{scope}'" in sql
        assert "`az_pd`.pd_provider p" in sql
        assert ".pat_dt" not in sql
        assert "GROUP BY p.primary_organization_id" in sql
    assert "IS NOT NULL AND p.active_provider = 1" in group_summary_insert_sql("az_pd", "active")
    assert "IS NOT NULL AND p.active_provider = 1" not in group_summary_insert_sql("az_pd", "all")
    assert "(p.active_provider = 0 OR p.active_provider IS NULL)" in group_summary_insert_sql("az_pd", "inactive")


def test_rebuild_truncates_then_fills_all_scopes():
    source = inspect.getsource(rebuild_group_summary)
    assert "TRUNCATE TABLE" in source
    assert "for scope in GROUP_SCOPES" in source
    assert "conn.commit()" in source


def test_phases_rebuild_group_summary():
    for fn in (rebuild_activity, slide_activity, rebuild_analytics, overlay_cms, rebuild_refresh):
        assert "rebuild_group_summary(conn, mart_db)" in inspect.getsource(fn), fn.__name__
