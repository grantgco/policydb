-- Rename issue_status 'Investigating' to 'In Hand' for all issue headers
UPDATE activity_log SET issue_status = 'In Hand' WHERE issue_status = 'Investigating';
