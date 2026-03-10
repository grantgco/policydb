-- Migration 003: add cn_number to clients
-- Internal client number (manually populated, e.g. from internal CRM)
ALTER TABLE clients ADD COLUMN cn_number TEXT;
