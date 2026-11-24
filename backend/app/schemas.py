"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import LEAD_STATUSES

LeadStatus = Literal["new", "contacted", "replied", "interested", "not_interested", "customer"]
assert set(LeadStatus.__args__) == set(LEAD_STATUSES)  # type: ignore[attr-defined]


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    org_number: str
    address: str
    city: str
    country: str
    industry: str
    website: str
    domain: str
    phone: str
    email: str
    email_status: str
    legal_form: str
    is_sole_trader: bool | None
    source: str
    status: str
    notes: str
    linkedin_contacted: bool
    search_id: int | None
    created_at: datetime


class LeadUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    org_number: str | None = None
    address: str | None = None
    city: str | None = None
    industry: str | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    is_sole_trader: bool | None = None
    status: LeadStatus | None = None
    notes: str | None = None
    linkedin_contacted: bool | None = None


class SearchCreate(BaseModel):
    country: str = Field(default="SE", min_length=2, max_length=2)
    niche: str = Field(min_length=2, max_length=100)
    city: str = Field(default="", max_length=100)
    limit: int = Field(default=30, ge=1, le=200)

    @field_validator("country")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.upper()
        if not v.isalpha():
            raise ValueError("country must be a 2-letter ISO code")
        return v


class SearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    country: str
    niche: str
    city: str
    result_limit: int
    status: str
    message: str
    found_count: int
    created_at: datetime
    finished_at: datetime | None


class PreviewRequest(BaseModel):
    subject: str
    body: str
    language: Literal["sv", "en"] = "sv"
    lead_ids: list[int] = Field(default_factory=list)
    include_sole_traders: bool = False
    skip_previously_contacted: bool = True


class CampaignCreate(PreviewRequest):
    name: str = Field(default="Campaign", max_length=200)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    to_email: str
    subject: str
    body: str
    status: str
    skip_reason: str
    error: str
    sent_at: datetime | None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    subject: str
    body: str
    language: str
    status: str
    note: str
    include_sole_traders: bool
    skip_previously_contacted: bool
    created_at: datetime
    counts: dict[str, int] = Field(default_factory=dict)
    running: bool = False


class CampaignDetail(CampaignOut):
    messages: list[MessageOut] = Field(default_factory=list)
    company_names: dict[int, str] = Field(default_factory=dict)


class SendRequest(BaseModel):
    dry_run: bool = False


class DncCreate(BaseModel):
    email: str = ""
    domain: str = ""
    reason: str = ""


class DncOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    domain: str
    reason: str
    created_at: datetime
