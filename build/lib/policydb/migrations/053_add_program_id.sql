ALTER TABLE policies ADD COLUMN program_id INTEGER REFERENCES policies(id);
CREATE INDEX IF NOT EXISTS idx_policies_program_id ON policies(program_id);
