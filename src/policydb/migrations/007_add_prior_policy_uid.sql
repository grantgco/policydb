-- Migration 007: Add prior_policy_uid to link renewed policy terms
ALTER TABLE policies ADD COLUMN prior_policy_uid TEXT REFERENCES policies(policy_uid);
