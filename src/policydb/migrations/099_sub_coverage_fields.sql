-- Add override fields to policy_sub_coverages for enriched ghost rows
-- NULL means inherit from parent policy; non-NULL overrides parent value
ALTER TABLE policy_sub_coverages ADD COLUMN premium REAL;
ALTER TABLE policy_sub_coverages ADD COLUMN carrier TEXT;
ALTER TABLE policy_sub_coverages ADD COLUMN policy_number TEXT;
ALTER TABLE policy_sub_coverages ADD COLUMN participation_of REAL;
ALTER TABLE policy_sub_coverages ADD COLUMN layer_position TEXT;
ALTER TABLE policy_sub_coverages ADD COLUMN description TEXT DEFAULT '';
