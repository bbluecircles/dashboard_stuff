from provider_directory.cli import build_parser
from provider_directory.models import ProviderSpine


def test_cli_phase2_command():
    args = build_parser().parse_args(["phase2"])
    assert args.cmd == "phase2"


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
    assert ProviderSpine.model_validate(dumped).last_name == "Smith"
