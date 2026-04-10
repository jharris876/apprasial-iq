from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import uuid


# ── Auth ───────────────────────────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: str
    full_name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=8)
    company: Optional[str] = None
    license_number: Optional[str] = None

class UserLogin(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    full_name: str
    is_admin: bool

class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    company: Optional[str]
    license_number: Optional[str]
    is_active: bool
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ── Report ─────────────────────────────────────────────────────────────────────

class ReportCreate(BaseModel):
    report_type: str = "commercial"
    review_standard: str = "all"
    report_text: Optional[str] = None  # for paste-in, not file upload

class ReportStatusOut(BaseModel):
    id: uuid.UUID
    status: str
    original_filename: str
    property_address: Optional[str]
    score: Optional[int]
    grade: Optional[str]
    created_at: datetime
    processing_started: Optional[datetime]
    processing_finished: Optional[datetime]
    error_message: Optional[str]

    class Config:
        from_attributes = True

class IssueOut(BaseModel):
    id: uuid.UUID
    issue_code: str
    severity: str
    section: str
    title: str
    location: Optional[str]
    problem: str
    why_it_matters: Optional[str]
    correction: Optional[str]
    rule_reference: Optional[str]
    extracted_value: Optional[str]
    correct_value: Optional[str]
    sort_order: int
    feedback: Optional[str]
    feedback_note: Optional[str]
    feedback_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True

class MathCheckOut(BaseModel):
    id: uuid.UUID
    item: str
    reported_value: Optional[str]
    calculated_value: Optional[str]
    is_match: bool
    variance: Optional[str]
    sort_order: int

    class Config:
        from_attributes = True

class ReportDetail(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    original_filename: str
    property_address: Optional[str]
    property_type: Optional[str]
    date_of_value: Optional[date]
    report_date: Optional[date]
    appraiser_name: Optional[str]
    appraiser_license: Optional[str]
    client_name: Optional[str]
    intended_use: Optional[str]
    final_value: Optional[Decimal]
    report_form: Optional[str]
    review_standard: str
    status: str
    score: Optional[int]
    grade: Optional[str]
    score_description: Optional[str]
    review_summary: Optional[str]
    created_at: datetime
    processing_started: Optional[datetime]
    processing_finished: Optional[datetime]
    issues: List[IssueOut] = []
    math_checks: List[MathCheckOut] = []

    class Config:
        from_attributes = True

class ReportListItem(BaseModel):
    id: uuid.UUID
    original_filename: str
    property_address: Optional[str]
    property_type: Optional[str]
    status: str
    score: Optional[int]
    grade: Optional[str]
    appraiser_name: Optional[str]
    final_value: Optional[Decimal]
    created_at: datetime
    critical_count: Optional[int] = 0
    major_count: Optional[int] = 0

    class Config:
        from_attributes = True


# ── Feedback ───────────────────────────────────────────────────────────────────

class IssueFeedback(BaseModel):
    feedback: str = Field(pattern="^(confirmed|dismissed|corrected|disputed)$")
    feedback_note: Optional[str] = None


# ── Audit ──────────────────────────────────────────────────────────────────────

class AuditLogOut(BaseModel):
    id: uuid.UUID
    event: str
    rule_ref: Optional[str]
    ip_address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Health ─────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    db: bool
    environment: str
