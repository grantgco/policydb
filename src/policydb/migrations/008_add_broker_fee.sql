-- Migration 008: Client-level fee + business description
-- broker_fee: flat annual fee charged at the account level (in lieu of or in addition to commission)
-- business_description: narrative about the client's business model and operations for LLM/submission use

ALTER TABLE clients ADD COLUMN broker_fee REAL;
ALTER TABLE clients ADD COLUMN business_description TEXT;
