"""Pydantic shapes the FastAPI layer can return as-is."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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


class ProviderSpineList(BaseModel):
    items: list[ProviderSpine] = Field(default_factory=list)
    total: int = 0
