-- Fix any policies with invalid layer_position values.
-- Valid values: 'Primary', 'Umbrella', 'Excess'. Anything else → 'Primary'.
UPDATE policies
SET layer_position = 'Primary'
WHERE layer_position IS NULL
   OR layer_position NOT IN ('Primary', 'Umbrella', 'Excess');
