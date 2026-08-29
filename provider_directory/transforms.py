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


AGE_BANDS = ("0_19", "20_44", "45_64", "65_84", "85_plus")
MAX_PLAUSIBLE_AGE = 120


def age_band(age: Any) -> str | None:
    years = parse_int(age)
    if years is None or years < 0 or years > MAX_PLAUSIBLE_AGE:
        return None
    if years <= 19:
        return "0_19"
    if years <= 44:
        return "20_44"
    if years <= 64:
        return "45_64"
    if years <= 84:
        return "65_84"
    return "85_plus"


def round_pct(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round(100.0 * part / whole, 2)


def summarize_panel(patients: list[Mapping[str, Any]]) -> dict[str, Any]:
    """One row per patient: age, gender. Average age, not median."""
    size = len(patients)
    bands = {key: 0 for key in AGE_BANDS}
    ages: list[int] = []
    female = 0
    male = 0
    for patient in patients:
        years = parse_int(patient.get("age"))
        band = age_band(years)
        if band is not None and years is not None:
            bands[band] += 1
            ages.append(years)
        gender = normalize_gender(patient.get("gender"))
        if gender == "F":
            female += 1
        elif gender == "M":
            male += 1
    return {
        "panel_size": size,
        "panel_average_age": round(sum(ages) / len(ages), 1) if ages else None,
        "panel_percent_age_0_19": round_pct(bands["0_19"], size),
        "panel_percent_age_20_44": round_pct(bands["20_44"], size),
        "panel_percent_age_45_64": round_pct(bands["45_64"], size),
        "panel_percent_age_65_84": round_pct(bands["65_84"], size),
        "panel_percent_age_85_plus": round_pct(bands["85_plus"], size),
        "panel_percent_female": round_pct(female, size),
        "panel_percent_male": round_pct(male, size),
    }


def top_n_codes(counts: Mapping[str, int], n: int = 3) -> list[str]:
    ranked = sorted(
        ((code, cnt) for code, cnt in counts.items() if nonempty(code) is not None),
        key=lambda item: (-item[1], item[0]),
    )
    return [code for code, _cnt in ranked[:n]]


def is_active(visits_total: int | None, panel_size: int | None) -> bool:
    return (visits_total or 0) > 0 or (panel_size or 0) > 0


_STREET_SUFFIX = {
    "STREET": "ST",
    "AVENUE": "AVE",
    "ROAD": "RD",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "LANE": "LN",
    "PARKWAY": "PKWY",
    "HIGHWAY": "HWY",
    "PLACE": "PL",
    "COURT": "CT",
    "CIRCLE": "CIR",
    "TRAIL": "TRL",
}
_DIRECTIONALS = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
}
_SUITE_RE = re.compile(
    r"\s+(STE|SUITE|APT|UNIT|BLDG|BUILDING|FLOOR|FL|#)([.\s].*)?$",
    re.IGNORECASE,
)
_POBOX_RE = re.compile(r"^POBOX")
_ENTITY_SUFFIX_RE = re.compile(r"\s*TYPE-2-ENTITY\s*$", re.IGNORECASE)


def is_po_box(street: Any) -> bool:
    text = nonempty(street)
    if text is None:
        return False
    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    return bool(_POBOX_RE.match(compact))


def is_junk_geocode(lat: Any, lon: Any) -> bool:
    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError):
        return True
    return latitude == 0.0 and longitude == 0.0


def city_without_state(value: Any) -> str | None:
    text = nonempty(value)
    if text is None:
        return None
    return nonempty(text.split(",", 1)[0])


def zip5(value: Any) -> str | None:
    text = nonempty(value)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) < 5:
        return None
    return digits[:5]


def normalize_street(value: Any) -> str | None:
    """Uppercase street with suite stripped so suite variants cluster as one site."""
    text = nonempty(value)
    if text is None or is_po_box(text):
        return None
    text = _SUITE_RE.sub("", text.upper())
    text = re.sub(r"[.,]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    parts = []
    for token in text.split():
        parts.append(_DIRECTIONALS.get(token) or _STREET_SUFFIX.get(token) or token)
    return " ".join(parts) or None


def cluster_key(
    *,
    sl_code: Any,
    street: Any,
    zip_code: Any,
    latitude: Any = None,
    longitude: Any = None,
    geo_decimals: int = 4,
) -> str:
    """Same building (street+zip), else ~11m geocode, else the raw sl_code."""
    street_key = normalize_street(street)
    zed = zip5(zip_code)
    if street_key and zed:
        return f"a:{street_key}|{zed}"
    if not is_junk_geocode(latitude, longitude):
        return f"g:{float(latitude):.{geo_decimals}f},{float(longitude):.{geo_decimals}f}"
    return f"s:{parse_int(sl_code) or 0}"


def strip_entity_suffix(value: Any) -> str | None:
    text = nonempty(value)
    if text is None:
        return None
    return nonempty(_ENTITY_SUFFIX_RE.sub("", text))


def pick_practice_name(
    *,
    npi_type: Any = None,
    hospital_system: Any = None,
    dba_name: Any = None,
    common_name: Any = None,
    sl_name: Any = None,
    facility_dba: Any = None,
    facility_hospital_system: Any = None,
) -> str | None:
    """Prefer system / Type 2 DBA over a Type 1 clone of the doctor's own name."""
    ntype = nonempty(npi_type)
    candidates = [
        hospital_system,
        facility_hospital_system,
        dba_name if ntype == "2" else None,
        sl_name if ntype == "2" else None,
        facility_dba,
        common_name,
        dba_name,
        sl_name,
    ]
    for value in candidates:
        found = strip_entity_suffix(value)
        if found is not None:
            return found
    return None


def pick_work_type(
    pos_type_name: Any = None,
    im_specialty_rollup: Any = None,
    hospital_system: Any = None,
) -> str | None:
    if nonempty(hospital_system):
        return nonempty(im_specialty_rollup) or "Hospital"
    return pick_value(pos_type_name, im_specialty_rollup)


def rank_clusters(
    clusters: list[Mapping[str, Any]],
    visits_total: int | None,
    *,
    max_sites: int = 5,
) -> list[dict[str, Any]]:
    """Primary = most visits, then lowest sl_code. Share may not sum to 100."""
    ranked = sorted(
        clusters,
        key=lambda row: (-int(row.get("visits") or 0), int(row.get("sl_code") or 0)),
    )
    total = visits_total or 0
    out: list[dict[str, Any]] = []
    for index, row in enumerate(ranked[:max_sites], start=1):
        visits = int(row.get("visits") or 0)
        out.append(
            {
                **dict(row),
                "site_rank": index,
                "visits_at_site": visits,
                "visit_share_pct": round_pct(visits, total),
            }
        )
    return out


def nppes_primary_taxonomy(row: Mapping[str, Any]) -> str | None:
    for i in range(1, 16):
        switch = nonempty(row.get(f"healthcare_provider_primary_taxonomy_switch_{i}"))
        code = nonempty(row.get(f"healthcare_provider_taxonomy_code_{i}"))
        if switch and switch.upper() == "Y" and code:
            return code
    return nonempty(row.get("healthcare_provider_taxonomy_code_1"))
