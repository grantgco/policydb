-- Sync existing assigned policies with their location's address.
-- Only updates where the location has a non-empty address.
UPDATE policies SET
  exposure_address = (SELECT address FROM projects WHERE projects.id = policies.project_id),
  exposure_city = (SELECT city FROM projects WHERE projects.id = policies.project_id),
  exposure_state = (SELECT state FROM projects WHERE projects.id = policies.project_id),
  exposure_zip = (SELECT zip FROM projects WHERE projects.id = policies.project_id)
WHERE project_id IS NOT NULL
  AND (SELECT address FROM projects WHERE projects.id = policies.project_id) IS NOT NULL
  AND (SELECT address FROM projects WHERE projects.id = policies.project_id) != '';
