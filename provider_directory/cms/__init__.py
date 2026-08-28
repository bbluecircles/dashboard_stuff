"""Resolve current CMS download URLs. Dataset IDs stay stable; file hashes do not."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import requests

from provider_directory.settings import (
    CMS_CACHE_DIR,
    NPPES_INDEX_URL,
    PDC_CLINICIAN_DATASET,
    PDC_FACILITY_DATASET,
    PDC_METASTORE,
)

_ZIP_HREF = re.compile(r"""href=["']([^"']+\.zip)["']""", re.I)


class CatalogError(RuntimeError):
    pass


def pdc_csv_url(dataset_id: str, session: requests.Session | None = None) -> str:
    http = session or requests.Session()
    response = http.get(PDC_METASTORE.format(dataset_id=dataset_id), timeout=60)
    response.raise_for_status()
    payload = response.json()
    for dist in payload.get("distribution") or []:
        data = dist.get("data") if isinstance(dist, dict) and "data" in dist else dist
        if not isinstance(data, dict):
            continue
        url = data.get("downloadURL")
        media = str(data.get("mediaType") or "")
        if url and ("csv" in media.lower() or str(url).lower().endswith(".csv")):
            return url
    raise CatalogError(f"No CSV distribution for dataset {dataset_id}")


def clinician_csv_url(session: requests.Session | None = None) -> str:
    return pdc_csv_url(PDC_CLINICIAN_DATASET, session=session)


def facility_csv_url(session: requests.Session | None = None) -> str:
    return pdc_csv_url(PDC_FACILITY_DATASET, session=session)


def nppes_monthly_zip_url(html: str | None = None, session: requests.Session | None = None) -> str:
    if html is None:
        http = session or requests.Session()
        response = http.get(NPPES_INDEX_URL, timeout=60)
        response.raise_for_status()
        html = response.text
    links = [urljoin(NPPES_INDEX_URL, href) for href in _ZIP_HREF.findall(html)]
    monthly = [
        url
        for url in links
        if "Dissemination" in url
        and "Weekly" not in url
        and "Deactivat" not in url
    ]
    if not monthly:
        raise CatalogError("Could not find a monthly NPPES V2 zip on the CMS index page")
    return monthly[0]


def download_file(url: str, dest: Path, *, session: requests.Session | None = None, chunk_mb: int = 1) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    http = session or requests.Session()
    with http.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_mb * 1024 * 1024):
                if chunk:
                    handle.write(chunk)
    tmp.replace(dest)
    return dest


def cached_path(filename: str) -> Path:
    return CMS_CACHE_DIR / filename
