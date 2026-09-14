from provider_directory.cli import build_parser
from provider_directory.lookup import _null_zero_open_payments
from provider_directory.models import ProviderDumpRow, ProviderPractice, ProviderSpine, GroupPracticeDumpRow


def test_cli_get_active_and_min_visits():
    args = build_parser().parse_args(
        ["get", "--last-name", "Smith", "--specialty", "Cardiovascular", "--active", "--min-visits", "50", "--limit", "5"]
    )
    assert args.active is True
    assert args.min_visits == 50
    assert args.limit == 5
    args = build_parser().parse_args(["get", "--last-name", "Smith", "--in-system"])
    assert args.in_system is True
    tx = build_parser().parse_args(["get", "--state", "TX", "1609236967"])
    assert tx.state == "TX"
    assert tx.npi == 1609236967
    dump = build_parser().parse_args(
        ["get", "--organization", "Mayo", "--city", "Phoenix", "--min-visits", "1", "--max-visits", "100"]
    )
    assert dump.organization == "Mayo"
    assert dump.city == "Phoenix"
    assert dump.max_visits == 100
    groups = build_parser().parse_args(
        ["groups", "--state", "AZ", "--organization", "Mayo", "--min-visits", "1", "--limit", "25"]
    )
    assert groups.cmd == "groups"
    assert groups.organization == "Mayo"
    assert groups.min_visits == 1
    members = build_parser().parse_args(["get", "--organization-id", "1234567893", "--min-visits", "1"])
    assert members.organization_id == 1234567893


def test_dump_row_includes_gender():
    row = ProviderDumpRow(npi=1952863797, last_name="Smith", gender="M", visits_total=6)
    assert row.model_dump()["gender"] == "M"


def test_group_dump_row_roundtrip():
    row = GroupPracticeDumpRow(
        organization_id=1234567893,
        organization_name="Mayo Clinic Arizona",
        provider_count=12,
        visits_total=4000,
        wrvu_total=10.5,
    )
    dumped = row.model_dump()
    assert dumped["organization_id"] == 1234567893
    assert dumped["provider_count"] == 12
    assert dumped["visits_total"] == 4000


def test_provider_spine_model_roundtrip():
    row = ProviderSpine(
        npi=1234567893,
        first_name="Jane",
        last_name="Smith",
        gender="F",
        estimated_age=52,
        in_system_provider=None,
        primary_specialty_code="207R00000X",
    )
    dumped = row.model_dump()
    assert dumped["npi"] == 1234567893
    assert dumped["in_system_provider"] is None
    assert dumped["practices"] == []
    assert dumped["group_practices"] == []
    assert dumped["hospital_affiliations"] == []
    assert dumped["referrals"] == []
    assert dumped["utilization"] == []
    assert ProviderSpine.model_validate(dumped).last_name == "Smith"


def test_provider_practice_nested_roundtrip():
    row = ProviderSpine(
        npi=1952863797,
        last_name="Smith",
        visits_total=6,
        provider_practices_total=1,
        practices=[
            ProviderPractice(
                npi=1952863797,
                site_rank=1,
                city="Phoenix",
                state="AZ",
                visits_at_site=6,
                visit_share_pct=100.0,
                location_flag="claims_confirmed",
            )
        ],
    )
    dumped = row.model_dump()
    assert dumped["practices"][0]["site_rank"] == 1
    assert dumped["practices"][0]["needs_geocode"] is False


def test_null_zero_open_payments_hides_missing_kinds():
    row = {
        "open_payments_year": 2025,
        "open_payments_general_total": 38.28,
        "open_payments_research_total": 0.0,
        "open_payments_ownership_total": 0,
        "open_payments_count": 2,
    }
    _null_zero_open_payments(row)
    assert row["open_payments_general_total"] == 38.28
    assert row["open_payments_research_total"] is None
    assert row["open_payments_ownership_total"] is None
    assert row["open_payments_count"] == 2
