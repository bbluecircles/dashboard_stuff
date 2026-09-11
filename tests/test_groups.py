import inspect

import pytest

from provider_directory.lookup import list_group_practices, list_providers
from provider_directory.models import GroupPracticeDumpList, GroupPracticeDumpRow


def test_group_dump_sql_rolls_up_org_id_not_pat_dt():
    source = inspect.getsource(list_group_practices)
    assert "GROUP BY p.primary_organization_id" in source
    assert "p.primary_organization_id IS NOT NULL" in source
    assert ".pat_dt" not in source
    assert "SUM(IFNULL(p.visits_total, 0))" in source
    assert "COUNT(*) AS provider_count" in source
    assert "visits_are_summed_across_npis=True" in source


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
