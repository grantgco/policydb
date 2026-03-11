"""Dataclasses for PolicyDB entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class Client:
    id: int
    name: str
    industry_segment: str
    account_exec: str
    date_onboarded: str
    archived: int
    created_at: str
    updated_at: str
    primary_contact: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    broker_fee: Optional[float] = None
    business_description: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "Client":
        return cls(**{k: row[k] for k in row.keys()})


@dataclass
class Policy:
    id: int
    policy_uid: str
    client_id: int
    policy_type: str
    carrier: str
    effective_date: str
    expiration_date: str
    premium: float
    renewal_status: str
    account_exec: str
    archived: int
    created_at: str
    updated_at: str
    policy_number: Optional[str] = None
    limit_amount: Optional[float] = None
    deductible: Optional[float] = None
    description: Optional[str] = None
    coverage_form: Optional[str] = None
    layer_position: Optional[str] = "Primary"
    tower_group: Optional[str] = None
    is_standalone: int = 0
    placement_colleague: Optional[str] = None
    placement_colleague_email: Optional[str] = None
    underwriter_name: Optional[str] = None
    underwriter_contact: Optional[str] = None
    commission_rate: Optional[float] = None
    prior_premium: Optional[float] = None
    exposure_basis: Optional[str] = None
    exposure_amount: Optional[float] = None
    exposure_unit: Optional[str] = None
    exposure_address: Optional[str] = None
    exposure_city: Optional[str] = None
    exposure_state: Optional[str] = None
    exposure_zip: Optional[str] = None
    notes: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "Policy":
        keys = row.keys()
        return cls(**{k: row[k] for k in keys if k in cls.__dataclass_fields__})


@dataclass
class ActivityLog:
    id: int
    activity_date: str
    client_id: int
    activity_type: str
    subject: str
    account_exec: str
    created_at: str
    policy_id: Optional[int] = None
    contact_person: Optional[str] = None
    details: Optional[str] = None
    follow_up_date: Optional[str] = None
    follow_up_done: int = 0

    @classmethod
    def from_row(cls, row) -> "ActivityLog":
        keys = row.keys()
        return cls(**{k: row[k] for k in keys if k in cls.__dataclass_fields__})


@dataclass
class PremiumHistory:
    id: int
    client_id: int
    policy_type: str
    term_effective: str
    term_expiration: str
    premium: float
    created_at: str
    carrier: Optional[str] = None
    limit_amount: Optional[float] = None
    deductible: Optional[float] = None
    notes: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "PremiumHistory":
        keys = row.keys()
        return cls(**{k: row[k] for k in keys if k in cls.__dataclass_fields__})
