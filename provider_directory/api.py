"""HTTP API for the .NET app. Calls the same functions as the CLI.

Lookup routes read az_pd only. Phase routes enqueue a background job so
IIS/NSSM HTTP timeouts do not kill a 12-month rebuild.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from provider_directory import __version__
from provider_directory.db import ConfigError, get_connection
from provider_directory.jobs import PHASES, JobConflict, JobRunner
from provider_directory.lookup import get_provider, search_providers
from provider_directory.models import ProviderSpine, ProviderSpineList
from provider_directory.refresh import read_refresh_state, resolve_window, warehouse_max_period
from provider_directory.settings import (
    API_HOST,
    API_PORT,
    MART_DB,
    NPI_MAX,
    NPI_MIN,
    SEARCH_LIMIT_MAX,
)


def _api_key() -> str:
    return os.environ.get("PD_API_KEY", "").strip()


def _cors_origins() -> list[str]:
    raw = os.environ.get("PD_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = _api_key()
    if not expected:
        return
    provided = x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")


def db_conn() -> Iterator:
    try:
        with get_connection() as conn:
            yield conn
    except ConfigError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "provider-directory"
    version: str = __version__


class MartStatus(BaseModel):
    mart_db: str
    window_start: int
    window_end: int
    prior_window_start: int
    prior_window_end: int
    warehouse_max_period: int | None = None
    warehouse_source: str | None = None
    slide_available: bool | None = None
    last_action: str | None = None
    current_job: dict | None = None
    get_reads_mart_only: bool = True


class JobRequest(BaseModel):
    download: bool = False
    skip_pdc: bool = False
    skip_nppes: bool = False
    slide: bool = False
    skip_staging_indexes: bool = False


class JobAccepted(BaseModel):
    id: str
    phase: str
    status: str
    params: dict = Field(default_factory=dict)
    created_at: str | None = None


def _job_params(phase: str, body: JobRequest) -> dict:
    if phase == "phase1":
        return {
            "download": body.download,
            "skip_pdc": body.skip_pdc,
            "skip_nppes": body.skip_nppes,
        }
    if phase == "phase6":
        return {
            "slide": body.slide,
            "skip_staging_indexes": body.skip_staging_indexes,
        }
    return {}


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), SEARCH_LIMIT_MAX))


def create_app(*, runner: JobRunner | None = None) -> FastAPI:
    job_runner = runner or JobRunner()
    docs = None if os.environ.get("PD_API_DOCS", "1") == "0" else "/docs"

    app = FastAPI(
        title="Arizona provider directory",
        version=__version__,
        description=(
            "Mart lookup for the .NET UI, plus background jobs for phase1–phase6. "
            "Providers are served from az_pd, not az.pat_dt."
        ),
        docs_url=docs,
        redoc_url=None if docs is None else "/redoc",
    )
    app.state.jobs = job_runner
    origins = _cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def jobs() -> JobRunner:
        return app.state.jobs

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/v1/mart", response_model=MartStatus, tags=["ops"])
    def mart_status(
        _: Annotated[None, Depends(require_api_key)],
        conn=Depends(db_conn),
        job_runner: JobRunner = Depends(jobs),
    ) -> MartStatus:
        window_start, window_end, prior_start, prior_end = resolve_window(conn)
        state = read_refresh_state(conn) or {}
        warehouse_max, warehouse_source = warehouse_max_period(conn)
        slide = state.get("slide_available")
        return MartStatus(
            mart_db=MART_DB,
            window_start=window_start,
            window_end=window_end,
            prior_window_start=prior_start,
            prior_window_end=prior_end,
            warehouse_max_period=warehouse_max
            if warehouse_max is not None
            else state.get("warehouse_max_period"),
            warehouse_source=warehouse_source or state.get("warehouse_source"),
            slide_available=bool(slide) if slide is not None else None,
            last_action=state.get("last_action"),
            current_job=job_runner.current(),
        )

    @app.get("/v1/providers/{npi}", response_model=ProviderSpine, tags=["providers"])
    def provider_get(
        npi: int,
        _: Annotated[None, Depends(require_api_key)],
        conn=Depends(db_conn),
    ) -> ProviderSpine:
        if npi < NPI_MIN or npi > NPI_MAX:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "NPI must be 10 digits")
        row = get_provider(conn, npi)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"NPI {npi} not in pd_provider")
        return row

    @app.get("/v1/providers", response_model=ProviderSpineList, tags=["providers"])
    def provider_search(
        _: Annotated[None, Depends(require_api_key)],
        conn=Depends(db_conn),
        last_name: str | None = None,
        npi: int | None = None,
        specialty: str | None = None,
        active: bool | None = None,
        min_visits: int | None = Query(default=None, ge=0),
        limit: int = Query(default=25, ge=1, le=SEARCH_LIMIT_MAX),
        offset: int = Query(default=0, ge=0),
        in_system: bool | None = None,
    ) -> ProviderSpineList:
        return search_providers(
            conn,
            last_name=last_name,
            npi=npi,
            specialty=specialty,
            active=active,
            min_visits=min_visits,
            limit=_clamp_limit(limit),
            offset=offset,
            in_system=in_system,
        )

    @app.get("/v1/jobs", tags=["jobs"])
    def jobs_list(
        _: Annotated[None, Depends(require_api_key)],
        job_runner: JobRunner = Depends(jobs),
        limit: int = Query(default=20, ge=1, le=20),
    ) -> dict:
        return {"items": job_runner.list(limit=limit), "current": job_runner.current()}

    @app.get("/v1/jobs/{job_id}", tags=["jobs"])
    def jobs_get(
        job_id: str,
        _: Annotated[None, Depends(require_api_key)],
        job_runner: JobRunner = Depends(jobs),
    ) -> dict:
        job = job_runner.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return job

    @app.post(
        "/v1/jobs/{phase}",
        response_model=JobAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["jobs"],
    )
    def jobs_start(
        phase: str,
        _: Annotated[None, Depends(require_api_key)],
        job_runner: JobRunner = Depends(jobs),
        body: JobRequest | None = None,
    ) -> JSONResponse:
        if phase not in PHASES:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown phase: {phase}")
        try:
            job = job_runner.start(phase, _job_params(phase, body or JobRequest()))
        except JobConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=job,
            headers={"Location": f"/v1/jobs/{job['id']}"},
        )

    return app


def serve(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    bind_host = host or API_HOST
    bind_port = API_PORT if port is None else port
    if not _api_key():
        print("PD_API_KEY is empty — API is open on this bind address", flush=True)
    print(f"provider directory API http://{bind_host}:{bind_port}/docs", flush=True)
    uvicorn.run(
        "provider_directory.api:create_app",
        factory=True,
        host=bind_host,
        port=bind_port,
        workers=1,
        log_level="info",
    )
