from contextlib import contextmanager

from fastapi.testclient import TestClient

from provider_directory.api import create_app, db_conn
from provider_directory.jobs import PHASES, JobRunner
from provider_directory.models import ProviderSpine, ProviderSpineList


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
        return ProviderSpineList(items=[row], total=1)

    monkeypatch.setattr("provider_directory.api.get_provider", fake_get)
    monkeypatch.setattr("provider_directory.api.search_providers", fake_search)

    missing = client.get("/v1/providers/1234567893")
    assert missing.status_code == 404
    found = client.get("/v1/providers/1952863797")
    assert found.status_code == 200
    assert found.json()["last_name"] == "Smith"
    assert found.json()["practices"] == []
    listed = client.get("/v1/providers", params={"last_name": "Smith", "active": True})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


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


def test_phase_job_202_and_unknown_phase(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post("/v1/jobs/phase6", json={"slide": False})
    assert res.status_code == 202
    job_id = res.json()["id"]
    assert res.headers["location"] == f"/v1/jobs/{job_id}"
    assert client.get(f"/v1/jobs/{job_id}").status_code == 200
    assert client.post("/v1/jobs/phase9").status_code == 404
