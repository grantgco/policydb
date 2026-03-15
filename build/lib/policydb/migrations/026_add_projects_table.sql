-- Migration 026: Add projects table as the canonical source for project/location data.
--
-- Previously, projects were identified solely by the free-text project_name column on
-- policies. This caused notes to orphan and no referential integrity on rename.
--
-- This migration:
--   1. Creates a projects table (absorbing project_notes) with a stable id.
--   2. Populates it from distinct (client_id, project_name) pairs already in policies,
--      merging in any notes from the project_notes table.
--   3. Adds project_id FK to policies and links existing rows.

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER  NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name       TEXT     NOT NULL,
    notes      TEXT     NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, name)
);

CREATE TRIGGER IF NOT EXISTS projects_updated_at
AFTER UPDATE ON projects
BEGIN
    UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Populate one row per distinct (client_id, normalized name) pair.
-- For each group, pick the most recently entered casing and pull in any notes.
INSERT INTO projects (client_id, name, notes)
SELECT
    grouped.client_id,
    (
        SELECT TRIM(p2.project_name)
        FROM policies p2
        WHERE p2.client_id = grouped.client_id
          AND LOWER(TRIM(p2.project_name)) = grouped.norm_name
        ORDER BY p2.id DESC
        LIMIT 1
    ) AS name,
    COALESCE(
        (
            SELECT pn.notes
            FROM project_notes pn
            WHERE pn.client_id = grouped.client_id
              AND LOWER(TRIM(pn.project_name)) = grouped.norm_name
            LIMIT 1
        ),
        ''
    ) AS notes
FROM (
    SELECT DISTINCT client_id, LOWER(TRIM(project_name)) AS norm_name
    FROM policies
    WHERE project_name IS NOT NULL AND TRIM(project_name) != ''
) grouped;

-- Add project_id to policies (nullable FK).
ALTER TABLE policies ADD COLUMN project_id INTEGER REFERENCES projects(id);

-- Link existing policies to their project rows.
UPDATE policies
SET project_id = (
    SELECT pr.id
    FROM projects pr
    WHERE pr.client_id = policies.client_id
      AND LOWER(TRIM(pr.name)) = LOWER(TRIM(policies.project_name))
)
WHERE project_name IS NOT NULL AND TRIM(project_name) != '';
