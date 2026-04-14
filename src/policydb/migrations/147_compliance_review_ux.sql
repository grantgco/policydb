-- Compliance Review UX Redesign — audit trail + policy endorsements
--
-- 1. Add reviewed_at / reviewed_by to coverage_requirements so we can answer
--    "when was this last reviewed and by whom?" The absence of reviewed_at is
--    the signal for "compute_auto_status may still fire on this row."
-- 2. Add endorsements JSON column to policies so the policy itself is the
--    canonical home for endorsements (Waiver of Subrogation, Additional
--    Insured, Primary & Non-contributory, etc.). Previously the system had
--    no way to record which endorsements a policy actually had, so every
--    requirement with required_endorsements was forever "Partial". This fix
--    enables the bidirectional flow: compliance review writes confirmed
--    endorsements back to the policy, and the policy edit screen can set
--    them directly as a primary source of truth.

ALTER TABLE coverage_requirements ADD COLUMN reviewed_at TEXT;
ALTER TABLE coverage_requirements ADD COLUMN reviewed_by TEXT;

ALTER TABLE policies ADD COLUMN endorsements TEXT DEFAULT '[]';
