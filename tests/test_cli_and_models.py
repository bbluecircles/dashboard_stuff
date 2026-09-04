from provider_directory.cli import build_parser
from provider_directory.lookup import _null_zero_open_payments
from provider_directory.models import ProviderPractice, ProviderSpine


def test_cli_get_active_and_min_visits():
    args = build_parser().parse_args(
        ["get", "--last-name", "Smith", "--specialty", "Cardiovascular", "--active", "--min-visits", "50", "--limit", "5"]
    )
    assert args.active is True
    assert args.min_visits == 50
    assert args.limit == 5
    args = build_parser().parse_args(["get", "--last-name", "Smith", "--in-system"])
    assert args.in_system is True


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
