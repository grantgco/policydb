ALTER TABLE policy_sub_coverages ADD COLUMN limit_amount REAL;
ALTER TABLE policy_sub_coverages ADD COLUMN deductible REAL;
ALTER TABLE policy_sub_coverages ADD COLUMN coverage_form TEXT DEFAULT '';
