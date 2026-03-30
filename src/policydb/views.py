"""SQL view definitions."""

V_POLICY_STATUS = """
CREATE VIEW v_policy_status AS
SELECT
    p.id,
    p.policy_uid,
    p.client_id,
    c.name AS client_name,
    c.cn_number,
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
    p.needs_investigation,
    p.is_opportunity,
    p.opportunity_status,
    p.target_effective_date,
    p.first_named_insured,
    COALESCE(
        (SELECT GROUP_CONCAT(co.name, ', ') FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND cpa.is_placement_colleague = 1),
        p.placement_colleague
    ) AS placement_colleague,
    COALESCE(
        (SELECT co.email FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND cpa.is_placement_colleague = 1 AND co.email IS NOT NULL
         LIMIT 1),
        p.placement_colleague_email
    ) AS placement_colleague_email,
    COALESCE(
        (SELECT GROUP_CONCAT(co.name, ', ') FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND cpa.role = 'Underwriter'),
        p.underwriter_name
    ) AS underwriter_name,
    COALESCE(
        (SELECT co.email FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND cpa.role = 'Underwriter' AND co.email IS NOT NULL
         LIMIT 1),
        p.underwriter_contact
    ) AS underwriter_contact,
    p.renewal_status,
    p.commission_rate,
    p.prior_premium,
    p.account_exec,
    p.notes,
    p.follow_up_date,
    CASE WHEN p.follow_up_date IS NOT NULL AND p.follow_up_date < date('now') THEN 1 ELSE 0 END AS followup_overdue,
    p.attachment_point,
    p.participation_of,
    p.access_point,
    p.project_name,
    p.project_id,
    p.exposure_basis,
    p.exposure_amount,
    p.exposure_unit,
    p.exposure_address,
    p.exposure_city,
    p.exposure_state,
    p.exposure_zip,
    -- Opportunities have no expiration date; NULL days/urgency keeps them out of renewal logic
    CASE WHEN p.is_opportunity = 1 THEN NULL
         ELSE CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER)
    END AS days_to_renewal,
    CASE WHEN p.is_opportunity = 1 THEN 'OPPORTUNITY'
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
    END AS rate_change,
    p.last_reviewed_at,
    p.review_cycle,
    p.program_id,
    pg.program_uid,
    pg.name AS program_name
FROM policies p
JOIN clients c ON p.client_id = c.id
LEFT JOIN programs pg ON pg.id = p.program_id
WHERE p.archived = 0
"""

V_CLIENT_SUMMARY = """
CREATE VIEW v_client_summary AS
SELECT
    c.id,
    c.name,
    c.cn_number,
    c.is_prospect,
    c.industry_segment,
    c.account_exec,
    COALESCE(
        (SELECT co.name FROM contact_client_assignments cca
         JOIN contacts co ON cca.contact_id = co.id
         WHERE cca.client_id = c.id AND cca.is_primary = 1 AND cca.contact_type = 'client'
         LIMIT 1),
        c.primary_contact
    ) AS primary_contact,
    c.date_onboarded,
    c.notes,
    c.broker_fee,
    -- Exclude opportunities from bound-policy counts and premium totals
    COUNT(CASE WHEN p.is_opportunity = 0 OR p.is_opportunity IS NULL THEN p.id END) AS total_policies,
    COALESCE(SUM(CASE WHEN p.is_opportunity = 0 OR p.is_opportunity IS NULL THEN p.premium ELSE 0 END), 0) AS total_premium,
    SUM(CASE WHEN (p.is_opportunity = 0 OR p.is_opportunity IS NULL) AND p.commission_rate > 0
        THEN ROUND(p.premium * p.commission_rate, 2) ELSE 0 END) AS total_commission,
    COALESCE(c.broker_fee, 0) AS total_fees,
    SUM(CASE WHEN (p.is_opportunity = 0 OR p.is_opportunity IS NULL) AND p.commission_rate > 0
        THEN ROUND(p.premium * p.commission_rate, 2) ELSE 0 END)
        + COALESCE(c.broker_fee, 0) AS total_revenue,
    SUM(CASE WHEN (p.is_opportunity = 0 OR p.is_opportunity IS NULL) AND p.is_standalone = 1 THEN 1 ELSE 0 END) AS standalone_count,
    COUNT(DISTINCT CASE WHEN p.is_opportunity = 0 OR p.is_opportunity IS NULL THEN p.policy_type END) AS coverage_lines,
    COUNT(DISTINCT CASE WHEN p.is_opportunity = 0 OR p.is_opportunity IS NULL THEN p.carrier END) AS carrier_count,
    MIN(CASE
        WHEN (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND julianday(p.expiration_date) - julianday('now') > 0
        THEN CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER)
    END) AS next_renewal_days,
    SUM(CASE
        WHEN (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND julianday(p.expiration_date) - julianday('now') BETWEEN 0 AND 180
        THEN p.premium ELSE 0
    END) AS premium_at_risk,
    COUNT(CASE WHEN p.is_opportunity = 1 THEN 1 END) AS opportunity_count,
    COALESCE(SUM(CASE WHEN p.is_opportunity = 1 THEN p.premium ELSE 0 END), 0) AS opportunity_premium,
    COALESCE(SUM(CASE WHEN p.is_opportunity = 1 AND p.commission_rate > 0
        THEN ROUND(p.premium * p.commission_rate, 2) ELSE 0 END), 0) AS opportunity_revenue,
    (SELECT COUNT(*) FROM programs pg2 WHERE pg2.client_id = c.id AND pg2.archived = 0) AS program_count,
    (SELECT COUNT(*) FROM activity_log a
     WHERE a.client_id = c.id
       AND a.activity_date >= date('now', '-90 days')) AS activity_last_90d,
    (SELECT MAX(a.activity_date) FROM activity_log a WHERE a.client_id = c.id) AS last_activity_date
FROM clients c
LEFT JOIN policies p ON p.client_id = c.id AND p.archived = 0
WHERE c.archived = 0
GROUP BY c.id
"""

V_SCHEDULE = """
CREATE VIEW v_schedule AS
SELECT
    c.name AS client_name,
    COALESCE(p.first_named_insured, c.name) AS "First Named Insured",
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
    COALESCE(ce.exposure_type || ' /' || ce.denominator, p.exposure_basis) AS "Exposure Basis",
    COALESCE(ce.amount, p.exposure_amount) AS "Exposure Amount",
    COALESCE('per ' || ce.denominator, p.exposure_unit) AS "Exposure Unit",
    pel.rate AS "Rate",
    p.description AS "Comments"
FROM policies p
JOIN clients c ON p.client_id = c.id
LEFT JOIN policy_exposure_links pel ON pel.policy_uid = p.policy_uid AND pel.is_primary = 1
LEFT JOIN client_exposures ce ON ce.id = pel.exposure_id
WHERE p.archived = 0
  AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
ORDER BY c.name, p.policy_type, p.layer_position
"""

V_TOWER = """
CREATE VIEW v_tower AS
SELECT
    c.name AS client_name,
    p.tower_group,
    p.program_id,
    pg.name AS program_name,
    p.layer_position,
    p.policy_type,
    p.carrier,
    p.policy_number,
    p.limit_amount,
    p.premium,
    p.expiration_date,
    COALESCE(
        (SELECT GROUP_CONCAT(co.name, ', ') FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND cpa.is_placement_colleague = 1),
        p.placement_colleague
    ) AS placement_colleague,
    p.renewal_status,
    p.attachment_point,
    p.participation_of,
    p.deductible,
    p.coverage_form,
    p.schematic_column,
    p.description,
    p.notes,
    p.layer_notation
FROM policies p
JOIN clients c ON p.client_id = c.id
LEFT JOIN programs pg ON pg.id = p.program_id
WHERE p.archived = 0
  AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
ORDER BY c.name, pg.name, COALESCE(p.attachment_point, 0) ASC
"""

V_RENEWAL_PIPELINE = """
CREATE VIEW v_renewal_pipeline AS
SELECT
    c.name AS client_name,
    c.cn_number,
    p.id,
    p.client_id,
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
    COALESCE(
        (SELECT GROUP_CONCAT(co.name, ', ') FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id),
        p.placement_colleague
    ) AS placement_colleague,
    p.follow_up_date,
    CASE WHEN p.follow_up_date IS NOT NULL AND p.follow_up_date < date('now') THEN 1 ELSE 0 END AS followup_overdue,
    p.project_name,
    p.project_id,
    p.access_point,
    p.is_standalone,
    p.needs_investigation,
    p.description,
    COALESCE(
        (SELECT co.email FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND co.email IS NOT NULL
         LIMIT 1),
        p.placement_colleague_email
    ) AS placement_colleague_email,
    COALESCE(
        (SELECT GROUP_CONCAT(co.name, ', ') FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND cpa.role = 'Underwriter'),
        p.underwriter_name
    ) AS underwriter_name,
    COALESCE(
        (SELECT co.email FROM contact_policy_assignments cpa
         JOIN contacts co ON cpa.contact_id = co.id
         WHERE cpa.policy_id = p.id AND cpa.role = 'Underwriter' AND co.email IS NOT NULL
         LIMIT 1),
        p.underwriter_contact
    ) AS underwriter_contact,
    p.last_reviewed_at,
    p.review_cycle,
    COALESCE(th.timeline_health, '') AS timeline_health
FROM policies p
JOIN clients c ON p.client_id = c.id
LEFT JOIN (
    SELECT policy_uid,
        CASE MIN(CASE health
            WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
            WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4
            ELSE 5 END)
            WHEN 1 THEN 'critical' WHEN 2 THEN 'at_risk'
            WHEN 3 THEN 'compressed' WHEN 4 THEN 'drifting'
            ELSE 'on_track' END as timeline_health
    FROM policy_timeline
    WHERE completed_date IS NULL
    GROUP BY policy_uid
) th ON th.policy_uid = p.policy_uid
WHERE p.archived = 0
  AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
  AND p.program_id IS NULL
  AND julianday(p.expiration_date) - julianday('now') <= 180
ORDER BY julianday(p.expiration_date) ASC
"""

V_OVERDUE_FOLLOWUPS = """
CREATE VIEW v_overdue_followups AS
SELECT
    a.id,
    a.activity_date,
    c.id   AS client_id,
    c.name AS client_name,
    c.cn_number,
    p.policy_uid,
    a.activity_type,
    a.subject,
    a.follow_up_date,
    CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
    COALESCE(th.timeline_health, '') AS timeline_health
FROM activity_log a
JOIN clients c ON a.client_id = c.id
LEFT JOIN policies p ON a.policy_id = p.id
LEFT JOIN (
    SELECT policy_uid,
        CASE MIN(CASE health
            WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
            WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4
            ELSE 5 END)
            WHEN 1 THEN 'critical' WHEN 2 THEN 'at_risk'
            WHEN 3 THEN 'compressed' WHEN 4 THEN 'drifting'
            ELSE 'on_track' END as timeline_health
    FROM policy_timeline
    WHERE completed_date IS NULL
    GROUP BY policy_uid
) th ON th.policy_uid = p.policy_uid
WHERE a.follow_up_date < date('now')
  AND a.follow_up_done = 0
ORDER BY a.follow_up_date ASC
"""

def _cycle_case(col: str) -> str:
    return f"""
         CASE COALESCE({col}, '1w')
              WHEN '1w' THEN 7
              WHEN '2w' THEN 14
              WHEN '1m' THEN 30
              WHEN '1q' THEN 90
              WHEN '6m' THEN 180
              WHEN '1y' THEN 365
              ELSE 7
         END"""


V_REVIEW_QUEUE = f"""
CREATE VIEW v_review_queue AS
SELECT
    p.id,
    p.policy_uid,
    p.client_id,
    c.name AS client_name,
    c.cn_number,
    p.policy_type,
    p.carrier,
    p.effective_date,
    p.expiration_date,
    p.premium,
    p.renewal_status,
    p.opportunity_status,
    p.target_effective_date,
    p.is_opportunity,
    p.is_standalone,
    p.needs_investigation,
    p.follow_up_date,
    CASE WHEN p.follow_up_date IS NOT NULL AND p.follow_up_date < date('now') THEN 1 ELSE 0 END AS followup_overdue,
    p.project_name,
    p.project_id,
    p.description,
    p.notes,
    p.account_exec,
    p.last_reviewed_at,
    p.review_cycle,
    CASE WHEN p.is_opportunity = 1 THEN NULL
         ELSE CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER)
    END AS days_to_renewal,
    CASE WHEN p.is_opportunity = 1 THEN 'OPPORTUNITY'
         WHEN julianday(p.expiration_date) - julianday('now') <= 0 THEN 'EXPIRED'
         WHEN julianday(p.expiration_date) - julianday('now') <= 90 THEN 'URGENT'
         WHEN julianday(p.expiration_date) - julianday('now') <= 120 THEN 'WARNING'
         WHEN julianday(p.expiration_date) - julianday('now') <= 180 THEN 'UPCOMING'
         ELSE 'OK'
    END AS urgency,
    CASE WHEN p.last_reviewed_at IS NULL THEN 9999
         ELSE CAST(julianday('now') - julianday(p.last_reviewed_at) AS INTEGER)
    END AS days_since_review,
    {_cycle_case('p.review_cycle')} AS review_cycle_days
FROM policies p
JOIN clients c ON c.id = p.client_id
WHERE p.archived = 0
  AND (p.program_id IS NULL)
  AND (
    p.last_reviewed_at IS NULL
    OR CAST(julianday('now') - julianday(p.last_reviewed_at) AS INTEGER) >= {_cycle_case('p.review_cycle')}
  )
ORDER BY
    days_to_renewal ASC NULLS LAST,
    p.last_reviewed_at ASC NULLS FIRST
"""

V_REVIEW_CLIENTS = f"""
CREATE VIEW v_review_clients AS
SELECT
    c.id,
    c.name,
    c.industry_segment,
    c.account_exec,
    c.last_reviewed_at,
    c.review_cycle,
    CASE WHEN c.last_reviewed_at IS NULL THEN 9999
         ELSE CAST(julianday('now') - julianday(c.last_reviewed_at) AS INTEGER)
    END AS days_since_review,
    {_cycle_case('c.review_cycle')} AS review_cycle_days,
    cs.total_policies,
    cs.total_premium,
    cs.next_renewal_days,
    cs.opportunity_count
FROM clients c
LEFT JOIN v_client_summary cs ON cs.id = c.id
WHERE c.archived = 0
  AND (
    c.last_reviewed_at IS NULL
    OR CAST(julianday('now') - julianday(c.last_reviewed_at) AS INTEGER) >= {_cycle_case('c.review_cycle')}
  )
ORDER BY c.last_reviewed_at ASC NULLS FIRST, c.name
"""

ALL_VIEWS = {
    "v_policy_status": V_POLICY_STATUS,
    "v_client_summary": V_CLIENT_SUMMARY,
    "v_schedule": V_SCHEDULE,
    "v_tower": V_TOWER,
    "v_renewal_pipeline": V_RENEWAL_PIPELINE,
    "v_overdue_followups": V_OVERDUE_FOLLOWUPS,
    "v_review_queue": V_REVIEW_QUEUE,
    "v_review_clients": V_REVIEW_CLIENTS,
}
