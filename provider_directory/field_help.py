"""Help text for every field the API returns, in two tiers.

`help` is the client-facing hover text: what the number means and its scope.
`dev` is the developer tier: the rule, the thresholds, and the mart and
warehouse tables the calculation reads. The API serves the catalog from
GET /v1/field-help; `dev` text is included only when the request asks for
it and clears the dev gate (see api.py). The UI binds tooltips by scope and
JSON key, so this module is the single place to change wording.

Placeholders {window} and {prior_window} are filled from pd_refresh_state at
request time, e.g. "Nov 2024 to Oct 2025".

Table names: `az.` is the claims warehouse (`az_cms.` on the blend), `azal.`
the code lookup; unprefixed pd_* / cms_* tables are built by this pipeline.
"""

from __future__ import annotations

import re
from typing import Any

from provider_directory.settings import (
    NPPES_INDEX_URL,
    OPEN_PAYMENTS_SITE,
    PDC_CLINICIAN_DATASET,
    PDC_DATASET_PAGE,
    PDC_FACILITY_DATASET,
    PDC_MIPS_DATASET,
    PDC_UTILIZATION_DATASET,
)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

SCOPES = ("provider", "group", "practice", "group_practice", "hospital_affiliation", "referral", "utilization")


def format_period(yyyymm: int | None) -> str:
    if yyyymm is None:
        return "?"
    year, month = divmod(int(yyyymm), 100)
    if not 1 <= month <= 12:
        return str(yyyymm)
    return f"{MONTHS[month - 1]} {year}"


def format_period_range(start: int | None, end: int | None) -> str:
    return f"{format_period(start)} to {format_period(end)}"


# ---------------------------------------------------------------------------
# Table registry. Every table a dev string names must be here, or the
# catalog test fails. kind: warehouse (your claims / lookup databases),
# mart (built by this pipeline in {st}_pd), cms (public CMS file loaded
# into the mart; external, with the dataset page it came from).
# ---------------------------------------------------------------------------

def _pdc(dataset_id: str) -> str:
    return PDC_DATASET_PAGE.format(dataset_id=dataset_id)


TABLE_REGISTRY: dict[str, dict[str, str]] = {
    # warehouse: claims database
    "claims.physician": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Provider master: every NPI that has billed, with names and specialty group."},
    "claims.pat_dt": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Claim lines. One row per service line with encounter, patient, rendering and referring NPI, codes, site, date and payer."},
    "claims.sl": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Service locations: address, geo, place-of-service type, specialty rollup, hospital system."},
    "claims.payor": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Payer master: payer code to name, parent name and payer class 1 to 5."},
    "claims.dash_physician_payor_all": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Monthly claim counts by rendering NPI and payer (summary table)."},
    "claims.dash_physician_referrals_to_rendering": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Monthly implied referral edges: referring NPI to rendering NPI with patient and claim counts."},
    "claims.physician_primary_affiliation": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "One row per NPI: primary professional and institutional billing org and hospital system."},
    "claims.physician_affiliation_p": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Every professional billing org an NPI billed under, with cases and last month."},
    "claims.physician_affiliation_i": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Every institutional billing org an NPI billed under, with cases and last month."},
    "claims.provider_facility_npi": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Facility NPIs with DBA and hospital system names."},
    "claims.period": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Loaded months; its max drives the refresh window."},
    # warehouse: lookup database
    "lookup.npi_spec_grp": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Specialty group code to name and taxonomy category."},
    "lookup.diagnosis": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "ICD-10 diagnosis code to description."},
    "lookup.procd": {"kind": "warehouse", "phase": "warehouse load, outside this pipeline", "description": "Procedure code to description and fee-schedule RVU columns."},
    # mart: built by this pipeline
    "pd_provider": {"kind": "mart", "phase": "Phase 1, overlaid by every later phase", "built_from": ['claims.physician', 'lookup.npi_spec_grp', 'cms_pdc_clinician', 'cms_nppes_type1', 'cms_pdc_facility_affil', 'claims.physician_primary_affiliation', 'pd_stg_visit', 'pd_stg_panel_patient', 'pd_stg_top_dx', 'pd_stg_top_px', 'pd_stg_npi_wrvu', 'pd_stg_npi_payor', 'pd_stg_npi_dow', 'pd_stg_npi_wrvu_prior', 'pd_stg_specialty_wrvu', 'pd_stg_npi_percentile', 'pd_stg_npi_visits_percentile', 'pd_stg_visit_site', 'cms_pdc_mips'], "description": "The provider profile, one row per Type 1 NPI. Phase 1 creates it; every later phase overlays columns."},
    "pd_provider_practice": {"kind": "mart", "phase": "Phase 3", "built_from": ['pd_stg_npi_sl', 'claims.sl', 'claims.provider_facility_npi', 'cms_pdc_clinician', 'pd_stg_site_wrvu', 'pd_stg_site_dow'], "description": "Top 5 practice sites per NPI (Phase 3), polished by Phases 4 and 5."},
    "pd_provider_group_practice": {"kind": "mart", "phase": "Phase 4", "built_from": ['claims.physician_affiliation_p', 'claims.physician_affiliation_i', 'pd_provider'], "description": "Billing organizations per NPI in the window (Phase 4)."},
    "pd_provider_hospital_affiliation": {"kind": "mart", "phase": "Phase 4", "built_from": ['pd_stg_npi_sl', 'claims.sl', 'claims.provider_facility_npi', 'pd_provider'], "description": "Top 5 distinct health systems per NPI by visits (Phase 4)."},
    "pd_provider_referral": {"kind": "mart", "phase": "Phase 5", "built_from": ['pd_stg_referral_edge', 'pd_provider'], "description": "Top 3 referral peers per NPI per direction (Phase 5)."},
    "pd_provider_utilization": {"kind": "mart", "phase": "extras", "built_from": ['cms_pdc_utilization'], "description": "Care Compare categories per NPI (extras)."},
    "pd_group_practice": {"kind": "mart", "phase": "Phase 2, 4, 6 and overlay-cms", "built_from": ['pd_provider'], "description": "Precomputed group-practice dump, one row per org per member scope."},
    "pd_network_npi": {"kind": "mart", "phase": "Phase 1", "built_from": ['cms_pdc_facility_affil', 'pd_provider'], "description": "NPI to hospital CCN from PDC facility affiliations (Phase 1)."},
    "pd_refresh_state": {"kind": "mart", "phase": "Phase 6", "built_from": ['claims.period'], "description": "Current window and refresh watermark (Phase 6)."},
    "pd_stg_window_claim": {"kind": "mart", "phase": "Phase 2", "built_from": ['claims.pat_dt'], "description": "Claim lines for the window, copied from claims.pat_dt (Phase 2)."},
    "pd_stg_visit": {"kind": "mart", "phase": "Phase 2", "built_from": ['pd_stg_window_claim'], "description": "One row per encounter: rendering NPI, one diagnosis, one procedure (Phase 2)."},
    "pd_stg_panel_patient": {"kind": "mart", "phase": "Phase 2", "built_from": ['pd_stg_window_claim', 'pd_provider'], "description": "Distinct patients per NPI with age and gender (Phase 2)."},
    "pd_stg_top_dx": {"kind": "mart", "phase": "Phase 2", "built_from": ['pd_stg_visit', 'lookup.diagnosis'], "description": "Top 3 diagnoses per NPI with visit counts (Phase 2)."},
    "pd_stg_top_px": {"kind": "mart", "phase": "Phase 2", "built_from": ['pd_stg_visit', 'lookup.procd'], "description": "Top 3 procedures per NPI with visit counts (Phase 2)."},
    "pd_stg_visit_site": {"kind": "mart", "phase": "Phase 3", "built_from": ['pd_stg_visit', 'pd_stg_window_claim'], "description": "One service location per encounter, the modal one (Phase 3)."},
    "pd_stg_npi_sl": {"kind": "mart", "phase": "Phase 3", "built_from": ['pd_stg_visit_site', 'claims.sl'], "description": "Visits per NPI per location with the site cluster key (Phase 3)."},
    "pd_stg_npi_wrvu": {"kind": "mart", "phase": "Phase 4", "built_from": ['pd_stg_visit', 'lookup.procd'], "description": "RVU total and contributing visits per NPI (Phase 4)."},
    "pd_stg_site_wrvu": {"kind": "mart", "phase": "Phase 4", "built_from": ['pd_stg_visit_site', 'pd_stg_visit', 'lookup.procd'], "description": "RVU per NPI per site (Phase 4)."},
    "pd_stg_npi_payor": {"kind": "mart", "phase": "Phase 4", "built_from": ['claims.dash_physician_payor_all', 'claims.payor', 'claims.pat_dt'], "description": "Claim counts per NPI per payer class and parent (Phase 4)."},
    "pd_stg_visit_date": {"kind": "mart", "phase": "Phase 5", "built_from": ['claims.pat_dt', 'pd_stg_visit'], "description": "Service date per encounter, cached (Phase 5)."},
    "pd_stg_npi_dow": {"kind": "mart", "phase": "Phase 5", "built_from": ['pd_stg_visit_date', 'pd_stg_visit'], "description": "Visits per NPI per weekday (Phase 5)."},
    "pd_stg_site_dow": {"kind": "mart", "phase": "Phase 5", "built_from": ['pd_stg_visit_date', 'pd_stg_visit_site'], "description": "Visits per NPI per site per weekday (Phase 5)."},
    "pd_stg_referral_edge": {"kind": "mart", "phase": "Phase 5", "built_from": ['claims.dash_physician_referrals_to_rendering'], "description": "Referral edges summed over the window (Phase 5)."},
    "pd_stg_npi_wrvu_prior": {"kind": "mart", "phase": "Phase 5", "built_from": ['claims.pat_dt', 'lookup.procd'], "description": "Prior-year RVU per NPI, cached (Phase 5)."},
    "pd_stg_specialty_wrvu": {"kind": "mart", "phase": "Phase 5", "built_from": ['pd_provider'], "description": "Specialty RVU average, p25, median, p75 and count (Phase 5)."},
    "pd_stg_npi_percentile": {"kind": "mart", "phase": "Phase 5", "built_from": ['pd_provider'], "description": "RVU percentile per NPI within specialty (Phase 5)."},
    "pd_stg_npi_visits_percentile": {"kind": "mart", "phase": "Phase 5", "built_from": ['pd_provider'], "description": "Visits percentile per NPI within specialty (Phase 5)."},
    # cms: external public files loaded into the mart
    "cms_pdc_clinician": {"kind": "cms", "phase": "Phase 1", "source": "CMS Provider Data Catalog: National Downloadable File (doctors and clinicians)", "url": _pdc(PDC_CLINICIAN_DATASET),
                          "description": "Clinician identity, school, graduation year, group PAC id and size, telehealth, secondary specialties. Loaded by Phase 1, filtered to spine and in-state NPIs."},
    "cms_pdc_facility_affil": {"kind": "cms", "phase": "Phase 1", "source": "CMS Provider Data Catalog: Facility Affiliation", "url": _pdc(PDC_FACILITY_DATASET),
                               "description": "Clinician to hospital or facility affiliations with CCN. Loaded by Phase 1."},
    "cms_pdc_mips": {"kind": "cms", "phase": "extras", "source": "CMS Provider Data Catalog: Quality Payment Program MIPS performance", "url": _pdc(PDC_MIPS_DATASET),
                     "description": "MIPS final and quality scores by clinician and group. Loaded by extras."},
    "cms_pdc_utilization": {"kind": "cms", "phase": "extras", "source": "CMS Provider Data Catalog: Care Compare clinician utilization", "url": _pdc(PDC_UTILIZATION_DATASET),
                            "description": "Procedure categories with volume labels and percentiles. Loaded by extras."},
    "cms_nppes_type1": {"kind": "cms", "phase": "Phase 1", "source": "CMS NPPES monthly data dissemination file", "url": NPPES_INDEX_URL,
                        "description": "National NPI registry, Type 1 rows only: names, credential, sex, taxonomy, deactivation. Loaded by Phase 1."},
    "cms_open_payments": {"kind": "cms", "phase": "retired", "source": "CMS Open Payments", "url": OPEN_PAYMENTS_SITE,
                          "description": "Retired. No longer loaded; stays empty."},
}

_QUALIFIED_RE = re.compile(r"\b(az_cms|az|azal)\.([a-z_]+[a-z])")
_MART_RE = re.compile(r"\bpd_[a-z_]*[a-z_]")
_CMS_RE = re.compile(r"\bcms_[a-z0-9_]*[a-z0-9]")


def tables_in(text: str) -> list[str]:
    """Registry keys named in a dev string, in first-mention order. Unknown names raise."""
    found: list[str] = []

    def add(key: str) -> None:
        if key not in TABLE_REGISTRY:
            raise KeyError(f"table {key!r} is not in TABLE_REGISTRY")
        if key not in found:
            found.append(key)

    for db, table in _QUALIFIED_RE.findall(text):
        add(("lookup." if db == "azal" else "claims.") + table)
    for match in _MART_RE.findall(text) + _CMS_RE.findall(text):
        if match.endswith("_"):
            for key in TABLE_REGISTRY:
                if key.startswith(match):
                    add(key)
        elif match in TABLE_REGISTRY:
            add(match)
        elif match.rstrip("_") in TABLE_REGISTRY:
            add(match.rstrip("_"))
        else:
            raise KeyError(f"table {match!r} is not in TABLE_REGISTRY")
    return found


def describe_table(key: str, *, claims_db: str = "az", lookup_db: str = "azal") -> dict[str, Any]:
    meta = TABLE_REGISTRY[key]
    if key.startswith("claims."):
        name = f"{claims_db}.{key[len('claims.'):]}"
    elif key.startswith("lookup."):
        name = f"{lookup_db}.{key[len('lookup.'):]}"
    else:
        name = key
    out: dict[str, Any] = {
        "key": key,
        "name": name,
        "kind": meta["kind"],
        "external": meta["kind"] == "cms",
        "phase": meta.get("phase", ""),
        "description": meta["description"],
    }
    if meta["kind"] == "cms":
        out["source"] = meta["source"]
        out["url"] = meta["url"]
    if meta["kind"] == "mart":
        out["built_from"] = [_qualify(k, claims_db=claims_db, lookup_db=lookup_db) for k in meta.get("built_from", [])]
    return out


def _qualify(key: str, *, claims_db: str = "az", lookup_db: str = "azal") -> str:
    if key.startswith("claims."):
        return f"{claims_db}.{key[len('claims.'):]}"
    if key.startswith("lookup."):
        return f"{lookup_db}.{key[len('lookup.'):]}"
    return key


# Profile hubs aggregate every staging table; following their built_from from a
# field would list the whole mart under every field, so field lineage stops here.
_HUB_TABLES = frozenset({"pd_provider", "pd_group_practice"})


def table_closure(key: str, seen: set[str] | None = None) -> list[str]:
    """A table plus everything it is built from, through staging but not through the profile hubs."""
    seen = set() if seen is None else seen
    if key in seen:
        return []
    seen.add(key)
    out = [key]
    if key in _HUB_TABLES:
        return out
    for dep in TABLE_REGISTRY[key].get("built_from", []):
        for k in table_closure(dep, seen):
            if k not in out:
                out.append(k)
    return out


def _entry(label: str, help_text: str, dev: str) -> dict[str, str]:
    return {"label": label, "help": help_text, "dev": dev}


_VISIT = "A visit is one distinct encounter in the claims window {window}, credited to the rendering clinician on the encounter."
_PANEL = "Panel patients are distinct patients on any claim line in {window} where this clinician is the rendering or the referring physician."
_PANEL_DEV = "Grain: pd_stg_panel_patient (npi, pat_id) built from pd_stg_window_claim. A patient's age is the greatest age_code across their lines; gender is the first non-null gender_code."
_RVU_DEV = (
    "Per visit: azal.procd row for the visit's 5-character CPT/HCPCS px (procd_code = px). "
    "WORK_RVU when >= 0.05; else NON_FACILITY_TOTAL - NON_FAC_PE_RVU - MP_RVU when >= 0.05; "
    "else the FACILITY equivalent; else nf_total_rvu when >= 0.5. Visits with no RVU are excluded. "
    "Source: pd_stg_visit joined to azal.procd, summed into pd_stg_npi_wrvu (Phase 4)."
)
_PAYER_DEV = (
    "Share of claims in payer classes 1, 2, 3, 4, 6 and 7 (az.payor.is_payor_code: 1 Medicare FFS, 2 Medicaid, 3 Commercial, 4 HMO, 6 Medicare Advantage, 7 Medicare Other). "
    "Class 5 Other and unmapped codes are outside the denominator. "
    "Native markets: az.dash_physician_payor_all claim_count by rendering_physician_code in {window}. "
    "AZ_CMS: az_cms.pat_dt lines joined to az_cms.payor on encounter_payor_code (else payor_code), counted per distinct encounter and payer, "
    "because the blend's summary table only carries native claims. Staged in pd_stg_npi_payor."
)
_DOW_DEV = (
    "Visit date = earliest valid service_end_date (YYYYMMDD) across the encounter's az.pat_dt lines, cached in pd_stg_visit_date. "
    "Share = visits on that MariaDB DAYOFWEEK / visits with a date, x100, 2 decimals. Staged in pd_stg_npi_dow (Phase 5)."
)
_POS_DEV = (
    "Each visit's site (pd_stg_visit_site) bucketed by az.sl.pos_type_code: 11 office; 19, 22 HOPD; 24 ASC; 23 ED; 02, 10 telehealth; 21 inpatient; 81 lab; "
    "else by the site's work_type label; else other. Share = bucket / visits with a site. Built by extras."
)
_TOP_DX_DEV = (
    "pd_stg_visit grouped by the visit's dx (MAX encounter_diagnosis_code over the encounter's lines), ranked by visit count desc then code, top 3, "
    "in pd_stg_top_dx. Name from azal.diagnosis on diagnosis_code, cut to 80 characters."
)
_TOP_PX_DEV = (
    "pd_stg_visit grouped by the visit's px (MAX encounter_work_procd_code), ranked by visit count desc then code, top 3, in pd_stg_top_px. "
    "Name from azal.procd on procd_code, cut to 80 characters."
)
_TOP_PCT_DEV = "Computed at read time: pd_stg_top_dx / pd_stg_top_px visit_count / pd_provider.visits_total x100, 2 decimals. Not stored; shares of all visits, so the three do not add to 100."


def _weekday_entries(scope_note: str, dev_note: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        name = day.capitalize()
        out[f"visits_percent_{day}"] = _entry(
            f"{name} %",
            f"Share of {scope_note} that fell on a {name}, by service date. Saturday and Sunday together are the weekend share.",
            dev_note,
        )
    return out


PROVIDER: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician's 10-digit National Provider Identifier. Only individual (Type 1) NPIs are in the directory.",
                  "az.physician.physician_code where npi_type = 1, between 1000000000 and 9999999999, excluding dummies 0 and 4 and state XX. Primary key of pd_provider."),
    "first_name": _entry("First name", "Clinician first name as billed on claims; filled from CMS when claims have none.",
                         "pick_value(az.physician.first_name, cms_pdc_clinician.first_name, cms_nppes_type1.first_name) in transforms.merge_identity."),
    "middle_name": _entry("Middle name", "Middle name from claims, else CMS.", "Same merge order as first_name: az.physician, then cms_pdc_clinician, then cms_nppes_type1."),
    "last_name": _entry("Last name", "Last name from claims, else CMS.", "Same merge order as first_name: az.physician, then cms_pdc_clinician, then cms_nppes_type1; name_source records which one won."),
    "suffix": _entry("Suffix", "Name suffix such as Jr or III, from claims, else CMS.", "Same merge order as first_name: az.physician, then cms_pdc_clinician, then cms_nppes_type1."),
    "credential": _entry("Credential", "Degree or credential such as MD, DO, NP, from claims, else CMS.",
                         "az.physician.degree_abbr, else cms_pdc_clinician.cred, else cms_nppes_type1 credential text."),
    "gender": _entry("Provider gender", "The clinician's gender from CMS public files. Not the patient panel.",
                     "cms_pdc_clinician.gndr first, else cms_nppes_type1 sex code, normalized to M or F. The warehouse carries no gender."),
    "medical_school_name": _entry("Medical school", "Medical school as reported to CMS.",
                                  "cms_pdc_clinician.med_sch on the preferred PDC row (in-state, with school, grad year, gender, phone), else NPPES. 'OTHER' is treated as blank."),
    "medical_school_graduation_year": _entry("Graduation year", "Medical school graduation year as reported to CMS.",
                                             "cms_pdc_clinician.grd_yr, else cms_nppes_type1."),
    "estimated_age": _entry("Estimated age", "Approximate age, estimated from medical school graduation year. Not a date of birth.",
                            "2024 - grd_yr + 26 (REPORT_YEAR, GRAD_AGE_OFFSET), grd_yr from cms_pdc_clinician else cms_nppes_type1. Null when grd_yr is missing, before 1950 or after 2024. Capped at 90. transforms.estimated_age."),
    "primary_specialty_code": _entry("Specialty code", "Taxonomy code of the clinician's primary specialty.",
                                     "az.physician.npi_spec_grp_code. Also the peer group for the specialty percentiles."),
    "primary_specialty_description": _entry("Specialty", "The clinician's primary specialty.",
                                            "azal.npi_spec_grp.npi_spec_grp_name on npi_spec_grp_code, else az.physician.npi_spec_grp_name."),
    "specialty_classification": _entry("Specialty group", "Broader classification of the specialty, for example Internal Medicine.",
                                       "azal.npi_spec_grp.taxonomy_cat_name, else az.physician.taxonomy_cat_name."),
    "in_system_provider": _entry("In-system", "The clinician has a hospital affiliation on file with CMS.",
                                 "1 when any cms_pdc_facility_affil row exists for the NPI (PDC Facility Affiliation dataset 27ea-46a8). pd_network_npi holds the CCNs. Not a contracted-network flag."),
    "name_source": _entry("Name source", "Which source supplied the name: claims, CMS PDC, or NPPES.", "transforms.merge_identity: 'claims' (az.physician) unless the claims last name is blank, then 'pdc' (cms_pdc_clinician), then 'nppes' (cms_nppes_type1)."),
    "gender_source": _entry("Gender source", "Which CMS file supplied the gender.", "'pdc' when cms_pdc_clinician has a gender, else 'nppes' from cms_nppes_type1, else null."),
    "school_source": _entry("School source", "Which CMS file supplied the medical school.", "'pdc' when cms_pdc_clinician has a school, else 'nppes' from cms_nppes_type1, else null."),
    "active_provider": _entry("Active", "The clinician had at least one visit or one panel patient in {window}.",
                              "pd_provider.active_provider = 1 when visits_total > 0 or panel_size > 0 (Phase 2 overlay)."),
    "visits_total": _entry("Visits", _VISIT, "COUNT(*) of pd_stg_visit rows where rendering_npi = this NPI. pd_stg_visit is one row per encounter_id from pd_stg_window_claim; rendering_npi = MAX(encounter_rendering_physician_code)."),
    "visits_top_diagnosis_1": _entry("Top diagnosis 1", "Most common diagnosis code across the clinician's visits in {window}.", _TOP_DX_DEV),
    "visits_top_diagnosis_1_name": _entry("Top diagnosis 1", "Description of the most common diagnosis.", _TOP_DX_DEV),
    "visits_top_diagnosis_1_percent": _entry("Share of visits", "Share of the clinician's visits that carried this diagnosis.", _TOP_PCT_DEV),
    "visits_top_diagnosis_2": _entry("Top diagnosis 2", "Second most common diagnosis code.", _TOP_DX_DEV),
    "visits_top_diagnosis_2_name": _entry("Top diagnosis 2", "Description of the second most common diagnosis.", _TOP_DX_DEV),
    "visits_top_diagnosis_2_percent": _entry("Share of visits", "Share of visits that carried this diagnosis.", _TOP_PCT_DEV),
    "visits_top_diagnosis_3": _entry("Top diagnosis 3", "Third most common diagnosis code.", _TOP_DX_DEV),
    "visits_top_diagnosis_3_name": _entry("Top diagnosis 3", "Description of the third most common diagnosis.", _TOP_DX_DEV),
    "visits_top_diagnosis_3_percent": _entry("Share of visits", "Share of visits that carried this diagnosis.", _TOP_PCT_DEV),
    "visits_top_procedure_1": _entry("Top procedure 1", "Most common procedure (CPT/HCPCS) across the clinician's visits in {window}.", _TOP_PX_DEV),
    "visits_top_procedure_1_name": _entry("Top procedure 1", "Description of the most common procedure.", _TOP_PX_DEV),
    "visits_top_procedure_1_percent": _entry("Share of visits", "Share of the clinician's visits with this procedure.", _TOP_PCT_DEV),
    "visits_top_procedure_2": _entry("Top procedure 2", "Second most common procedure code.", _TOP_PX_DEV),
    "visits_top_procedure_2_name": _entry("Top procedure 2", "Description of the second most common procedure.", _TOP_PX_DEV),
    "visits_top_procedure_2_percent": _entry("Share of visits", "Share of visits with this procedure.", _TOP_PCT_DEV),
    "visits_top_procedure_3": _entry("Top procedure 3", "Third most common procedure code.", _TOP_PX_DEV),
    "visits_top_procedure_3_name": _entry("Top procedure 3", "Description of the third most common procedure.", _TOP_PX_DEV),
    "visits_top_procedure_3_percent": _entry("Share of visits", "Share of visits with this procedure.", _TOP_PCT_DEV),
    "panel_size": _entry("Panel size", _PANEL + " A clinician who only refers has a panel but no visits.", _PANEL_DEV + " panel_size = COUNT(*) per npi."),
    "panel_average_age": _entry("Average patient age", "Mean age of the clinician's panel patients.", _PANEL_DEV + " AVG(age_code) over ages 0 to 120, 1 decimal."),
    "panel_percent_age_0_19": _entry("Age 0 to 19", "Share of panel patients aged 0 to 19.", _PANEL_DEV + " Patients with age_code 0 to 19 / panel_size x100."),
    "panel_percent_age_20_44": _entry("Age 20 to 44", "Share of panel patients aged 20 to 44.", _PANEL_DEV + " age_code 20 to 44 / panel_size x100."),
    "panel_percent_age_45_64": _entry("Age 45 to 64", "Share of panel patients aged 45 to 64.", _PANEL_DEV + " age_code 45 to 64 / panel_size x100."),
    "panel_percent_age_65_84": _entry("Age 65 to 84", "Share of panel patients aged 65 to 84.", _PANEL_DEV + " age_code 65 to 84 / panel_size x100."),
    "panel_percent_age_85_plus": _entry("Age 85 and over", "Share of panel patients aged 85 or older.", _PANEL_DEV + " age_code 85 to 120 / panel_size x100."),
    "panel_percent_female": _entry("Female", "Share of panel patients recorded as female. Unknown gender stays in the denominator.", _PANEL_DEV + " gender_code in (F, FEMALE) / panel_size x100."),
    "panel_percent_male": _entry("Male", "Share of panel patients recorded as male.", _PANEL_DEV + " gender_code in (M, MALE) / panel_size x100."),
    "provider_practices_total": _entry("Sites", "Number of practice sites shown, at most 5.", "COUNT of pd_provider_practice rows for the NPI after ranking to MAX_PRACTICE_SITES = 5."),
    "wrvu_total": _entry("RVU sum (one procedure per visit)", "Sum of fee-schedule RVU across the clinician's visits in {window}, pricing one procedure per visit. Under review; not comparable to a wRVU report.", _RVU_DEV + " ROUND(SUM, 2)."),
    "wrvu_average": _entry("RVU per priced visit", "The RVU sum divided by the number of visits that had a priced procedure.", _RVU_DEV + " wrvu_total / wrvu_procedure_count, 3 decimals. The UI leads with median and percentile instead."),
    "wrvu_procedure_count": _entry("Priced visits", "Visits whose one procedure had a fee-schedule RVU and so contributed to the RVU sum.", _RVU_DEV + " COUNT(*) of contributing visits."),
    "visits_percent_third_party": _entry("Commercial", "Share of claims paid by commercial insurers.", _PAYER_DEV + " Class 3."),
    "visits_percent_medicaid": _entry("Medicaid", "Share of claims paid by Medicaid, including managed care.", _PAYER_DEV + " Class 2."),
    "visits_percent_medicare_advantage": _entry("Medicare Advantage", "Share of claims paid by Medicare Advantage plans.", _PAYER_DEV + " Class 6. Before the class-7 rework this column held class 4 (HMO)."),
    "visits_percent_medicare_traditional": _entry("Medicare FFS", "Share of claims paid by traditional fee-for-service Medicare.", _PAYER_DEV + " Class 1."),
    "visits_percent_hmo": _entry("HMO", "Share of claims paid by HMO plans.", _PAYER_DEV + " Class 4."),
    "visits_percent_medicare_other": _entry("Medicare Other", "Share of claims paid by other Medicare programs, neither fee-for-service nor Medicare Advantage.", _PAYER_DEV + " Class 7."),
    "top_payer_name_1": _entry("Top commercial payer 1", "Largest commercial payer parent by claims.", "Class 3 rows in pd_stg_npi_payor grouped by payor_parent_name (az.payor.payor_parent_name, else payor_name), ranked by claim count. Commercial only."),
    "top_payer_percent_1": _entry("Share of commercial", "This payer's share of the clinician's commercial claims, not of all claims.", "pd_stg_npi_payor claim_count / SUM(claim_count) over the NPI's class 3 rows x100."),
    "top_payer_name_2": _entry("Top commercial payer 2", "Second largest commercial payer parent.", "Rank 2 of the same pd_stg_npi_payor ranking."),
    "top_payer_percent_2": _entry("Share of commercial", "Share of the clinician's commercial claims.", "Same pd_stg_npi_payor formula, rank 2."),
    "top_payer_name_3": _entry("Top commercial payer 3", "Third largest commercial payer parent.", "Rank 3 of the same pd_stg_npi_payor ranking."),
    "top_payer_percent_3": _entry("Share of commercial", "Share of the clinician's commercial claims.", "Same pd_stg_npi_payor formula, rank 3."),
    "top_payer_name_4": _entry("Top commercial payer 4", "Fourth largest commercial payer parent.", "Rank 4 of the same pd_stg_npi_payor ranking."),
    "top_payer_percent_4": _entry("Share of commercial", "Share of the clinician's commercial claims.", "Same pd_stg_npi_payor formula, rank 4."),
    "top_payer_name_5": _entry("Top commercial payer 5", "Fifth largest commercial payer parent.", "Rank 5 of the same pd_stg_npi_payor ranking."),
    "top_payer_percent_5": _entry("Share of commercial", "Share of the clinician's commercial claims.", "Same pd_stg_npi_payor formula, rank 5."),
    "primary_organization_id": _entry("Organization id", "Billing identifier of the clinician's primary organization.",
                                      "az.physician_primary_affiliation: p_provider_billing_code when not a dummy, else i_provider_billing_code. Also the key for the group-practice profile."),
    "primary_organization_name": _entry("Organization", "The organization the clinician primarily bills under.", "Group practice name on the same az.physician_primary_affiliation row."),
    "primary_organization_npi": _entry("Organization NPI", "NPI of the primary billing organization.", "Same billing code as primary_organization_id, from az.physician_primary_affiliation."),
    "primary_organization_parent_id": _entry("Parent organization id", "Identifier of the parent organization of the primary billing organization.", "IP_hospital_system_code, else OP_hospital_system_code on az.physician_primary_affiliation."),
    "primary_organization_parent_name": _entry("Parent organization", "Parent organization of the primary billing organization, as recorded in the warehouse.", "IP_hospital_system_name, else OP_hospital_system_name on az.physician_primary_affiliation."),
    **_weekday_entries("the clinician's visits", _DOW_DEV),
    "wrvu_prior_year_total": _entry("Prior-year RVU sum", "The same one-procedure-per-visit RVU sum for the 12 months before the window, {prior_window}.", "Same RVU formula over az.pat_dt for {prior_window}, joined to azal.procd on procd_code, cached in pd_stg_npi_wrvu_prior (Phase 5)."),
    "wrvu_prior_year_average": _entry("Prior-year RVU per priced visit", "Prior-year RVU sum divided by prior-year priced visits, {prior_window}.", "pd_stg_npi_wrvu_prior total / procedure_count, 3 decimals."),
    "wrvu_prior_year_procedure_count": _entry("Prior-year priced visits", "Visits that contributed to the prior-year RVU sum.", "pd_stg_npi_wrvu_prior.procedure_count: contributing visits in {prior_window}."),
    "wrvu_yoy_change_pct": _entry("RVU sum change vs prior year", "Percent change of the RVU sum against the prior-year RVU sum.", "(pd_provider.wrvu_total - pd_stg_npi_wrvu_prior total) / prior x100, 2 decimals. Null when prior < 1.0; clamped to +/-9999.99. complete.yoy_change_sql."),
    "wrvu_state_specialty_average": _entry("Specialty mean of provider RVU sums", "Mean of the per-provider RVU sums among clinicians in the same specialty statewide. Heavily skewed by a few very high sums; the UI does not show it.", "AVG(wrvu_total) over pd_provider rows with the same primary_specialty_code and wrvu_total > 0, in pd_stg_specialty_wrvu."),
    "wrvu_state_specialty_median": _entry("Specialty median of provider RVU sums", "The middle value of the per-provider RVU sums among clinicians in the same specialty statewide. A benchmark of sums, not of RVU per procedure.", "pd_stg_specialty_wrvu: value at row FLOOR(n x 0.50) of the specialty's pd_provider.wrvu_total sorted ascending, n = NPIs with wrvu_total > 0."),
    "wrvu_state_specialty_p25": _entry("Specialty 25th percentile of provider RVU sums", "The per-provider RVU sum at the 25th percentile of the specialty.", "pd_stg_specialty_wrvu: value at row FLOOR(n x 0.25), same ranking."),
    "wrvu_state_specialty_p75": _entry("Specialty 75th percentile of provider RVU sums", "The per-provider RVU sum at the 75th percentile of the specialty.", "pd_stg_specialty_wrvu: value at row FLOOR(n x 0.75), same ranking."),
    "wrvu_state_specialty_npi_count": _entry("Providers in specialty with an RVU sum", "Number of clinicians in the specialty with a positive RVU sum.", "pd_stg_specialty_wrvu.npi_count: COUNT(*) of the specialty's pd_provider NPIs with wrvu_total > 0."),
    "wrvu_specialty_percentile": _entry("RVU sum percentile within specialty", "Where the clinician's RVU sum ranks among same-specialty clinicians. 100 is the highest.", "ROW_NUMBER over the specialty ordered by wrvu_total asc / specialty count x100, 1 decimal (pd_stg_npi_percentile). 93.9 = 93.9% of peers have a lower or equal total."),
    "visits_specialty_percentile": _entry("Visits percentile", "Where the clinician's visit count ranks within their specialty. 100 is the highest.", "Same rank on pd_provider.visits_total among specialty NPIs with visits_total > 0 (pd_stg_npi_visits_percentile)."),
    "is_primary_member": _entry("Primary member", "On a group member list, whether this organization is the clinician's primary billing organization. Blank outside a group list.", "Picker dump only: (pd_provider.primary_organization_id = requested organization_id) when organization_id is given, else NULL. With membership=any, false rows bill the group but count toward another group's totals."),
    "activity_specialty_percentile": _entry("Activity vs specialty peers (visits and RVU sum)", "Combined volume rank within the specialty: the average of the visits percentile and the RVU sum percentile. Volume, not quality.", "ROUND((pd_stg_npi_visits_percentile + pd_stg_npi_percentile) / 2, 1) when both exist, else whichever exists."),
    "group_size": _entry("Group size", "Number of clinicians in the group practice as reported to CMS.", "cms_pdc_clinician.num_org_mem on the preferred PDC row (extras)."),
    "telehealth_offered": _entry("Telehealth offered", "The clinician reports offering telehealth to CMS.", "cms_pdc_clinician.telehlth: Y/YES/TRUE/1 -> true, N/NO/FALSE/0 -> false, else null."),
    "secondary_specialty_1": _entry("Secondary specialty 1", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_1, loaded by extras --reload-pdc."),
    "secondary_specialty_2": _entry("Secondary specialty 2", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_2, loaded by extras --reload-pdc."),
    "secondary_specialty_3": _entry("Secondary specialty 3", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_3, loaded by extras --reload-pdc."),
    "secondary_specialty_4": _entry("Secondary specialty 4", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_4, loaded by extras --reload-pdc."),
    "visits_new_patient": _entry("New patient visits", "Office visits billed as new-patient evaluation and management.", "pd_stg_visit rows whose px is in 99202 to 99205 (extras)."),
    "visits_established": _entry("Established visits", "Office visits billed as established-patient evaluation and management.", "pd_stg_visit rows whose px is in 99212 to 99215 (extras)."),
    "visits_percent_new_patient": _entry("New patient %", "New-patient share of office evaluation and management visits.", "pd_stg_visit: visits_new_patient / (visits_new_patient + visits_established) x100. Null when both are zero."),
    "visits_percent_office": _entry("Office", "Share of visits in a physician office.", _POS_DEV + " Bucket office."),
    "visits_percent_hopd": _entry("Hospital outpatient", "Share of visits in a hospital outpatient department.", _POS_DEV + " Bucket hopd."),
    "visits_percent_asc": _entry("Ambulatory surgery", "Share of visits in an ambulatory surgery center.", _POS_DEV + " Bucket asc."),
    "visits_percent_ed": _entry("Emergency", "Share of visits in an emergency department.", _POS_DEV + " Bucket ed."),
    "visits_percent_telehealth": _entry("Telehealth", "Share of visits delivered by telehealth.", _POS_DEV + " Bucket telehealth."),
    "visits_percent_inpatient": _entry("Inpatient", "Share of visits in an inpatient hospital setting.", _POS_DEV + " Bucket inpatient."),
    "visits_percent_lab": _entry("Laboratory", "Share of visits at an independent laboratory.", _POS_DEV + " Bucket lab."),
    "visits_percent_other_pos": _entry("Other settings", "Share of visits in any other place of service.", _POS_DEV + " Bucket other."),
    "mips_final_score": _entry("MIPS final score", "CMS Merit-based Incentive Payment System final score, latest published year.", "cms_pdc_mips.final_score on npi, the individual row first, else the row for the NPI's org_pac_id (dataset a174-a962)."),
    "mips_quality_score": _entry("MIPS quality score", "CMS MIPS quality category score.", "cms_pdc_mips.quality_score, same row choice as the final score."),
    "open_payments_year": _entry("Open Payments year", "Not shown. Open Payments was retired from the directory.", "cms_open_payments is no longer loaded; always null. Omitted from HTTP GET."),
    "open_payments_general_total": _entry("Open Payments general", "Not shown; retired.", "cms_open_payments is no longer loaded; always null. Omitted from HTTP GET."),
    "open_payments_research_total": _entry("Open Payments research", "Not shown; retired.", "cms_open_payments is no longer loaded; always null. Omitted from HTTP GET."),
    "open_payments_ownership_total": _entry("Open Payments ownership", "Not shown; retired.", "cms_open_payments is no longer loaded; always null. Omitted from HTTP GET."),
    "open_payments_count": _entry("Open Payments count", "Not shown; retired.", "cms_open_payments is no longer loaded; always null. Omitted from HTTP GET."),
    "practices": _entry("Sites", "Up to 5 practice locations ranked by the clinician's visits there, from claims.", "pd_provider_practice rows, site_rank 1 to 5. See the practice scope."),
    "group_practices": _entry("Group practices", "Every organization the clinician billed under in {window}, primary first.", "pd_provider_group_practice rows. See the group_practice scope."),
    "hospital_affiliations": _entry("Facility affiliations (not shown)", "Not shown in the UI. A visit-based grouping of facilities whose parent grouping data is not reliable enough to display.", "pd_provider_hospital_affiliation rows. See the hospital_affiliation scope."),
    "referrals": _entry("Referrals", "Top 3 clinicians who refer to this clinician, and top 3 this clinician refers to.", "pd_provider_referral rows. See the referral scope."),
    "utilization": _entry("Care Compare utilization", "Not on the HTTP profile.", "pd_provider_utilization from cms_pdc_utilization (n0yb-util), top 5 by percentile. CLI get only."),
    # Picker (dump) rows only
    "practice_name": _entry("Primary site", "Name of the clinician's primary practice site.", "pd_provider_practice.name where site_rank = 1."),
    "city": _entry("City", "City of the clinician's primary practice site.", "pd_provider_practice.city where site_rank = 1."),
    "state": _entry("State", "State of the clinician's primary practice site.", "pd_provider_practice.state where site_rank = 1."),
}

GROUP_OVERRIDES: dict[str, dict[str, str]] = {
    "organization_id": _entry("Organization id", "Billing identifier of the group. Members are the clinicians whose primary organization this is.", "pd_provider.primary_organization_id. One pd_group_practice row per org per member scope (all / active / inactive)."),
    "organization_name": _entry("Organization", "Name of the group practice.", "MAX(pd_provider.primary_organization_name) over members, in pd_group_practice."),
    "organization_npi": _entry("Organization NPI", "NPI of the billing organization.", "MAX(pd_provider.primary_organization_npi) over members, in pd_group_practice."),
    "parent_id": _entry("Parent organization id", "Identifier of the group's parent organization.", "MAX(pd_provider.primary_organization_parent_id) over members, in pd_group_practice."),
    "parent_name": _entry("Parent organization", "Parent organization of the group, as recorded in the warehouse.", "MAX(pd_provider.primary_organization_parent_name) over members, in pd_group_practice."),
    "provider_count": _entry("Clinicians", "Number of individual clinicians whose primary organization is this group.", "COUNT(*) of pd_provider rows with this primary_organization_id, in the requested member scope."),
    "active_provider_count": _entry("Active clinicians", "Members with at least one visit or panel patient in {window}.", "SUM(pd_provider.active_provider = 1) over members, in pd_group_practice."),
    "in_system_provider_count": _entry("In-system clinicians", "Members with a CMS hospital affiliation.", "SUM(pd_provider.in_system_provider = 1) over members, in pd_group_practice."),
    "visits_total": _entry("Visits", "Sum of member clinicians' visits in {window}. An encounter billed by two members counts twice.", "SUM(IFNULL(pd_provider.visits_total, 0)) over members, precomputed in pd_group_practice."),
    "panel_size": _entry("Panel size", "Sum of member clinicians' panel sizes. Patients seen by two members count twice.", "SUM(IFNULL(pd_provider.panel_size, 0)) over members, in pd_group_practice."),
    "wrvu_total": _entry("Sum of members' RVU sums", "Sum of the member clinicians' one-procedure-per-visit RVU sums. Under review; not comparable to a wRVU report.", "SUM(IFNULL(pd_provider.wrvu_total, 0)) over members, in pd_group_practice."),
    "visits_per_provider": _entry("Visits per clinician", "Group visits divided by number of member clinicians.", "pd_group_practice.visits_total / provider_count, 1 decimal."),
    "visits_percentile": _entry("Visits percentile", "Where this group's visit total ranks among all groups. 100 is the highest.", "pd_group_practice: ROW_NUMBER over groups with visits_total > 0 ordered by visits_total / count x100, 1 decimal. Among groups, not specialty peers."),
    "activity_percentile": _entry("Activity percentile", "Where this group's visits per clinician ranks among all groups. 100 is the highest.", "pd_group_practice: same rank on visits_total / provider_count."),
    "visits_are_summed_across_npis": _entry("Summed across clinicians", "Volume on this page is summed across member clinicians.", "Always true on the group payload; no table."),
    "member_count_any": _entry("Clinicians billing here", "Number of clinicians with any billing under this organization in {window}, primary or not. The totals on this page use only primary members.", "Group profile only: COUNT(DISTINCT npi) of pd_provider_group_practice rows for the organization_id that exist in pd_provider. The picker with membership=any returns this many."),
    "primary_specialty_code": _entry("Specialty code", "Taxonomy code of the group's most common member specialty.", "pd_provider.primary_specialty_code of members, the code with the most summed visits."),
    "primary_specialty_description": _entry("Specialty", "The group's most common member specialty, by visits.", "pd_provider.primary_specialty_description for the member specialty code with the highest summed visits."),
    "in_system_provider": _entry("In-system", "At least one member has a CMS hospital affiliation.", "pd_group_practice.in_system_provider_count >= 1."),
    "active_provider": _entry("Active", "At least one member was active in {window}.", "pd_group_practice.active_provider_count >= 1."),
    "group_size": _entry("Group size", "Largest CMS-reported group size among members.", "MAX(pd_provider.group_size) over members, not provider_count."),
    "telehealth_offered": _entry("Telehealth offered", "At least one member reports offering telehealth to CMS.", "MAX(pd_provider.telehealth_offered) over members."),
    "wrvu_average": _entry("RVU per priced visit, members combined", "Sum of members' RVU sums divided by members' priced visits.", "SUM(pd_provider.wrvu_total) / SUM(pd_provider.wrvu_procedure_count) over members."),
    "wrvu_procedure_count": _entry("Priced visits, members combined", "Sum of member visits that contributed to an RVU sum.", "SUM(pd_provider.wrvu_procedure_count) over members."),
}

_GROUP_PCT_NOTE = " On the group page this is weighted across members by their visits."


def _group_scope() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for key, entry in PROVIDER.items():
        if key in ("visits_top_diagnosis_1_percent", "visits_top_diagnosis_2_percent", "visits_top_diagnosis_3_percent",
                   "visits_top_procedure_1_percent", "visits_top_procedure_2_percent", "visits_top_procedure_3_percent"):
            out[key] = _entry(entry["label"], entry["help"] + " On the group page this is a lower bound: only members' stored top 3 codes are summed.",
                              entry["dev"] + " Group: SUM of members' pd_stg_top_* visit_count for the code / group visits_total.")
        elif key.startswith("panel_percent_") or key.startswith("visits_percent_") or key == "panel_average_age":
            out[key] = _entry(entry["label"], entry["help"] + _GROUP_PCT_NOTE, entry["dev"] + " Group: member percent weighted by member visits_total (group_profile.weighted_pct_sql).")
        else:
            out[key] = dict(entry)
    out.update(GROUP_OVERRIDES)
    return out


PRACTICE: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this site belongs to.", "pd_provider_practice.npi."),
    "site_rank": _entry("Rank", "Site order by the clinician's visits there. 1 is the primary site.", "pd_stg_npi_sl: ROW_NUMBER per npi over street+ZIP clusters by visits desc, then lowest sl_code; ranks past 5 dropped into pd_provider_practice (Phase 3)."),
    "sl_code": _entry("Location code", "Warehouse code of the busiest location in this site.", "az.sl.sl_code of the cluster's canonical location (most visits)."),
    "cluster_key": _entry("Site key", "Key that groups suite variants of one address into a single site.", "From az.sl street, zip_code, latitude, longitude: 'a:' normalized street|ZIP5 when both exist; else 'g:' lat,lon rounded to 4 decimals; else 's:' sl_code. transforms.cluster_key."),
    "name": _entry("Site name", "Name of the practice site.", "az.sl.sl_dba_name, then az.sl.sl_name for Type 2 locations. A Type 1 site named LAST, FIRST is rewritten to street and city in Phase 4."),
    "street": _entry("Street", "Street address of the site.", "az.sl.street on the canonical location."),
    "city": _entry("City", "City of the site.", "az.sl.city, text before the first comma."),
    "county": _entry("County", "County of the site.", "az.sl.county_name, text before the first comma."),
    "state": _entry("State", "State of the site.", "az.sl.state_abbr on the canonical location."),
    "zip": _entry("ZIP", "5-digit ZIP code of the site.", "LEFT(az.sl.zip_code, 5)."),
    "latitude": _entry("Latitude", "Latitude of the site. Not used by the UI in v1.", "az.sl.latitude on the cluster's canonical location."),
    "longitude": _entry("Longitude", "Longitude of the site. Not used by the UI in v1.", "az.sl.longitude on the cluster's canonical location."),
    "phone": _entry("Phone", "Phone number from CMS for this clinician at this address, when one matches. Blank means no match; never invented.", "cms_pdc_clinician phone where the PDC row shares the site's ZIP5 and either the normalized street or the city."),
    "work_type": _entry("Work type", "Type of care setting, from the place-of-service code on claims.", "az.sl.pos_type_code mapped via POS_WORK_TYPE (11 Office, 22 Hospital Outpatient, 23 Emergency Department, 24 Ambulatory Surgery Center, ...); else keyword match on az.sl.im_specialty_rollup; else null."),
    "visits_at_site": _entry("Visits at site", "The clinician's visits at this site in {window}.", "SUM of pd_stg_npi_sl.visits over the cluster's locations. Each visit's location is the modal sl_code across its claim lines (pd_stg_visit_site)."),
    "visit_share_pct": _entry("Visit share", "Share of the clinician's visits that happened at this site. Sites past the top 5 and visits with no location keep the shares from adding to 100.", "visits_at_site / pd_provider.visits_total x100, 2 decimals."),
    "npi_type": _entry("Location type", "Whether the location code is an organization (2) or an individual (1).", "az.sl.npi_type of the canonical location: 2 = organization, 1 = individual."),
    "location_source": _entry("Location source", "Where the address came from. Always claims today.", "Constant 'claims_sl' on pd_provider_practice. Reserved for geocoded or CMS-sourced sites."),
    "location_flag": _entry("Location status", "Confidence flag for the address. Always claims-confirmed today.", "Constant 'claims_confirmed' on pd_provider_practice."),
    "phone_source": _entry("Phone source", "Where the phone came from: CMS PDC, or blank.", "'pdc' when the cms_pdc_clinician match found a phone, else null."),
    "needs_geocode": _entry("Needs geocode", "The warehouse has no coordinates for this location.", "az.sl.latitude or longitude is null."),
    "wrvu_at_site": _entry("RVU sum at site", "The clinician's one-procedure-per-visit RVU sum over visits at this site.", "SUM of visit-grain RVU over visits at the site (pd_stg_site_wrvu, Phase 4)."),
    "wrvu_share_pct": _entry("Share of RVU sum at site", "This site's part of the clinician's RVU sum.", "wrvu_at_site / pd_provider.wrvu_total x100, 2 decimals."),
    **_weekday_entries("the clinician's visits at this site", "pd_stg_site_dow: visits at the site by DAYOFWEEK of the visit date / visits at the site with a date x100 (Phase 5)."),
}

GROUP_PRACTICE: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this organization row belongs to.", "pd_provider_group_practice.npi."),
    "org_rank": _entry("Rank", "Order of the clinician's organizations: primary first.", "pd_provider_group_practice: ROW_NUMBER per npi: is_primary first, then more cases, then later max_period_code (Phase 4)."),
    "organization_id": _entry("Organization id", "Billing identifier of the organization.", "provider_billing_code on az.physician_affiliation_p / az.physician_affiliation_i, or pd_provider.primary_organization_id for the primary row."),
    "organization_npi": _entry("Organization NPI", "NPI of the billing organization.", "Same billing code as organization_id, from az.physician_affiliation_p / az.physician_affiliation_i."),
    "organization_name": _entry("Organization", "Name of the organization the clinician billed under.", "dba_name on the az.physician_affiliation_p / az.physician_affiliation_i row, or pd_provider.primary_organization_name."),
    "billing_type": _entry("Billing type", "P for professional claims, I for institutional claims. Blank when the row came only from the primary affiliation.", "'P' from az.physician_affiliation_p, 'I' from az.physician_affiliation_i. P beats I when both exist for one org."),
    "is_primary": _entry("Primary", "This is the clinician's primary billing organization.", "pd_provider_group_practice.organization_id = pd_provider.primary_organization_id."),
    "cases": _entry("Cases", "Case count the warehouse attributes to the clinician at this organization.", "count_of_cases on the az.physician_affiliation_p / az.physician_affiliation_i row; null for the primary-only row."),
    "max_period_code": _entry("Last month billed", "Latest month (YYYYMM) the clinician billed under this organization.", "max_period_code on the az.physician_affiliation_p / az.physician_affiliation_i row; must be >= window_start to be listed."),
}

HOSPITAL_AFFILIATION: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this affiliation belongs to.", "pd_provider_hospital_affiliation.npi."),
    "affiliation_rank": _entry("Rank", "Not shown in the UI. Affiliated organizations ordered by the clinician's visits there, at most 5.", "pd_provider_hospital_affiliation: ROW_NUMBER per npi by visits_at_system desc, then system name (Phase 4)."),
    "hospital_system_name": _entry("Affiliated organization (not shown)", "Not shown in the UI. Parent organization of the facilities where the clinician's visits took place; facilities of one parent are combined.", "az.sl.sl_hospital_system_name, else hospital system name on az.provider_facility_npi joined on sl_code; blank and Unknown dropped; grouped case-insensitively across pd_stg_npi_sl sites."),
    "facility_name": _entry("Facility", "The busiest facility within the organization, when it has its own name.", "az.sl DBA or name (or az.provider_facility_npi DBA) of the site with the most visits in the system; null when it equals the system name."),
    "sl_code": _entry("Location code", "Warehouse code of the busiest campus.", "pd_stg_npi_sl.sl_code of the site with the most visits in the system."),
    "visits_at_system": _entry("Visits at organization", "The clinician's visits across all sites of this organization in {window}.", "SUM(pd_stg_npi_sl.visits) over the system's sites. Parent fallback rows carry visits_total."),
    "visit_share_pct": _entry("Visit share", "Share of the clinician's visits that took place at this organization.", "pd_provider_hospital_affiliation.visits_at_system / pd_provider.visits_total x100. Null on the parent-fallback row."),
    "provider_count": _entry("Clinicians", "On a group page, number of member clinicians affiliated with this organization.", "Group profile only: COUNT(DISTINCT npi) of pd_provider_hospital_affiliation members with the system. Null on a provider."),
}

REFERRAL: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this referral row belongs to.", "pd_provider_referral.npi."),
    "direction": _entry("Direction", "in: clinicians who send patients to this clinician. out: clinicians this clinician sends patients to.", "'in' = this NPI is rendering_physician_code; 'out' = this NPI is implied_all_referring_physician_code on az.dash_physician_referrals_to_rendering."),
    "peer_rank": _entry("Rank", "Top 3 per direction by patients.", "pd_stg_referral_edge: ROW_NUMBER per npi and direction by patient_count desc, claim_count desc, peer_npi, into pd_provider_referral (Phase 5)."),
    "peer_npi": _entry("Peer NPI", "NPI of the referring or receiving clinician.", "The other NPI on the az.dash_physician_referrals_to_rendering edge. Both must be Type 1, neither a dummy, not the same NPI."),
    "peer_name": _entry("Peer", "Name of the referring or receiving clinician.", "pd_provider last_name, first_name of the peer."),
    "peer_specialty": _entry("Peer specialty", "Specialty of the referring or receiving clinician.", "pd_provider.primary_specialty_description of the peer."),
    "patient_count": _entry("Patients", "Patients referred between the two clinicians in {window}. Monthly counts are summed, so a patient referred in three months counts three times.", "SUM(patient_count) over window months on az.dash_physician_referrals_to_rendering, staged in pd_stg_referral_edge."),
    "claim_count": _entry("Claims", "Claims behind the referral relationship in {window}.", "SUM(claim_count) over window months on az.dash_physician_referrals_to_rendering."),
}

UTILIZATION: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this utilization row belongs to.", "pd_provider_utilization.npi."),
    "rk": _entry("Rank", "Procedure categories ordered by CMS percentile.", "cms_pdc_utilization: ROW_NUMBER per npi by percentile desc, category; top 5 into pd_provider_utilization (extras)."),
    "procedure_category": _entry("Procedure category", "Care Compare procedure category.", "cms_pdc_utilization.procedure_category (dataset n0yb-util)."),
    "count_label": _entry("Volume", "CMS volume label for the category.", "cms_pdc_utilization.count_label."),
    "percentile": _entry("Percentile", "CMS percentile for the category.", "cms_pdc_utilization.percentile."),
    "profile_display": _entry("Display flag", "CMS profile display indicator.", "cms_pdc_utilization.profile_display_indicator."),
}


# ---------------------------------------------------------------------------
# Caveats (shown as notes) and lineage (which entries a field is built on).
# Keys are (scope, field). Family rules below fill repeated patterns.
# ---------------------------------------------------------------------------

_RVU_SCALE = "Total-RVU scale, one work procedure per visit. Not a CMS physician work RVU report."
_RVU_EXCL = "Visits whose procedure has no fee-schedule RVU are excluded."
_DOUBLE = "An encounter billed by two members of the group counts twice."
_VOLUME = "A volume rank, not a quality measure."
_WEIGHTED = "Weighted by each member's visits."

CAVEATS: dict[tuple[str, str], list[str]] = {
    ("provider", "visits_total"): ["Only encounters with a Type 1 rendering clinician are counted.", "An encounter is one visit however many claim lines it has."],
    ("provider", "panel_size"): ["Wider than visits: a clinician who only refers has a panel but no visits."],
    ("provider", "panel_percent_female"): ["Unknown gender stays in the denominator, so female and male may not sum to 100."],
    ("provider", "panel_percent_male"): ["Unknown gender stays in the denominator, so female and male may not sum to 100."],
    ("provider", "wrvu_total"): [_RVU_SCALE, _RVU_EXCL],
    ("provider", "wrvu_average"): [_RVU_SCALE, "The UI leads with the specialty median and percentile instead."],
    ("provider", "wrvu_procedure_count"): [_RVU_EXCL],
    ("provider", "wrvu_prior_year_total"): [_RVU_SCALE, _RVU_EXCL],
    ("provider", "wrvu_prior_year_average"): [_RVU_SCALE],
    ("provider", "wrvu_yoy_change_pct"): ["Null when the prior-year total is under 1.0; clamped to plus or minus 9999.99."],
    ("provider", "wrvu_state_specialty_average"): ["Skewed by a few very high totals; the median is the better centre.", _RVU_SCALE],
    ("provider", "wrvu_state_specialty_median"): [_RVU_SCALE, "Peers are same-specialty clinicians with a positive RVU total."],
    ("provider", "wrvu_state_specialty_p25"): [_RVU_SCALE],
    ("provider", "wrvu_state_specialty_p75"): [_RVU_SCALE],
    ("provider", "wrvu_specialty_percentile"): [_VOLUME, "Peers are same-specialty clinicians with a positive RVU total."],
    ("provider", "visits_specialty_percentile"): [_VOLUME, "Peers are same-specialty clinicians with at least one visit."],
    ("provider", "activity_specialty_percentile"): [_VOLUME, "Null when the clinician has no specialty, or no visits and no RVU."],
    ("provider", "visits_percent_third_party"): ["Class 5 Other and unmapped payer classes are outside the denominator."],
    ("provider", "visits_percent_medicaid"): ["Class 5 Other and unmapped payer classes are outside the denominator."],
    ("provider", "visits_percent_medicare_advantage"): ["Class 5 Other and unmapped payer classes are outside the denominator.", "Payer class 6. HMO (class 4) is a separate column."],
    ("provider", "visits_percent_medicare_traditional"): ["Class 5 Other and unmapped payer classes are outside the denominator."],
    ("provider", "visits_percent_hmo"): ["Class 5 Other and unmapped payer classes are outside the denominator.", "Payer class 4, which is not Medicare Advantage."],
    ("provider", "visits_percent_medicare_other"): ["Class 5 Other and unmapped payer classes are outside the denominator."],
    ("provider", "top_payer_percent_1"): ["A share of commercial claims, not of all claims."],
    ("provider", "top_payer_percent_2"): ["A share of commercial claims, not of all claims."],
    ("provider", "top_payer_percent_3"): ["A share of commercial claims, not of all claims."],
    ("provider", "top_payer_percent_4"): ["A share of commercial claims, not of all claims."],
    ("provider", "top_payer_percent_5"): ["A share of commercial claims, not of all claims."],
    ("group", "member_count_any"): ["Counts every clinician with a billing row for the group; totals on the page count only primary members."],
    ("provider", "estimated_age"): ["An estimate from medical school graduation year, not a date of birth."],
    ("provider", "gender"): ["The clinician's gender from CMS, not the patient panel."],
    ("provider", "in_system_provider"): ["A hospital affiliation on file with CMS, not a contracted-network flag."],
    ("provider", "group_size"): ["CMS-reported group size, not the number of members in this directory."],
    ("provider", "mips_final_score"): ["Latest published CMS program year, which lags the claims window."],
    ("provider", "mips_quality_score"): ["Latest published CMS program year, which lags the claims window."],
    ("provider", "visits_percent_new_patient"): ["Office evaluation and management codes only; procedure-only visits fall in neither bucket."],
    ("provider", "visits_new_patient"): ["Office evaluation and management codes only."],
    ("provider", "visits_established"): ["Office evaluation and management codes only."],
    ("group", "visits_total"): [_DOUBLE],
    ("group", "panel_size"): ["A patient seen by two members of the group counts twice."],
    ("group", "wrvu_total"): [_DOUBLE, _RVU_SCALE, _RVU_EXCL],
    ("group", "wrvu_average"): [_RVU_SCALE],
    ("group", "visits_per_provider"): [_DOUBLE],
    ("group", "visits_percentile"): [_VOLUME, "Among all groups, not specialty peers."],
    ("group", "activity_percentile"): [_VOLUME, "Among all groups, not specialty peers."],
    ("group", "group_size"): ["Largest CMS-reported group size among members, not the member count."],
    ("group", "provider_count"): ["Members are clinicians whose primary billing organization is this group."],
    ("practice", "visit_share_pct"): ["Sites past the top 5 and visits with no location keep the shares from summing to 100."],
    ("practice", "wrvu_share_pct"): [_RVU_SCALE],
    ("practice", "wrvu_at_site"): [_RVU_SCALE, _RVU_EXCL],
    ("practice", "phone"): ["Blank when CMS has no matching phone; never inferred."],
    ("practice", "name"): ["A Type 1 location named after a clinician is shown as street and city instead."],
    ("practice", "work_type"): ["From the place-of-service code on claims; null when neither the code nor the rollup text maps."],
    ("hospital_affiliation", "hospital_system_name"): ["Campuses of one system are combined into one row."],
    ("hospital_affiliation", "visit_share_pct"): ["Null on a parent-fallback row, which has no site-level visits."],
    ("referral", "patient_count"): ["Monthly counts summed: one patient referred in three months counts three times."],
    ("referral", "claim_count"): ["Monthly counts summed over the window."],
}

DERIVED_FROM: dict[tuple[str, str], list[str]] = {
    ("provider", "active_provider"): ["provider.visits_total", "provider.panel_size"],
    ("provider", "estimated_age"): ["provider.medical_school_graduation_year"],
    ("provider", "wrvu_average"): ["provider.wrvu_total", "provider.wrvu_procedure_count"],
    ("provider", "wrvu_prior_year_average"): ["provider.wrvu_prior_year_total", "provider.wrvu_prior_year_procedure_count"],
    ("provider", "wrvu_yoy_change_pct"): ["provider.wrvu_total", "provider.wrvu_prior_year_total"],
    ("provider", "wrvu_state_specialty_average"): ["provider.wrvu_total"],
    ("provider", "wrvu_state_specialty_median"): ["provider.wrvu_total"],
    ("provider", "wrvu_state_specialty_p25"): ["provider.wrvu_total"],
    ("provider", "wrvu_state_specialty_p75"): ["provider.wrvu_total"],
    ("provider", "wrvu_state_specialty_npi_count"): ["provider.wrvu_total"],
    ("provider", "wrvu_specialty_percentile"): ["provider.wrvu_total"],
    ("provider", "visits_specialty_percentile"): ["provider.visits_total"],
    ("provider", "activity_specialty_percentile"): ["provider.visits_specialty_percentile", "provider.wrvu_specialty_percentile"],
    ("provider", "visits_percent_new_patient"): ["provider.visits_new_patient", "provider.visits_established"],
    ("provider", "provider_practices_total"): ["practice.site_rank"],
    ("practice", "visit_share_pct"): ["practice.visits_at_site", "provider.visits_total"],
    ("practice", "wrvu_at_site"): ["provider.wrvu_total"],
    ("practice", "wrvu_share_pct"): ["practice.wrvu_at_site", "provider.wrvu_total"],
    ("practice", "site_rank"): ["practice.visits_at_site"],
    ("hospital_affiliation", "visit_share_pct"): ["hospital_affiliation.visits_at_system", "provider.visits_total"],
    ("hospital_affiliation", "affiliation_rank"): ["hospital_affiliation.visits_at_system"],
    ("group", "visits_per_provider"): ["group.visits_total", "group.provider_count"],
    ("group", "visits_percentile"): ["group.visits_total"],
    ("group", "activity_percentile"): ["group.visits_per_provider"],
    ("group", "active_provider_count"): ["provider.active_provider"],
    ("group", "in_system_provider_count"): ["provider.in_system_provider"],
    ("group", "in_system_provider"): ["group.in_system_provider_count"],
    ("group", "active_provider"): ["group.active_provider_count"],
}

_GROUP_PASSTHROUGH = {"organization_id", "organization_name", "organization_npi", "parent_id", "parent_name",
                      "provider_count", "visits_are_summed_across_npis", "visits_per_provider", "visits_percentile",
                      "activity_percentile", "in_system_provider", "active_provider", "primary_specialty_code",
                      "primary_specialty_description", "active_provider_count", "in_system_provider_count",
                      "member_count_any"}


def _caveats_for(scope: str, key: str) -> list[str]:
    out = list(CAVEATS.get((scope, key), []))
    if key.startswith("visits_top_") and key.endswith("_percent"):
        out.append("A share of all visits; the three shares do not add to 100.")
        if scope == "group":
            out.append("A lower bound on the group page: only members' stored top 3 codes are summed.")
    if key.startswith("panel_percent_age_"):
        out.append("Ages outside 0 to 120 are ignored.")
    if key.startswith("visits_percent_") and key[len("visits_percent_"):] in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        out.append("Visits without a valid service date are excluded.")
    if key in ("visits_percent_office", "visits_percent_hopd", "visits_percent_asc", "visits_percent_ed",
               "visits_percent_telehealth", "visits_percent_inpatient", "visits_percent_lab", "visits_percent_other_pos"):
        out.append("Visits with no service location are excluded.")
    if scope == "group" and (key.startswith("panel_percent_") or key.startswith("visits_percent_") or key == "panel_average_age"):
        out.append(_WEIGHTED)
    return out


def _derived_for(scope: str, key: str) -> list[str]:
    out = list(DERIVED_FROM.get((scope, key), []))
    if key.startswith("visits_top_") and key.endswith("_percent"):
        out += [f"{scope}.{key[:-len('_percent')]}", f"{scope}.visits_total"]
    if key.startswith("panel_percent_") or key == "panel_average_age":
        out.append(f"{scope}.panel_size")
    if scope == "group" and key not in _GROUP_PASSTHROUGH and key in PROVIDER and (scope, key) not in DERIVED_FROM:
        out.append(f"provider.{key}")
    return out


def _upstream_tables(scopes: dict[str, dict[str, dict[str, Any]]], ref: str, seen: set[str]) -> list[str]:
    """Tables of every entry reachable through derived_from, depth-first, deduped."""
    if ref in seen:
        return []
    seen.add(ref)
    scope, key = ref.split(".", 1)
    entry = scopes[scope][key]
    found: list[str] = list(entry.get("tables", []))
    for parent in entry.get("derived_from", []):
        for table in _upstream_tables(scopes, parent, seen):
            if table not in found:
                found.append(table)
    return found


def catalog(*, dev: bool = False) -> dict[str, dict[str, dict[str, Any]]]:
    """All scopes. Drops the dev tier unless asked."""
    scopes = {
        "provider": PROVIDER,
        "group": _group_scope(),
        "practice": PRACTICE,
        "group_practice": GROUP_PRACTICE,
        "hospital_affiliation": HOSPITAL_AFFILIATION,
        "referral": REFERRAL,
        "utilization": UTILIZATION,
    }
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for scope, fields in scopes.items():
        out[scope] = {}
        for key, entry in fields.items():
            item: dict[str, Any] = {"label": entry["label"], "help": entry["help"]}
            if dev:
                item["dev"] = entry["dev"]
                item["caveats"] = _caveats_for(scope, key)
                item["derived_from"] = _derived_for(scope, key)
                item["tables"] = tables_in(entry["dev"])
            out[scope][key] = item
    if dev:
        for scope, fields in out.items():
            for key, item in fields.items():
                for ref in item["derived_from"]:
                    pscope, pkey = ref.split(".", 1)
                    if pkey not in out.get(pscope, {}):
                        raise KeyError(f"{scope}.{key} derives from unknown entry {ref!r}")
                own = list(item["tables"])
                chain = _upstream_tables(out, f"{scope}.{key}", set())
                upstream: list[str] = []
                for t in own + chain:
                    for k in table_closure(t):
                        if k not in own and k not in upstream:
                            upstream.append(k)
                item["upstream_tables"] = upstream
    return out


def render_catalog(
    *,
    dev: bool = False,
    window_start: int | None = None,
    window_end: int | None = None,
    prior_window_start: int | None = None,
    prior_window_end: int | None = None,
    claims_db: str = "az",
    lookup_db: str = "azal",
) -> dict[str, Any]:
    """Catalog with {window} / {prior_window} filled in, plus the window itself.

    In dev mode each entry also carries `tables`: the sources behind the
    number, each with kind (warehouse / mart / cms), an `external` flag and,
    for CMS files, the dataset page `url`. `tables` at the top level is the
    whole registry for a glossary.
    """
    window = format_period_range(window_start, window_end)
    prior = format_period_range(prior_window_start, prior_window_end)
    scopes = catalog(dev=dev)
    for fields in scopes.values():
        for item in fields.values():
            for tier in ("help", "dev"):
                if tier in item:
                    item[tier] = item[tier].replace("{window}", window).replace("{prior_window}", prior)
            if "tables" in item:
                own = [dict(describe_table(k, claims_db=claims_db, lookup_db=lookup_db), upstream=False) for k in item["tables"]]
                up = [dict(describe_table(k, claims_db=claims_db, lookup_db=lookup_db), upstream=True) for k in item.pop("upstream_tables", [])]
                item["tables"] = own + up
            if "derived_from" in item:
                refs = []
                for ref in item["derived_from"]:
                    pscope, pkey = ref.split(".", 1)
                    refs.append({"ref": ref, "scope": pscope, "field": pkey, "label": scopes[pscope][pkey]["label"]})
                item["derived_from"] = refs
    body: dict[str, Any] = {
        "dev": dev,
        "window_start": window_start,
        "window_end": window_end,
        "window": window,
        "prior_window": prior,
        "scopes": scopes,
    }
    if dev:
        body["tables"] = [describe_table(k, claims_db=claims_db, lookup_db=lookup_db) for k in TABLE_REGISTRY]
    return body


def _table_md(table: dict[str, Any]) -> str:
    name = f"`{table['name']}`"
    if table.get("external"):
        return f"[{name}]({table['url']})*"
    return name


def catalog_markdown(rendered: dict[str, Any]) -> str:
    """One markdown table per scope, for a handout or the CLI. External tables carry * and a link."""
    dev = bool(rendered.get("dev"))
    lines = [f"# Field help ({rendered.get('window')})", ""]
    if dev:
        lines.append("Sources marked * are external CMS files; the link is the dataset page they were downloaded from.")
        lines.append("")
    for scope, fields in rendered["scopes"].items():
        lines.append(f"## {scope}")
        lines.append("")
        lines.append("| Field | Label | Help |" + (" Developer | Sources |" if dev else ""))
        lines.append("| --- | --- | --- |" + (" --- | --- |" if dev else ""))
        for key, item in fields.items():
            row = f"| `{key}` | {item['label']} | {item['help']} |"
            if dev:
                dev_text = item.get("dev", "")
                if item.get("derived_from"):
                    dev_text += " Built on: " + ", ".join(f"`{r['ref']}`" for r in item["derived_from"]) + "."
                if item.get("caveats"):
                    dev_text += " Notes: " + " ".join(item["caveats"])
                own = [t for t in item.get("tables", []) if not t.get("upstream")]
                up = [t for t in item.get("tables", []) if t.get("upstream")]
                sources = ", ".join(_table_md(t) for t in own)
                if up:
                    sources += " (upstream: " + ", ".join(_table_md(t) for t in up) + ")"
                row += f" {dev_text} | {sources} |"
            lines.append(row)
        lines.append("")
    if dev:
        lines.append("## Tables")
        lines.append("")
        lines.append("| Table | Kind | Refreshed by | Built from | Description |")
        lines.append("| --- | --- | --- | --- | --- |")
        for table in rendered.get("tables", []):
            desc = table["description"]
            if table.get("external"):
                desc = f"{table['source']}. {desc}"
            built = ", ".join(f"`{n}`" for n in table.get("built_from", []))
            lines.append(f"| {_table_md(table)} | {table['kind']} | {table.get('phase', '')} | {built} | {desc} |")
        lines.append("")
    return "\n".join(lines)
