"""Pydantic shapes the FastAPI layer can return as-is."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProviderPractice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    npi: int
    site_rank: int
    sl_code: int | None = None
    cluster_key: str | None = None
    name: str | None = None
    street: str | None = None
    city: str | None = None
    county: str | None = None
    state: str | None = None
    zip: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None = None
    work_type: str | None = None
    visits_at_site: int | None = None
    visit_share_pct: float | None = None
    npi_type: str | None = None
    location_source: str | None = None
    location_flag: str | None = None
    phone_source: str | None = None
    needs_geocode: bool = False
    wrvu_at_site: float | None = None
    wrvu_share_pct: float | None = None
    visits_percent_monday: float | None = None
    visits_percent_tuesday: float | None = None
    visits_percent_wednesday: float | None = None
    visits_percent_thursday: float | None = None
    visits_percent_friday: float | None = None
    visits_percent_saturday: float | None = None
    visits_percent_sunday: float | None = None


class ProviderReferral(BaseModel):
    model_config = ConfigDict(extra="ignore")

    npi: int
    direction: str
    peer_rank: int
    peer_npi: int
    peer_name: str | None = None
    peer_specialty: str | None = None
    patient_count: int | None = None
    claim_count: int | None = None


class ProviderUtilization(BaseModel):
    model_config = ConfigDict(extra="ignore")

    npi: int
    rk: int
    procedure_category: str | None = None
    count_label: str | None = None
    percentile: int | None = None
    profile_display: str | None = None


class ProviderSpine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    npi: int
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    suffix: str | None = None
    credential: str | None = None
    gender: str | None = None
    medical_school_name: str | None = None
    medical_school_graduation_year: int | None = None
    estimated_age: int | None = None
    primary_specialty_code: str | None = None
    primary_specialty_description: str | None = None
    specialty_classification: str | None = None
    in_system_provider: bool | None = None
    name_source: str | None = None
    gender_source: str | None = None
    school_source: str | None = None
    active_provider: bool | None = None
    visits_total: int | None = None
    visits_top_diagnosis_1: str | None = None
    visits_top_diagnosis_1_name: str | None = None
    visits_top_diagnosis_2: str | None = None
    visits_top_diagnosis_2_name: str | None = None
    visits_top_diagnosis_3: str | None = None
    visits_top_diagnosis_3_name: str | None = None
    visits_top_procedure_1: str | None = None
    visits_top_procedure_1_name: str | None = None
    visits_top_procedure_2: str | None = None
    visits_top_procedure_2_name: str | None = None
    visits_top_procedure_3: str | None = None
    visits_top_procedure_3_name: str | None = None
    panel_size: int | None = None
    panel_average_age: float | None = None
    panel_percent_age_0_19: float | None = None
    panel_percent_age_20_44: float | None = None
    panel_percent_age_45_64: float | None = None
    panel_percent_age_65_84: float | None = None
    panel_percent_age_85_plus: float | None = None
    panel_percent_female: float | None = None
    panel_percent_male: float | None = None
    provider_practices_total: int | None = None
    wrvu_total: float | None = None
    wrvu_average: float | None = None
    wrvu_procedure_count: int | None = None
    visits_percent_third_party: float | None = None
    visits_percent_medicaid: float | None = None
    visits_percent_medicare_advantage: float | None = None
    visits_percent_medicare_traditional: float | None = None
    top_payer_name_1: str | None = None
    top_payer_percent_1: float | None = None
    top_payer_name_2: str | None = None
    top_payer_percent_2: float | None = None
    top_payer_name_3: str | None = None
    top_payer_percent_3: float | None = None
    primary_organization_id: int | None = None
    primary_organization_name: str | None = None
    primary_organization_npi: int | None = None
    primary_organization_parent_id: int | None = None
    primary_organization_parent_name: str | None = None
    visits_percent_monday: float | None = None
    visits_percent_tuesday: float | None = None
    visits_percent_wednesday: float | None = None
    visits_percent_thursday: float | None = None
    visits_percent_friday: float | None = None
    visits_percent_saturday: float | None = None
    visits_percent_sunday: float | None = None
    wrvu_prior_year_total: float | None = None
    wrvu_prior_year_average: float | None = None
    wrvu_prior_year_procedure_count: int | None = None
    wrvu_yoy_change_pct: float | None = None
    wrvu_state_specialty_average: float | None = None
    wrvu_state_specialty_median: float | None = None
    wrvu_state_specialty_p25: float | None = None
    wrvu_state_specialty_p75: float | None = None
    wrvu_state_specialty_npi_count: int | None = None
    wrvu_specialty_percentile: float | None = None
    group_size: int | None = None
    telehealth_offered: bool | None = None
    secondary_specialty_1: str | None = None
    secondary_specialty_2: str | None = None
    secondary_specialty_3: str | None = None
    secondary_specialty_4: str | None = None
    visits_new_patient: int | None = None
    visits_established: int | None = None
    visits_percent_new_patient: float | None = None
    visits_percent_office: float | None = None
    visits_percent_hopd: float | None = None
    visits_percent_asc: float | None = None
    visits_percent_ed: float | None = None
    visits_percent_telehealth: float | None = None
    visits_percent_other_pos: float | None = None
    mips_final_score: float | None = None
    mips_quality_score: float | None = None
    open_payments_year: int | None = None
    open_payments_general_total: float | None = None
    open_payments_research_total: float | None = None
    open_payments_ownership_total: float | None = None
    open_payments_count: int | None = None
    practices: list[ProviderPractice] = Field(default_factory=list)
    referrals: list[ProviderReferral] = Field(default_factory=list)
    utilization: list[ProviderUtilization] = Field(default_factory=list)


class ProviderSpineList(BaseModel):
    items: list[ProviderSpine] = Field(default_factory=list)
    total: int = 0
