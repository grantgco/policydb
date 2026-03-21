-- 061_project_pipeline.sql
ALTER TABLE projects ADD COLUMN project_type TEXT DEFAULT 'Location';
ALTER TABLE projects ADD COLUMN status TEXT DEFAULT 'Upcoming';
ALTER TABLE projects ADD COLUMN project_value REAL;
ALTER TABLE projects ADD COLUMN start_date DATE;
ALTER TABLE projects ADD COLUMN target_completion DATE;
ALTER TABLE projects ADD COLUMN insurance_needed_by DATE;
ALTER TABLE projects ADD COLUMN scope_description TEXT;
ALTER TABLE projects ADD COLUMN general_contractor TEXT;
ALTER TABLE projects ADD COLUMN owner_name TEXT;
ALTER TABLE projects ADD COLUMN address TEXT;
ALTER TABLE projects ADD COLUMN city TEXT;
ALTER TABLE projects ADD COLUMN state TEXT;
ALTER TABLE projects ADD COLUMN zip TEXT;
