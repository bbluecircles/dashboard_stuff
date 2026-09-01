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


# MariaDB DAYOFWEEK: 1=Sunday … 7=Saturday.
DOW_PERCENT_COLUMNS = (
    (2, "visits_percent_monday"),
    (3, "visits_percent_tuesday"),
    (4, "visits_percent_wednesday"),
    (5, "visits_percent_thursday"),
    (6, "visits_percent_friday"),
    (7, "visits_percent_saturday"),
    (1, "visits_percent_sunday"),
)


def dow_percentages(counts: Mapping[int, int]) -> dict[str, float | None]:
    total = sum(int(counts.get(day, 0) or 0) for day, _col in DOW_PERCENT_COLUMNS)
    return {col: round_pct(int(counts.get(day, 0) or 0), total) for day, col in DOW_PERCENT_COLUMNS}


def wrvu_yoy_change_pct(current: Any, prior: Any) -> float | None:
    try:
        cur = float(current)
        old = float(prior)
    except (TypeError, ValueError):
        return None
    if old == 0:
        return None
    return round(100.0 * (cur - old) / old, 2)


def specialty_percentile(rank: int | None, n: int | None) -> float | None:
    """1-based rank from lowest wRVU. 100 = highest in the specialty."""
    if rank is None or n is None or n <= 0 or rank <= 0:
        return None
    if n == 1:
        return 100.0
    return round(100.0 * rank / n, 1)


def referral_display_name(*, last_name: Any = None, first_name: Any = None) -> str | None:
    last = nonempty(last_name)
    first = nonempty(first_name)
    if last and first:
        return f"{last}, {first}"
    return last or first


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
_PERSON_NAME_RE = re.compile(r"^[A-Z][A-Z'`. \-]*,[ ]+[A-Z][A-Z'`. \-]*$", re.IGNORECASE)
ORG_NAME_HINTS = (
    "CLINIC",
    "HOSPITAL",
    "HEALTH",
    "MEDICAL",
    "GROUP",
    "CENTER",
    "ASSOCIATES",
    "INSTITUTE",
    "SURGERY",
    "UNIVERSITY",
    "PHARMACY",
    "LABORATOR",
    " LLC",
    "L.L.C",
    " P.C",
    " PC",
    " INC",
    " PLLC",
    "FOUNDATION",
    "SYSTEM",
)
# MariaDB REGEXP equivalent of ORG_NAME_HINTS. No `%` — pymysql would treat it
# as a format placeholder when the statement also has %s params.
ORG_NAME_REGEXP = (
    "CLINIC|HOSPITAL|HEALTH|MEDICAL|GROUP|CENTER|ASSOCIATES|INSTITUTE|"
    "SURGERY|UNIVERSITY|PHARMACY|LABORATOR| LLC| PLLC| INC|FOUNDATION|"
    "SYSTEM|[[:space:]]PC"
)
PERSON_NAME_REGEXP = "^[^,]+,[[:space:]]*[^,]+$"
MIN_PLAUSIBLE_WORK_RVU = 0.05
MIN_PLAUSIBLE_TOTAL_RVU = 0.5


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


def looks_like_person_name(value: Any) -> bool:
    """True for LAST, FIRST clones (Type 1 SL / another clinician's DBA)."""
    text = nonempty(value)
    if text is None:
        return False
    upper = text.upper()
    if any(hint in upper for hint in ORG_NAME_HINTS):
        return False
    return _PERSON_NAME_RE.match(upper) is not None


def street_city_label(*, street: Any = None, city: Any = None) -> str | None:
    street_s = nonempty(street)
    city_s = nonempty(city)
    if street_s and city_s:
        return f"{street_s}, {city_s}"
    return street_s or city_s


def pick_practice_name(
    *,
    npi_type: Any = None,
    hospital_system: Any = None,
    dba_name: Any = None,
    common_name: Any = None,
    sl_name: Any = None,
    facility_dba: Any = None,
    facility_hospital_system: Any = None,
    street: Any = None,
    city: Any = None,
) -> str | None:
    """Prefer system / Type 2 DBA over a Type 1 clone of a person's name."""
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
        if found is None or looks_like_person_name(found):
            continue
        return found
    return street_city_label(street=street, city=city)


def physician_work_rvu(
    *,
    work_rvu: Any = None,
    non_facility_total: Any = None,
    non_fac_pe_rvu: Any = None,
    mp_rvu: Any = None,
    facility_total: Any = None,
    facility_pe_rvu: Any = None,
    nf_total_rvu: Any = None,
) -> float | None:
    """Physician work RVU from azal.procd PFS columns.

    azal.procd.WORK_RVU is trustworthy on many ICD-10-PCS rows and wrong or
    near-zero on some CPT/HCPCS rows (Sean Smith 93306/99204 summed to 0.01).
    Prefer a plausible WORK_RVU; else reconstruct work as total − PE − MP;
    else nf_total_rvu (includes practice expense — last resort).
    """

    def as_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    work = as_float(work_rvu)
    if work is not None and work >= MIN_PLAUSIBLE_WORK_RVU:
        return work
    nf_total = as_float(non_facility_total)
    nf_pe = as_float(non_fac_pe_rvu)
    mp = as_float(mp_rvu)
    if nf_total is not None and nf_pe is not None and mp is not None:
        reconstructed = nf_total - nf_pe - mp
        if reconstructed >= MIN_PLAUSIBLE_WORK_RVU:
            return reconstructed
    fac_total = as_float(facility_total)
    fac_pe = as_float(facility_pe_rvu)
    if fac_total is not None and fac_pe is not None and mp is not None:
        reconstructed = fac_total - fac_pe - mp
        if reconstructed >= MIN_PLAUSIBLE_WORK_RVU:
            return reconstructed
    nf = as_float(nf_total_rvu)
    if nf is not None and nf >= MIN_PLAUSIBLE_TOTAL_RVU and (
        work is None or work < MIN_PLAUSIBLE_WORK_RVU
    ):
        return nf
    if work is not None and work > 0:
        return work
    return None


POS_WORK_TYPE = {
    11: "Office",
    12: "Home",
    19: "Off Campus Outpatient Hospital",
    20: "Urgent Care",
    21: "Short Term Acute Care Hospital",
    22: "Hospital Outpatient",
    23: "Emergency Department",
    24: "Ambulatory Surgery Center",
    31: "Skilled Nursing Facility",
    32: "Nursing Facility",
    34: "Hospice",
    41: "Ambulance (Land)",
    49: "Independent Clinic",
    50: "Federally Qualified Health Center",
    65: "End-Stage Renal Disease Facility",
    72: "Rural Health Clinic",
    81: "Independent Laboratory",
}


def polish_work_type(
    *,
    pos_type_code: Any = None,
    pos_type_name: Any = None,
    im_specialty_rollup: Any = None,
    hospital_system: Any = None,
) -> str | None:
    """Trilliant-style site label from CMS POS, then warehouse rollup."""
    pos = parse_int(pos_type_code)
    if pos in POS_WORK_TYPE:
        return POS_WORK_TYPE[pos]
    roll = (nonempty(im_specialty_rollup) or "").lower()
    if "urgent" in roll:
        return "Urgent Care"
    if "emergency" in roll:
        return "Emergency Department"
    if "ambulatory surg" in roll:
        return "Ambulatory Surgery Center"
    if "skilled nursing" in roll:
        return "Skilled Nursing Facility"
    if "hospice" in roll:
        return "Hospice"
    if "dialysis" in roll or "end-stage" in roll:
        return "End-Stage Renal Disease Facility"
    if "acute care hospital" in roll or "general acute" in roll:
        return "Short Term Acute Care Hospital"
    if "outpatient hospital" in roll:
        return "Hospital Outpatient"
    if "office specialty" in roll:
        return "Single Specialty Group"
    if "office" in roll:
        return "Office"
    if nonempty(hospital_system) and "hospital" in roll:
        return "Short Term Acute Care Hospital"
    return nonempty(pos_type_name) or nonempty(im_specialty_rollup)


def payer_mix_percents(counts: Mapping[int, int]) -> dict[str, float | None]:
    """Percents over is_payor 1–4. Code 5 Other is excluded from the denominator."""
    from provider_directory.settings import (
        PAYOR_COMMERCIAL,
        PAYOR_HMO_MA,
        PAYOR_MEDICAID,
        PAYOR_MEDICARE_FFS,
        PAYOR_MIX_CODES,
    )

    four = sum(int(counts.get(code, 0) or 0) for code in PAYOR_MIX_CODES)
    return {
        "visits_percent_medicare_traditional": round_pct(int(counts.get(PAYOR_MEDICARE_FFS, 0) or 0), four),
        "visits_percent_medicaid": round_pct(int(counts.get(PAYOR_MEDICAID, 0) or 0), four),
        "visits_percent_third_party": round_pct(int(counts.get(PAYOR_COMMERCIAL, 0) or 0), four),
        "visits_percent_medicare_advantage": round_pct(int(counts.get(PAYOR_HMO_MA, 0) or 0), four),
    }


def top_commercial_payers(
    volumes: Mapping[str, int],
    *,
    n: int = 3,
) -> list[tuple[str, float]]:
    """Top commercial parent names. Percents are of commercial volume only."""
    ranked = sorted(
        ((nonempty(name), int(cnt)) for name, cnt in volumes.items() if nonempty(name) is not None),
        key=lambda item: (-item[1], item[0]),
    )
    total = sum(cnt for _name, cnt in ranked)
    out: list[tuple[str, float]] = []
    for name, cnt in ranked[:n]:
        pct = round_pct(cnt, total)
        if name is not None and pct is not None:
            out.append((name, pct))
    return out


def pick_work_type(
    pos_type_name: Any = None,
    im_specialty_rollup: Any = None,
    hospital_system: Any = None,
) -> str | None:
    """Raw Phase 3 label. Phase 4 polishes with polish_work_type."""
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
