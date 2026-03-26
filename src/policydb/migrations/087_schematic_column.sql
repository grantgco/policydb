-- Migration 087: Add schematic_column to policies for tower chart positioning
-- Primary/underlying policies get a column number (1,2,3...) for left-to-right
-- position within their tower_group schematic. Excess layers leave this NULL
-- to span all columns.
ALTER TABLE policies ADD COLUMN schematic_column INTEGER;
