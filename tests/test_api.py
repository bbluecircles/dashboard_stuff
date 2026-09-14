from contextlib import contextmanager

from fastapi.testclient import TestClient

from provider_directory.api import create_app, db_conn
from provider_directory.jobs import PHASES, JobRunner
from provider_directory.models import ProviderDumpList, ProviderDumpRow, ProviderSpine


@contextmanager
def fake_connect(*, autocommit: bool = False):
    yield object()


def _ok(conn, **kwargs):
    return {"ok": True, **kwargs}


def _client(tmp_path, monkeypatch, **phase_overrides) -> TestClient:
    monkeypatch.setenv("PD_API_KEY", "")
    funcs = {phase: _ok for phase in PHASES}
    funcs.update(phase_overrides)
    runner = JobRunner(store_path=tmp_path / "api_jobs.json", phase_funcs=funcs, connect=fake_connect)
    app = create_app(runner=runner)

    def override_db():
        yield object()

    app.dependency_overrides[db_conn] = override_db
    return TestClient(app)


def test_health_and_cli_serve():
    from provider_directory.cli import build_parser

    args = build_parser().parse_args(["serve", "--host", "127.0.0.1", "--port", "8080"])
    assert args.cmd == "serve"
    assert args.port == 8080


def test_health_open(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["service"] == "provider-directory"


def test_api_key_required(tmp_path, monkeypatch):
    monkeypatch.setenv("PD_API_KEY", "secret-key")
    funcs = {phase: _ok for phase in PHASES}
    runner = JobRunner(store_path=tmp_path / "api_jobs.json", phase_funcs=funcs, connect=fake_connect)
    app = create_app(runner=runner)

    def override_db():
        yield object()

    app.dependency_overrides[db_conn] = override_db
    client = TestClient(app)
    assert client.get("/v1/providers/1952863797").status_code == 401
    assert client.get("/v1/providers/1952863797", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_get_and_search_providers(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    row = ProviderSpine(npi=1952863797, last_name="Smith", visits_total=6)

    def fake_get(conn, npi, **kwargs):
        return row if npi == 1952863797 else None

    def fake_search(conn, **kwargs):
        assert kwargs["last_name"] == "Smith"
        assert kwargs["offset"] == 0
        assert kwargs["state"] == "AZ"
        assert kwargs["mart_db"] == "az_pd"
        return ProviderDumpList(
            state="AZ",
            mart_db="az_pd",
            items=[ProviderDumpRow(npi=1952863797, last_name="Smith", visits_total=6)],
            total=1,
            limit=50,
            offset=0,
        )

    monkeypatch.setattr("provider_directory.api.get_provider", fake_get)
    monkeypatch.setattr("provider_directory.api.list_providers", fake_search)

    missing = client.get("/v1/providers/1234567893")
    assert missing.status_code == 404
    found = client.get("/v1/providers/1952863797")
    assert found.status_code == 200
    assert found.json()["last_name"] == "Smith"
    assert found.json()["practices"] == []
    assert found.json()["group_practices"] == []
    assert found.json()["hospital_affiliations"] == []
    listed = client.get("/v1/providers", params={"state": "AZ", "last_name": "Smith", "active": True})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["state"] == "AZ"
    assert "practices" not in listed.json()["items"][0]
    assert client.get("/v1/providers", params={"state": "Arizona"}).status_code == 422

    def fake_filtered(conn, **kwargs):
        assert kwargs["organization"] == "Mayo"
        assert kwargs["city"] == "Phoenix"
        assert kwargs["min_visits"] == 1
        assert kwargs["max_visits"] == 20
        return ProviderDumpList(state="AZ", mart_db="az_pd", items=[], total=0, limit=50, offset=0)

    monkeypatch.setattr("provider_directory.api.list_providers", fake_filtered)
    filtered = client.get(
        "/v1/providers",
        params={
            "state": "AZ",
            "organization": "Mayo",
            "city": "Phoenix",
            "min_visits": 1,
            "max_visits": 20,
        },
    )
    assert filtered.status_code == 200
    bad_range = client.get("/v1/providers", params={"min_visits": 50, "max_visits": 10})
    assert bad_range.status_code == 422

    def fake_org_id(conn, **kwargs):
        assert kwargs["organization_id"] == 1234567893
        return ProviderDumpList(state="AZ", mart_db="az_pd", items=[], total=0, limit=50, offset=0)

    monkeypatch.setattr("provider_directory.api.list_providers", fake_org_id)
    by_org = client.get("/v1/providers", params={"state": "AZ", "organization_id": 1234567893})
    assert by_org.status_code == 200


def test_group_practice_dump(tmp_path, monkeypatch):
    from provider_directory.models import GroupPracticeDumpList, GroupPracticeDumpRow

    client = _client(tmp_path, monkeypatch)

    def fake_groups(conn, **kwargs):
        assert kwargs["state"] == "AZ"
        assert kwargs["mart_db"] == "az_pd"
        assert kwargs["organization"] == "Mayo"
        assert kwargs["min_visits"] == 1
        return GroupPracticeDumpList(
            state="AZ",
            mart_db="az_pd",
            items=[
                GroupPracticeDumpRow(
                    organization_id=1234567893,
                    organization_name="Mayo Clinic Arizona",
                    provider_count=12,
                    visits_total=4000,
                )
            ],
            total=1,
            limit=50,
            offset=0,
        )

    def fake_one(conn, organization_id, **kwargs):
        if organization_id != 1234567893:
            return None
        from provider_directory.models import GroupPracticeProfile

        return GroupPracticeProfile(
            organization_id=1234567893,
            organization_name="Mayo Clinic Arizona",
            provider_count=12,
            visits_total=4000,
            hospital_affiliations=[],
        )

    monkeypatch.setattr("provider_directory.api.list_group_practices", fake_groups)
    monkeypatch.setattr("provider_directory.api.get_group_practice", fake_one)
    listed = client.get(
        "/v1/group-practices",
        params={"state": "AZ", "organization": "Mayo", "min_visits": 1},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["visits_are_summed_across_npis"] is True
    assert body["items"][0]["organization_id"] == 1234567893
    assert "npi" not in body["items"][0]
    assert "hospital_affiliations" not in body["items"][0]
    found = client.get("/v1/group-practices/1234567893", params={"state": "AZ"})
    assert found.status_code == 200
    assert found.json()["provider_count"] == 12
    assert found.json()["hospital_affiliations"] == []
    missing = client.get("/v1/group-practices/1111111111", params={"state": "AZ"})
    assert missing.status_code == 404
    bad = client.get("/v1/group-practices", params={"min_visits": 50, "max_visits": 10})
    assert bad.status_code == 422


def test_mart_status(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "provider_directory.api.resolve_window",
        lambda conn, mart_db="az_pd": (202308, 202407, 202208, 202307),
    )
    monkeypatch.setattr(
        "provider_directory.api.read_refresh_state",
        lambda conn, mart_db="az_pd": {
            "slide_available": 0,
            "last_action": "indexes",
            "warehouse_max_period": 202409,
            "warehouse_source": "az.period.period_code",
        },
    )
    monkeypatch.setattr(
        "provider_directory.api.warehouse_max_period",
        lambda conn, claims_db="az": (202409, "az.period.period_code"),
    )
    res = client.get("/v1/mart")
    assert res.status_code == 200
    body = res.json()
    assert body["window_end"] == 202407
    assert body["get_reads_mart_only"] is True
    assert body["warehouse_max_period"] == 202409
    assert body["state"] == "AZ"
    assert body["mart_db"] == "az_pd"


def test_phase_job_202_and_unknown_phase(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post("/v1/jobs/phase6", json={"slide": False})
    assert res.status_code == 202
    job_id = res.json()["id"]
    assert res.headers["location"] == f"/v1/jobs/{job_id}"
    assert client.get(f"/v1/jobs/{job_id}").status_code == 200
    assert client.post("/v1/jobs/phase9").status_code == 404
