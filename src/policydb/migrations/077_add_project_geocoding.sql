-- Add latitude/longitude columns to projects for map geocoding cache
ALTER TABLE projects ADD COLUMN latitude REAL;
ALTER TABLE projects ADD COLUMN longitude REAL;
