"""
AppraisalIQ Review Engine
Calls Anthropic Claude with a structured USPAP/secondary-market review prompt,
parses the JSON response, and persists all results to PostgreSQL.
"""
import json
import re
import uuid
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
from typing import AsyncIterator, Optional

import anthropic
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.config import settings
from db.models import (
    Report, Issue, MathCheck, AuditLog, ReportRevision,
    ReportStatus, SeverityLevel, ReportSection, Grade
)

logger = structlog.get_logger()

def get_client():
    import os
    return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are a senior certified commercial appraisal reviewer with 25 years of experience reviewing appraisals for secondary market delivery. You enforce USPAP 2024, Fannie Mae Selling Guide (B4-1), and Freddie Mac requirements with the precision of a bank chief appraiser.

Your job: perform a complete, line-by-line technical review of the appraisal report. You must:
1. Identify ALL errors, deficiencies, omissions, unsupported conclusions, and compliance violations — no matter how small
2. Perform exact mathematical recalculation of every figure (price/SF, price/acre, adjustments, GRM, NOI, cap rate, EGI, EGIM, indicated values, reconciled totals). When recalculating tabular data such as comparable sales grids, YOU MUST use only the values from the same row — never mix sale prices, square footages, or adjustments from different rows or different comparables. Verify each row independently.
3. Cross-reference every section for internal consistency — comp data, narrative descriptions, adjustment grids, certification language
4. Evaluate narrative quality: USPAP-required language, scope of work, intended use/user statements, limiting conditions, certification compliance
5. Evaluate HBU analysis: legally permissible, physically possible, financially feasible, maximally productive
6. Evaluate income approach: rent comparables support, vacancy derivation, expense ratio, cap rate support, DCF if applicable
7. Evaluate reconciliation: is the final value conclusion logically derived from the approaches?
8. Assign severity: CRITICAL = deal-stopper requiring correction before delivery; MAJOR = significant deficiency; MINOR = quality/professionalism issue; INFO = observation

Output ONLY valid compact JSON — no markdown, no preamble, no commentary outside the JSON structure.

JSON schema:
{
  "report_metadata": {
    "property_address": "string",
    "property_type": "commercial|residential|multifamily|land|industrial|retail|office|mixed_use",
    "date_of_value": "YYYY-MM-DD or null",
    "report_date": "YYYY-MM-DD or null",
    "appraiser": "string",
    "appraiser_license": "string or null",
    "client_name": "string or null",
    "intended_use": "string or null",
    "final_value": "numeric string or null",
    "report_form": "string or null"
  },
  "score": <0-100 integer>,
  "grade": "PASS|CONDITIONAL|FAIL",
  "score_description": "one sentence explaining the score",
  "summary": "2-3 paragraph professional review summary written as a bank reviewer would",
  "issues": [
    {
      "id": "ISS-001",
      "severity": "critical|major|minor|info",
      "section": "uspap|math|narrative|comps|adjustments|income|hbu|reconciliation|cost|zoning|site|certification|general",
      "title": "concise issue title",
      "location": "where in report, e.g. Sales Comparison Approach — Comparable 1",
      "problem": "clear technical explanation of the exact deficiency",
      "why_it_matters": "underwriting and compliance impact — what risk does this create?",
      "correction": "specific, actionable correction the appraiser must make",
      "rule_reference": "specific rule/guideline, e.g. USPAP SR 1-1(c), Fannie Mae B4-1.3-09, Freddie Mac 5605.1",
      "extracted_value": "what the report states (if numeric/verifiable issue)",
      "correct_value": "what it should be (if calculable)"
    }
  ],
  "math_checks": [
    {
      "item": "descriptive label, e.g. Comparable 1 — Price per SF",
      "reported": "value as stated in report",
      "calculated": "your recalculated value",
      "match": true|false,
      "variance": "difference or null if match"
    }
  ]
}"""


def _build_user_prompt(report_text: str, report_type: str, review_standard: str) -> str:
    standard_desc = {
        "all": "USPAP 2024, Fannie Mae Selling Guide (B4-1), and Freddie Mac Single-Family Seller/Servicer Guide",
        "uspap": "USPAP 2024 only (Ethics Rule, Competency Rule, Scope of Work Rule, Standards 1 and 2)",
        "fannie": "Fannie Mae Selling Guide (B4-1) requirements",
        "freddie": "Freddie Mac Single-Family Seller/Servicer Guide requirements",
    }.get(review_standard, "USPAP 2024 and secondary market guidelines")

    return f"""Perform a complete technical appraisal review of the following {report_type} appraisal report under {standard_desc}.

Be exhaustive. Recalculate every number. Flag every inconsistency between sections. Evaluate every required USPAP element. Review every adjustment for support. Check the HBU analysis for completeness. Verify income approach inputs. Confirm reconciliation logic.

APPRAISAL REPORT TEXT:
{report_text}

Return only the JSON object described in the system prompt. No other text."""


async def run_review_streaming(
    report_id: uuid.UUID,
    report_text: str,
    report_type: str,
    review_standard: str,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """
    Runs the full AI review with streaming.
    Yields Server-Sent Event strings for the frontend progress display.
    Persists results to DB when complete.
    """
    log = logger.bind(report_id=str(report_id))

    # Mark as processing
    report = await db.get(Report, report_id)
    if not report:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Report not found'})}\n\n"
        return

    report.status = ReportStatus.processing
    report.processing_started = datetime.now(timezone.utc)
    await db.commit()

    await _audit(db, report_id, report.user_id, "AI review started", "ENGINE_START")

    yield f"data: {json.dumps({'type': 'status', 'step': 'Sending to AI review engine'})}\n\n"

    full_text = ""
    try:
        async with get_client().messages.stream(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(report_text, report_type, review_standard)}],
        ) as stream:
            async for chunk in stream.text_stream:
                full_text += chunk
                # Send progress ping every ~200 chars so frontend knows we're alive
                if len(full_text) % 200 < len(chunk):
                    yield f"data: {json.dumps({'type': 'progress', 'chars': len(full_text)})}\n\n"

        log.info("ai_response_received", chars=len(full_text))

    except anthropic.APIError as e:
        log.error("anthropic_api_error", error=str(e))
        report.status = ReportStatus.failed
        report.error_message = str(e)
        await db.commit()
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    # Parse JSON
    yield f"data: {json.dumps({'type': 'status', 'step': 'Parsing review results'})}\n\n"
    try:
        # Strip markdown code blocks
        clean = re.sub(r"```json\s*", "", full_text)
        clean = re.sub(r"```\s*", "", clean).strip()
        # Extract just the JSON object
        json_start = clean.find("{")
        json_end = clean.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            clean = clean[json_start:json_end]
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        log.error("json_parse_failed", error=str(e), raw=full_text[:500])
        report.status = ReportStatus.failed
        report.error_message = f"Failed to parse AI response: {str(e)}"
        await db.commit()
        yield f"data: {json.dumps({'type': 'error', 'message': 'Failed to parse AI response. Please retry.'})}\n\n"
        return

    # Persist results
    yield f"data: {json.dumps({'type': 'status', 'step': 'Saving to database'})}\n\n"
    try:
        await _persist_results(db, report, data, full_text)
        await db.commit()
    except Exception as e:
        log.error("persist_failed", error=str(e))
        await db.rollback()
        report.status = ReportStatus.failed
        report.error_message = f"Database error: {str(e)}"
        await db.commit()
        yield f"data: {json.dumps({'type': 'error', 'message': 'Database error saving results.'})}\n\n"
        return

    await _audit(db, report_id, report.user_id, f"Review complete. Score: {data.get('score')}. Issues: {len(data.get('issues', []))}", "REVIEW_COMPLETE")

    yield f"data: {json.dumps({'type': 'complete', 'report_id': str(report_id)})}\n\n"


async def _persist_results(db: AsyncSession, report: Report, data: dict, raw_text: str) -> None:
    meta = data.get("report_metadata", {})

    # Update report fields
    report.property_address  = meta.get("property_address")
    report.appraiser_name    = meta.get("appraiser")
    report.appraiser_license = meta.get("appraiser_license")
    report.client_name       = meta.get("client_name")
    report.intended_use      = meta.get("intended_use")
    report.report_form       = meta.get("report_form")
    score_val = data.get("score")
    report.score             = int(score_val) if score_val is not None and str(score_val).isdigit() else None
    report.grade             = data.get("grade")
    report.score_description = data.get("score_description")
    report.review_summary    = data.get("summary")
    report.raw_ai_response   = data
    report.status            = ReportStatus.complete
    report.processing_finished = datetime.now(timezone.utc)

    # Parse property_type
    pt = meta.get("property_type", "").lower().replace("-", "_")
    valid_types = {"commercial","residential","multifamily","land","industrial","retail","office","mixed_use"}
    report.property_type = pt if pt in valid_types else None

    # Parse dates safely
    for field, key in [("date_of_value", "date_of_value"), ("report_date", "report_date")]:
        raw_date = meta.get(key)
        if raw_date:
            try:
                setattr(report, field, date.fromisoformat(str(raw_date)))
            except (ValueError, TypeError):
                pass

    # Parse final value
    fv = meta.get("final_value")
    if fv:
        try:
            numeric = re.sub(r"[^\d.]", "", str(fv))
            if numeric:
                report.final_value = Decimal(numeric)
        except InvalidOperation:
            pass

    # Clear old issues / math checks (re-review scenario)
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(Issue).where(Issue.report_id == report.id))
    await db.execute(sql_delete(MathCheck).where(MathCheck.report_id == report.id))
    await db.flush()

    # Insert issues
    valid_sections = {s.value for s in ReportSection}
    valid_severities = {s.value for s in SeverityLevel}
    for i, raw_issue in enumerate(data.get("issues", [])):
        section = raw_issue.get("section", "general").lower()
        if section not in valid_sections:
            section = "general"
        severity = raw_issue.get("severity", "minor").lower()
        if severity not in valid_severities:
            severity = "minor"

        db.add(Issue(
            report_id      = report.id,
            issue_code     = raw_issue.get("id", f"ISS-{i+1:03d}"),
            severity       = severity,
            section        = section,
            title          = raw_issue.get("title", "Untitled Issue")[:500],
            location       = raw_issue.get("location"),
            problem        = raw_issue.get("problem", ""),
            why_it_matters = raw_issue.get("why_it_matters"),
            correction     = raw_issue.get("correction"),
            rule_reference = raw_issue.get("rule_reference"),
            extracted_value= raw_issue.get("extracted_value"),
            correct_value  = raw_issue.get("correct_value"),
            sort_order     = i,
        ))

    # Insert math checks
    for i, mc in enumerate(data.get("math_checks", [])):
        db.add(MathCheck(
            report_id        = report.id,
            item             = mc.get("item", ""),
            reported_value   = mc.get("reported"),
            calculated_value = mc.get("calculated"),
            is_match         = bool(mc.get("match", True)),
            variance         = mc.get("variance"),
            sort_order       = i,
        ))


async def _audit(db: AsyncSession, report_id: uuid.UUID, user_id: uuid.UUID, event: str, rule_ref: str) -> None:
    try:
        db.add(AuditLog(
            report_id = report_id,
            user_id   = user_id,
            event     = event,
            rule_ref  = rule_ref,
        ))
        await db.flush()
    except Exception:
        pass  # Audit failures should never break the main flow
