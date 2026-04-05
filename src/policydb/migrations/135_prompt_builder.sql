-- Migration 135: Prompt Builder tables and seed templates

CREATE TABLE IF NOT EXISTS prompt_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    deliverable_type TEXT NOT NULL DEFAULT 'other',
    description TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    closing_instruction TEXT DEFAULT '',
    required_record_types TEXT DEFAULT '[]',
    depth_overrides TEXT,
    active      INTEGER DEFAULT 1,
    is_builtin  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS prompt_templates_updated_at
    AFTER UPDATE ON prompt_templates
    BEGIN UPDATE prompt_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id; END;

CREATE TABLE IF NOT EXISTS prompt_export_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER REFERENCES prompt_templates(id),
    record_type TEXT NOT NULL,
    record_id   INTEGER NOT NULL,
    exported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_export_log_template
    ON prompt_export_log(template_id);

-- Seed built-in templates

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Renewal Status Email',
    'email',
    'Client-facing update on renewal progress and outstanding items',
    'You are a senior insurance broker drafting a professional, client-facing email. Write in a clear, confident tone. Use the structured data below as your sole source of facts. Do not invent details not present in the data.',
    'Draft a client-facing renewal status email from the above data. Include current renewal status, key upcoming milestones, any open issues requiring client attention, and next steps. Keep the tone professional but approachable.',
    '["renewal","client","milestones","issues"]',
    NULL,
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Open Items Call Agenda',
    'agenda',
    'Structured agenda for an open items call, sorted by priority',
    'You are a senior insurance broker preparing a structured meeting agenda. Organize items by priority and group related topics. The agenda should be actionable and time-efficient.',
    'Create a structured call agenda from the above data. Group items by priority (critical first), include responsible parties where known, and suggest time allocations. End with a recap of action items needed before the next call.',
    '["client","issues","follow_ups"]',
    NULL,
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Stewardship Report Shell',
    'report',
    'Annual stewardship narrative from policy and activity data',
    'You are a senior insurance broker drafting an annual stewardship report for a client. This document summarizes the year''s insurance program activity, claims, renewals, and strategic recommendations. Write in a formal, polished tone suitable for C-suite review.',
    'Draft an annual stewardship report shell from the above data. Include sections for: Executive Summary, Program Overview (policies and coverages), Renewal Activity, Open Issues and Resolutions, Market Conditions, and Strategic Recommendations for the coming year. Flag any data gaps where additional information would strengthen the report.',
    '["client","policies","renewals","issues"]',
    NULL,
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Submission Cover Note',
    'submission',
    'Carrier-facing submission context and coverage summary',
    'You are a senior insurance broker drafting a submission cover note for an underwriter. Write in a professional, factual tone. Present the risk clearly and highlight favorable attributes. Do not overstate or minimize exposures.',
    'Draft a carrier-facing submission cover note from the above data. Include: Account Overview, Coverage Requested, Current Program Summary, Risk Highlights, and any Loss History context available. Structure it for easy underwriter review.',
    '["policy","client","renewals"]',
    NULL,
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Client Coverage Narrative',
    'narrative',
    'Plain-language summary of a client''s full coverage profile',
    'You are a senior insurance broker writing a plain-language coverage summary for a client who may not be familiar with insurance terminology. Explain coverages clearly, avoid jargon where possible, and define technical terms when used.',
    'Write a plain-language summary of this client''s full insurance coverage profile from the above data. For each policy, explain what it covers and why it matters. Note any coverage gaps or areas that may need attention. Keep it accessible for a non-insurance audience.',
    '["client","policies"]',
    NULL,
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Issue Escalation Memo',
    'memo',
    'Single issue with full history for internal or client communication',
    'You are a senior insurance broker drafting an escalation memo about a specific issue. Be precise about timelines, responsibilities, and impact. The memo may be shared internally or with the client.',
    'Draft an escalation memo for the issue described above. Include: Issue Summary, Timeline of Events, Current Status, Impact Assessment, Recommended Next Steps, and Responsible Parties. Use a factual, action-oriented tone.',
    '["issue","client"]',
    '{"issues":1,"follow_ups":1}',
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'New Business Prospect Brief',
    'narrative',
    'Internal handoff summary for a new or prospective client',
    'You are a senior insurance broker writing an internal briefing document about a new or prospective client. This will be used by colleagues to quickly understand the account. Focus on risk profile, current coverage, and strategic opportunity.',
    'Write an internal prospect brief from the above data. Include: Company Overview, Current Insurance Program, Key Contacts, Risk Profile, Competitive Positioning, and Recommended Approach. Highlight any immediate opportunities or concerns.',
    '["client","policies"]',
    NULL,
    1
);
