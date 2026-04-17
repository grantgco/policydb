-- Migration 161: repair policies whose layer_position is "Primary" but whose
-- attachment_point is greater than $0. A primary by definition attaches at $0;
-- these rows are internally inconsistent (often imported from spreadsheets that
-- omit the layer_position column, which previously defaulted to "Primary"
-- regardless of attachment_point).
--
-- Fix forward by promoting the misclassified rows to "Excess". The chart
-- classifier already trusts attachment_point as authoritative, but other
-- surfaces (renewal pipeline, schedule, exec summary) read layer_position
-- directly, so the data needs to match reality.

UPDATE policies
SET layer_position = 'Excess'
WHERE layer_position = 'Primary'
  AND attachment_point IS NOT NULL
  AND attachment_point > 0;
