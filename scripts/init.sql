-- =============================================================================
-- AppraisalIQ — PostgreSQL Schema
-- =============================================================================
-- Run order: extensions → types → tables → indexes → functions → triggers
-- Compatible with PostgreSQL 14+
-- =============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";       -- fuzzy text search
CREATE EXTENSION IF NOT EXISTS "btree_gin";     -- multi-column GIN indexes

-- =============================================================================
-- ENUM TYPES
-- =============================================================================

CREATE TYPE report_status AS ENUM (
    'uploaded',
    'queued',
    'processing',
    'complete',
    'failed'
);

CREATE TYPE severity_level AS ENUM (
    'critical',
    'major',
    'minor',
    'info'
);

CREATE TYPE report_section AS ENUM (
    'uspap',
    'math',
    'narrative',
    'comps',
    'adjustments',
    'income',
    'hbu',
    'reconciliation',
    'cost',
    'zoning',
    'site',
    'certification',
    'general'
);

CREATE TYPE appraisal_type AS ENUM (
    'commercial',
    'residential',
    'multifamily',
    'land',
    'industrial',
    'retail',
    'office',
    'mixed_use'
);

CREATE TYPE review_standard AS ENUM (
    'all',
    'uspap',
    'fannie',
    'freddie'
);

CREATE TYPE feedback_type AS ENUM (
    'confirmed',
    'dismissed',
    'corrected',
    'disputed'
);

CREATE TYPE grade AS ENUM (
    'PASS',
    'CONDITIONAL',
    'FAIL'
);

-- =============================================================================
-- USERS
-- =============================================================================

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT UNIQUE NOT NULL,
    full_name       TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    company         TEXT,
    license_number  TEXT,                       -- appraiser license
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);

-- =============================================================================
-- REPORTS
-- =============================================================================

CREATE TABLE reports (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- File metadata
    original_filename   TEXT NOT NULL,
    file_path           TEXT,                   -- server-side path or S3/DO Spaces key
    file_size_bytes     BIGINT,
    file_mime_type      TEXT,

    -- Extracted report metadata
    property_address    TEXT,
    property_type       appraisal_type,
    date_of_value       DATE,
    report_date         DATE,
    appraiser_name      TEXT,
    appraiser_license   TEXT,
    client_name         TEXT,
    intended_use        TEXT,
    final_value         NUMERIC(15, 2),
    report_form         TEXT,                   -- "URAR 1004", "Narrative", etc.

    -- Review config
    review_standard     review_standard NOT NULL DEFAULT 'all',
    report_type         appraisal_type,

    -- Processing state
    status              report_status NOT NULL DEFAULT 'uploaded',
    error_message       TEXT,
    processing_started  TIMESTAMPTZ,
    processing_finished TIMESTAMPTZ,

    -- Review scores
    score               INTEGER CHECK (score BETWEEN 0 AND 100),
    grade               grade,
    score_description   TEXT,
    review_summary      TEXT,

    -- Raw extracted text (for re-analysis)
    extracted_text      TEXT,

    -- Raw AI response (for audit / debugging)
    raw_ai_response     JSONB,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reports_user_id     ON reports(user_id);
CREATE INDEX idx_reports_status      ON reports(status);
CREATE INDEX idx_reports_created_at  ON reports(created_at DESC);
CREATE INDEX idx_reports_property    ON reports USING GIN (property_address gin_trgm_ops);

-- =============================================================================
-- ISSUES  (flagged items found during review)
-- =============================================================================

CREATE TABLE issues (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id       UUID NOT NULL REFERENCES reports(id) ON DELETE CASCADE,

    -- Identity
    issue_code      TEXT NOT NULL,              -- "ISS-001", "ISS-002"…
    severity        severity_level NOT NULL,
    section         report_section NOT NULL,

    -- Content
    title           TEXT NOT NULL,
    location        TEXT,                       -- "Sales Comparison — Comp 1"
    problem         TEXT NOT NULL,
    why_it_matters  TEXT,
    correction      TEXT,
    rule_reference  TEXT,                       -- "USPAP SR 1-1(c)"

    -- Math specifics
    extracted_value TEXT,
    correct_value   TEXT,

    -- Display order
    sort_order      INTEGER NOT NULL DEFAULT 0,

    -- Feedback
    feedback        feedback_type,
    feedback_note   TEXT,
    feedback_at     TIMESTAMPTZ,
    feedback_by     UUID REFERENCES users(id),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_issues_report_id  ON issues(report_id);
CREATE INDEX idx_issues_severity   ON issues(severity);
CREATE INDEX idx_issues_section    ON issues(section);
CREATE INDEX idx_issues_feedback   ON issues(feedback);

-- =============================================================================
-- MATH CHECKS  (per-report numerical verification results)
-- =============================================================================

CREATE TABLE math_checks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id       UUID NOT NULL REFERENCES reports(id) ON DELETE CASCADE,

    item            TEXT NOT NULL,              -- "Comp 1 Price per SF"
    reported_value  TEXT,
    calculated_value TEXT,
    is_match        BOOLEAN NOT NULL DEFAULT TRUE,
    variance        TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_math_checks_report_id ON math_checks(report_id);
CREATE INDEX idx_math_checks_mismatch  ON math_checks(report_id) WHERE is_match = FALSE;

-- =============================================================================
-- AUDIT LOG  (every action taken on every report, immutable)
-- =============================================================================

CREATE TABLE audit_log (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id   UUID REFERENCES reports(id) ON DELETE SET NULL,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    event       TEXT NOT NULL,
    rule_ref    TEXT,
    metadata    JSONB,
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_report_id  ON audit_log(report_id);
CREATE INDEX idx_audit_user_id    ON audit_log(user_id);
CREATE INDEX idx_audit_created_at ON audit_log(created_at DESC);

-- =============================================================================
-- REPORT REVISIONS  (track re-reviews / corrections over time)
-- =============================================================================

CREATE TABLE report_revisions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id       UUID NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL DEFAULT 1,
    triggered_by    UUID REFERENCES users(id),
    change_summary  TEXT,
    score_before    INTEGER,
    score_after     INTEGER,
    issue_delta     JSONB,                      -- {added: N, resolved: N, unchanged: N}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (report_id, revision_number)
);

CREATE INDEX idx_revisions_report_id ON report_revisions(report_id);

-- =============================================================================
-- REFERENCE REPORTS  (bank-approved example reports used as AI context)
-- =============================================================================

CREATE TABLE reference_reports (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL,
    description     TEXT,
    property_type   appraisal_type,
    report_text     TEXT NOT NULL,
    file_path       TEXT,
    file_mime_type  TEXT,
    approved_by     TEXT,
    uploaded_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reference_reports_type     ON reference_reports(property_type);
CREATE INDEX idx_reference_reports_active   ON reference_reports(is_active) WHERE is_active = TRUE;

-- =============================================================================
-- API KEYS  (for programmatic / lender integrations)
-- =============================================================================

CREATE TABLE api_keys (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash    TEXT UNIQUE NOT NULL,           -- bcrypt hash of the raw key
    key_prefix  TEXT NOT NULL,                 -- first 8 chars for display "aiq_xxxx"
    label       TEXT,
    last_used   TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_api_keys_user_id   ON api_keys(user_id);
CREATE INDEX idx_api_keys_key_hash  ON api_keys(key_hash);

-- =============================================================================
-- AUTO-UPDATE updated_at via trigger
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_reports_updated_at
    BEFORE UPDATE ON reports
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_issues_updated_at
    BEFORE UPDATE ON issues
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =============================================================================
-- VIEWS  (useful query shortcuts)
-- =============================================================================

-- Summary view per report
CREATE VIEW report_summary AS
SELECT
    r.id,
    r.user_id,
    r.original_filename,
    r.property_address,
    r.property_type,
    r.date_of_value,
    r.appraiser_name,
    r.final_value,
    r.status,
    r.score,
    r.grade,
    r.created_at,
    COUNT(i.id) FILTER (WHERE i.severity = 'critical') AS critical_count,
    COUNT(i.id) FILTER (WHERE i.severity = 'major')    AS major_count,
    COUNT(i.id) FILTER (WHERE i.severity = 'minor')    AS minor_count,
    COUNT(i.id) FILTER (WHERE i.severity = 'info')     AS info_count,
    COUNT(i.id)                                         AS total_issues
FROM reports r
LEFT JOIN issues i ON i.report_id = r.id
GROUP BY r.id;

-- Math errors only
CREATE VIEW math_errors AS
SELECT
    mc.*,
    r.property_address,
    r.user_id
FROM math_checks mc
JOIN reports r ON r.id = mc.report_id
WHERE mc.is_match = FALSE;

-- =============================================================================
-- SEED DATA  (default admin user — password: AdminPass1!)
-- Change immediately after first login.
-- =============================================================================

INSERT INTO users (email, full_name, hashed_password, is_admin)
VALUES (
    'admin@appraisaliq.local',
    'System Administrator',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj4J/HS.iADO',  -- AdminPass1!
    TRUE
)
ON CONFLICT (email) DO NOTHING;
