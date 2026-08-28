"""Parse CMS PDC / NPPES rows into dicts the loader and tests can share."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator, Mapping, TextIO

from provider_directory.settings import MARKET_STATE, TYPE1_CODE
from provider_directory.transforms import (
    first_present,
    nonempty,
    normalize_gender,
    normalize_row,
    nppes_primary_taxonomy,
    parse_int,
    parse_npi,
    pdc_identity_score,
)

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d")


def open_text(path: Path) -> TextIO:
    return path.open("r", encoding="utf-8-sig", errors="replace", newline="")


def iter_csv_rows(handle: TextIO) -> Iterator[dict]:
    reader = csv.DictReader(handle)
    for raw in reader:
        yield normalize_row(raw)


def parse_date(value) -> str | None:
    text = nonempty(value)
    if text is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:10] if fmt != "%Y%m%d" else text[:8], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_pdc_clinician_row(row: Mapping) -> dict | None:
    npi = parse_npi(first_present(row, "npi"))
    if npi is None:
        return None
    state = nonempty(first_present(row, "state", "st"))
    if state:
        state = state.upper()[:2]
    return {
        "npi": npi,
        "ind_pac_id": nonempty(first_present(row, "ind_pac_id")),
        "ind_enrl_id": nonempty(first_present(row, "ind_enrl_id")),
        "last_name": nonempty(first_present(row, "provider_last_name", "lst_nm", "last_name")),
        "first_name": nonempty(first_present(row, "provider_first_name", "frst_nm", "first_name")),
        "middle_name": nonempty(first_present(row, "provider_middle_name", "mid_nm", "middle_name")),
        "suffix": nonempty(first_present(row, "suff", "suffix")),
        "gender": normalize_gender(first_present(row, "gndr", "gender")),
        "credential": nonempty(first_present(row, "cred", "credential")),
        "med_sch": nonempty(first_present(row, "med_sch")),
        "grd_yr": parse_int(first_present(row, "grd_yr")),
        "pri_spec": nonempty(first_present(row, "pri_spec")),
        "telehlth": nonempty(first_present(row, "telehlth")),
        "org_pac_id": nonempty(first_present(row, "org_pac_id")),
        "num_org_mem": parse_int(first_present(row, "num_org_mem")),
        "adr_ln_1": nonempty(first_present(row, "adr_ln_1")),
        "adr_ln_2": nonempty(first_present(row, "adr_ln_2")),
        "city": nonempty(first_present(row, "city_town", "cty", "city")),
        "state": state,
        "zip": nonempty(first_present(row, "zip_code", "zip")),
        "phone": nonempty(first_present(row, "telephone_number", "phn_numbr", "phone")),
        "adrs_id": nonempty(first_present(row, "adrs_id")),
    }


def keep_pdc_clinician_row(parsed: Mapping, spine_npis: set[int] | None, market_state: str = MARKET_STATE) -> bool:
    if spine_npis is None:
        return (parsed.get("state") or "") == market_state
    if parsed["npi"] in spine_npis:
        return True
    return (parsed.get("state") or "") == market_state


def parse_facility_row(row: Mapping) -> dict | None:
    npi = parse_npi(first_present(row, "npi"))
    if npi is None:
        return None
    return {
        "npi": npi,
        "ind_pac_id": nonempty(first_present(row, "ind_pac_id")),
        "last_name": nonempty(first_present(row, "provider_last_name", "lst_nm", "last_name")),
        "first_name": nonempty(first_present(row, "provider_first_name", "frst_nm", "first_name")),
        "facility_type": nonempty(first_present(row, "facility_type")),
        "ccn": nonempty(
            first_present(
                row,
                "facility_affiliations_certification_number",
                "facility_affiliation_ccn",
            )
        ),
        "facility_type_ccn": nonempty(first_present(row, "facility_type_certification_number")),
    }


def parse_nppes_row(row: Mapping) -> dict | None:
    if str(first_present(row, "entity_type_code") or "").strip() != TYPE1_CODE:
        return None
    npi = parse_npi(first_present(row, "npi"))
    if npi is None:
        return None
    practice_state = nonempty(first_present(row, "provider_business_practice_location_address_state_name"))
    mailing_state = nonempty(first_present(row, "provider_business_mailing_address_state_name"))
    return {
        "npi": npi,
        "last_name": nonempty(first_present(row, "provider_last_name_legal_name", "provider_last_name")),
        "first_name": nonempty(first_present(row, "provider_first_name")),
        "middle_name": nonempty(first_present(row, "provider_middle_name")),
        "suffix": nonempty(first_present(row, "provider_name_suffix_text")),
        "credential": nonempty(first_present(row, "provider_credential_text")),
        "gender": normalize_gender(first_present(row, "provider_sex_code", "provider_gender_code")),
        "primary_taxonomy": nppes_primary_taxonomy(row),
        "practice_state": (practice_state or "")[:2].upper() or None,
        "mailing_state": (mailing_state or "")[:2].upper() or None,
        "last_update_date": parse_date(first_present(row, "last_update_date")),
        "deactivation_date": parse_date(first_present(row, "npi_deactivation_date")),
        "sole_proprietor": nonempty(first_present(row, "is_sole_proprietor")),
    }


def keep_nppes_row(parsed: Mapping, spine_npis: set[int] | None, market_state: str = MARKET_STATE) -> bool:
    if spine_npis is not None and parsed["npi"] in spine_npis:
        return True
    return market_state in {
        parsed.get("practice_state"),
        parsed.get("mailing_state"),
    }


def better_pdc_identity(current: Mapping | None, candidate: Mapping) -> dict:
    if current is None:
        return dict(candidate)
    current_score = pdc_identity_score(
        state=current.get("state"),
        med_sch=current.get("med_sch"),
        grd_yr=current.get("grd_yr"),
        gender=current.get("gender"),
        phone=current.get("phone"),
    )
    candidate_score = pdc_identity_score(
        state=candidate.get("state"),
        med_sch=candidate.get("med_sch"),
        grd_yr=candidate.get("grd_yr"),
        gender=candidate.get("gender"),
        phone=candidate.get("phone"),
    )
    return dict(candidate) if candidate_score > current_score else dict(current)


def pdc_row_as_identity(row: Mapping) -> dict:
    return {
        "npi": row.get("npi"),
        "first_name": row.get("first_name"),
        "middle_name": row.get("middle_name"),
        "last_name": row.get("last_name"),
        "suffix": row.get("suffix"),
        "credential": row.get("credential"),
        "gender": row.get("gender"),
        "medical_school_name": row.get("med_sch"),
        "graduation_year": row.get("grd_yr"),
    }


def nppes_row_as_identity(row: Mapping) -> dict:
    return {
        "npi": row.get("npi"),
        "first_name": row.get("first_name"),
        "middle_name": row.get("middle_name"),
        "last_name": row.get("last_name"),
        "suffix": row.get("suffix"),
        "credential": row.get("credential"),
        "gender": row.get("gender"),
        "medical_school_name": None,
        "graduation_year": None,
    }


def iter_nppes_from_zip(path: Path) -> Iterator[dict]:
    with zipfile.ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv") and "npidata_pfile" in name.lower()
        ]
        if not names:
            raise FileNotFoundError(f"No npidata_pfile CSV inside {path}")
        with archive.open(names[0]) as raw:
            handle = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
            yield from iter_csv_rows(handle)


def iter_local_csv(path: Path) -> Iterator[dict]:
    with open_text(path) as handle:
        yield from iter_csv_rows(handle)
