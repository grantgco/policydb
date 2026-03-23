-- Consolidate email template contexts from 7 to 2 (policy + client)
UPDATE email_templates SET context = 'policy' WHERE context IN ('location', 'followup', 'timeline');
UPDATE email_templates SET context = 'client' WHERE context IN ('general', 'meeting');
