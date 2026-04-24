"""
AppraisalIQ Review Engine
Calls Anthropic Claude with a structured USPAP/secondary-market review prompt,
parses the JSON response, and persists all results to PostgreSQL.
"""
import base64
import json
import os
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
1. Identify ALL errors, deficiencies, omissions, unsupported conclusions, and compliance violations — no matter how small, but do not classify items as errors if they are consistent with standard appraisal rounding conventions or are stylistic rather than factual or compliance-related issues.

2. Perform exact mathematical recalculation of every figure (price/SF, price/acre, adjustments, GRM, NOI, cap rate, EGI, EGIM, indicated values, reconciled totals). When recalculating tabular data such as comparable sales grids, you MUST use only the values from the same row — never mix sale prices, square footages, or other inputs across rows. All unit values must be evaluated at two decimal places. If your recalculated result produces extended decimals, you must round to two decimal places before comparison. If the rounded result matches the reported figure, it is considered correct and must not be flagged. Only flag a mathematical error if the rounded value does not match the reported value or if the calculation fails to reconcile to the total. Totals must be tested using the reported rounded unit values, not extended internal decimals. Reconciled value conclusions are often expressed in rounded form, and minor differences caused by rounding are acceptable. Do not flag a discrepancy if the difference between the indicated value and the reconciled value is within a reasonable rounding range, such as rounding to the nearest thousand or nearest five thousand.

   ABSOLUTE ROUNDING TOLERANCE RULE (no exceptions): For all per-unit value calculations — including but not limited to price per SF, price per acre, price per unit, GRM, GIM, EGIM — a difference of $0.01 or less between the reported value and your calculated value is ALWAYS within acceptable appraisal rounding convention. You MUST mark these as match=true and variance=null. Appraisers routinely round intermediate division results differently than extended-decimal computation, producing $0.01 discrepancies that are mathematically valid. Never flag a $0.01 per-unit difference as an error of any severity. Never include it as an issue. If the only discrepancy is $0.01, the calculation is correct.

3. Cross-reference every section for internal consistency — comp data, narrative descriptions, adjustment grids, certification language — and ensure that all unit values, totals, and conclusions reconcile exactly when using the stated rounding conventions. Do not flag minor rounding differences that reconcile at two decimal places. When comparing indicated values to final reconciled values, recognize that reconciliation is typically reported in rounded figures. Do not flag a difference as an error if the reconciled value is a reasonable rounding of the indicated value and falls within a typical rounding range such as the nearest thousand or nearest five thousand.

4. Evaluate narrative quality: USPAP-required language, scope of work, intended use/user statements, limiting conditions, and all assumptions including but not limited to extraordinary assumptions and hypothetical conditions, certification compliance. If a required section is completely missing or non-compliant, it should be flagged as a major or critical issue. If the section is present but lacks depth, explanation, or detail, it should be classified as a minor issue only and not escalated to critical or major unless it is completely missing required data. The extent of detail in the scope of work is up to the appraiser's discretion.

5. Evaluate HBU analysis: legally permissible, physically possible, financially feasible, maximally productive. If all four tests are present but minimally explained, classify as a minor deficiency. Only classify as major if one or more required components are missing or unsupported.

6. Evaluate income approach: rent comparable support, vacancy derivation, expense ratio, cap rate support, DCF if applicable. Ensure lease structures (NNN, MG, etc.) are treated consistently and calculations are accurate. Only flag issues as major if they impact value conclusions or are unsupported; minor formatting or presentation issues should not be escalated.

7. Evaluate reconciliation: is the final value conclusion logically derived from the approaches? If reasoning is present but could be expanded, classify as minor. If the conclusion is unsupported, inconsistent, or contradicts the data, classify as major. Evaluate that the appraiser used the correct appraisal premise such as "as is", "at completion", "at stabilization", "retrospective", or any other premises required for the appraisal in review.

8. Assign severity: CRITICAL = deal-stopper requiring correction before delivery (USPAP violations, missing required elements, or material math errors impacting value conclusion by more than $1,000); MAJOR = significant deficiency affecting credibility or value support — a math error only qualifies as MAJOR if the correct value differs from the reported value by more than $0.01 per unit or more than $1,000 in total; MINOR = rounding differences, formatting issues, or areas where additional detail or clarity could improve the report; INFO = observation. Do not classify rounding differences or lack of narrative depth alone as CRITICAL. A $0.01 per-unit rounding difference is NEVER an error and must never appear in the issues list at any severity.

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


def _build_user_prompt(
    report_text: str,
    report_type: str,
    review_standard: str,
    reference_examples: list[dict] | None = None,
) -> str:
    standard_desc = {
        "all": "USPAP 2024, Fannie Mae Selling Guide (B4-1), and Freddie Mac Single-Family Seller/Servicer Guide",
        "uspap": "USPAP 2024 only (Ethics Rule, Competency Rule, Scope of Work Rule, Standards 1 and 2)",
        "fannie": "Fannie Mae Selling Guide (B4-1) requirements",
        "freddie": "Freddie Mac Single-Family Seller/Servicer Guide requirements",
    }.get(review_standard, "USPAP 2024 and secondary market guidelines")

    reference_section = ""
    if reference_examples:
        parts = []
        for i, ex in enumerate(reference_examples, 1):
            parts.append(
                f"--- APPROVED REFERENCE REPORT {i}: {ex['name']} "
                f"({ex.get('property_type', 'commercial')}"
                f"{', approved by ' + ex['approved_by'] if ex.get('approved_by') else ''}) ---\n"
                f"{ex['text'][:6000]}\n"  # cap per-example to avoid token overflow
                f"--- END REFERENCE REPORT {i} ---"
            )
        reference_section = (
            "\n\nCALIBRATION REFERENCES — IMPORTANT INSTRUCTIONS:\n"
            "The following are bank-approved appraisals for this property type. "
            "Use them ONLY to calibrate your understanding of acceptable narrative depth, how adjustments are supported, "
            "how math is presented, and what USPAP-compliant language looks like. "
            "Every appraisal covers a different property, market, and set of comparables. "
            "The report under review is NOT expected to resemble these references in content, structure, or conclusions. "
            "NEVER flag a difference between the report under review and a reference report as an issue. "
            "ONLY flag deviations from USPAP 2024 and secondary market guidelines.\n\n"
            + "\n\n".join(parts)
            + "\n\n"
        )

    return f"""Perform a complete technical appraisal review of the following {report_type} appraisal report under {standard_desc}.
{reference_section}
Be exhaustive. Recalculate every number. Flag every inconsistency between sections. Evaluate every required USPAP element. Review every adjustment for support. Check the HBU analysis for completeness. Verify income approach inputs. Confirm reconciliation logic.

APPRAISAL REPORT TEXT:
{report_text}

Return only the JSON object described in the system prompt. No other text."""


def _build_message_content(
    report_text: str,
    report_type: str,
    review_standard: str,
    report_file_path: str | None,
    reference_examples: list[dict] | None,
) -> list[dict]:
    """Build the Claude messages content array, using PDF document blocks when files are available."""
    content = []

    # Add up to 2 reference PDFs as document blocks so the AI sees charts/tables/graphics
    for ref in (reference_examples or [])[:2]:
        fp = ref.get("file_path")
        if fp and os.path.exists(fp):
            try:
                with open(fp, "rb") as fh:
                    pdf_data = base64.standard_b64encode(fh.read()).decode()
                content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data},
                    "title": f"Bank-Approved Reference: {ref['name']}",
                    "context": (
                        f"CALIBRATION REFERENCE ONLY — DO NOT COMPARE TO THE REPORT UNDER REVIEW. "
                        f"This is a bank-approved, fully compliant {ref.get('property_type','commercial')} appraisal"
                        + (f" accepted by {ref['approved_by']}" if ref.get("approved_by") else "")
                        + ". Use it solely to calibrate your understanding of: acceptable narrative depth, "
                        "how adjustments are supported, how math is presented, and what USPAP-compliant "
                        "language looks like. Every appraisal covers a different property, market, and set "
                        "of comparables — the report under review is NOT expected to resemble this reference "
                        "in content, structure, or conclusions. Never flag a difference between the report "
                        "under review and this reference as an issue. Only flag deviations from USPAP 2024 "
                        "and secondary market guidelines."
                    ),
                })
            except OSError:
                pass  # fall through to text fallback below

        # Text fallback if no file stored
        if not fp or not os.path.exists(fp or ""):
            excerpt = ref.get("text", "")[:6000]
            if excerpt:
                content.append({
                    "type": "text",
                    "text": (
                        f"\n\n--- CALIBRATION REFERENCE ONLY (DO NOT COMPARE TO REPORT UNDER REVIEW): "
                        f"{ref['name']}"
                        + (f" accepted by {ref['approved_by']}" if ref.get("approved_by") else "")
                        + " — Use this solely to calibrate acceptable narrative depth, adjustment support, "
                        "math presentation, and USPAP-compliant language. Never flag a difference between "
                        "the report under review and this reference as an issue. Only flag deviations from "
                        f"USPAP 2024 and secondary market guidelines. ---\n{excerpt}\n--- END REFERENCE ---"
                    ),
                })

    # Add the main report being reviewed — prefer the raw PDF for full visual fidelity
    if report_file_path and os.path.exists(report_file_path):
        try:
            with open(report_file_path, "rb") as fh:
                pdf_data = base64.standard_b64encode(fh.read()).decode()
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data},
                "title": "Appraisal Report Under Review",
            })
            content.append({"type": "text", "text": _build_instruction_prompt(report_type, review_standard)})
            return content
        except OSError:
            pass

    # Fall back to extracted text
    content.append({"type": "text", "text": _build_user_prompt(report_text, report_type, review_standard)})
    return content


def _build_instruction_prompt(report_type: str, review_standard: str) -> str:
    """Instruction-only prompt used when the report is sent as a PDF document block."""
    standard_desc = {
        "all": "USPAP 2024, Fannie Mae Selling Guide (B4-1), and Freddie Mac Single-Family Seller/Servicer Guide",
        "uspap": "USPAP 2024 only (Ethics Rule, Competency Rule, Scope of Work Rule, Standards 1 and 2)",
        "fannie": "Fannie Mae Selling Guide (B4-1) requirements",
        "freddie": "Freddie Mac Single-Family Seller/Servicer Guide requirements",
    }.get(review_standard, "USPAP 2024 and secondary market guidelines")

    return (
        f"Perform a complete technical appraisal review of the {report_type} appraisal report provided above "
        f"under {standard_desc}.\n\n"
        "Be exhaustive. Recalculate every number. Flag every inconsistency between sections. "
        "Evaluate every required USPAP element. Review every adjustment for support. "
        "Check the HBU analysis for completeness. Verify income approach inputs. Confirm reconciliation logic. "
        "Use all visual content — tables, charts, photos, maps — in your analysis.\n\n"
        "Return only the JSON object described in the system prompt. No other text."
    )


async def run_review_streaming(
    report_id: uuid.UUID,
    report_text: str,
    report_type: str,
    review_standard: str,
    db: AsyncSession,
    reference_examples: list[dict] | None = None,
    report_file_path: str | None = None,
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
        message_content = _build_message_content(
            report_text, report_type, review_standard, report_file_path, reference_examples
        )
        async with get_client().messages.stream(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message_content}],
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
