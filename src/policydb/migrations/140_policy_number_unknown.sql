-- Migration 140: Add policy_number_unknown flag.
--
-- When toggled on, the policy number field is excluded from data health
-- scoring — useful for policies where the number genuinely isn't available
-- yet (e.g., new placements, binders awaiting issuance).

ALTER TABLE policies ADD COLUMN policy_number_unknown INTEGER DEFAULT 0;
