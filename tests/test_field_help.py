from fastapi.testclient import TestClient

from provider_directory import models
from provider_directory.api import create_app, db_conn, dev_help_allowed
from provider_directory.cli import build_parser
from provider_directory.field_help import (
    SCOPES,
    catalog,
    catalog_markdown,
    format_period_range,
    render_catalog,
)
from provider_directory.jobs import PHASES, JobRunner
from tests.test_api import _ok, fake_connect


SCOPE_MODELS = {
    "provider": (models.ProviderSpine, models.ProviderDumpRow),
    "group": (models.GroupPracticeProfile, models.GroupPracticeDumpRow),
    "practice": (models.ProviderPractice,),
    "group_practice": (models.ProviderGroupPractice,),
    "hospital_affiliation": (models.HospitalAffiliation,),
    "referral": (models.ProviderReferral,),
    "utilization": (models.ProviderUtilization,),
}


def test_every_api_field_has_help_in_both_tiers():
    cat = catalog(dev=True)
    assert set(cat) == set(SCOPES) == set(SCOPE_MODELS)
    for scope, model_types in SCOPE_MODELS.items():
        for model in model_types:
            missing = [f for f in model.model_fields if f not in cat[scope]]
            assert not missing, f"{scope}: {missing}"
        for key, item in cat[scope].items():
            assert item["label"].strip(), (scope, key)
            assert len(item["help"].strip()) > 15, (scope, key)
            assert len(item["dev"].strip()) > 15, (scope, key)


def test_client_tier_omits_dev_and_window_resolves():
    plain = catalog(dev=False)
    assert all("dev" not in item for fields in plain.values() for item in fields.values())
    rendered = render_catalog(dev=True, window_start=202411, window_end=202510,
                              prior_window_start=202311, prior_window_end=202410)
    assert rendered["window"] == "Nov 2024 to Oct 2025"
    assert format_period_range(202308, 202407) == "Aug 2023 to Jul 2024"
    visits = rendered["scopes"]["provider"]["visits_total"]
    assert "Nov 2024 to Oct 2025" in visits["help"]
    assert "{window}" not in visits["help"]
    prior = rendered["scopes"]["provider"]["wrvu_prior_year_total"]
    assert "Nov 2023 to Oct 2024" in prior["help"]
    md = catalog_markdown(rendered)
    assert "## provider" in md and "| Developer |" in md


def test_group_scope_overrides_summed_meaning():
    cat = catalog(dev=True)
    assert "summed" in cat["group"]["visits_total"]["help"].lower() or "Sum" in cat["group"]["visits_total"]["help"]
    assert "pd_group_practice" in cat["group"]["visits_total"]["dev"]
    assert "weighted" in cat["group"]["panel_percent_female"]["help"].lower()
    assert "pd_stg_visit" in cat["provider"]["visits_total"]["dev"]


def test_dev_gate():
    assert dev_help_allowed(False, "anything") is False
    import os
    os.environ.pop("PD_DEV_HELP_KEY", None)
    assert dev_help_allowed(True, None) is True
    os.environ["PD_DEV_HELP_KEY"] = "letmein"
    try:
        assert dev_help_allowed(True, None) is False
        assert dev_help_allowed(True, "wrong") is False
        assert dev_help_allowed(True, "letmein") is True
    finally:
        os.environ.pop("PD_DEV_HELP_KEY", None)


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PD_API_KEY", "")
    runner = JobRunner(store_path=tmp_path / "api_jobs.json", phase_funcs={p: _ok for p in PHASES}, connect=fake_connect)
    app = create_app(runner=runner)

    def override_db():
        yield object()

    app.dependency_overrides[db_conn] = override_db
    return TestClient(app)


def test_field_help_route(tmp_path, monkeypatch):
    monkeypatch.setattr("provider_directory.api.resolve_window", lambda conn, mart_db: (202411, 202510, 202311, 202410))
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/field-help?state=AZ")
    assert res.status_code == 200
    body = res.json()
    assert body["dev"] is False and body["state"] == "AZ" and body["mart_db"] == "az_pd"
    assert body["window"] == "Nov 2024 to Oct 2025"
    assert "dev" not in body["scopes"]["provider"]["visits_total"]
    assert body["scopes"]["practice"]["visit_share_pct"]["label"] == "Visit share"

    monkeypatch.setenv("PD_DEV_HELP_KEY", "letmein")
    assert client.get("/v1/field-help?state=AZ&dev=true").status_code == 403
    res = client.get("/v1/field-help?state=AZ&dev=true", headers={"X-Dev-Key": "letmein"})
    assert res.status_code == 200
    assert "pd_stg_visit" in res.json()["scopes"]["provider"]["visits_total"]["dev"]


def test_cli_field_help_flags():
    args = build_parser().parse_args(["field-help", "--state", "AZ_CMS", "--dev", "--format", "json", "--no-db"])
    assert args.cmd == "field-help" and args.dev and args.format == "json" and args.no_db
