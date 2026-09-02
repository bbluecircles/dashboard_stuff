"""In-process phase jobs for the API.

NSSM runs a single uvicorn worker. Phases 2–5 can take hours, so HTTP
returns 202 and the .NET app polls. One job at a time — overlapping
rebuilds would fight over az_pd staging.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from provider_directory.db import get_connection
from provider_directory.locations import Phase2Required
from provider_directory.pipeline import run_phase1, run_phase2, run_phase3, run_phase4, run_phase5, run_phase6
from provider_directory.settings import API_JOB_STORE

PHASES = ("phase1", "phase2", "phase3", "phase4", "phase5", "phase6")
RUNNING = frozenset({"queued", "running"})
KEEP_JOBS = 20

PhaseFn = Callable[..., dict]


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _default_phase_funcs() -> dict[str, PhaseFn]:
    return {
        "phase1": run_phase1,
        "phase2": run_phase2,
        "phase3": run_phase3,
        "phase4": run_phase4,
        "phase5": run_phase5,
        "phase6": run_phase6,
    }


class JobConflict(RuntimeError):
    def __init__(self, job: dict):
        super().__init__(f"{job['id']} is still {job['status']} ({job['phase']})")
        self.job = job


class JobRunner:
    def __init__(
        self,
        *,
        store_path: Path | None = None,
        phase_funcs: dict[str, PhaseFn] | None = None,
        connect=get_connection,
    ) -> None:
        self.store_path = Path(store_path) if store_path else API_JOB_STORE
        self._phase_funcs = phase_funcs or _default_phase_funcs()
        self._connect = connect
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self._load()
        self._fail_interrupted()

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        items = payload.get("jobs") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return
        for row in items:
            if isinstance(row, dict) and row.get("id"):
                job_id = str(row["id"])
                self._jobs[job_id] = row
                self._order.append(job_id)

    def _fail_interrupted(self) -> None:
        changed = False
        for job in self._jobs.values():
            if job.get("status") in RUNNING:
                job["status"] = "failed"
                job["error"] = "API process restarted while this job was running"
                job["finished_at"] = _utcnow()
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        keep = self._order[-KEEP_JOBS:]
        self._order = keep
        self._jobs = {job_id: self._jobs[job_id] for job_id in keep if job_id in self._jobs}
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"jobs": [self._jobs[job_id] for job_id in self._order]}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.store_path)

    def current(self) -> dict | None:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs.get(job_id)
                if job and job.get("status") in RUNNING:
                    return dict(job)
            return None

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, *, limit: int = 20) -> list[dict]:
        with self._lock:
            ids = list(reversed(self._order))[: max(1, min(int(limit), KEEP_JOBS))]
            return [dict(self._jobs[job_id]) for job_id in ids if job_id in self._jobs]

    def start(self, phase: str, params: dict | None = None) -> dict:
        if phase not in PHASES:
            raise ValueError(f"Unknown phase: {phase}")
        params = dict(params or {})
        with self._lock:
            busy = self._running_locked()
            if busy:
                raise JobConflict(busy)
            job = {
                "id": str(uuid.uuid4()),
                "phase": phase,
                "status": "queued",
                "params": params,
                "result": None,
                "error": None,
                "created_at": _utcnow(),
                "started_at": None,
                "finished_at": None,
            }
            self._jobs[job["id"]] = job
            self._order.append(job["id"])
            self._save()
            thread = threading.Thread(
                target=self._run, args=(job["id"],), name=f"pd-{phase}", daemon=True
            )
            thread.start()
            return dict(job)

    def _running_locked(self) -> dict | None:
        for job_id in reversed(self._order):
            job = self._jobs.get(job_id)
            if job and job.get("status") in RUNNING:
                return dict(job)
        return None

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(fields)
            self._save()

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return
        phase = job["phase"]
        params = job.get("params") or {}
        fn = self._phase_funcs[phase]
        self._update(job_id, status="running", started_at=_utcnow())
        try:
            with self._connect(autocommit=False) as conn:
                result = fn(conn, **params)
            self._update(
                job_id,
                status="succeeded",
                result=json_safe(result),
                finished_at=_utcnow(),
                error=None,
            )
        except Phase2Required as exc:
            self._update(job_id, status="failed", error=str(exc), finished_at=_utcnow())
        except Exception as exc:
            self._update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=_utcnow())
