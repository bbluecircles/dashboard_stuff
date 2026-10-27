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

from typing import Any

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
    "Share of claims in payer classes 1 to 4 (az.payor.is_payor_code). Class 5 Other and unmapped codes are outside the denominator. "
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
    "middle_name": _entry("Middle name", "Middle name from claims, else CMS.", "Same merge order as first_name."),
    "last_name": _entry("Last name", "Last name from claims, else CMS.", "Same merge order as first_name; name_source records which one won."),
    "suffix": _entry("Suffix", "Name suffix such as Jr or III, from claims, else CMS.", "Same merge order as first_name."),
    "credential": _entry("Credential", "Degree or credential such as MD, DO, NP, from claims, else CMS.",
                         "az.physician.degree_abbr, else cms_pdc_clinician.cred, else cms_nppes_type1 credential text."),
    "gender": _entry("Provider gender", "The clinician's gender from CMS public files. Not the patient panel.",
                     "cms_pdc_clinician.gndr first, else cms_nppes_type1 sex code, normalized to M or F. The warehouse carries no gender."),
    "medical_school_name": _entry("Medical school", "Medical school as reported to CMS.",
                                  "cms_pdc_clinician.med_sch on the preferred PDC row (in-state, with school, grad year, gender, phone), else NPPES. 'OTHER' is treated as blank."),
    "medical_school_graduation_year": _entry("Graduation year", "Medical school graduation year as reported to CMS.",
                                             "cms_pdc_clinician.grd_yr, else NPPES."),
    "estimated_age": _entry("Estimated age", "Approximate age, estimated from medical school graduation year. Not a date of birth.",
                            "2024 - grd_yr + 26 (REPORT_YEAR, GRAD_AGE_OFFSET). Null when grd_yr is missing, before 1950 or after 2024. Capped at 90. transforms.estimated_age."),
    "primary_specialty_code": _entry("Specialty code", "Taxonomy code of the clinician's primary specialty.",
                                     "az.physician.npi_spec_grp_code. Also the peer group for the specialty percentiles."),
    "primary_specialty_description": _entry("Specialty", "The clinician's primary specialty.",
                                            "azal.npi_spec_grp.npi_spec_grp_name on npi_spec_grp_code, else az.physician.npi_spec_grp_name."),
    "specialty_classification": _entry("Specialty group", "Broader classification of the specialty, for example Internal Medicine.",
                                       "azal.npi_spec_grp.taxonomy_cat_name, else az.physician.taxonomy_cat_name."),
    "in_system_provider": _entry("In-system", "The clinician has a hospital affiliation on file with CMS.",
                                 "1 when any cms_pdc_facility_affil row exists for the NPI (PDC Facility Affiliation dataset 27ea-46a8). pd_network_npi holds the CCNs. Not a contracted-network flag."),
    "name_source": _entry("Name source", "Which source supplied the name: claims, CMS PDC, or NPPES.", "transforms.merge_identity: 'claims' unless the claims last name is blank, then 'pdc', then 'nppes'."),
    "gender_source": _entry("Gender source", "Which CMS file supplied the gender.", "'pdc' when cms_pdc_clinician has a gender, else 'nppes', else null."),
    "school_source": _entry("School source", "Which CMS file supplied the medical school.", "'pdc' when cms_pdc_clinician has a school, else 'nppes', else null."),
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
    "wrvu_total": _entry("RVU total", "Total relative value units across the clinician's visits in {window}, one work procedure per visit. A volume measure, not a quality score.", _RVU_DEV + " ROUND(SUM, 2)."),
    "wrvu_average": _entry("RVU per visit", "Average RVU per visit that had a procedure with an RVU.", _RVU_DEV + " wrvu_total / wrvu_procedure_count, 3 decimals. The UI leads with median and percentile instead."),
    "wrvu_procedure_count": _entry("RVU visits", "Visits that contributed to the RVU total.", _RVU_DEV + " COUNT(*) of contributing visits."),
    "visits_percent_third_party": _entry("Commercial", "Share of claims paid by commercial insurers.", _PAYER_DEV + " Class 3."),
    "visits_percent_medicaid": _entry("Medicaid", "Share of claims paid by Medicaid, including managed care.", _PAYER_DEV + " Class 2."),
    "visits_percent_medicare_advantage": _entry("Medicare Advantage", "Share of claims paid by Medicare Advantage plans.", _PAYER_DEV + " Class 4."),
    "visits_percent_medicare_traditional": _entry("Medicare FFS", "Share of claims paid by traditional fee-for-service Medicare.", _PAYER_DEV + " Class 1."),
    "top_payer_name_1": _entry("Top commercial payer 1", "Largest commercial payer parent by claims.", "Class 3 rows in pd_stg_npi_payor grouped by payor_parent_name (az.payor.payor_parent_name, else payor_name), ranked by claim count. Commercial only."),
    "top_payer_percent_1": _entry("Share of commercial", "This payer's share of the clinician's commercial claims, not of all claims.", "claim_count / SUM(claim_count) over the NPI's class 3 rows x100."),
    "top_payer_name_2": _entry("Top commercial payer 2", "Second largest commercial payer parent.", "Rank 2 of the same ranking."),
    "top_payer_percent_2": _entry("Share of commercial", "Share of the clinician's commercial claims.", "Same formula, rank 2."),
    "top_payer_name_3": _entry("Top commercial payer 3", "Third largest commercial payer parent.", "Rank 3 of the same ranking."),
    "top_payer_percent_3": _entry("Share of commercial", "Share of the clinician's commercial claims.", "Same formula, rank 3."),
    "primary_organization_id": _entry("Organization id", "Billing identifier of the clinician's primary organization.",
                                      "az.physician_primary_affiliation: p_provider_billing_code when not a dummy, else i_provider_billing_code. Also the key for the group-practice profile."),
    "primary_organization_name": _entry("Organization", "The organization the clinician primarily bills under.", "Group practice name on the same az.physician_primary_affiliation row."),
    "primary_organization_npi": _entry("Organization NPI", "NPI of the primary billing organization.", "Same billing code as primary_organization_id."),
    "primary_organization_parent_id": _entry("Parent id", "Identifier of the parent health system.", "IP_hospital_system_code, else OP_hospital_system_code on az.physician_primary_affiliation."),
    "primary_organization_parent_name": _entry("Parent system", "Parent health system of the primary organization.", "IP_hospital_system_name, else OP_hospital_system_name on az.physician_primary_affiliation."),
    **_weekday_entries("the clinician's visits", _DOW_DEV),
    "wrvu_prior_year_total": _entry("Prior-year RVU", "RVU total for the 12 months before the window, {prior_window}.", "Same RVU formula over az.pat_dt for {prior_window}, joined to azal.procd on procd_code, cached in pd_stg_npi_wrvu_prior (Phase 5)."),
    "wrvu_prior_year_average": _entry("Prior-year RVU per visit", "Average RVU per contributing visit in {prior_window}.", "prior total / prior procedure_count, 3 decimals."),
    "wrvu_prior_year_procedure_count": _entry("Prior-year RVU visits", "Visits that contributed to the prior-year RVU total.", "COUNT(*) of contributing visits in {prior_window}."),
    "wrvu_yoy_change_pct": _entry("RVU change", "Percent change in RVU total versus the prior year.", "(wrvu_total - prior) / prior x100, 2 decimals. Null when prior < 1.0; clamped to +/-9999.99. complete.yoy_change_sql."),
    "wrvu_state_specialty_average": _entry("Specialty average RVU", "Mean RVU total among clinicians in the same specialty statewide. Heavily skewed by a few very high totals; prefer the median.", "AVG(wrvu_total) over pd_provider rows with the same primary_specialty_code and wrvu_total > 0, in pd_stg_specialty_wrvu."),
    "wrvu_state_specialty_median": _entry("Specialty median RVU", "Median RVU total among clinicians in the same specialty statewide.", "Value at row FLOOR(n x 0.50) of the specialty's wrvu_total sorted ascending, n = NPIs with wrvu_total > 0."),
    "wrvu_state_specialty_p25": _entry("Specialty 25th percentile", "RVU total at the 25th percentile of the specialty.", "Value at row FLOOR(n x 0.25), same ranking."),
    "wrvu_state_specialty_p75": _entry("Specialty 75th percentile", "RVU total at the 75th percentile of the specialty.", "Value at row FLOOR(n x 0.75), same ranking."),
    "wrvu_state_specialty_npi_count": _entry("Specialty peers", "Number of clinicians in the specialty with an RVU total.", "COUNT(*) of the specialty's NPIs with wrvu_total > 0."),
    "wrvu_specialty_percentile": _entry("RVU percentile", "Where the clinician's RVU total ranks within their specialty. 100 is the highest.", "ROW_NUMBER over the specialty ordered by wrvu_total asc / specialty count x100, 1 decimal (pd_stg_npi_percentile). 93.9 = 93.9% of peers have a lower or equal total."),
    "visits_specialty_percentile": _entry("Visits percentile", "Where the clinician's visit count ranks within their specialty. 100 is the highest.", "Same rank on visits_total among specialty NPIs with visits_total > 0 (pd_stg_npi_visits_percentile)."),
    "activity_specialty_percentile": _entry("Activity vs specialty peers", "Combined volume rank within the specialty: the average of the visits and RVU percentiles. Volume, not quality.", "ROUND((visits_specialty_percentile + wrvu_specialty_percentile) / 2, 1) when both exist, else whichever exists."),
    "group_size": _entry("Group size", "Number of clinicians in the group practice as reported to CMS.", "cms_pdc_clinician.num_org_mem on the preferred PDC row (extras)."),
    "telehealth_offered": _entry("Telehealth offered", "The clinician reports offering telehealth to CMS.", "cms_pdc_clinician.telehlth: Y/YES/TRUE/1 -> true, N/NO/FALSE/0 -> false, else null."),
    "secondary_specialty_1": _entry("Secondary specialty 1", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_1, loaded by extras --reload-pdc."),
    "secondary_specialty_2": _entry("Secondary specialty 2", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_2, loaded by extras --reload-pdc."),
    "secondary_specialty_3": _entry("Secondary specialty 3", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_3, loaded by extras --reload-pdc."),
    "secondary_specialty_4": _entry("Secondary specialty 4", "Additional specialty reported to CMS.", "cms_pdc_clinician.sec_spec_4, loaded by extras --reload-pdc."),
    "visits_new_patient": _entry("New patient visits", "Office visits billed as new-patient evaluation and management.", "pd_stg_visit rows whose px is in 99202 to 99205 (extras)."),
    "visits_established": _entry("Established visits", "Office visits billed as established-patient evaluation and management.", "pd_stg_visit rows whose px is in 99212 to 99215."),
    "visits_percent_new_patient": _entry("New patient %", "New-patient share of office evaluation and management visits.", "visits_new_patient / (visits_new_patient + visits_established) x100. Null when both are zero."),
    "visits_percent_office": _entry("Office", "Share of visits in a physician office.", _POS_DEV + " Bucket office."),
    "visits_percent_hopd": _entry("Hospital outpatient", "Share of visits in a hospital outpatient department.", _POS_DEV + " Bucket hopd."),
    "visits_percent_asc": _entry("Ambulatory surgery", "Share of visits in an ambulatory surgery center.", _POS_DEV + " Bucket asc."),
    "visits_percent_ed": _entry("Emergency", "Share of visits in an emergency department.", _POS_DEV + " Bucket ed."),
    "visits_percent_telehealth": _entry("Telehealth", "Share of visits delivered by telehealth.", _POS_DEV + " Bucket telehealth."),
    "visits_percent_inpatient": _entry("Inpatient", "Share of visits in an inpatient hospital setting.", _POS_DEV + " Bucket inpatient."),
    "visits_percent_lab": _entry("Laboratory", "Share of visits at an independent laboratory.", _POS_DEV + " Bucket lab."),
    "visits_percent_other_pos": _entry("Other settings", "Share of visits in any other place of service.", _POS_DEV + " Bucket other."),
    "mips_final_score": _entry("MIPS final score", "CMS Merit-based Incentive Payment System final score, latest published year.", "cms_pdc_mips.final_score on npi, the individual row first, else the row for the NPI's org_pac_id (dataset a174-a962)."),
    "mips_quality_score": _entry("MIPS quality score", "CMS MIPS quality category score.", "cms_pdc_mips.quality_score, same row choice."),
    "open_payments_year": _entry("Open Payments year", "Not shown. Open Payments was retired from the directory.", "cms_open_payments is no longer loaded; always null. Omitted from HTTP GET."),
    "open_payments_general_total": _entry("Open Payments general", "Not shown; retired.", "Always null. Omitted from HTTP GET."),
    "open_payments_research_total": _entry("Open Payments research", "Not shown; retired.", "Always null. Omitted from HTTP GET."),
    "open_payments_ownership_total": _entry("Open Payments ownership", "Not shown; retired.", "Always null. Omitted from HTTP GET."),
    "open_payments_count": _entry("Open Payments count", "Not shown; retired.", "Always null. Omitted from HTTP GET."),
    "practices": _entry("Sites", "Up to 5 practice locations ranked by the clinician's visits there, from claims.", "pd_provider_practice rows, site_rank 1 to 5. See the practice scope."),
    "group_practices": _entry("Group practices", "Every organization the clinician billed under in {window}, primary first.", "pd_provider_group_practice rows. See the group_practice scope."),
    "hospital_affiliations": _entry("Hospital affiliations", "Up to 5 distinct health systems where the clinician's visits took place, by visit share.", "pd_provider_hospital_affiliation rows. See the hospital_affiliation scope."),
    "referrals": _entry("Referrals", "Top 3 clinicians who refer to this clinician, and top 3 this clinician refers to.", "pd_provider_referral rows. See the referral scope."),
    "utilization": _entry("Care Compare utilization", "Not on the HTTP profile.", "pd_provider_utilization from cms_pdc_utilization (n0yb-util), top 5 by percentile. CLI get only."),
    # Picker (dump) rows only
    "practice_name": _entry("Primary site", "Name of the clinician's primary practice site.", "pd_provider_practice.name where site_rank = 1."),
    "city": _entry("City", "City of the clinician's primary practice site.", "pd_provider_practice.city where site_rank = 1."),
    "state": _entry("State", "State of the clinician's primary practice site.", "pd_provider_practice.state where site_rank = 1."),
}

GROUP_OVERRIDES: dict[str, dict[str, str]] = {
    "organization_id": _entry("Organization id", "Billing identifier of the group. Members are the clinicians whose primary organization this is.", "pd_provider.primary_organization_id. One pd_group_practice row per org per member scope (all / active / inactive)."),
    "organization_name": _entry("Organization", "Name of the group practice.", "MAX(primary_organization_name) over member pd_provider rows."),
    "organization_npi": _entry("Organization NPI", "NPI of the billing organization.", "MAX(primary_organization_npi) over members."),
    "parent_id": _entry("Parent id", "Identifier of the parent health system.", "MAX(primary_organization_parent_id) over members."),
    "parent_name": _entry("Parent system", "Parent health system of the group.", "MAX(primary_organization_parent_name) over members."),
    "provider_count": _entry("Clinicians", "Number of individual clinicians whose primary organization is this group.", "COUNT(*) of pd_provider rows with this primary_organization_id, in the requested member scope."),
    "active_provider_count": _entry("Active clinicians", "Members with at least one visit or panel patient in {window}.", "SUM(active_provider = 1) over members."),
    "in_system_provider_count": _entry("In-system clinicians", "Members with a CMS hospital affiliation.", "SUM(in_system_provider = 1) over members."),
    "visits_total": _entry("Visits", "Sum of member clinicians' visits in {window}. An encounter billed by two members counts twice.", "SUM(IFNULL(pd_provider.visits_total, 0)) over members, precomputed in pd_group_practice."),
    "panel_size": _entry("Panel size", "Sum of member clinicians' panel sizes. Patients seen by two members count twice.", "SUM(IFNULL(panel_size, 0)) over members."),
    "wrvu_total": _entry("RVU total", "Sum of member clinicians' RVU totals.", "SUM(IFNULL(wrvu_total, 0)) over members."),
    "visits_per_provider": _entry("Visits per clinician", "Group visits divided by number of member clinicians.", "visits_total / provider_count, 1 decimal."),
    "visits_percentile": _entry("Visits percentile", "Where this group's visit total ranks among all groups. 100 is the highest.", "ROW_NUMBER over groups with visits_total > 0 ordered by visits_total / count x100, 1 decimal. Among groups, not specialty peers."),
    "activity_percentile": _entry("Activity percentile", "Where this group's visits per clinician ranks among all groups. 100 is the highest.", "Same rank on visits_total / provider_count."),
    "visits_are_summed_across_npis": _entry("Summed across clinicians", "Volume on this page is summed across member clinicians.", "Always true on the group payload."),
    "primary_specialty_code": _entry("Specialty code", "Taxonomy code of the group's most common member specialty.", "Member primary_specialty_code with the most visits."),
    "primary_specialty_description": _entry("Specialty", "The group's most common member specialty, by visits.", "Specialty of the member specialty code with the highest summed visits."),
    "in_system_provider": _entry("In-system", "At least one member has a CMS hospital affiliation.", "in_system_provider_count >= 1."),
    "active_provider": _entry("Active", "At least one member was active in {window}.", "active_provider_count >= 1."),
    "group_size": _entry("Group size", "Largest CMS-reported group size among members.", "MAX(pd_provider.group_size) over members, not provider_count."),
    "telehealth_offered": _entry("Telehealth offered", "At least one member reports offering telehealth to CMS.", "MAX(telehealth_offered) over members."),
    "wrvu_average": _entry("RVU per visit", "Group RVU total divided by contributing visits across members.", "SUM(wrvu_total) / SUM(wrvu_procedure_count) over members."),
    "wrvu_procedure_count": _entry("RVU visits", "Sum of member visits that contributed an RVU.", "SUM(wrvu_procedure_count) over members."),
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
    "site_rank": _entry("Rank", "Site order by the clinician's visits there. 1 is the primary site.", "ROW_NUMBER per npi over street+ZIP clusters by visits desc, then lowest sl_code; ranks past 5 dropped (Phase 3)."),
    "sl_code": _entry("Location code", "Warehouse code of the busiest location in this site.", "az.sl.sl_code of the cluster's canonical location (most visits)."),
    "cluster_key": _entry("Site key", "Key that groups suite variants of one address into a single site.", "'a:' normalized street|ZIP5 when both exist; else 'g:' lat,lon rounded to 4 decimals; else 's:' sl_code. transforms.cluster_key."),
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
    "npi_type": _entry("Location type", "Whether the location code is an organization (2) or an individual (1).", "az.sl.npi_type."),
    "location_source": _entry("Location source", "Where the address came from. Always claims today.", "Constant 'claims_sl'. Reserved for geocoded or CMS-sourced sites."),
    "location_flag": _entry("Location status", "Confidence flag for the address. Always claims-confirmed today.", "Constant 'claims_confirmed'."),
    "phone_source": _entry("Phone source", "Where the phone came from: CMS PDC, or blank.", "'pdc' when the PDC match found a phone, else null."),
    "needs_geocode": _entry("Needs geocode", "The warehouse has no coordinates for this location.", "az.sl.latitude or longitude is null."),
    "wrvu_at_site": _entry("RVU at site", "The clinician's RVU total at this site.", "SUM of visit-grain RVU over visits at the site (pd_stg_site_wrvu, Phase 4)."),
    "wrvu_share_pct": _entry("RVU share", "Share of the clinician's RVU total earned at this site.", "wrvu_at_site / pd_provider.wrvu_total x100, 2 decimals."),
    **_weekday_entries("the clinician's visits at this site", "pd_stg_site_dow: visits at the site by DAYOFWEEK of the visit date / visits at the site with a date x100 (Phase 5)."),
}

GROUP_PRACTICE: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this organization row belongs to.", "pd_provider_group_practice.npi."),
    "org_rank": _entry("Rank", "Order of the clinician's organizations: primary first.", "ROW_NUMBER per npi: is_primary first, then more cases, then later max_period_code (Phase 4)."),
    "organization_id": _entry("Organization id", "Billing identifier of the organization.", "provider_billing_code on az.physician_affiliation_p / _i, or pd_provider.primary_organization_id for the primary row."),
    "organization_npi": _entry("Organization NPI", "NPI of the billing organization.", "Same billing code."),
    "organization_name": _entry("Organization", "Name of the organization the clinician billed under.", "dba_name on the affiliation row, or the profile's primary_organization_name."),
    "billing_type": _entry("Billing type", "P for professional claims, I for institutional claims. Blank when the row came only from the primary affiliation.", "'P' from az.physician_affiliation_p, 'I' from az.physician_affiliation_i. P beats I when both exist for one org."),
    "is_primary": _entry("Primary", "This is the clinician's primary billing organization.", "organization_id = pd_provider.primary_organization_id."),
    "cases": _entry("Cases", "Case count the warehouse attributes to the clinician at this organization.", "count_of_cases on the affiliation row; null for the primary-only row."),
    "max_period_code": _entry("Last month billed", "Latest month (YYYYMM) the clinician billed under this organization.", "max_period_code on the affiliation row; must be >= window_start to be listed."),
}

HOSPITAL_AFFILIATION: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this affiliation belongs to.", "pd_provider_hospital_affiliation.npi."),
    "affiliation_rank": _entry("Rank", "Health systems ordered by the clinician's visits there. At most 5 distinct systems.", "ROW_NUMBER per npi by visits_at_system desc, then system name (Phase 4)."),
    "hospital_system_name": _entry("Health system", "Parent health system where the clinician's visits took place. Campuses of one system are combined.", "az.sl.sl_hospital_system_name, else hospital system name on az.provider_facility_npi joined on sl_code; blank and Unknown dropped; grouped case-insensitively across pd_stg_npi_sl sites."),
    "facility_name": _entry("Campus", "The busiest campus or facility within the system, when it has its own name.", "Facility name of the site with the most visits in the system; null when it equals the system name."),
    "sl_code": _entry("Location code", "Warehouse code of the busiest campus.", "sl_code of the site with the most visits in the system."),
    "visits_at_system": _entry("Visits at system", "The clinician's visits across all sites of this system in {window}.", "SUM(pd_stg_npi_sl.visits) over the system's sites. Parent fallback rows carry visits_total."),
    "visit_share_pct": _entry("Visit share", "Share of the clinician's visits that took place at this system.", "visits_at_system / pd_provider.visits_total x100. Null on the parent-fallback row."),
    "provider_count": _entry("Clinicians", "On a group page, number of member clinicians affiliated with this system.", "Group profile only: COUNT(DISTINCT npi) of members with the system. Null on a provider."),
}

REFERRAL: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this referral row belongs to.", "pd_provider_referral.npi."),
    "direction": _entry("Direction", "in: clinicians who send patients to this clinician. out: clinicians this clinician sends patients to.", "'in' = this NPI is rendering_physician_code; 'out' = this NPI is implied_all_referring_physician_code on az.dash_physician_referrals_to_rendering."),
    "peer_rank": _entry("Rank", "Top 3 per direction by patients.", "ROW_NUMBER per npi and direction by patient_count desc, claim_count desc, peer_npi (Phase 5)."),
    "peer_npi": _entry("Peer NPI", "NPI of the referring or receiving clinician.", "The other NPI on the edge. Both must be Type 1, neither a dummy, not the same NPI."),
    "peer_name": _entry("Peer", "Name of the referring or receiving clinician.", "pd_provider last_name, first_name of the peer."),
    "peer_specialty": _entry("Peer specialty", "Specialty of the referring or receiving clinician.", "pd_provider.primary_specialty_description of the peer."),
    "patient_count": _entry("Patients", "Patients referred between the two clinicians in {window}. Monthly counts are summed, so a patient referred in three months counts three times.", "SUM(patient_count) over window months on az.dash_physician_referrals_to_rendering, staged in pd_stg_referral_edge."),
    "claim_count": _entry("Claims", "Claims behind the referral relationship in {window}.", "SUM(claim_count) over window months."),
}

UTILIZATION: dict[str, dict[str, str]] = {
    "npi": _entry("NPI", "The clinician this utilization row belongs to.", "pd_provider_utilization.npi."),
    "rk": _entry("Rank", "Procedure categories ordered by CMS percentile.", "ROW_NUMBER per npi by percentile desc, category; top 5 (extras)."),
    "procedure_category": _entry("Procedure category", "Care Compare procedure category.", "cms_pdc_utilization.procedure_category (dataset n0yb-util)."),
    "count_label": _entry("Volume", "CMS volume label for the category.", "cms_pdc_utilization.count_label."),
    "percentile": _entry("Percentile", "CMS percentile for the category.", "cms_pdc_utilization.percentile."),
    "profile_display": _entry("Display flag", "CMS profile display indicator.", "cms_pdc_utilization.profile_display_indicator."),
}


def catalog(*, dev: bool = False) -> dict[str, dict[str, dict[str, str]]]:
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
    out: dict[str, dict[str, dict[str, str]]] = {}
    for scope, fields in scopes.items():
        out[scope] = {}
        for key, entry in fields.items():
            item = {"label": entry["label"], "help": entry["help"]}
            if dev:
                item["dev"] = entry["dev"]
            out[scope][key] = item
    return out


def render_catalog(
    *,
    dev: bool = False,
    window_start: int | None = None,
    window_end: int | None = None,
    prior_window_start: int | None = None,
    prior_window_end: int | None = None,
) -> dict[str, Any]:
    """Catalog with {window} / {prior_window} filled in, plus the window itself."""
    window = format_period_range(window_start, window_end)
    prior = format_period_range(prior_window_start, prior_window_end)
    scopes = catalog(dev=dev)
    for fields in scopes.values():
        for item in fields.values():
            for tier in ("help", "dev"):
                if tier in item:
                    item[tier] = item[tier].replace("{window}", window).replace("{prior_window}", prior)
    return {
        "dev": dev,
        "window_start": window_start,
        "window_end": window_end,
        "window": window,
        "prior_window": prior,
        "scopes": scopes,
    }


def catalog_markdown(rendered: dict[str, Any]) -> str:
    """One markdown table per scope, for a handout or the CLI."""
    dev = bool(rendered.get("dev"))
    lines = [f"# Field help ({rendered.get('window')})", ""]
    for scope, fields in rendered["scopes"].items():
        lines.append(f"## {scope}")
        lines.append("")
        lines.append("| Field | Label | Help |" + (" Developer |" if dev else ""))
        lines.append("| --- | --- | --- |" + (" --- |" if dev else ""))
        for key, item in fields.items():
            row = f"| `{key}` | {item['label']} | {item['help']} |"
            if dev:
                row += f" {item.get('dev', '')} |"
            lines.append(row)
        lines.append("")
    return "\n".join(lines)
