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


class ProviderSpineList(BaseModel):
    items: list[ProviderSpine] = Field(default_factory=list)
    total: int = 0
