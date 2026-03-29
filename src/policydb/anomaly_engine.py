"""Anomaly detection engine — scans the book of business for workflow problems.

Called on server startup via scan_anomalies(conn). Each rule returns a list of
findings; reconcile() inserts new ones, refreshes active ones, and auto-resolves
stale ones no longer seen in the current scan.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import policydb.config as cfg

logger = logging.getLogger("policydb.anomaly_engine")


# ── Main scan entry point ────────────────────────────────────────────────────


def scan_anomalies(conn) -> int:
    """Run all anomaly rules and reconcile findings.

    Called on every server startup. Returns count of active findings.
    """
    thresholds: dict[str, Any] = cfg.get("anomaly_thresholds", {})
    scan_id = datetime.utcnow().isoformat()

    existing = _load_existing(conn)

    rules = [
        _rule_renewal_not_started,
        _rule_stale_followup_backlog,
        _rule_milestone_drift,
        _rule_overdue_review,
        _rule_no_activity,
        _rule_no_followup_scheduled,
        _rule_heavy_week,
        _rule_light_week,
        _rule_status_contradiction,
        _rule_expired_no_renewal,
    ]

    new_findings: list[tuple] = []
    for rule_fn in rules:
        try:
            findings = rule_fn(conn, thresholds)
            new_findings.extend(findings)
        except Exception:
            logger.exception("Anomaly rule %s failed", rule_fn.__name__)

    count = _reconcile(conn, scan_id, existing, new_findings)
    logger.info("Anomaly scan complete: %d active findings", count)
    return count


# ── Load / reconcile helpers ─────────────────────────────────────────────────


def _load_existing(conn) -> dict:
    """Load current new/acknowledged findings keyed by (rule_key, client_id, policy_id)."""
    rows = conn.execute(
        "SELECT * FROM anomalies WHERE status IN ('new', 'acknowledged')"
    ).fetchall()
    return {
        (r["rule_key"], r["client_id"] or 0, r["policy_id"] or 0): dict(r)
        for r in rows
    }


def _reconcile(conn, scan_id: str, existing: dict, new_findings: list[tuple]) -> int:
    """Insert new findings, refresh active ones, auto-resolve stale.

    Each finding tuple: (rule_key, category, severity, client_id, policy_id, title, details)
    """
    seen_keys: set[tuple] = set()

    for rule_key, category, severity, client_id, policy_id, title, details in new_findings:
        key = (rule_key, client_id or 0, policy_id or 0)
        seen_keys.add(key)

        if key in existing:
            # Refresh existing finding
            conn.execute(
                """UPDATE anomalies
                   SET scan_id = ?, title = ?, details = ?, severity = ?
                   WHERE id = ?""",
                (scan_id, title, details, severity, existing[key]["id"]),
            )
        else:
            # Insert new finding
            conn.execute(
                """INSERT INTO anomalies
                   (rule_key, category, severity, client_id, policy_id, title, details, status, scan_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
                (rule_key, category, severity, client_id, policy_id, title, details, scan_id),
            )

    # Auto-resolve stale findings (those NOT seen in this scan)
    now_iso = datetime.utcnow().isoformat()
    for key, row in existing.items():
        if key not in seen_keys:
            conn.execute(
                "UPDATE anomalies SET status = 'resolved', resolved_at = ? WHERE id = ?",
                (now_iso, row["id"]),
            )

    conn.commit()

    count_row = conn.execute(
        "SELECT COUNT(*) FROM anomalies WHERE status IN ('new', 'acknowledged')"
    ).fetchone()
    return count_row[0] if count_row else 0


# ── Rule functions ────────────────────────────────────────────────────────────
# Each returns list of tuples:
#   (rule_key, category, severity, client_id, policy_id, title, details)


def _rule_renewal_not_started(conn, thresholds: dict) -> list[tuple]:
    """Policies expiring soon with no activity logged against them."""
    days = thresholds.get("renewal_not_started_days", 60)
    excluded: list[str] = cfg.get("renewal_statuses_excluded", [])
    today = date.today()
    window_end = today + timedelta(days=days)

    query = """
        SELECT p.id, p.policy_uid, p.policy_type, p.expiration_date,
               p.renewal_status, p.client_id,
               c.name AS client_name
        FROM policies p
        JOIN clients c ON c.id = p.client_id
        WHERE p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND p.expiration_date IS NOT NULL
          AND DATE(p.expiration_date) BETWEEN DATE(?) AND DATE(?)
          AND NOT EXISTS (
              SELECT 1 FROM activity_log al
              WHERE al.policy_id = p.id
                AND DATE(al.activity_date) >= DATE(?)
          )
    """
    rows = conn.execute(query, (today.isoformat(), window_end.isoformat(), today.isoformat())).fetchall()

    findings = []
    for r in rows:
        if r["renewal_status"] and r["renewal_status"] in excluded:
            continue
        try:
            exp = datetime.strptime(r["expiration_date"][:10], "%Y-%m-%d").date()
            days_out = (exp - today).days
        except (ValueError, TypeError):
            days_out = 0

        findings.append((
            "renewal_not_started",
            "falling_behind",
            "alert",
            r["client_id"],
            r["id"],
            f"{r['policy_type'] or 'Policy'} renewal {days_out}d out — no activity",
            f"{r['client_name']} · {r['policy_uid']} · expires {r['expiration_date'][:10] if r['expiration_date'] else '?'}",
        ))
    return findings


def _rule_stale_followup_backlog(conn, thresholds: dict) -> list[tuple]:
    """Total open follow-up backlog exceeds threshold."""
    threshold = thresholds.get("stale_followup_count", 10)
    today = date.today().isoformat()

    row = conn.execute(
        """SELECT COUNT(*) AS cnt FROM activity_log
           WHERE follow_up_done = 0
             AND follow_up_date IS NOT NULL
             AND DATE(follow_up_date) <= DATE(?)""",
        (today,),
    ).fetchone()
    count = row["cnt"] if row else 0

    if count > threshold:
        return [(
            "stale_followup_backlog",
            "falling_behind",
            "alert",
            None,
            None,
            f"{count} open follow-ups across the book (threshold: {threshold})",
            None,
        )]
    return []


def _rule_milestone_drift(conn, thresholds: dict) -> list[tuple]:
    """Policies with at_risk/critical unacknowledged timeline milestones."""
    query = """
        SELECT DISTINCT p.id, p.policy_uid, p.policy_type, p.client_id,
               c.name AS client_name
        FROM policy_timeline pt
        JOIN policies p ON p.policy_uid = pt.policy_uid
        JOIN clients c ON c.id = p.client_id
        WHERE pt.health IN ('at_risk', 'critical')
          AND (pt.acknowledged = 0 OR pt.acknowledged IS NULL)
          AND pt.completed_date IS NULL
          AND p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
    """
    try:
        rows = conn.execute(query).fetchall()
    except Exception:
        return []

    findings = []
    for r in rows:
        findings.append((
            "milestone_drift",
            "falling_behind",
            "alert",
            r["client_id"],
            r["id"],
            f"{r['policy_type'] or 'Policy'} timeline at risk — milestone slipping",
            f"{r['client_name']} · {r['policy_uid']}",
        ))
    return findings


def _rule_overdue_review(conn, thresholds: dict) -> list[tuple]:
    """Policies not reviewed within the configured window."""
    days = thresholds.get("overdue_review_days", 90)
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    query = """
        SELECT p.id, p.policy_uid, p.policy_type, p.last_reviewed_at, p.client_id,
               c.name AS client_name
        FROM policies p
        JOIN clients c ON c.id = p.client_id
        WHERE p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND (
              p.last_reviewed_at IS NULL
              OR DATE(p.last_reviewed_at) <= DATE(?)
          )
    """
    rows = conn.execute(query, (cutoff,)).fetchall()

    findings = []
    for r in rows:
        if r["last_reviewed_at"]:
            try:
                last = datetime.strptime(r["last_reviewed_at"][:10], "%Y-%m-%d").date()
                days_ago = (date.today() - last).days
                title = f"Overdue for review — {days_ago}d since last review"
            except (ValueError, TypeError):
                title = "Overdue for review"
        else:
            title = "Never reviewed"

        findings.append((
            "overdue_review",
            "falling_behind",
            "warning",
            r["client_id"],
            r["id"],
            title,
            f"{r['client_name']} · {r['policy_uid']} · {r['policy_type'] or 'Policy'}",
        ))
    return findings


def _rule_no_activity(conn, thresholds: dict) -> list[tuple]:
    """Clients with no activity or review touches in the configured window."""
    days = thresholds.get("no_activity_days", 90)
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    # Get all active clients with at least one non-opportunity policy
    clients = conn.execute(
        """SELECT c.id, c.name,
                  COUNT(p.id) AS policy_count
           FROM clients c
           JOIN policies p ON p.client_id = c.id
           WHERE c.archived = 0
             AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           GROUP BY c.id, c.name"""
    ).fetchall()

    findings = []
    for c in clients:
        # Any recent activity?
        act_row = conn.execute(
            """SELECT MAX(DATE(activity_date)) AS last_act
               FROM activity_log
               WHERE client_id = ? AND DATE(activity_date) >= DATE(?)""",
            (c["id"], cutoff),
        ).fetchone()
        if act_row and act_row["last_act"]:
            continue

        # Any recent review on any policy?
        rev_row = conn.execute(
            """SELECT MAX(DATE(last_reviewed_at)) AS last_rev
               FROM policies
               WHERE client_id = ? AND last_reviewed_at IS NOT NULL
                 AND DATE(last_reviewed_at) >= DATE(?)""",
            (c["id"], cutoff),
        ).fetchone()
        if rev_row and rev_row["last_rev"]:
            continue

        # How long since last activity at all?
        last_any = conn.execute(
            """SELECT MAX(DATE(activity_date)) AS last_act
               FROM activity_log WHERE client_id = ?""",
            (c["id"],),
        ).fetchone()
        if last_any and last_any["last_act"]:
            try:
                last_date = datetime.strptime(last_any["last_act"], "%Y-%m-%d").date()
                days_since = (date.today() - last_date).days
            except (ValueError, TypeError):
                days_since = days
        else:
            days_since = days

        severity = "alert" if days_since > 2 * days else "warning"

        findings.append((
            "no_activity",
            "neglected",
            severity,
            c["id"],
            None,
            f"No activity in {days_since}d",
            f"{c['name']} · {c['policy_count']} active {'policy' if c['policy_count'] == 1 else 'policies'}",
        ))
    return findings


def _rule_no_followup_scheduled(conn, thresholds: dict) -> list[tuple]:
    """Clients with active policies but no pending follow-ups."""
    if not thresholds.get("no_followup_scheduled", True):
        return []

    today = date.today().isoformat()

    clients = conn.execute(
        """SELECT c.id, c.name,
                  COUNT(p.id) AS policy_count
           FROM clients c
           JOIN policies p ON p.client_id = c.id
           WHERE c.archived = 0
             AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           GROUP BY c.id, c.name"""
    ).fetchall()

    findings = []
    for c in clients:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM activity_log
               WHERE client_id = ?
                 AND follow_up_done = 0
                 AND follow_up_date IS NOT NULL
                 AND DATE(follow_up_date) >= DATE(?)""",
            (c["id"], today),
        ).fetchone()
        if row and row["cnt"] > 0:
            continue

        findings.append((
            "no_followup_scheduled",
            "neglected",
            "warning",
            c["id"],
            None,
            "No follow-ups scheduled",
            f"{c['name']} · {c['policy_count']} active {'policy' if c['policy_count'] == 1 else 'policies'}",
        ))
    return findings


def _rule_heavy_week(conn, thresholds: dict) -> list[tuple]:
    """Weeks within the forecast window that have more expirations than the threshold."""
    window_days = thresholds.get("forecast_window_days", 30)
    heavy_threshold = thresholds.get("heavy_week_threshold", 5)
    excluded: list[str] = cfg.get("renewal_statuses_excluded", [])

    today = date.today()
    window_end = today + timedelta(days=window_days)

    rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.expiration_date,
                  p.renewal_status, p.carrier,
                  c.id AS client_id, c.name AS client_name
           FROM policies p
           JOIN clients c ON c.id = p.client_id
           WHERE p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
             AND p.expiration_date IS NOT NULL
             AND DATE(p.expiration_date) BETWEEN DATE(?) AND DATE(?)""",
        (today.isoformat(), window_end.isoformat()),
    ).fetchall()

    # Group by ISO week (Monday-based)
    week_map: dict[date, list[dict]] = {}
    for r in rows:
        if r["renewal_status"] and r["renewal_status"] in excluded:
            continue
        try:
            exp = datetime.strptime(r["expiration_date"][:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        monday = exp - timedelta(days=exp.weekday())
        week_map.setdefault(monday, []).append({
            "policy_uid": r["policy_uid"],
            "policy_type": r["policy_type"] or "Policy",
            "carrier": r["carrier"] or "",
            "expiration_date": r["expiration_date"],
            "client_name": r["client_name"],
            "client_id": r["client_id"],
        })

    import json
    findings = []
    for monday, policies in week_map.items():
        if len(policies) > heavy_threshold:
            friday = monday + timedelta(days=4)
            details_json = json.dumps(policies)
            findings.append((
                f"heavy_week_{monday.isoformat()}",
                "workload",
                "warning",
                None,
                None,
                f"Heavy week: {len(policies)} expirations {monday.strftime('%b %d')}–{friday.strftime('%b %d')}",
                details_json,
            ))
    return findings


def _rule_light_week(conn, thresholds: dict) -> list[tuple]:
    """No expirations at all in the next N days — good catch-up window."""
    window_days = thresholds.get("light_week_window_days", 14)
    today = date.today()
    window_end = today + timedelta(days=window_days)

    row = conn.execute(
        """SELECT COUNT(*) AS cnt FROM policies
           WHERE archived = 0
             AND (is_opportunity = 0 OR is_opportunity IS NULL)
             AND expiration_date IS NOT NULL
             AND DATE(expiration_date) BETWEEN DATE(?) AND DATE(?)""",
        (today.isoformat(), window_end.isoformat()),
    ).fetchone()

    if row and row["cnt"] == 0:
        return [(
            "light_week",
            "workload",
            "warning",
            None,
            None,
            f"No expirations in next {window_days}d — good time to catch up",
            None,
        )]
    return []


def _rule_status_contradiction(conn, thresholds: dict) -> list[tuple]:
    """Policies with contradictory status vs data state."""
    status_no_activity_days = thresholds.get("status_no_activity_days", 30)
    cutoff = (date.today() - timedelta(days=status_no_activity_days)).isoformat()
    excluded: list[str] = cfg.get("renewal_statuses_excluded", [])

    findings = []

    # (a) Bound/Issued but no effective date
    bound_rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.renewal_status, p.client_id,
                  c.name AS client_name
           FROM policies p
           JOIN clients c ON c.id = p.client_id
           WHERE p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
             AND p.renewal_status IN ('Bound', 'Issued')
             AND (p.effective_date IS NULL OR p.effective_date = '')""",
    ).fetchall()

    for r in bound_rows:
        if r["renewal_status"] in excluded:
            continue
        findings.append((
            "status_contradiction_bound_no_eff",
            "mismatch",
            "alert",
            r["client_id"],
            r["id"],
            "Bound/Issued but no effective date",
            f"{r['client_name']} · {r['policy_uid']} · status: {r['renewal_status']}",
        ))

    # (b) In-progress status but no activity recently
    in_progress_statuses = [
        s for s in cfg.get("renewal_statuses", [])
        if s.lower() not in ("bound", "issued", "not started")
        and s not in excluded
    ]
    if in_progress_statuses:
        placeholders = ",".join("?" * len(in_progress_statuses))
        ip_rows = conn.execute(
            f"""SELECT p.id, p.policy_uid, p.policy_type, p.renewal_status, p.client_id,
                       c.name AS client_name
                FROM policies p
                JOIN clients c ON c.id = p.client_id
                WHERE p.archived = 0
                  AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
                  AND p.renewal_status IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM activity_log al
                      WHERE al.policy_id = p.id
                        AND DATE(al.activity_date) >= DATE(?)
                  )""",
            (*in_progress_statuses, cutoff),
        ).fetchall()

        for r in ip_rows:
            findings.append((
                "status_contradiction_no_activity",
                "mismatch",
                "alert",
                r["client_id"],
                r["id"],
                f"In Progress but no activity in {status_no_activity_days}d",
                f"{r['client_name']} · {r['policy_uid']} · status: {r['renewal_status']}",
            ))

    return findings


def _rule_expired_no_renewal(conn, thresholds: dict) -> list[tuple]:
    """Policies that have expired with no renewal found."""
    if not thresholds.get("expired_no_renewal", True):
        return []

    excluded: list[str] = cfg.get("renewal_statuses_excluded", [])
    today = date.today()
    today_iso = today.isoformat()

    rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.expiration_date,
                  p.renewal_status, p.client_id,
                  c.name AS client_name
           FROM policies p
           JOIN clients c ON c.id = p.client_id
           WHERE p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
             AND p.expiration_date IS NOT NULL
             AND DATE(p.expiration_date) < DATE(?)""",
        (today_iso,),
    ).fetchall()

    findings = []
    for r in rows:
        # Skip if status is terminal / excluded
        if r["renewal_status"] and r["renewal_status"] in excluded:
            continue

        # Check if a newer policy of the same type exists for this client
        newer = conn.execute(
            """SELECT COUNT(*) AS cnt FROM policies
               WHERE client_id = ?
                 AND policy_type = ?
                 AND id != ?
                 AND effective_date IS NOT NULL
                 AND DATE(effective_date) > DATE(?)""",
            (r["client_id"], r["policy_type"], r["id"], r["expiration_date"]),
        ).fetchone()
        if newer and newer["cnt"] > 0:
            continue

        try:
            exp = datetime.strptime(r["expiration_date"][:10], "%Y-%m-%d").date()
            days_ago = (today - exp).days
        except (ValueError, TypeError):
            days_ago = 0

        findings.append((
            "expired_no_renewal",
            "mismatch",
            "alert",
            r["client_id"],
            r["id"],
            f"Expired {days_ago}d ago — no renewal found",
            f"{r['client_name']} · {r['policy_uid']} · {r['policy_type'] or 'Policy'} · expired {r['expiration_date'][:10] if r['expiration_date'] else '?'}",
        ))
    return findings


# ── Query helpers ─────────────────────────────────────────────────────────────


def get_anomaly_counts(conn) -> dict:
    """Return dict of active anomaly counts by category."""
    rows = conn.execute(
        """SELECT category, COUNT(*) AS cnt FROM anomalies
           WHERE status IN ('new', 'acknowledged')
           GROUP BY category"""
    ).fetchall()
    return {r["category"]: r["cnt"] for r in rows}


def get_all_active_anomalies(conn) -> list[dict]:
    """Return all non-resolved findings ordered by severity then detected_at."""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT a.*, c.name AS client_name, p.policy_uid, p.policy_type
               FROM anomalies a
               LEFT JOIN clients c ON c.id = a.client_id
               LEFT JOIN policies p ON p.id = a.policy_id
               WHERE a.status IN ('new', 'acknowledged')
               ORDER BY
                   CASE a.severity WHEN 'alert' THEN 0 ELSE 1 END,
                   a.detected_at DESC"""
        ).fetchall()
    ]


def get_anomalies_for_client(conn, client_id: int) -> list[dict]:
    """Return active findings for a specific client."""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM anomalies
               WHERE client_id = ? AND status IN ('new', 'acknowledged')
               ORDER BY CASE severity WHEN 'alert' THEN 0 ELSE 1 END, detected_at DESC""",
            (client_id,),
        ).fetchall()
    ]


def get_anomalies_for_policy(conn, policy_id: int) -> list[dict]:
    """Return active findings for a specific policy."""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM anomalies
               WHERE policy_id = ? AND status IN ('new', 'acknowledged')
               ORDER BY CASE severity WHEN 'alert' THEN 0 ELSE 1 END, detected_at DESC""",
            (policy_id,),
        ).fetchall()
    ]


def acknowledge_anomaly(conn, anomaly_id: int) -> None:
    """Mark an anomaly as acknowledged."""
    conn.execute(
        "UPDATE anomalies SET status = 'acknowledged', acknowledged_at = DATETIME('now') WHERE id = ?",
        (anomaly_id,),
    )
    conn.commit()


# ── Review gate ───────────────────────────────────────────────────────────────


def get_review_gate_status(conn, record_type: str, record_id: int) -> dict:
    """Evaluate 4 review gate conditions.

    Returns {"all_pass": bool, "conditions": [{"name": str, "passed": bool, "detail": str}]}
    """
    thresholds: dict[str, Any] = cfg.get("anomaly_thresholds", {})
    min_health = thresholds.get("review_min_health_score", 70)
    activity_window = thresholds.get("review_activity_window_days", 30)
    activity_cutoff = (date.today() - timedelta(days=activity_window)).isoformat()

    conditions: list[dict] = []

    # 1. Data health score
    try:
        from policydb.data_health import score_client, score_policies, detect_stage

        if record_type == "client":
            client_row = conn.execute(
                "SELECT * FROM clients WHERE id = ?", (record_id,)
            ).fetchone()
            if client_row:
                client_dict = dict(client_row)
                score_client(conn, client_dict)
                score = client_dict.get("health_score", 0)
            else:
                score = 0
        else:
            policy_row = conn.execute(
                "SELECT * FROM policies WHERE id = ?", (record_id,)
            ).fetchone()
            if policy_row:
                p = dict(policy_row)
                score_policies(conn, [p])
                score = p.get("health_score", 0)
            else:
                score = 0

        passed = score >= min_health
        conditions.append({
            "name": "Data Health",
            "passed": passed,
            "detail": f"Score {score}/100 (min {min_health})",
        })
    except Exception as e:
        conditions.append({
            "name": "Data Health",
            "passed": False,
            "detail": f"Could not compute: {e}",
        })

    # 2. Recent activity
    if record_type == "client":
        act_row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM activity_log
               WHERE client_id = ? AND DATE(activity_date) >= DATE(?)""",
            (record_id, activity_cutoff),
        ).fetchone()
    else:
        act_row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM activity_log
               WHERE policy_id = ? AND DATE(activity_date) >= DATE(?)""",
            (record_id, activity_cutoff),
        ).fetchone()

    act_count = act_row["cnt"] if act_row else 0
    conditions.append({
        "name": "Recent Activity",
        "passed": act_count > 0,
        "detail": f"{act_count} activities in last {activity_window}d",
    })

    # 3. No open anomalies
    if record_type == "client":
        anoms = get_anomalies_for_client(conn, record_id)
        policy_rows = conn.execute(
            "SELECT id FROM policies WHERE client_id = ? AND archived = 0",
            (record_id,),
        ).fetchall()
        for pr in policy_rows:
            anoms.extend(get_anomalies_for_policy(conn, pr["id"]))
    else:
        anoms = get_anomalies_for_policy(conn, record_id)

    anom_count = len(anoms)
    conditions.append({
        "name": "No Open Anomalies",
        "passed": anom_count == 0,
        "detail": f"{anom_count} open finding{'s' if anom_count != 1 else ''}",
    })

    # 4. No overdue follow-ups
    today_iso = date.today().isoformat()
    if record_type == "client":
        fu_row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM activity_log
               WHERE client_id = ?
                 AND follow_up_done = 0
                 AND follow_up_date IS NOT NULL
                 AND DATE(follow_up_date) < DATE(?)""",
            (record_id, today_iso),
        ).fetchone()
    else:
        fu_row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM activity_log
               WHERE policy_id = ?
                 AND follow_up_done = 0
                 AND follow_up_date IS NOT NULL
                 AND DATE(follow_up_date) < DATE(?)""",
            (record_id, today_iso),
        ).fetchone()

    overdue_count = fu_row["cnt"] if fu_row else 0
    conditions.append({
        "name": "No Overdue Follow-ups",
        "passed": overdue_count == 0,
        "detail": f"{overdue_count} overdue follow-up{'s' if overdue_count != 1 else ''}",
    })

    return {
        "all_pass": all(c["passed"] for c in conditions),
        "conditions": conditions,
    }
