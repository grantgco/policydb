ALTER TABLE inbox ADD COLUMN contact_id INTEGER REFERENCES contacts(id);
