import inspect

from provider_directory.group_profile import (
    attach_group_profile,
    fetch_group_metrics,
    fetch_group_referrals,
    fetch_group_sites,
    fetch_group_top_codes,
    fetch_group_top_payers,
    weighted_pct_sql,
)
from provider_directory.lookup import fetch_top_code_percents, list_group_practices
from provider_directory.models import GroupPracticeProfile, ProviderPractice, ProviderReferral


def test_weighted_pct_sql_uses_member_visits():
    sql = weighted_pct_sql("visits_percent_office", "visits_total")
    assert "p.visits_percent_office" in sql
    assert "p.visits_total" in sql
    assert "%" not in sql.replace("%%", "")


def test_group_profile_sql_aggregates_mart_not_pat_dt():
    metrics = inspect.getsource(fetch_group_metrics)
    sites = inspect.getsource(fetch_group_sites)
    refs = inspect.getsource(fetch_group_referrals)
    codes = inspect.getsource(fetch_group_top_codes)
    payers = inspect.getsource(fetch_group_top_payers)
    attach = inspect.getsource(attach_group_profile)
    blob = "\n".join([metrics, sites, refs, codes, payers, attach])
    assert "pd_stg_visit" not in codes
    assert "pd_stg_visit" not in attach
    assert "pd_stg_top_dx" in codes
    assert "pd_stg_top_px" in attach
    assert "SUM(d.visit_count)" in codes
    assert "visit_count" in codes
    assert "_percent" in codes
    assert ".pat_dt" not in blob
    assert "primary_organization_id = %s" in metrics
    assert "pd_provider_practice" in sites
    assert "GROUP BY pr.cluster_key" in sites
    assert "pd_provider_referral" in refs
    assert "GROUP BY r.direction, r.peer_npi" in refs
    assert "pd_stg_top_dx" in attach
    assert "pd_stg_top_px" in attach
    assert "pd_stg_npi_payor" in payers
    dump = inspect.getsource(list_group_practices)
    assert "pd_provider_practice" not in dump
    assert "visits_percent_office" not in dump


def test_provider_top_code_percents_use_staging_counts():
    source = inspect.getsource(fetch_top_code_percents)
    assert "pd_stg_top_dx" in source
    assert "pd_stg_top_px" in source
    assert "visit_count" in source
    assert ".pat_dt" not in source
    assert "_percent" in source


def test_group_profile_model_shares_provider_keys():
    row = GroupPracticeProfile(
        organization_id=1234567893,
        organization_name="Mayo Clinic Arizona",
        visits_total=4000,
        visits_percent_office=40.0,
        practices=[
            ProviderPractice(
                npi=1234567893,
                site_rank=1,
                city="Phoenix",
                visits_at_site=100,
            )
        ],
        referrals=[
            ProviderReferral(
                npi=1234567893,
                direction="out",
                peer_rank=1,
                peer_npi=1952863797,
                patient_count=3,
            )
        ],
    )
    dumped = row.model_dump()
    assert dumped["visits_percent_office"] == 40.0
    assert dumped["visits_top_diagnosis_1_percent"] is None
    assert dumped["practices"][0]["city"] == "Phoenix"
    assert dumped["referrals"][0]["direction"] == "out"
    assert dumped["visits_are_summed_across_npis"] is True
    assert dumped["group_practices"] == []
    assert dumped["utilization"] == []
