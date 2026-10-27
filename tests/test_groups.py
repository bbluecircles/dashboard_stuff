import inspect

import pytest

from provider_directory.lookup import (
    group_member_where,
    group_scope,
    group_scored_sql,
    list_group_practices,
    list_providers,
)
from provider_directory.models import GroupPracticeDumpList, GroupPracticeDumpRow


def test_group_dump_sql_rolls_up_org_id_not_pat_dt():
    sql = group_scored_sql("az_pd", "all")
    assert "GROUP BY p.primary_organization_id" in sql
    assert "p.primary_organization_id IS NOT NULL" in sql
    assert ".pat_dt" not in sql
    assert "SUM(IFNULL(p.visits_total, 0))" in sql
    assert "COUNT(*) AS provider_count" in sql
    assert "activity_percentile" in sql
    assert "visits_per_provider" in sql
    assert "ROW_NUMBER() OVER" in sql
    assert "pd_provider_hospital_affiliation" not in sql
    source = inspect.getsource(list_group_practices)
    assert "visits_are_summed_across_npis=True" in source
    assert ".pat_dt" not in source


def test_group_dump_serves_from_summary_table_with_live_fallback():
    source = inspect.getsource(list_group_practices)
    assert "group_summary_ready(conn, mart_db)" in source
    assert "GROUP_SUMMARY_TABLE" in source
    assert "s.member_scope = %s" in source
    assert "group_scored_sql(mart_db, scope)" in source
    assert "ORDER BY visits_total DESC, provider_count DESC, organization_name, organization_id" in source


def test_group_scope_matches_active_filter():
    assert group_scope(None) == "all"
    assert group_scope(True) == "active"
    assert group_scope(False) == "inactive"
    assert "active_provider" not in group_member_where("all")
    assert "p.active_provider = 1" in group_member_where("active")
    assert "p.active_provider = 0" in group_member_where("inactive")
    with pytest.raises(ValueError):
        group_member_where("everyone")


def test_group_dump_rejects_min_above_max():
    with pytest.raises(ValueError, match="min_visits"):
        list_group_practices(object(), min_visits=10, max_visits=5)


def test_list_providers_sql_filters_organization_id():
    source = inspect.getsource(list_providers)
    assert "organization_id" in source
    from provider_directory.lookup import _provider_filter_clauses

    clauses, params = _provider_filter_clauses(prefix="p.", organization_id=1234567893)
    assert any("primary_organization_id = %s" in c for c in clauses)
    assert params == [1234567893]


def test_group_dump_list_flag():
    body = GroupPracticeDumpList(
        state="AZ",
        mart_db="az_pd",
        items=[GroupPracticeDumpRow(organization_id=1, visits_total=10)],
        total=1,
        limit=50,
        offset=0,
    )
    assert body.visits_are_summed_across_npis is True
    assert body.items[0].organization_id == 1
