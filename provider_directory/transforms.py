"""Pure transforms. No database. These are the FastAPI-safe building blocks."""

from __future__ import annotations

import re
from typing import Any, Mapping

from provider_directory.settings import (
    DUMMY_NPIS,
    DUMMY_STATES,
    GRAD_AGE_OFFSET,
    MARKET_STATE,
    MAX_ESTIMATED_AGE,
    MIN_GRAD_YEAR,
    NPI_MAX,
    NPI_MIN,
    REPORT_YEAR,
    TYPE1_CODE,
)

_HEADER_RE = re.compile(r"[^a-z0-9]+")
_EMPTY = frozenset({"", "NA", "N/A", "NONE", "NULL", "UNKNOWN", ".", "-"})


def nonempty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in _EMPTY:
        return None
    return text


def parse_int(value: Any) -> int | None:
    text = nonempty(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parse_npi(value: Any) -> int | None:
    npi = parse_int(value)
    if npi is None or npi < NPI_MIN or npi > NPI_MAX:
        return None
    return npi


def normalize_gender(value: Any) -> str | None:
    text = nonempty(value)
    if text is None:
        return None
    letter = text[0].upper()
    if letter in {"M", "F"}:
        return letter
    return None


def estimated_age(
    grd_yr: int | None,
    *,
    report_year: int = REPORT_YEAR,
    offset: int = GRAD_AGE_OFFSET,
    min_grad_year: int = MIN_GRAD_YEAR,
    cap: int = MAX_ESTIMATED_AGE,
) -> int | None:
    """2024 − Grd_yr + 26. Null if missing/implausible. Cap at 90."""
    if grd_yr is None:
        return None
    if grd_yr < min_grad_year or grd_yr > report_year:
        return None
    age = report_year - grd_yr + offset
    if age < 0:
        return None
    return min(age, cap)


def is_type1_universe(npi: Any, npi_type: Any, state_abbr: Any) -> bool:
    parsed = parse_npi(npi)
    if parsed is None or parsed in DUMMY_NPIS:
        return False
    if str(npi_type or "").strip() != TYPE1_CODE:
        return False
    state = str(state_abbr or "").strip().upper()
    if state in DUMMY_STATES:
        return False
    return True


def normalize_header(name: str) -> str:
    cleaned = name.replace("\ufeff", "").strip().lower().replace("/", " ")
    return _HEADER_RE.sub("_", cleaned).strip("_")


def normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {normalize_header(str(k)): v for k, v in row.items()}


def first_present(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and nonempty(row[key]) is not None:
            return row[key]
    return None


def pdc_identity_score(
    *,
    state: str | None,
    med_sch: str | None,
    grd_yr: int | None,
    gender: str | None,
    phone: str | None,
    market_state: str = MARKET_STATE,
) -> tuple[int, int, int, int, int]:
    """Higher tuple wins when collapsing PDC clinician×location rows to one NPI."""
    school = nonempty(med_sch)
    return (
        1 if (state or "").upper() == market_state else 0,
        1 if school is not None and school.upper() != "OTHER" else 0,
        1 if grd_yr is not None else 0,
        1 if gender in {"M", "F"} else 0,
        1 if nonempty(phone) is not None else 0,
    )


def pick_value(*candidates: Any) -> str | None:
    """First nonempty wins. Used to overlay CMS onto claims identity."""
    for value in candidates:
        found = nonempty(value)
        if found is not None:
            return found
    return None


def merge_identity(
    claims: Mapping[str, Any],
    pdc: Mapping[str, Any] | None = None,
    nppes: Mapping[str, Any] | None = None,
    *,
    report_year: int = REPORT_YEAR,
) -> dict[str, Any]:
    """Claims first, then PDC, then NPPES. Specialty stays on the claims keys."""
    pdc = pdc or {}
    nppes = nppes or {}

    first_name = pick_value(claims.get("first_name"), pdc.get("first_name"), nppes.get("first_name"))
    middle_name = pick_value(
        claims.get("middle_name"), pdc.get("middle_name"), nppes.get("middle_name")
    )
    last_name = pick_value(claims.get("last_name"), pdc.get("last_name"), nppes.get("last_name"))
    suffix = pick_value(claims.get("suffix"), pdc.get("suffix"), nppes.get("suffix"))
    credential = pick_value(
        claims.get("credential"), pdc.get("credential"), nppes.get("credential")
    )
    gender = normalize_gender(pick_value(pdc.get("gender"), nppes.get("gender"), claims.get("gender")))
    school = pick_value(pdc.get("medical_school_name"), nppes.get("medical_school_name"))
    grd_yr = parse_int(pdc.get("graduation_year"))
    if grd_yr is None:
        grd_yr = parse_int(nppes.get("graduation_year"))

    name_source = "claims"
    if not nonempty(claims.get("last_name")) and nonempty(pdc.get("last_name")):
        name_source = "pdc"
    elif not nonempty(claims.get("last_name")) and nonempty(nppes.get("last_name")):
        name_source = "nppes"

    gender_source = None
    if normalize_gender(pdc.get("gender")):
        gender_source = "pdc"
    elif normalize_gender(nppes.get("gender")):
        gender_source = "nppes"

    school_source = None
    if nonempty(pdc.get("medical_school_name")):
        school_source = "pdc"
    elif nonempty(nppes.get("medical_school_name")):
        school_source = "nppes"

    return {
        "npi": claims.get("npi") or pdc.get("npi") or nppes.get("npi"),
        "first_name": first_name,
        "middle_name": middle_name,
        "last_name": last_name,
        "suffix": suffix,
        "credential": credential,
        "gender": gender,
        "medical_school_name": school,
        "medical_school_graduation_year": grd_yr,
        "estimated_age": estimated_age(grd_yr, report_year=report_year),
        "primary_specialty_code": nonempty(claims.get("primary_specialty_code")),
        "primary_specialty_description": nonempty(claims.get("primary_specialty_description")),
        "specialty_classification": nonempty(claims.get("specialty_classification")),
        "in_system_provider": claims.get("in_system_provider"),
        "name_source": name_source,
        "gender_source": gender_source,
        "school_source": school_source,
    }


def nppes_primary_taxonomy(row: Mapping[str, Any]) -> str | None:
    for i in range(1, 16):
        switch = nonempty(row.get(f"healthcare_provider_primary_taxonomy_switch_{i}"))
        code = nonempty(row.get(f"healthcare_provider_taxonomy_code_{i}"))
        if switch and switch.upper() == "Y" and code:
            return code
    return nonempty(row.get("healthcare_provider_taxonomy_code_1"))
