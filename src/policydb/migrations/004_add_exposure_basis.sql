-- Migration 004: add exposure basis to policies
-- Captures the exposure description (e.g. "Payroll", "Revenue") and its value
ALTER TABLE policies ADD COLUMN exposure_basis TEXT;
ALTER TABLE policies ADD COLUMN exposure_amount REAL;
