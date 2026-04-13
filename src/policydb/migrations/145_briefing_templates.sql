-- Migration 144: Status & What's Next briefing templates for the Prompt Builder
--
-- Seeds four new built-in prompt templates that produce internal "status and
-- what's next" briefings for clients, policies, renewals, and issues. Each
-- template pulls from the new briefing-oriented assemblers (focus_items,
-- deliverables_due, recent_activity_log, pending_emails) registered in
-- prompt_assembler.py.
--
-- No schema changes — the prompt_templates table from migration 135 already
-- supports arbitrary required_record_types and depth_overrides JSON.

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Client Status & Next Steps',
    'briefing',
    'Internal briefing — what needs to happen next on this client, right now',
    'You are a senior insurance broker''s personal chief of staff. Produce an internal briefing that tells the broker exactly what to do next for this account. No client-facing language. Be direct, triage-focused, and specific about who or what is blocking each item.',
    'Produce the briefing in this order: (1) Top 3 priorities today, (2) Overdue items, (3) Due this week, (4) Waiting on external — and how to nudge each one, (5) Deliverables due, (6) Recent activity context. Each item must state the specific next action (email, call, internal task) and who/what it is blocked on. End with a one-line "bottom line" recommendation.',
    '["client","focus_items","deliverables_due","recent_activity_log","issues","renewals","follow_ups","pending_emails","contacts"]',
    '{"focus_items":1,"deliverables_due":1,"issues":1,"pending_emails":1,"recent_activity_log":1}',
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Policy Status & Next Steps',
    'briefing',
    'Internal briefing — status and next actions for a single policy',
    'You are a senior insurance broker''s personal chief of staff. Produce an internal briefing for a single policy. Focus on what the broker needs to do next to move this policy forward. No client-facing language. Be direct and specific.',
    'Produce the briefing in this order: (1) Current state of the policy in one sentence, (2) Overdue items specific to this policy, (3) Upcoming milestones and deliverables, (4) Open issues touching this policy, (5) Anything waiting on external parties, (6) Recent activity context. Each item must include the specific next action. End with a one-line "bottom line" recommendation.',
    '["policy","focus_items","milestones","deliverables_due","recent_activity_log","issues","follow_ups","pending_emails"]',
    '{"focus_items":1,"milestones":1,"deliverables_due":1,"pending_emails":1,"recent_activity_log":1}',
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Renewal Status & Next Steps',
    'briefing',
    'Internal briefing — status and next actions for a renewal in progress',
    'You are a senior insurance broker''s personal chief of staff. Produce an internal briefing for a renewal in progress. Emphasize timeline health, overdue milestones, and whether the renewal is at risk. No client-facing language. Be direct and specific.',
    'Produce the briefing in this order: (1) Renewal health in one sentence (on track / at risk / compressed / critical), (2) Days to expiration and key dates, (3) Overdue milestones and whose court they are in, (4) Deliverables due this week, (5) Open issues that could derail the renewal, (6) Pending emails and follow-ups. Each item must state the specific next action. End with a one-line "bottom line" recommendation.',
    '["renewal","focus_items","milestones","deliverables_due","recent_activity_log","issues","follow_ups","pending_emails"]',
    '{"focus_items":1,"milestones":1,"deliverables_due":1,"pending_emails":1,"recent_activity_log":1}',
    1
);

INSERT INTO prompt_templates (name, deliverable_type, description, system_prompt, closing_instruction, required_record_types, depth_overrides, is_builtin)
VALUES (
    'Issue Status & Next Steps',
    'briefing',
    'Internal briefing — status and next actions for a single open issue',
    'You are a senior insurance broker''s personal chief of staff. Produce an internal briefing for a single open issue. Focus on what the broker needs to do to move this issue to resolution. No client-facing language. Be direct and specific.',
    'Produce the briefing in this order: (1) Issue state in one sentence (with days open and severity), (2) Most recent activity and what it tells us, (3) What is currently blocking resolution and who owns the next step, (4) Specific next action — email, call, internal task, (5) Follow-ups already scheduled, (6) Any linked policies that complicate the picture. End with a one-line "bottom line" recommendation.',
    '["issue","focus_items","recent_activity_log","follow_ups","pending_emails"]',
    '{"focus_items":1,"follow_ups":1,"pending_emails":1,"recent_activity_log":1}',
    1
);
