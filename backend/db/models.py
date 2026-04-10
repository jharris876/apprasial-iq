from sqlalchemy import (
    String, Text, Integer, Boolean, BigInteger, Numeric,
    ForeignKey, Enum as SAEnum, TIMESTAMP, JSON, Date, func
)
from sqlalchemy.dialects.postgresql import UUID, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, date
from decimal import Decimal
import uuid
import enum

from db.database import Base


# ── Python Enums (mirror SQL enums) ───────────────────────────────────────────

class ReportStatus(str, enum.Enum):
    uploaded   = "uploaded"
    queued     = "queued"
    processing = "processing"
    complete   = "complete"
    failed     = "failed"

class SeverityLevel(str, enum.Enum):
    critical = "critical"
    major    = "major"
    minor    = "minor"
    info     = "info"

class ReportSection(str, enum.Enum):
    uspap          = "uspap"
    math           = "math"
    narrative      = "narrative"
    comps          = "comps"
    adjustments    = "adjustments"
    income         = "income"
    hbu            = "hbu"
    reconciliation = "reconciliation"
    cost           = "cost"
    zoning         = "zoning"
    site           = "site"
    certification  = "certification"
    general        = "general"

class AppraisalType(str, enum.Enum):
    commercial  = "commercial"
    residential = "residential"
    multifamily = "multifamily"
    land        = "land"
    industrial  = "industrial"
    retail      = "retail"
    office      = "office"
    mixed_use   = "mixed_use"

class ReviewStandard(str, enum.Enum):
    all     = "all"
    uspap   = "uspap"
    fannie  = "fannie"
    freddie = "freddie"

class FeedbackType(str, enum.Enum):
    confirmed = "confirmed"
    dismissed = "dismissed"
    corrected = "corrected"
    disputed  = "disputed"

class Grade(str, enum.Enum):
    PASS        = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL        = "FAIL"


# ── ORM Models ────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id:             Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email:          Mapped[str]        = mapped_column(String, unique=True, nullable=False)
    full_name:      Mapped[str]        = mapped_column(String, nullable=False)
    hashed_password:Mapped[str]        = mapped_column(String, nullable=False)
    company:        Mapped[str | None] = mapped_column(String)
    license_number: Mapped[str | None] = mapped_column(String)
    is_active:      Mapped[bool]       = mapped_column(Boolean, default=True)
    is_admin:       Mapped[bool]       = mapped_column(Boolean, default=False)
    created_at:     Mapped[datetime]   = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at:     Mapped[datetime]   = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    reports:   "list[Report]"  = relationship("Report", back_populates="user")
    api_keys:  "list[ApiKey]"  = relationship("ApiKey", back_populates="user")


class Report(Base):
    __tablename__ = "reports"

    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:             Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    original_filename:   Mapped[str]            = mapped_column(String, nullable=False)
    file_path:           Mapped[str | None]     = mapped_column(String)
    file_size_bytes:     Mapped[int | None]     = mapped_column(BigInteger)
    file_mime_type:      Mapped[str | None]     = mapped_column(String)

    property_address:    Mapped[str | None]     = mapped_column(Text)
    property_type:       Mapped[str | None]     = mapped_column(SAEnum(AppraisalType, name="appraisal_type"))
    date_of_value:       Mapped[date | None]    = mapped_column(Date)
    report_date:         Mapped[date | None]    = mapped_column(Date)
    appraiser_name:      Mapped[str | None]     = mapped_column(String)
    appraiser_license:   Mapped[str | None]     = mapped_column(String)
    client_name:         Mapped[str | None]     = mapped_column(String)
    intended_use:        Mapped[str | None]     = mapped_column(Text)
    final_value:         Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    report_form:         Mapped[str | None]     = mapped_column(String)

    review_standard:     Mapped[str]            = mapped_column(SAEnum(ReviewStandard, name="review_standard"), default="all")
    report_type:         Mapped[str | None]     = mapped_column(SAEnum(AppraisalType, name="appraisal_type"))

    status:              Mapped[str]            = mapped_column(SAEnum(ReportStatus, name="report_status"), default="uploaded")
    error_message:       Mapped[str | None]     = mapped_column(Text)
    processing_started:  Mapped[datetime | None]= mapped_column(TIMESTAMP(timezone=True))
    processing_finished: Mapped[datetime | None]= mapped_column(TIMESTAMP(timezone=True))

    score:               Mapped[int | None]     = mapped_column(Integer)
    grade:               Mapped[str | None]     = mapped_column(SAEnum(Grade, name="grade"))
    score_description:   Mapped[str | None]     = mapped_column(Text)
    review_summary:      Mapped[str | None]     = mapped_column(Text)

    extracted_text:      Mapped[str | None]     = mapped_column(Text)
    raw_ai_response:     Mapped[dict | None]    = mapped_column(JSONB)

    created_at:          Mapped[datetime]       = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at:          Mapped[datetime]       = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    user:        "User"             = relationship("User", back_populates="reports")
    issues:      "list[Issue]"      = relationship("Issue", back_populates="report", cascade="all, delete-orphan", order_by="Issue.sort_order")
    math_checks: "list[MathCheck]"  = relationship("MathCheck", back_populates="report", cascade="all, delete-orphan", order_by="MathCheck.sort_order")
    revisions:   "list[ReportRevision]" = relationship("ReportRevision", back_populates="report", cascade="all, delete-orphan")


class Issue(Base):
    __tablename__ = "issues"

    id:              Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id:       Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)

    issue_code:      Mapped[str]            = mapped_column(String, nullable=False)
    severity:        Mapped[str]            = mapped_column(SAEnum(SeverityLevel, name="severity_level"), nullable=False)
    section:         Mapped[str]            = mapped_column(SAEnum(ReportSection, name="report_section"), nullable=False)

    title:           Mapped[str]            = mapped_column(Text, nullable=False)
    location:        Mapped[str | None]     = mapped_column(Text)
    problem:         Mapped[str]            = mapped_column(Text, nullable=False)
    why_it_matters:  Mapped[str | None]     = mapped_column(Text)
    correction:      Mapped[str | None]     = mapped_column(Text)
    rule_reference:  Mapped[str | None]     = mapped_column(String)

    extracted_value: Mapped[str | None]     = mapped_column(Text)
    correct_value:   Mapped[str | None]     = mapped_column(Text)

    sort_order:      Mapped[int]            = mapped_column(Integer, default=0)

    feedback:        Mapped[str | None]     = mapped_column(SAEnum(FeedbackType, name="feedback_type"))
    feedback_note:   Mapped[str | None]     = mapped_column(Text)
    feedback_at:     Mapped[datetime | None]= mapped_column(TIMESTAMP(timezone=True))
    feedback_by:     Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    created_at:      Mapped[datetime]       = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at:      Mapped[datetime]       = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    report: "Report" = relationship("Report", back_populates="issues")


class MathCheck(Base):
    __tablename__ = "math_checks"

    id:               Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id:        Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)

    item:             Mapped[str]        = mapped_column(Text, nullable=False)
    reported_value:   Mapped[str | None] = mapped_column(Text)
    calculated_value: Mapped[str | None] = mapped_column(Text)
    is_match:         Mapped[bool]       = mapped_column(Boolean, default=True)
    variance:         Mapped[str | None] = mapped_column(Text)
    sort_order:       Mapped[int]        = mapped_column(Integer, default=0)

    created_at:       Mapped[datetime]   = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    report: "Report" = relationship("Report", back_populates="math_checks")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id:         Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id:  Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("reports.id", ondelete="SET NULL"))
    user_id:    Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    event:      Mapped[str]            = mapped_column(Text, nullable=False)
    rule_ref:   Mapped[str | None]     = mapped_column(String)
    metadata:   Mapped[dict | None]    = mapped_column(JSONB)
    ip_address: Mapped[str | None]     = mapped_column(String)
    created_at: Mapped[datetime]       = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class ReportRevision(Base):
    __tablename__ = "report_revisions"

    id:              Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id:       Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    revision_number: Mapped[int]        = mapped_column(Integer, default=1)
    triggered_by:    Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    change_summary:  Mapped[str | None] = mapped_column(Text)
    score_before:    Mapped[int | None] = mapped_column(Integer)
    score_after:     Mapped[int | None] = mapped_column(Integer)
    issue_delta:     Mapped[dict | None]= mapped_column(JSONB)
    created_at:      Mapped[datetime]   = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    report: "Report" = relationship("Report", back_populates="revisions")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id:          Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:     Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key_hash:    Mapped[str]            = mapped_column(String, unique=True, nullable=False)
    key_prefix:  Mapped[str]            = mapped_column(String, nullable=False)
    label:       Mapped[str | None]     = mapped_column(String)
    last_used:   Mapped[datetime | None]= mapped_column(TIMESTAMP(timezone=True))
    expires_at:  Mapped[datetime | None]= mapped_column(TIMESTAMP(timezone=True))
    is_active:   Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime]       = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    user: "User" = relationship("User", back_populates="api_keys")
