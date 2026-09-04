"""Phase 1 orchestration. CLI and (later) a FastAPI admin route can call this."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from provider_directory.activity import rebuild_activity
from provider_directory.analytics import rebuild_analytics
from provider_directory.complete import rebuild_complete
from provider_directory.cms import (
    cached_path,
    clinician_csv_url,
    download_file,
    facility_csv_url,
    nppes_monthly_zip_url,
)
from provider_directory.cms.load import load_nppes, load_pdc_clinician, load_pdc_facility, load_spine_npis
from provider_directory.db import ensure_mart_database
from provider_directory.locations import rebuild_locations
from provider_directory.mart import overlay_cms
from provider_directory.refresh import rebuild_refresh, resolve_window, upsert_refresh_state
from provider_directory.schema import create_schema
from provider_directory.settings import CMS_CACHE_DIR, MART_DB
from provider_directory.spine import rebuild_spine

CLINICIAN_CSV = "DAC_NationalDownloadableFile.csv"
FACILITY_CSV = "Facility_Affiliation.csv"


def _filename_from_url(url: str, fallback: str) -> str:
    name = Path(unquote(urlparse(url).path)).name
    return name or fallback


def download_cms_files(
    *,
    skip_pdc: bool = False,
    skip_nppes: bool = False,
    session: requests.Session | None = None,
) -> dict[str, Path]:
    http = session or requests.Session()
    CMS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    if not skip_pdc:
        clinician_url = clinician_csv_url(session=http)
        paths["clinician"] = download_file(
            clinician_url, cached_path(_filename_from_url(clinician_url, CLINICIAN_CSV)), session=http
        )
        facility_url = facility_csv_url(session=http)
        paths["facility"] = download_file(
            facility_url, cached_path(_filename_from_url(facility_url, FACILITY_CSV)), session=http
        )
    if not skip_nppes:
        nppes_url = nppes_monthly_zip_url(session=http)
        paths["nppes"] = download_file(
            nppes_url, cached_path(_filename_from_url(nppes_url, "nppes_monthly_v2.zip")), session=http
        )
    return paths


def find_cached_cms() -> dict[str, Path]:
    found: dict[str, Path] = {}
    clinician = cached_path(CLINICIAN_CSV)
    if clinician.exists():
        found["clinician"] = clinician
    else:
        matches = sorted(CMS_CACHE_DIR.glob("*NationalDownloadable*.csv"))
        if matches:
            found["clinician"] = matches[-1]
    facility = cached_path(FACILITY_CSV)
    if facility.exists():
        found["facility"] = facility
    else:
        matches = sorted(CMS_CACHE_DIR.glob("*Facility_Affiliation*.csv"))
        if matches:
            found["facility"] = matches[-1]
    nppes_matches = sorted(CMS_CACHE_DIR.glob("*Dissemination*V2.zip")) or sorted(
        CMS_CACHE_DIR.glob("nppes*.zip")
    )
    if nppes_matches:
        found["nppes"] = nppes_matches[-1]
    return found


def run_phase1(
    conn,
    *,
    mart_db: str = MART_DB,
    download: bool = False,
    skip_pdc: bool = False,
    skip_nppes: bool = False,
) -> dict:
    ensure_mart_database(conn, mart_db)
    create_schema(conn, mart_db)
    spine_rows = rebuild_spine(conn, mart_db=mart_db)
    spine_npis = load_spine_npis(conn, mart_db=mart_db)

    if download:
        download_cms_files(skip_pdc=skip_pdc, skip_nppes=skip_nppes)
    cached = find_cached_cms()

    loaded = {"clinician": 0, "facility": 0, "nppes": 0}
    skipped = []
    if not skip_pdc and "clinician" in cached:
        loaded["clinician"] = load_pdc_clinician(conn, cached["clinician"], spine_npis, mart_db=mart_db)
    elif not skip_pdc:
        skipped.append("pdc clinician CSV not in data/cms — pass --download or copy DAC_NationalDownloadableFile.csv")
    if not skip_pdc and "facility" in cached:
        loaded["facility"] = load_pdc_facility(conn, cached["facility"], spine_npis, mart_db=mart_db)
    elif not skip_pdc:
        skipped.append("pdc facility CSV not in data/cms — pass --download or copy Facility_Affiliation.csv")
    if not skip_nppes and "nppes" in cached:
        loaded["nppes"] = load_nppes(conn, cached["nppes"], spine_npis, mart_db=mart_db)
    elif not skip_nppes:
        skipped.append("NPPES zip not in data/cms — pass --download (this file is ~1.1 GB)")

    overlay_cms(conn, mart_db=mart_db)
    return {
        "mart_db": mart_db,
        "spine_rows": spine_rows,
        "loaded": loaded,
        "skipped": skipped,
        "cached": {key: str(path) for key, path in cached.items()},
    }


def run_phase2(conn, *, mart_db: str = MART_DB) -> dict:
    ensure_mart_database(conn, mart_db)
    create_schema(conn, mart_db)
    window_start, window_end, prior_start, prior_end = resolve_window(conn, mart_db)
    summary = rebuild_activity(
        conn, mart_db=mart_db, window_start=window_start, window_end=window_end
    )
    upsert_refresh_state(
        conn,
        mart_db,
        window_start=window_start,
        window_end=window_end,
        prior_window_start=prior_start,
        prior_window_end=prior_end,
        last_action="phase2",
        notes="full window scan into pd_stg_window_claim",
    )
    return summary


def run_phase3(conn, *, mart_db: str = MART_DB) -> dict:
    ensure_mart_database(conn, mart_db)
    create_schema(conn, mart_db)
    return rebuild_locations(conn, mart_db=mart_db)


def run_phase4(conn, *, mart_db: str = MART_DB) -> dict:
    ensure_mart_database(conn, mart_db)
    create_schema(conn, mart_db)
    window_start, window_end, _prior_start, _prior_end = resolve_window(conn, mart_db)
    return rebuild_analytics(
        conn, mart_db=mart_db, window_start=window_start, window_end=window_end
    )


def run_phase5(conn, *, mart_db: str = MART_DB) -> dict:
    ensure_mart_database(conn, mart_db)
    create_schema(conn, mart_db)
    window_start, window_end, prior_start, prior_end = resolve_window(conn, mart_db)
    return rebuild_complete(
        conn,
        mart_db=mart_db,
        window_start=window_start,
        window_end=window_end,
        prior_start=prior_start,
        prior_end=prior_end,
    )


def run_extras(
    conn,
    *,
    mart_db: str = MART_DB,
    download: bool = False,
    reload_pdc: bool = False,
    skip_mips: bool = False,
    skip_utilization: bool = False,
    skip_open_payments: bool = False,
    year: int | None = None,
    open_payments_kinds: tuple[str, ...] | None = None,
    open_payments_overlay_only: bool = False,
) -> dict:
    from provider_directory.extras import OPEN_PAYMENTS_KINDS, rebuild_extras

    ensure_mart_database(conn, mart_db)
    return rebuild_extras(
        conn,
        mart_db=mart_db,
        download=download,
        reload_pdc=reload_pdc,
        skip_mips=skip_mips,
        skip_utilization=skip_utilization,
        skip_open_payments=skip_open_payments,
        year=year,
        open_payments_kinds=open_payments_kinds or OPEN_PAYMENTS_KINDS,
        open_payments_overlay_only=open_payments_overlay_only,
    )


def run_phase6(
    conn,
    *,
    mart_db: str = MART_DB,
    slide: bool = False,
    skip_staging_indexes: bool = False,
) -> dict:
    ensure_mart_database(conn, mart_db)
    create_schema(conn, mart_db)
    return rebuild_refresh(
        conn,
        mart_db=mart_db,
        slide=slide,
        skip_staging_indexes=skip_staging_indexes,
    )
