"""
AppraisalIQ API Routes
All endpoints: auth, reports (upload + review), issues, feedback, audit log.
"""
import os
import uuid
import json
import aiofiles
from datetime import datetime, timezone
from typing import List, Optional
from pathlib import Path

from fastapi import (
    APIRouter, Depends, HTTPException, UploadFile, File,
    Form, Request, status, BackgroundTasks
)
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from core.config import settings
from core.auth import (
    verify_password, hash_password, create_access_token,
    get_current_user, get_current_admin
)
from db.database import get_db, check_db_connection
from db.models import (
    User, Report, Issue, MathCheck, AuditLog, ReportRevision,
    ReportStatus, FeedbackType
)
from api.schemas import (
    UserRegister, UserLogin, TokenResponse, UserOut,
    ReportDetail, ReportListItem, ReportStatusOut,
    IssueOut, MathCheckOut, IssueFeedback, AuditLogOut, HealthResponse
)
from services.extractor import extract_text_from_bytes
from services.review_engine import run_review_streaming

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    db_ok = await check_db_connection()
    return {
        "status": "ok" if db_ok else "degraded",
        "version": settings.APP_VERSION,
        "db": db_ok,
        "environment": settings.ENVIRONMENT,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/auth/register", response_model=TokenResponse, tags=["Auth"])
async def register(body: UserRegister, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        company=body.company,
        license_number=body.license_number,
    )
    db.add(user)
    await db.flush()

    token = create_access_token(str(user.id))
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "is_admin": user.is_admin,
    }


@router.post("/auth/login", response_model=TokenResponse, tags=["Auth"])
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(str(user.id))
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "is_admin": user.is_admin,
    }


@router.get("/auth/me", response_model=UserOut, tags=["Auth"])
async def me(user: User = Depends(get_current_user)):
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTS — Upload & List
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/reports/upload", tags=["Reports"])
async def upload_report(
    request: Request,
    file: Optional[UploadFile] = File(None),
    report_text: Optional[str] = Form(None),
    report_type: str = Form("commercial"),
    review_standard: str = Form("all"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a file or paste text. Returns report_id for polling/streaming."""

    if not file and not report_text:
        raise HTTPException(status_code=400, detail="Must provide file or report_text")

    # ── Handle file upload ────────────────────────────────────────────────────
    file_path = None
    extracted_text = report_text or ""
    original_filename = "pasted_report.txt"
    file_size = 0
    mime_type = "text/plain"

    if file:
        original_filename = file.filename or "upload"
        content = await file.read()
        file_size = len(content)

        if file_size > settings.max_file_size_bytes:
            raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit")

        mime_type = file.content_type or "application/octet-stream"

        # Save to disk
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        safe_name = f"{uuid.uuid4()}_{original_filename}"
        file_path = str(Path(settings.UPLOAD_DIR) / safe_name)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)

        extracted_text = extract_text_from_bytes(content, mime_type, original_filename)

    if not extracted_text or len(extracted_text.strip()) < 50:
        raise HTTPException(status_code=422, detail="Could not extract meaningful text from the provided content. Ensure the file is readable and contains report text.")

    # ── Create DB record ──────────────────────────────────────────────────────
    report = Report(
        user_id           = user.id,
        original_filename = original_filename,
        file_path         = file_path,
        file_size_bytes   = file_size if file else len(extracted_text.encode()),
        file_mime_type    = mime_type,
        review_standard   = review_standard,
        report_type       = report_type if report_type in {"commercial","residential","multifamily","land","industrial","retail","office","mixed_use"} else "commercial",
        extracted_text    = extracted_text,
        status            = ReportStatus.queued,
    )
    db.add(report)
    await db.flush()

    # Audit
    db.add(AuditLog(
        report_id  = report.id,
        user_id    = user.id,
        event      = f"Report uploaded: {original_filename}",
        rule_ref   = "UPLOAD",
        ip_address = str(request.client.host) if request.client else None,
    ))

    return {"report_id": str(report.id), "status": "queued"}


@router.get("/reports", response_model=List[ReportListItem], tags=["Reports"])
async def list_reports(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all reports for the current user, newest first."""
    # Subquery for issue counts
    critical_sq = (
        select(Issue.report_id, func.count(Issue.id).label("cnt"))
        .where(Issue.severity == "critical")
        .group_by(Issue.report_id)
        .subquery()
    )
    major_sq = (
        select(Issue.report_id, func.count(Issue.id).label("cnt"))
        .where(Issue.severity == "major")
        .group_by(Issue.report_id)
        .subquery()
    )

    stmt = (
        select(
            Report,
            func.coalesce(critical_sq.c.cnt, 0).label("critical_count"),
            func.coalesce(major_sq.c.cnt, 0).label("major_count"),
        )
        .outerjoin(critical_sq, Report.id == critical_sq.c.report_id)
        .outerjoin(major_sq, Report.id == major_sq.c.report_id)
        .where(Report.user_id == user.id)
        .order_by(desc(Report.created_at))
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    result = []
    for row in rows:
        r = row[0]
        item = ReportListItem(
            id=r.id, original_filename=r.original_filename,
            property_address=r.property_address, property_type=r.property_type,
            status=r.status, score=r.score, grade=r.grade,
            appraiser_name=r.appraiser_name, final_value=r.final_value,
            created_at=r.created_at, critical_count=row[1], major_count=row[2],
        )
        result.append(item)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTS — Review (Streaming SSE)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/reports/{report_id}/review", tags=["Reports"])
async def start_review(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start/restart AI review for a report. Returns streaming SSE."""
    report = await db.get(Report, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status == ReportStatus.processing:
        raise HTTPException(status_code=409, detail="Review already in progress")

    if not report.extracted_text:
        raise HTTPException(status_code=422, detail="No text content available for review")

    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured on server")

    async def event_stream():
        async for chunk in run_review_streaming(
            report_id=report.id,
            report_text=report.extracted_text,
            report_type=report.report_type or "commercial",
            review_standard=report.review_standard or "all",
            db=db,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTS — Detail & Status
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/reports/{report_id}", response_model=ReportDetail, tags=["Reports"])
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = await db.get(Report, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/reports/{report_id}/status", response_model=ReportStatusOut, tags=["Reports"])
async def get_report_status(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = await db.get(Report, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.delete("/reports/{report_id}", tags=["Reports"])
async def delete_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = await db.get(Report, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")
    # Delete file from disk
    if report.file_path and os.path.exists(report.file_path):
        os.remove(report.file_path)
    await db.delete(report)
    return {"deleted": True}


# ═══════════════════════════════════════════════════════════════════════════════
# ISSUES — Feedback
# ═══════════════════════════════════════════════════════════════════════════════

@router.patch("/issues/{issue_id}/feedback", response_model=IssueOut, tags=["Issues"])
async def submit_feedback(
    issue_id: uuid.UUID,
    body: IssueFeedback,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Confirm, dismiss, correct, or dispute a flagged issue."""
    issue = await db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    # Verify ownership via report
    report = await db.get(Report, issue.report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    issue.feedback      = body.feedback
    issue.feedback_note = body.feedback_note
    issue.feedback_at   = datetime.now(timezone.utc)
    issue.feedback_by   = user.id

    # Audit
    db.add(AuditLog(
        report_id = report.id,
        user_id   = user.id,
        event     = f"Issue feedback: {body.feedback} — {issue.issue_code}: {issue.title}",
        rule_ref  = "USER_FEEDBACK",
    ))

    return issue


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/reports/{report_id}/audit", response_model=List[AuditLogOut], tags=["Audit"])
async def get_audit_log(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = await db.get(Report, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.report_id == report_id)
        .order_by(desc(AuditLog.created_at))
        .limit(200)
    )
    return result.scalars().all()


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/users", response_model=List[UserOut], tags=["Admin"])
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    result = await db.execute(select(User).order_by(desc(User.created_at)))
    return result.scalars().all()


@router.get("/admin/stats", tags=["Admin"])
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    total_reports  = (await db.execute(select(func.count(Report.id)))).scalar()
    complete       = (await db.execute(select(func.count(Report.id)).where(Report.status == "complete"))).scalar()
    total_users    = (await db.execute(select(func.count(User.id)))).scalar()
    total_issues   = (await db.execute(select(func.count(Issue.id)))).scalar()
    avg_score      = (await db.execute(select(func.avg(Report.score)).where(Report.score != None))).scalar()
    math_errors    = (await db.execute(select(func.count(MathCheck.id)).where(MathCheck.is_match == False))).scalar()
    return {
        "total_reports": total_reports,
        "complete_reports": complete,
        "total_users": total_users,
        "total_issues": total_issues,
        "avg_score": round(float(avg_score), 1) if avg_score else None,
        "math_errors_found": math_errors,
    }
