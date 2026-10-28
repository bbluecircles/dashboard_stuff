from fastapi.testclient import TestClient

from provider_directory import models
from provider_directory.api import create_app, db_conn, dev_help_allowed
from provider_directory.cli import build_parser
from provider_directory.field_help import (
    SCOPES,
    TABLE_REGISTRY,
    catalog,
    catalog_markdown,
    describe_table,
    format_period_range,
    render_catalog,
    tables_in,
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
    res = client.get("/v1/field-help?state=AZ_CMS&dev=true", headers={"X-Dev-Key": "letmein"})
    assert res.status_code == 200
    body = res.json()
    visits = body["scopes"]["provider"]["visits_total"]
    assert "pd_stg_visit" in visits["dev"]
    assert visits["tables"][0]["name"] == "pd_stg_visit" and visits["tables"][0]["kind"] == "mart"
    mips = body["scopes"]["provider"]["mips_final_score"]["tables"][0]
    assert mips["external"] is True and mips["url"].endswith("/a174-a962")
    assert any(t["name"] == "az_cms.pat_dt" for t in body["tables"])


def test_cli_field_help_flags():
    args = build_parser().parse_args(["field-help", "--state", "AZ_CMS", "--dev", "--format", "json", "--no-db"])
    assert args.cmd == "field-help" and args.dev and args.format == "json" and args.no_db


# Fields whose value is a constant or a pure derivation with no table of its own.
NO_TABLE_FIELDS = {("group", "visits_are_summed_across_npis")}


def test_every_dev_entry_names_registered_tables():
    cat = catalog(dev=True)
    for scope, fields in cat.items():
        for key, item in fields.items():
            if (scope, key) in NO_TABLE_FIELDS:
                continue
            assert item["tables"], (scope, key)
            for table in item["tables"]:
                assert table in TABLE_REGISTRY, (scope, key, table)
    assert "dev" not in catalog(dev=False)["provider"]["visits_total"]
    assert "tables" not in catalog(dev=False)["provider"]["visits_total"]


def test_tables_in_parses_prefixes_wildcards_and_rejects_unknown():
    found = tables_in("az_cms.pat_dt joined to azal.procd on procd_code; pd_stg_top_* visit_count / pd_provider.visits_total; cms_pdc_clinician.gndr")
    assert found == ["claims.pat_dt", "lookup.procd", "pd_stg_top_dx", "pd_stg_top_px", "pd_provider", "cms_pdc_clinician"]
    import pytest
    with pytest.raises(KeyError):
        tables_in("reads pd_stg_made_up")
    with pytest.raises(KeyError):
        tables_in("reads az.not_a_table")


def test_describe_table_marks_external_with_cms_links():
    pdc = describe_table("cms_pdc_clinician")
    assert pdc["external"] is True and pdc["kind"] == "cms"
    assert pdc["url"] == "https://data.cms.gov/provider-data/dataset/mj5m-pzi6"
    assert pdc["name"] == "cms_pdc_clinician" and "National Downloadable" in pdc["source"]
    assert describe_table("cms_nppes_type1")["url"].startswith("https://download.cms.gov/nppes/")
    assert describe_table("cms_pdc_mips")["url"].endswith("/a174-a962")
    assert describe_table("cms_pdc_utilization")["url"].endswith("/n0yb-util")
    wh = describe_table("claims.pat_dt", claims_db="az_cms", lookup_db="azal")
    assert wh == {"key": "claims.pat_dt", "name": "az_cms.pat_dt", "kind": "warehouse", "external": False,
                  "phase": "warehouse load, outside this pipeline", "description": TABLE_REGISTRY["claims.pat_dt"]["description"]}
    assert describe_table("lookup.procd")["name"] == "azal.procd"
    mart = describe_table("pd_stg_visit")
    assert mart["kind"] == "mart" and mart["external"] is False and "url" not in mart


def test_rendered_dev_entries_carry_tables_and_markdown_marks_external():
    rendered = render_catalog(dev=True, window_start=202411, window_end=202510,
                              prior_window_start=202311, prior_window_end=202410,
                              claims_db="az_cms", lookup_db="azal")
    rvu = rendered["scopes"]["provider"]["wrvu_total"]
    names = [t["name"] for t in rvu["tables"]]
    assert "azal.procd" in names and "pd_stg_visit" in names and "pd_stg_npi_wrvu" in names
    gender = rendered["scopes"]["provider"]["gender"]
    ext = [t for t in gender["tables"] if t["external"]]
    assert {t["name"] for t in ext} == {"cms_pdc_clinician", "cms_nppes_type1"}
    assert all(t["url"].startswith("https://") for t in ext)
    assert any(t["key"] == "claims.pat_dt" and t["name"] == "az_cms.pat_dt" for t in rendered["tables"])
    md = catalog_markdown(rendered)
    assert "| Sources |" in md
    assert "[`cms_pdc_clinician`](https://data.cms.gov/provider-data/dataset/mj5m-pzi6)*" in md
    assert "## Tables" in md
    plain = render_catalog(dev=False, window_start=202411, window_end=202510)
    assert "tables" not in plain and "tables" not in plain["scopes"]["provider"]["gender"]


def test_lineage_pulls_upstream_tables_through_the_chain():
    rendered = render_catalog(dev=True, window_start=202411, window_end=202510,
                              prior_window_start=202311, prior_window_end=202410,
                              claims_db="az_cms", lookup_db="azal")
    grp = rendered["scopes"]["group"]["wrvu_total"]
    assert [r["ref"] for r in grp["derived_from"]] == ["provider.wrvu_total"]
    assert grp["derived_from"][0]["label"] == "RVU total"
    own = [t["name"] for t in grp["tables"] if not t["upstream"]]
    up = [t["name"] for t in grp["tables"] if t["upstream"]]
    assert own == ["pd_provider", "pd_group_practice"]
    assert "azal.procd" in up and "pd_stg_visit" in up and "pd_stg_npi_wrvu" in up
    assert "pd_provider" not in up
    act = rendered["scopes"]["provider"]["activity_specialty_percentile"]
    assert {r["ref"] for r in act["derived_from"]} == {"provider.visits_specialty_percentile", "provider.wrvu_specialty_percentile"}
    share = rendered["scopes"]["practice"]["visit_share_pct"]
    assert {r["ref"] for r in share["derived_from"]} == {"practice.visits_at_site", "provider.visits_total"}
    assert all("phase" in t for t in grp["tables"])
    assert rendered["tables"][0]["phase"]


def test_caveats_cover_the_fields_clients_ask_about():
    cat = catalog(dev=True)
    must = [("provider", "wrvu_total"), ("group", "wrvu_total"), ("group", "visits_total"), ("provider", "estimated_age"),
            ("provider", "in_system_provider"), ("referral", "patient_count"), ("provider", "visits_percent_medicaid"),
            ("provider", "activity_specialty_percentile"), ("practice", "visit_share_pct"), ("provider", "visits_top_diagnosis_1_percent"),
            ("group", "panel_percent_female"), ("provider", "visits_percent_monday"), ("provider", "visits_percent_office")]
    for scope, key in must:
        assert cat[scope][key]["caveats"], (scope, key)
    assert any("twice" in c for c in cat["group"]["wrvu_total"]["caveats"])
    assert any("Weighted" in c for c in cat["group"]["panel_percent_female"]["caveats"])
    for scope, fields in cat.items():
        for key, item in fields.items():
            assert all(c.strip() for c in item["caveats"]), (scope, key)
            assert isinstance(item["derived_from"], list)
    plain = catalog(dev=False)
    assert "caveats" not in plain["provider"]["wrvu_total"] and "derived_from" not in plain["provider"]["wrvu_total"]


def test_markdown_shows_built_on_notes_and_upstream():
    rendered = render_catalog(dev=True, window_start=202411, window_end=202510)
    md = catalog_markdown(rendered)
    assert "Built on: `provider.wrvu_total`" in md
    assert "Notes: An encounter billed by two members of the group counts twice." in md
    assert "(upstream:" in md
    assert "| Refreshed by |" in md
