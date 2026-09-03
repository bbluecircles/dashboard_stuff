"""Resolve current CMS download URLs. Dataset IDs stay stable; file hashes do not."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import requests

from provider_directory.settings import (
    CMS_CACHE_DIR,
    NPPES_INDEX_URL,
    OPEN_PAYMENTS_DATASETS,
    PDC_CLINICIAN_DATASET,
    PDC_FACILITY_DATASET,
    PDC_METASTORE,
    PDC_MIPS_DATASET,
    PDC_UTILIZATION_DATASET,
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


def mips_csv_url(session: requests.Session | None = None) -> str:
    return pdc_csv_url(PDC_MIPS_DATASET, session=session)


def utilization_csv_url(session: requests.Session | None = None) -> str:
    return pdc_csv_url(PDC_UTILIZATION_DATASET, session=session)


_OPEN_PAYMENTS_KIND = {
    "general payments": "general",
    "research payments": "research",
    "ownership payments": "ownership",
}


def _open_payments_year(item: dict) -> int | None:
    for token in item.get("keyword") or []:
        text = str(token).strip()
        if text.isdigit() and len(text) == 4:
            year = int(text)
            if 2013 <= year <= 2100:
                return year
    title = str(item.get("title") or "")
    match = re.search(r"\b(20\d{2})\b", title)
    if match:
        return int(match.group(1))
    return None


def _open_payments_kind(item: dict) -> str | None:
    for theme in item.get("theme") or []:
        kind = _OPEN_PAYMENTS_KIND.get(str(theme).strip().lower())
        if kind:
            return kind
    title = str(item.get("title") or "").lower()
    if "general payment" in title:
        return "general"
    if "research payment" in title:
        return "research"
    if "ownership payment" in title:
        return "ownership"
    return None


def _open_payments_download_url(item: dict) -> str | None:
    for dist in item.get("distribution") or []:
        if not isinstance(dist, dict):
            continue
        url = dist.get("downloadURL")
        media = str(dist.get("mediaType") or dist.get("format") or "")
        if url and ("csv" in media.lower() or str(url).lower().endswith(".csv")):
            return url
    return None


def open_payments_detail_urls(
    year: int | None = None,
    session: requests.Session | None = None,
) -> tuple[int, dict[str, str]]:
    """Latest (or requested) Open Payments general/research/ownership CSV URLs."""
    http = session or requests.Session()
    response = http.get(OPEN_PAYMENTS_DATASETS, timeout=60)
    response.raise_for_status()
    payload = response.json()
    items = payload if isinstance(payload, list) else payload.get("results") or payload.get("data") or []
    by_year: dict[int, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = _open_payments_kind(item)
        program_year = _open_payments_year(item)
        url = _open_payments_download_url(item)
        if kind is None or program_year is None or url is None:
            continue
        by_year.setdefault(program_year, {})[kind] = url
    complete = {
        program_year: urls
        for program_year, urls in by_year.items()
        if {"general", "research", "ownership"} <= set(urls)
    }
    if not complete:
        raise CatalogError("Could not find Open Payments general/research/ownership CSVs")
    chosen = year if year in complete else max(complete)
    if year is not None and year not in complete:
        raise CatalogError(f"No complete Open Payments files for {year}")
    return chosen, complete[chosen]


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


def download_file(
    url: str,
    dest: Path,
    *,
    session: requests.Session | None = None,
    chunk_mb: int = 1,
    timeout: float | tuple[float, float] = 120,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    http = session or requests.Session()
    with http.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_mb * 1024 * 1024):
                if chunk:
                    handle.write(chunk)
    tmp.replace(dest)
    return dest


def cached_path(filename: str) -> Path:
    return CMS_CACHE_DIR / filename
