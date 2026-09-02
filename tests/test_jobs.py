from contextlib import contextmanager
from pathlib import Path
import threading
import time

from provider_directory.jobs import PHASES, JobConflict, JobRunner
from provider_directory.locations import Phase2Required


@contextmanager
def fake_connect(*, autocommit: bool = False):
    yield object()


def _ok(conn, **kwargs):
    return {"ok": True, **kwargs}


def _runner(tmp_path: Path, **overrides) -> JobRunner:
    funcs = {phase: _ok for phase in PHASES}
    funcs.update(overrides)
    return JobRunner(store_path=tmp_path / "api_jobs.json", phase_funcs=funcs, connect=fake_connect)


def _wait(runner: JobRunner, job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = runner.get(job_id)
        assert job is not None
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError(runner.get(job_id))


def test_job_succeeds_and_persists(tmp_path: Path):
    runner = _runner(tmp_path)
    job = runner.start("phase6", {"slide": False})
    done = _wait(runner, job["id"])
    assert done["status"] == "succeeded"
    assert done["result"]["ok"] is True
    assert done["result"]["slide"] is False
    reloaded = JobRunner(store_path=tmp_path / "api_jobs.json", phase_funcs={p: _ok for p in PHASES}, connect=fake_connect)
    assert reloaded.get(job["id"])["status"] == "succeeded"


def test_one_job_at_a_time(tmp_path: Path):
    started = threading.Event()
    release = threading.Event()

    def slow(conn, **kwargs):
        started.set()
        release.wait(timeout=2)
        return {"ok": True}

    runner = _runner(tmp_path, phase2=slow)
    first = runner.start("phase2")
    assert started.wait(timeout=2)
    try:
        runner.start("phase3")
        raise AssertionError("expected JobConflict")
    except JobConflict as exc:
        assert first["id"] in str(exc)
    release.set()
    assert _wait(runner, first["id"])["status"] == "succeeded"


def test_interrupted_running_job_is_failed_on_load(tmp_path: Path):
    store = tmp_path / "api_jobs.json"
    store.write_text(
        '{"jobs": [{"id": "abc", "phase": "phase2", "status": "running", "params": {}, "result": null, "error": null}]}',
        encoding="utf-8",
    )
    runner = JobRunner(store_path=store, phase_funcs={p: _ok for p in PHASES}, connect=fake_connect)
    job = runner.get("abc")
    assert job["status"] == "failed"
    assert "restarted" in job["error"]


def test_phase2_required_becomes_failed(tmp_path: Path):
    def boom(conn, **kwargs):
        raise Phase2Required("run phase2 first")

    runner = _runner(tmp_path, phase3=boom)
    job = runner.start("phase3")
    done = _wait(runner, job["id"])
    assert done["status"] == "failed"
    assert "phase2" in done["error"]
