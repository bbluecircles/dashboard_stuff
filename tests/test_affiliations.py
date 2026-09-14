import inspect

from provider_directory.affiliations import _affiliation_name_ok_sql, rebuild_org_lists
from provider_directory.analytics import rebuild_analytics
from provider_directory.locations import hospital_facility_name_sql, hospital_system_name_sql
from provider_directory.lookup import fetch_group_hospital_affiliations, get_group_practice, list_group_practices
from provider_directory.models import (
    GroupPracticeProfile,
    HospitalAffiliation,
    ProviderGroupPractice,
    ProviderSpine,
)
from provider_directory.schema import TABLES, ddl_statements
from provider_directory.settings import MAX_HOSPITAL_AFFILIATIONS, MIN_HOSPITAL_AFFILIATION_SHARE_PCT


def test_schema_has_affiliation_mart_tables():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_provider_group_practice" in TABLES
    assert "pd_provider_hospital_affiliation" in TABLES
    assert "pd_provider_group_practice" in sql
    assert "pd_provider_hospital_affiliation" in sql
    assert "hospital_system_name VARCHAR(180) NOT NULL" in sql
    assert "billing_type CHAR(1)" in sql


def test_hospital_system_sql_drops_unknown():
    sql = hospital_system_name_sql()
    assert "sl_hospital_system_name" in sql
    assert "provider_facility_npi_hospital_system_name" in sql
    assert "'UNKNOWN'" in sql
    fac = hospital_facility_name_sql()
    assert "npi_type = '2'" in fac
    assert "sl_hospital_system_name" not in fac


def test_org_list_sql_uses_affiliation_tables_not_pat_dt():
    source = inspect.getsource(rebuild_org_lists)
    names = inspect.getsource(_affiliation_name_ok_sql)
    assert "physician_affiliation_p" in source
    assert "physician_affiliation_i" in source
    assert "max_period_code >=" in source
    assert "UNKNOWN GROUP PRACTICE" in names
    assert "GROUP BY x.npi, UPPER(x.hospital_system_name)" in source
    assert "dedupe_rk" in source
    assert "pd_stg_npi_sl" in source
    assert ".pat_dt" not in source
    assert "affiliation_rank <=" in source
    analytics = inspect.getsource(rebuild_analytics)
    assert "rebuild_org_lists" in analytics


def test_group_dump_stays_slim_profile_joins_systems():
    dump = inspect.getsource(list_group_practices)
    assert "pd_provider_hospital_affiliation" not in dump
    assert "hospital_affiliations" not in dump
    profile = inspect.getsource(get_group_practice)
    assert "hospital_affiliations" in profile
    assert "fetch_group_hospital_affiliations" in profile
    group_sql = inspect.getsource(fetch_group_hospital_affiliations)
    assert "p.primary_organization_id = %s" in group_sql
    assert "GROUP BY UPPER(h.hospital_system_name)" in group_sql
    assert ".pat_dt" not in group_sql


def test_constants_match_meeting_caps():
    assert MAX_HOSPITAL_AFFILIATIONS == 5
    assert MIN_HOSPITAL_AFFILIATION_SHARE_PCT == 2.0


def test_nested_affiliation_models_roundtrip():
    spine = ProviderSpine(
        npi=1952863797,
        last_name="Smith",
        group_practices=[
            ProviderGroupPractice(
                npi=1952863797,
                org_rank=1,
                organization_id=1234567893,
                organization_name="Mayo Clinic Arizona",
                billing_type="P",
                is_primary=True,
            )
        ],
        hospital_affiliations=[
            HospitalAffiliation(
                npi=1952863797,
                affiliation_rank=1,
                hospital_system_name="Mayo Clinic",
                facility_name="Mayo Clinic Hospital",
                visits_at_system=6,
                visit_share_pct=100.0,
            )
        ],
    )
    dumped = spine.model_dump()
    assert dumped["group_practices"][0]["is_primary"] is True
    assert dumped["hospital_affiliations"][0]["hospital_system_name"] == "Mayo Clinic"
    profile = GroupPracticeProfile(
        organization_id=1234567893,
        organization_name="Mayo Clinic Arizona",
        visits_total=4000,
        hospital_affiliations=[
            HospitalAffiliation(
                affiliation_rank=1,
                hospital_system_name="Mayo Clinic",
                visits_at_system=3500,
                visit_share_pct=87.5,
                provider_count=12,
            )
        ],
    )
    assert profile.hospital_affiliations[0].provider_count == 12
    assert "practices" not in profile.model_dump()
