-- Migration 158: Set issues to DEPTH_FULL on templates that include issues
-- but had no depth override, so checklists and activity history are surfaced.

UPDATE prompt_templates
SET depth_overrides = '{"issues": 1}'
WHERE is_builtin = 1
  AND name IN ('Renewal Status Email', 'Open Items Call Agenda', 'Stewardship Report Shell')
  AND (depth_overrides IS NULL OR depth_overrides = '');
