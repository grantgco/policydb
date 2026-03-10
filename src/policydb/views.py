"""SQL view definitions."""

V_POLICY_STATUS = """
CREATE VIEW v_policy_status AS
SELECT
    p.id,
    p.policy_uid,
    p.client_id,
    c.name AS client_name,
    c.industry_segment,
    p.policy_type,
    p.carrier,
    p.policy_number,
    p.effective_date,
    p.expiration_date,
    p.premium,
    p.limit_amount,
    p.deductible,
    p.description,
    p.coverage_form,
    p.layer_position,
    p.tower_group,
    p.is_standalone,
    p.placement_colleague,
    p.underwriter_name,
    p.underwriter_contact,
    p.renewal_status,
    p.commission_rate,
    p.prior_premium,
    p.account_exec,
    p.notes,
    p.project_name,
    CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
    CASE
        WHEN julianday(p.expiration_date) - julianday('now') <= 0 THEN 'EXPIRED'
        WHEN julianday(p.expiration_date) - julianday('now') <= 90 THEN 'URGENT'
        WHEN julianday(p.expiration_date) - julianday('now') <= 120 THEN 'WARNING'
        WHEN julianday(p.expiration_date) - julianday('now') <= 180 THEN 'UPCOMING'
        ELSE 'OK'
    END AS urgency,
    CASE WHEN p.commission_rate > 0
        THEN ROUND(p.premium * p.commission_rate, 2)
        ELSE NULL
    END AS commission_amount,
    CASE WHEN p.prior_premium > 0
        THEN ROUND((p.premium - p.prior_premium) * 1.0 / p.prior_premium, 4)
        ELSE NULL
    END AS rate_change
FROM policies p
JOIN clients c ON p.client_id = c.id
WHERE p.archived = 0
"""

V_CLIENT_SUMMARY = """
CREATE VIEW v_client_summary AS
SELECT
    c.id,
    c.name,
    c.industry_segment,
    c.account_exec,
    c.primary_contact,
    c.date_onboarded,
    c.notes,
    COUNT(p.id) AS total_policies,
    COALESCE(SUM(p.premium), 0) AS total_premium,
    SUM(CASE WHEN p.commission_rate > 0
        THEN ROUND(p.premium * p.commission_rate, 2) ELSE 0 END) AS total_commission,
    SUM(CASE WHEN p.is_standalone = 1 THEN 1 ELSE 0 END) AS standalone_count,
    COUNT(DISTINCT p.policy_type) AS coverage_lines,
    COUNT(DISTINCT p.carrier) AS carrier_count,
    MIN(CASE
        WHEN julianday(p.expiration_date) - julianday('now') > 0
        THEN CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER)
    END) AS next_renewal_days,
    SUM(CASE
        WHEN julianday(p.expiration_date) - julianday('now') BETWEEN 0 AND 180
        THEN p.premium ELSE 0
    END) AS premium_at_risk,
    (SELECT COUNT(*) FROM activity_log a
     WHERE a.client_id = c.id
       AND a.activity_date >= date('now', '-90 days')) AS activity_last_90d
FROM clients c
LEFT JOIN policies p ON p.client_id = c.id AND p.archived = 0
WHERE c.archived = 0
GROUP BY c.id
"""

V_SCHEDULE = """
CREATE VIEW v_schedule AS
SELECT
    c.name AS client_name,
    p.policy_type AS "Line of Business",
    p.carrier AS "Carrier",
    p.policy_number AS "Policy Number",
    p.effective_date AS "Effective",
    p.expiration_date AS "Expiration",
    p.premium AS "Premium",
    p.limit_amount AS "Limit",
    p.deductible AS "Deductible",
    p.coverage_form AS "Form",
    p.layer_position AS "Layer",
    p.project_name AS "Project",
    p.description AS "Comments"
FROM policies p
JOIN clients c ON p.client_id = c.id
WHERE p.archived = 0
ORDER BY c.name, p.policy_type, p.layer_position
"""

V_TOWER = """
CREATE VIEW v_tower AS
SELECT
    c.name AS client_name,
    p.tower_group,
    p.layer_position,
    p.policy_type,
    p.carrier,
    p.policy_number,
    p.limit_amount,
    p.premium,
    p.expiration_date,
    p.placement_colleague,
    p.renewal_status,
    p.description,
    p.notes
FROM policies p
JOIN clients c ON p.client_id = c.id
WHERE p.archived = 0
ORDER BY c.name, p.tower_group, p.layer_position
"""

V_RENEWAL_PIPELINE = """
CREATE VIEW v_renewal_pipeline AS
SELECT
    c.name AS client_name,
    p.policy_uid,
    p.policy_type,
    p.carrier,
    p.expiration_date,
    CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
    CASE
        WHEN julianday(p.expiration_date) - julianday('now') <= 0 THEN 'EXPIRED'
        WHEN julianday(p.expiration_date) - julianday('now') <= 90 THEN 'URGENT'
        WHEN julianday(p.expiration_date) - julianday('now') <= 120 THEN 'WARNING'
        WHEN julianday(p.expiration_date) - julianday('now') <= 180 THEN 'UPCOMING'
        ELSE 'OK'
    END AS urgency,
    p.premium,
    p.renewal_status,
    p.placement_colleague,
    p.description
FROM policies p
JOIN clients c ON p.client_id = c.id
WHERE p.archived = 0
  AND julianday(p.expiration_date) - julianday('now') <= 180
ORDER BY julianday(p.expiration_date) ASC
"""

V_OVERDUE_FOLLOWUPS = """
CREATE VIEW v_overdue_followups AS
SELECT
    a.id,
    a.activity_date,
    c.name AS client_name,
    p.policy_uid,
    a.activity_type,
    a.subject,
    a.follow_up_date,
    CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue
FROM activity_log a
JOIN clients c ON a.client_id = c.id
LEFT JOIN policies p ON a.policy_id = p.id
WHERE a.follow_up_date < date('now')
  AND a.follow_up_done = 0
ORDER BY a.follow_up_date ASC
"""

ALL_VIEWS = {
    "v_policy_status": V_POLICY_STATUS,
    "v_client_summary": V_CLIENT_SUMMARY,
    "v_schedule": V_SCHEDULE,
    "v_tower": V_TOWER,
    "v_renewal_pipeline": V_RENEWAL_PIPELINE,
    "v_overdue_followups": V_OVERDUE_FOLLOWUPS,
}
