-- Migration: add reference_reports table
-- Run this against existing databases that were initialized before this table was added.

CREATE TABLE IF NOT EXISTS reference_reports (
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

CREATE INDEX IF NOT EXISTS idx_reference_reports_type   ON reference_reports(property_type);
CREATE INDEX IF NOT EXISTS idx_reference_reports_active ON reference_reports(is_active) WHERE is_active = TRUE;
