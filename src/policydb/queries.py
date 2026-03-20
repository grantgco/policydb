"""Named query functions over the database."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional

from rapidfuzz import process, fuzz


# ─── CLIENT QUERIES ──────────────────────────────────────────────────────────

def get_all_clients(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM v_client_summary ORDER BY name"
    ).fetchall()


def get_client_by_id(conn: sqlite3.Connection, client_id: int, include_archived: bool = False) -> Optional[sqlite3.Row]:
    if include_archived:
        return conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return conn.execute(
        "SELECT * FROM clients WHERE id = ? AND archived = 0", (client_id,)
    ).fetchone()


def get_client_by_name(conn: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
    """Exact match first, then fuzzy fallback."""
    row = conn.execute(
        "SELECT * FROM clients WHERE LOWER(name) = LOWER(?) AND archived = 0", (name,)
    ).fetchone()
    if row:
        return row
    all_clients = conn.execute(
        "SELECT * FROM clients WHERE archived = 0"
    ).fetchall()
    if not all_clients:
        return None
    names = [r["name"] for r in all_clients]
    result = process.extractOne(name, names, scorer=fuzz.WRatio, score_cutoff=70)
    if result:
        matched_name = result[0]
        return conn.execute(
            "SELECT * FROM clients WHERE name = ? AND archived = 0", (matched_name,)
        ).fetchone()
    return None


def get_client_summary(conn: sqlite3.Connection, client_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM v_client_summary WHERE id = ?", (client_id,)
    ).fetchone()


def fuzzy_find_client(conn: sqlite3.Connection, query: str) -> list[tuple[str, float]]:
    """Return list of (client_name, score) for fuzzy matching."""
    all_clients = conn.execute(
        "SELECT name FROM clients WHERE archived = 0"
    ).fetchall()
    names = [r["name"] for r in all_clients]
    if not names:
        return []
    results = process.extract(query, names, scorer=fuzz.WRatio, limit=5, score_cutoff=50)
    return [(r[0], r[1]) for r in results]


# ─── POLICY QUERIES ───────────────────────────────────────────────────────────

def get_policies_for_client(
    conn: sqlite3.Connection,
    client_id: int,
    include_archived: bool = False,
) -> list[sqlite3.Row]:
    if include_archived:
        return conn.execute(
            "SELECT * FROM v_policy_status WHERE client_id = ? ORDER BY policy_type, layer_position",
            (client_id,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM v_policy_status WHERE client_id = ? ORDER BY policy_type, layer_position",
        (client_id,),
    ).fetchall()


def get_policy_by_uid(conn: sqlite3.Connection, uid: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM v_policy_status WHERE policy_uid = ?", (uid,)
    ).fetchone()


def get_policy_by_id(conn: sqlite3.Connection, policy_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM policies WHERE id = ?", (policy_id,)
    ).fetchone()


def get_all_policies(
    conn: sqlite3.Connection,
    client_id: Optional[int] = None,
    urgency: Optional[str] = None,
    policy_type: Optional[str] = None,
    standalone_only: bool = False,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM v_policy_status WHERE 1=1"
    params: list = []
    if client_id is not None:
        sql += " AND client_id = ?"
        params.append(client_id)
    if urgency:
        sql += " AND urgency = ?"
        params.append(urgency.upper())
    if policy_type:
        sql += " AND policy_type LIKE ?"
        params.append(f"%{policy_type}%")
    if standalone_only:
        sql += " AND is_standalone = 1"
    sql += " ORDER BY client_name, policy_type, layer_position"
    return conn.execute(sql, params).fetchall()


def get_tower_for_client(conn: sqlite3.Connection, client_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM v_tower WHERE client_name = (SELECT name FROM clients WHERE id = ?)"
        " ORDER BY tower_group, layer_position",
        (client_id,),
    ).fetchall()


# ─── RENEWAL QUERIES ──────────────────────────────────────────────────────────

def get_renewal_pipeline(
    conn: sqlite3.Connection,
    window_days: int = 180,
    urgency: Optional[str] = None,
    renewal_status: Optional[str] = None,
    excluded_statuses: Optional[list] = None,
    client_ids: list[int] | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM v_renewal_pipeline WHERE days_to_renewal <= ?"
    params: list = [window_days]
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        sql += f" AND client_id IN ({ph})"
        params.extend(client_ids)
    if urgency:
        sql += " AND urgency = ?"
        params.append(urgency.upper())
    if renewal_status:
        sql += " AND renewal_status = ?"
        params.append(renewal_status)
    if excluded_statuses:
        placeholders = ",".join("?" * len(excluded_statuses))
        sql += f" AND (renewal_status NOT IN ({placeholders}) OR renewal_status IS NULL)"
        params.extend(excluded_statuses)
    sql += " ORDER BY expiration_date ASC"
    return conn.execute(sql, params).fetchall()


def get_stale_renewals(
    conn: sqlite3.Connection,
    window_days: int = 180,
    stale_days: int = 14,
    excluded_statuses: Optional[list] = None,
    client_ids: list[int] | None = None,
) -> list[sqlite3.Row]:
    sql = """SELECT v.*, p.created_at AS policy_created
           FROM v_renewal_pipeline v
           JOIN policies p ON p.policy_uid = v.policy_uid
           WHERE v.days_to_renewal <= ?
             AND v.renewal_status = 'Not Started'
             AND julianday('now') - julianday(p.created_at) > ?"""
    params: list = [window_days, stale_days]
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        sql += f" AND v.client_id IN ({ph})"
        params.extend(client_ids)
    if excluded_statuses:
        placeholders = ",".join("?" * len(excluded_statuses))
        sql += f" AND (v.renewal_status NOT IN ({placeholders}) OR v.renewal_status IS NULL)"
        params.extend(excluded_statuses)
    sql += " ORDER BY v.expiration_date ASC"
    return conn.execute(sql, params).fetchall()


def get_escalation_alerts(
    conn: sqlite3.Connection,
    excluded_statuses: Optional[list] = None,
    client_ids: list[int] | None = None,
) -> list[dict]:
    """Return renewal alerts with escalation tiers: CRITICAL, WARNING, NUDGE."""
    from policydb import config as cfg
    esc = cfg.get("escalation_thresholds", {})
    critical_days = esc.get("critical_days", 60)
    critical_stale = esc.get("critical_stale_days", 14)
    warning_days = esc.get("warning_days", 90)
    nudge_days = esc.get("nudge_days", 120)
    nudge_stale = esc.get("nudge_stale_days", 30)
    inner = f"""
        SELECT v.*, p.created_at AS policy_created,
               (SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id) AS last_activity_date,
               CASE
                   WHEN v.days_to_renewal <= {critical_days}
                        AND v.renewal_status = 'Not Started'
                        AND ((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id) IS NULL
                             OR julianday('now') - julianday((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id)) > {critical_stale})
                   THEN 'CRITICAL'
                   WHEN v.days_to_renewal <= {warning_days} AND v.renewal_status = 'Not Started'
                   THEN 'WARNING'
                   WHEN v.days_to_renewal <= {nudge_days} AND v.follow_up_date IS NULL
                        AND ((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id) IS NULL
                             OR julianday('now') - julianday((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id)) > {nudge_stale})
                   THEN 'NUDGE'
               END AS escalation_tier
        FROM v_renewal_pipeline v
        JOIN policies p ON p.policy_uid = v.policy_uid
        WHERE 1=1"""
    params: list = []
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        inner += f" AND p.client_id IN ({ph})"
        params.extend(client_ids)
    if excluded_statuses:
        placeholders = ",".join("?" * len(excluded_statuses))
        inner += f" AND (v.renewal_status NOT IN ({placeholders}) OR v.renewal_status IS NULL)"
        params.extend(excluded_statuses)
    sql = f"""
        SELECT * FROM ({inner})
        WHERE escalation_tier IS NOT NULL
        ORDER BY
            CASE escalation_tier WHEN 'CRITICAL' THEN 1 WHEN 'WARNING' THEN 2 WHEN 'NUDGE' THEN 3 END,
            expiration_date ASC"""
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_renewal_metrics(conn: sqlite3.Connection, client_ids: list[int] | None = None) -> dict:
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        rows = conn.execute(f"""
            SELECT urgency, COUNT(*) AS policy_count, COALESCE(SUM(premium), 0) AS total_premium
            FROM v_policy_status WHERE client_id IN ({ph}) GROUP BY urgency
        """, client_ids).fetchall()
    else:
        rows = conn.execute("""
            SELECT urgency, COUNT(*) AS policy_count, COALESCE(SUM(premium), 0) AS total_premium
            FROM v_policy_status GROUP BY urgency
        """).fetchall()
    metrics = {r["urgency"]: {"count": r["policy_count"], "premium": r["total_premium"]} for r in rows}
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        book = conn.execute(f"""
            SELECT COUNT(*) AS total_clients, COALESCE(SUM(total_policies), 0) AS total_policies,
                   COALESCE(SUM(total_premium), 0) AS total_premium,
                   COALESCE(SUM(total_commission), 0) AS total_commission,
                   COALESCE(SUM(total_fees), 0) AS total_fees,
                   COALESCE(SUM(total_revenue), 0) AS total_revenue
            FROM v_client_summary WHERE id IN ({ph})
        """, client_ids).fetchone()
    else:
        book = conn.execute("""
            SELECT COUNT(*) AS total_clients, COALESCE(SUM(total_policies), 0) AS total_policies,
                   COALESCE(SUM(total_premium), 0) AS total_premium,
                   COALESCE(SUM(total_commission), 0) AS total_commission,
                   COALESCE(SUM(total_fees), 0) AS total_fees,
                   COALESCE(SUM(total_revenue), 0) AS total_revenue
            FROM v_client_summary
        """).fetchone()
    metrics["book"] = dict(book) if book else {}
    return metrics


def get_renewal_calendar(
    conn: sqlite3.Connection, months: int = 6
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT
            strftime('%Y-%m', expiration_date) AS month,
            COUNT(*) AS policy_count,
            SUM(premium) AS total_premium,
            GROUP_CONCAT(client_name || ' (' || policy_type || ')', ', ') AS policies
           FROM v_policy_status
           WHERE expiration_date BETWEEN date('now') AND date('now', ? || ' months')
             AND urgency != 'EXPIRED'
           GROUP BY month
           ORDER BY month""",
        (f"+{months}",),
    ).fetchall()


# ─── RENEWAL TERM CREATION ────────────────────────────────────────────────────

def renew_policy(conn: sqlite3.Connection, uid: str) -> str:
    """Create a new annual term from an existing policy.

    Copies all fields from the prior term, advances dates by one year,
    archives the old record, snapshots premium to premium_history, and
    returns the new policy_uid.
    """
    from policydb.db import next_policy_uid

    old = conn.execute("SELECT * FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    if old is None:
        raise ValueError(f"Policy {uid} not found")

    # Advance dates by one year with Feb-29 fallback
    exp = date.fromisoformat(old["expiration_date"])
    new_eff = exp
    try:
        new_exp = exp.replace(year=exp.year + 1)
    except ValueError:
        # Feb 29 → Mar 1 in non-leap year
        new_exp = exp.replace(year=exp.year + 1, day=exp.day - 1)

    new_uid = next_policy_uid(conn)

    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, prior_premium,
            limit_amount, deductible, description, coverage_form,
            layer_position, tower_group, is_standalone,
            renewal_status, commission_rate, account_exec, notes,
            project_name, project_id, exposure_basis, exposure_amount, exposure_unit,
            exposure_address, exposure_city, exposure_state, exposure_zip,
            prior_policy_uid)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            new_uid, old["client_id"], old["policy_type"], old["carrier"], None,
            new_eff.isoformat(), new_exp.isoformat(),
            old["premium"], old["premium"],  # premium carries over; prior_premium = old premium
            old["limit_amount"], old["deductible"], old["description"], old["coverage_form"],
            old["layer_position"] or "Primary", old["tower_group"], old["is_standalone"],
            "Not Started", old["commission_rate"], old["account_exec"], None,
            old["project_name"], old["project_id"],
            old["exposure_basis"], old["exposure_amount"], old["exposure_unit"],
            old["exposure_address"], old["exposure_city"], old["exposure_state"], old["exposure_zip"],
            uid,
        ),
    )

    # Copy contact_policy_assignments from the expiring term to the new term
    new_policy_id = conn.execute(
        "SELECT id FROM policies WHERE policy_uid=?", (new_uid,)
    ).fetchone()["id"]
    old_assignments = conn.execute(
        "SELECT contact_id, role, title, notes, is_placement_colleague FROM contact_policy_assignments WHERE policy_id=?",
        (old["id"],),
    ).fetchall()
    for c in old_assignments:
        try:
            conn.execute(
                "INSERT INTO contact_policy_assignments (contact_id, policy_id, role, title, notes, is_placement_colleague) VALUES (?,?,?,?,?,?)",
                (c["contact_id"], new_policy_id, c["role"], c["title"], c["notes"], c["is_placement_colleague"]),
            )
        except Exception:
            pass  # UNIQUE constraint

    # Snapshot the expiring term to premium_history (ignore if already recorded)
    conn.execute(
        """INSERT OR IGNORE INTO premium_history
           (client_id, policy_type, term_effective, term_expiration, premium, carrier, limit_amount, deductible)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            old["client_id"], old["policy_type"],
            old["effective_date"], old["expiration_date"],
            old["premium"], old["carrier"], old["limit_amount"], old["deductible"],
        ),
    )

    # Auto-schedule follow-up for the new term
    from policydb import config as _cfg
    auto_days = _cfg.get("auto_followup_days_before_expiry", 120)
    from datetime import timedelta as _td
    auto_fu_date = (new_exp - _td(days=auto_days)).isoformat()
    conn.execute(
        "UPDATE policies SET follow_up_date=? WHERE policy_uid=?",
        (auto_fu_date, new_uid),
    )

    # Archive the prior term
    conn.execute("UPDATE policies SET archived=1 WHERE policy_uid=?", (uid,))
    conn.commit()

    return new_uid


# ─── ACTIVITY QUERIES ─────────────────────────────────────────────────────────

def get_activities(
    conn: sqlite3.Connection,
    client_id: Optional[int] = None,
    days: Optional[int] = None,
    activity_type: Optional[str] = None,
    client_ids: list[int] | None = None,
) -> list[sqlite3.Row]:
    sql = """SELECT a.*, c.name AS client_name, c.cn_number, p.policy_uid, p.project_id
             FROM activity_log a
             JOIN clients c ON a.client_id = c.id
             LEFT JOIN policies p ON a.policy_id = p.id
             WHERE 1=1"""
    params: list = []
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        sql += f" AND a.client_id IN ({ph})"
        params.extend(client_ids)
    elif client_id is not None:
        sql += " AND a.client_id = ?"
        params.append(client_id)
    if days is not None:
        sql += " AND a.activity_date >= date('now', ?)"
        params.append(f"-{days - 1} days")
    if activity_type:
        sql += " AND a.activity_type = ?"
        params.append(activity_type)
    sql += """ ORDER BY
        CASE WHEN a.follow_up_date IS NOT NULL AND (a.follow_up_done IS NULL OR a.follow_up_done = 0) THEN 0 ELSE 1 END,
        CASE WHEN a.follow_up_date IS NOT NULL AND (a.follow_up_done IS NULL OR a.follow_up_done = 0) THEN a.follow_up_date END ASC,
        a.activity_date DESC, a.id DESC"""
    return conn.execute(sql, params).fetchall()


def get_time_summary(
    conn: sqlite3.Connection,
    client_id: Optional[int] = None,
    days: Optional[int] = None,
    activity_type: Optional[str] = None,
    client_ids: list[int] | None = None,
) -> dict:
    """Aggregated time stats matching the given activity filters.

    Returns:
        total_hours: float
        by_type: [{"activity_type", "hours", "count"}, ...] DESC by hours
        by_client: [{"client_name", "client_id", "hours"}, ...] top 15 DESC
    """
    where = "WHERE a.duration_hours IS NOT NULL AND a.duration_hours > 0"
    params: list = []
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        where += f" AND a.client_id IN ({ph})"
        params.extend(client_ids)
    elif client_id is not None:
        where += " AND a.client_id = ?"
        params.append(client_id)
    if days is not None:
        where += " AND a.activity_date >= date('now', ?)"
        params.append(f"-{days - 1} days")
    if activity_type:
        where += " AND a.activity_type = ?"
        params.append(activity_type)

    total = conn.execute(
        f"SELECT COALESCE(SUM(a.duration_hours), 0) AS t FROM activity_log a {where}",
        params,
    ).fetchone()["t"]

    by_type = conn.execute(
        f"""SELECT a.activity_type,
                   COALESCE(SUM(a.duration_hours), 0) AS hours,
                   COUNT(*) AS count
            FROM activity_log a {where}
            GROUP BY a.activity_type ORDER BY hours DESC""",
        params,
    ).fetchall()

    by_client = conn.execute(
        f"""SELECT c.name AS client_name, c.id AS client_id,
                   COALESCE(SUM(a.duration_hours), 0) AS hours
            FROM activity_log a
            JOIN clients c ON a.client_id = c.id
            {where}
            GROUP BY a.client_id ORDER BY hours DESC LIMIT 15""",
        params,
    ).fetchall()

    return {
        "total_hours": float(total),
        "by_type": [dict(r) for r in by_type],
        "by_client": [dict(r) for r in by_client],
    }


def get_dashboard_hours_this_month(conn: sqlite3.Connection) -> float:
    """Total hours logged in the current calendar month."""
    row = conn.execute(
        """SELECT COALESCE(SUM(duration_hours), 0) AS t FROM activity_log
           WHERE duration_hours IS NOT NULL
             AND activity_date >= date('now', 'start of month')"""
    ).fetchone()
    return float(row["t"])


def get_client_total_hours(conn: sqlite3.Connection, client_id: int) -> float:
    """Total hours logged for a client (all time)."""
    row = conn.execute(
        """SELECT COALESCE(SUM(duration_hours), 0) AS t FROM activity_log
           WHERE client_id = ? AND duration_hours IS NOT NULL""",
        (client_id,),
    ).fetchone()
    return float(row["t"])


def get_policy_total_hours(conn: sqlite3.Connection, policy_id: int) -> float:
    """Total hours logged for a specific policy."""
    row = conn.execute(
        """SELECT COALESCE(SUM(duration_hours), 0) AS t FROM activity_log
           WHERE policy_id = ? AND duration_hours IS NOT NULL""",
        (policy_id,),
    ).fetchone()
    return float(row["t"])


def supersede_followups(conn, policy_id: int, new_date: str) -> None:
    """When logging a new activity with a follow-up, supersede all older follow-ups.

    1. Mark all pending activity follow-ups for this policy as done.
    2. Sync the policy's own follow_up_date to the new date.
    """
    conn.execute(
        """UPDATE activity_log SET follow_up_done = 1
           WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL""",
        (policy_id,),
    )
    conn.execute(
        "UPDATE policies SET follow_up_date = ? WHERE id = ?",
        (new_date, policy_id),
    )


def get_all_followups(
    conn: sqlite3.Connection, window: int = 30, client_ids: list[int] | None = None
) -> tuple[list[dict], list[dict]]:
    """Return (overdue, upcoming) follow-ups from both activity_log and policy records."""
    sql = """
    SELECT 'activity' AS source,
           a.id, a.subject, a.follow_up_date, a.activity_type,
           a.contact_person, a.disposition, a.thread_id,
           c.name AS client_name, c.id AS client_id, c.cn_number,
           p.policy_uid, p.policy_type, p.carrier, p.project_name, p.project_id,
           0 AS is_opportunity,
           CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
           co_a.email AS contact_email,
           (SELECT GROUP_CONCAT(co_i.email, ',')
            FROM contact_client_assignments cca_i
            JOIN contacts co_i ON cca_i.contact_id = co_i.id
            WHERE cca_i.client_id = c.id AND cca_i.contact_type = 'internal' AND co_i.email IS NOT NULL
           ) AS internal_cc,
           a.details AS note_details,
           NULL AS note_subject,
           a.activity_date AS note_date
    FROM activity_log a
    JOIN clients c ON a.client_id = c.id
    LEFT JOIN policies p ON a.policy_id = p.id
    LEFT JOIN contacts co_a ON a.contact_id = co_a.id
    WHERE a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL

    UNION ALL

    SELECT 'policy' AS source,
           p.id,
           COALESCE(p.carrier, p.policy_type) AS subject,
           p.follow_up_date,
           CASE WHEN p.is_opportunity = 1 THEN 'Opportunity' ELSE 'Policy Reminder' END AS activity_type,
           (SELECT co_pc.name FROM contact_policy_assignments cpa_pc
            JOIN contacts co_pc ON cpa_pc.contact_id = co_pc.id
            WHERE cpa_pc.policy_id = p.id ORDER BY cpa_pc.id LIMIT 1) AS contact_person,
           NULL AS disposition, NULL AS thread_id,
           c.name AS client_name, c.id AS client_id, c.cn_number,
           p.policy_uid, p.policy_type, p.carrier, p.project_name, p.project_id,
           p.is_opportunity,
           CAST(julianday('now') - julianday(p.follow_up_date) AS INTEGER) AS days_overdue,
           (SELECT co_pe.email FROM contact_policy_assignments cpa_pe
            JOIN contacts co_pe ON cpa_pe.contact_id = co_pe.id
            WHERE cpa_pe.policy_id = p.id AND co_pe.email IS NOT NULL ORDER BY cpa_pe.id LIMIT 1) AS contact_email,
           (SELECT GROUP_CONCAT(co_i2.email, ',')
            FROM contact_client_assignments cca_i2
            JOIN contacts co_i2 ON cca_i2.contact_id = co_i2.id
            WHERE cca_i2.client_id = c.id AND cca_i2.contact_type = 'internal' AND co_i2.email IS NOT NULL
           ) AS internal_cc,
           (SELECT a2.details FROM activity_log a2
            WHERE a2.policy_id = p.id ORDER BY a2.activity_date DESC, a2.id DESC LIMIT 1) AS note_details,
           (SELECT a2.subject FROM activity_log a2
            WHERE a2.policy_id = p.id ORDER BY a2.activity_date DESC, a2.id DESC LIMIT 1) AS note_subject,
           (SELECT a2.activity_date FROM activity_log a2
            WHERE a2.policy_id = p.id ORDER BY a2.activity_date DESC, a2.id DESC LIMIT 1) AS note_date
    FROM policies p
    JOIN clients c ON p.client_id = c.id
    WHERE p.follow_up_date IS NOT NULL AND p.archived = 0
      AND NOT EXISTS (
          SELECT 1 FROM activity_log a
          WHERE a.policy_id = p.id
            AND a.follow_up_done = 0
            AND a.follow_up_date IS NOT NULL
      )

    UNION ALL

    SELECT 'client' AS source,
           c.id,
           'Client Follow-Up: ' || c.name AS subject,
           c.follow_up_date,
           'Client Reminder' AS activity_type,
           NULL AS contact_person,
           NULL AS disposition, NULL AS thread_id,
           c.name AS client_name, c.id AS client_id, c.cn_number,
           NULL AS policy_uid, NULL AS policy_type, NULL AS carrier,
           NULL AS project_name, NULL AS project_id,
           0 AS is_opportunity,
           CAST(julianday('now') - julianday(c.follow_up_date) AS INTEGER) AS days_overdue,
           NULL AS contact_email,
           (SELECT GROUP_CONCAT(co_i3.email, ',')
            FROM contact_client_assignments cca_i3
            JOIN contacts co_i3 ON cca_i3.contact_id = co_i3.id
            WHERE cca_i3.client_id = c.id AND cca_i3.contact_type = 'internal' AND co_i3.email IS NOT NULL
           ) AS internal_cc,
           c.notes AS note_details,
           NULL AS note_subject,
           NULL AS note_date
    FROM clients c
    WHERE c.follow_up_date IS NOT NULL AND c.archived = 0

    ORDER BY follow_up_date ASC
    """
    params: list = []
    if client_ids:
        placeholders = ",".join("?" * len(client_ids))
        sql = f"SELECT * FROM ({sql}) _fu WHERE _fu.client_id IN ({placeholders})"
        params = list(client_ids)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=window)).isoformat()
    overdue = [r for r in rows if r["follow_up_date"] < today]
    upcoming = [r for r in rows if today <= r["follow_up_date"] <= cutoff]

    # Compute thread stats for rows with thread_id
    all_rows = overdue + upcoming
    thread_ids = {r["thread_id"] for r in all_rows if r.get("thread_id")}
    if thread_ids:
        placeholders = ",".join("?" * len(thread_ids))
        stats = conn.execute(f"""
            SELECT thread_id, COUNT(*) AS thread_total,
                   MAX(activity_date) AS latest_date
            FROM activity_log WHERE thread_id IN ({placeholders})
            GROUP BY thread_id
        """, list(thread_ids)).fetchall()
        stats_map = {s["thread_id"]: dict(s) for s in stats}

        # Get previous disposition per thread (the second-to-last activity)
        prev_map = {}
        for tid in thread_ids:
            prev = conn.execute("""
                SELECT disposition, activity_date FROM activity_log
                WHERE thread_id = ? ORDER BY activity_date DESC, id DESC LIMIT 1 OFFSET 1
            """, (tid,)).fetchone()
            if prev:
                prev_map[tid] = dict(prev)

        for r in all_rows:
            tid = r.get("thread_id")
            if tid and tid in stats_map:
                r["thread_total"] = stats_map[tid]["thread_total"]
                r["thread_attempt_num"] = conn.execute(
                    "SELECT COUNT(*) FROM activity_log WHERE thread_id = ? AND id <= ?",
                    (tid, r["id"]),
                ).fetchone()[0]
                if tid in prev_map:
                    r["prev_disposition"] = prev_map[tid].get("disposition")
                    prev_date = prev_map[tid].get("activity_date")
                    if prev_date:
                        try:
                            r["prev_days_ago"] = (date.today() - date.fromisoformat(prev_date)).days
                        except (ValueError, TypeError):
                            r["prev_days_ago"] = None

    # Attach placement colleague email for "Forward to Colleague" button
    policy_uids = {r.get("policy_uid") for r in all_rows if r.get("policy_uid") and r.get("source") == "activity"}
    if policy_uids:
        _pc_rows = conn.execute(f"""
            SELECT p.policy_uid, co.name AS pc_name, co.email AS pc_email
            FROM contact_policy_assignments cpa
            JOIN contacts co ON cpa.contact_id = co.id
            JOIN policies p ON cpa.policy_id = p.id
            WHERE p.policy_uid IN ({','.join('?' * len(policy_uids))})
              AND cpa.is_placement_colleague = 1
              AND co.email IS NOT NULL AND TRIM(co.email) != ''
        """, list(policy_uids)).fetchall()
        _pc_map = {r["policy_uid"]: {"pc_name": r["pc_name"], "pc_email": r["pc_email"]} for r in _pc_rows}
        for r in all_rows:
            pc = _pc_map.get(r.get("policy_uid"))
            if pc:
                r["pc_name"] = pc["pc_name"]
                r["pc_email"] = pc["pc_email"]

    return overdue, upcoming


def get_contacts_for_client(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Return deduplicated contacts for a client (from unified contacts + assignments) for autocomplete."""
    rows = conn.execute("""
        SELECT DISTINCT co.name, COALESCE(cca.role, cca.title, '') AS detail, cca.contact_type AS source
        FROM contacts co
        JOIN contact_client_assignments cca ON co.id = cca.contact_id
        WHERE cca.client_id = ?

        UNION

        SELECT DISTINCT co.name, COALESCE(cpa.role, cpa.title, '') AS detail, 'placement' AS source
        FROM contacts co
        JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
        JOIN policies p ON cpa.policy_id = p.id
        WHERE p.client_id = ? AND p.archived = 0

        ORDER BY name
    """, (client_id, client_id)).fetchall()
    return [dict(r) for r in rows]


# ─── UNIFIED CONTACT QUERIES ────────────────────────────────────────────────


def get_or_create_contact(conn: sqlite3.Connection, name: str, **fields) -> int:
    """Find contact by LOWER(TRIM(name)) or create; update shared fields if provided. Returns contact id."""
    name = name.strip()
    if not name:
        raise ValueError("Contact name cannot be empty")
    row = conn.execute(
        "SELECT id FROM contacts WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))", (name,)
    ).fetchone()
    if row:
        contact_id = row["id"]
        # Update non-null fields
        updates = []
        params = []
        for field in ("email", "phone", "mobile", "organization"):
            val = fields.get(field)
            if val:
                updates.append(f"{field}=?")
                params.append(val)
        if updates:
            updates.append("updated_at=CURRENT_TIMESTAMP")
            params.append(contact_id)
            conn.execute(f"UPDATE contacts SET {', '.join(updates)} WHERE id=?", params)
        return contact_id
    else:
        cur = conn.execute(
            "INSERT INTO contacts (name, email, phone, mobile, organization) VALUES (?,?,?,?,?)",
            (name, fields.get("email"), fields.get("phone"),
             fields.get("mobile"), fields.get("organization")),
        )
        return cur.lastrowid


def get_contact_by_id(conn: sqlite3.Connection, contact_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()


def search_contacts(conn: sqlite3.Connection, q: str, limit: int = 20) -> list[dict]:
    """Search contacts by name, email, or organization (LIKE match)."""
    pattern = f"%{q.strip()}%"
    rows = conn.execute(
        """SELECT id, name, email, phone, mobile, organization
           FROM contacts
           WHERE name LIKE ? OR email LIKE ? OR organization LIKE ?
           ORDER BY name
           LIMIT ?""",
        (pattern, pattern, pattern, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_client_contacts(conn: sqlite3.Connection, client_id: int, contact_type: str = "client") -> list[dict]:
    """Return contacts assigned to a client via contact_client_assignments, joined with contacts table."""
    rows = conn.execute(
        """SELECT cca.id AS assignment_id, co.id AS contact_id,
                  co.name, co.email, co.phone, co.mobile, co.organization,
                  cca.contact_type, cca.role, cca.title, cca.assignment, cca.notes, cca.is_primary
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id = ? AND cca.contact_type = ?
           ORDER BY cca.is_primary DESC, co.name""",
        (client_id, contact_type),
    ).fetchall()
    # Return with id = assignment_id for template backward compat
    result = []
    for r in rows:
        d = dict(r)
        d["id"] = d["assignment_id"]
        result.append(d)
    return result


def get_policy_contacts(conn: sqlite3.Connection, policy_id: int) -> list[dict]:
    """Return contacts assigned to a policy via contact_policy_assignments, joined with contacts table."""
    rows = conn.execute(
        """SELECT cpa.id AS assignment_id, co.id AS contact_id,
                  co.name, co.email, co.phone, co.mobile, co.organization,
                  cpa.role, cpa.title, cpa.notes, cpa.is_placement_colleague
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.policy_id = ?
           ORDER BY cpa.role, co.name""",
        (policy_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["id"] = d["assignment_id"]
        result.append(d)
    return result


def assign_contact_to_client(conn: sqlite3.Connection, contact_id: int, client_id: int,
                             contact_type: str = "client", **fields) -> int:
    """Create or update a contact-client assignment. Returns assignment id."""
    existing = conn.execute(
        "SELECT id FROM contact_client_assignments WHERE contact_id=? AND client_id=? AND contact_type=?",
        (contact_id, client_id, contact_type),
    ).fetchone()
    if existing:
        updates = []
        params = []
        for field in ("role", "title", "assignment", "notes", "is_primary"):
            if field in fields:
                updates.append(f"{field}=?")
                params.append(fields[field])
        if updates:
            params.append(existing["id"])
            conn.execute(f"UPDATE contact_client_assignments SET {', '.join(updates)} WHERE id=?", params)
        return existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO contact_client_assignments
               (contact_id, client_id, contact_type, role, title, assignment, notes, is_primary)
               VALUES (?,?,?,?,?,?,?,?)""",
            (contact_id, client_id, contact_type,
             fields.get("role"), fields.get("title"), fields.get("assignment"),
             fields.get("notes"), fields.get("is_primary", 0)),
        )
        return cur.lastrowid


def assign_contact_to_policy(conn: sqlite3.Connection, contact_id: int, policy_id: int, **fields) -> int:
    """Create or update a contact-policy assignment. Returns assignment id."""
    existing = conn.execute(
        "SELECT id FROM contact_policy_assignments WHERE contact_id=? AND policy_id=?",
        (contact_id, policy_id),
    ).fetchone()
    if existing:
        updates = []
        params = []
        for field in ("role", "title", "notes", "is_placement_colleague"):
            if field in fields:
                updates.append(f"{field}=?")
                params.append(fields[field])
        if updates:
            params.append(existing["id"])
            conn.execute(f"UPDATE contact_policy_assignments SET {', '.join(updates)} WHERE id=?", params)
        return existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO contact_policy_assignments
               (contact_id, policy_id, role, title, notes, is_placement_colleague)
               VALUES (?,?,?,?,?,?)""",
            (contact_id, policy_id,
             fields.get("role"), fields.get("title"), fields.get("notes"),
             fields.get("is_placement_colleague", 0)),
        )
        return cur.lastrowid


def remove_contact_from_client(conn: sqlite3.Connection, assignment_id: int) -> None:
    conn.execute("DELETE FROM contact_client_assignments WHERE id=?", (assignment_id,))


def remove_contact_from_policy(conn: sqlite3.Connection, assignment_id: int) -> None:
    conn.execute("DELETE FROM contact_policy_assignments WHERE id=?", (assignment_id,))


def set_primary_contact(conn: sqlite3.Connection, client_id: int, assignment_id: int) -> None:
    """Toggle is_primary: clear all for this client/type, then set if not already primary."""
    existing = conn.execute(
        "SELECT is_primary, contact_type FROM contact_client_assignments WHERE id=?", (assignment_id,)
    ).fetchone()
    if not existing:
        return
    conn.execute(
        "UPDATE contact_client_assignments SET is_primary=0 WHERE client_id=? AND contact_type=?",
        (client_id, existing["contact_type"]),
    )
    if not existing["is_primary"]:
        conn.execute("UPDATE contact_client_assignments SET is_primary=1 WHERE id=?", (assignment_id,))


def set_placement_colleague(conn: sqlite3.Connection, assignment_id: int) -> None:
    """Toggle is_placement_colleague on a policy assignment."""
    current = conn.execute(
        "SELECT is_placement_colleague FROM contact_policy_assignments WHERE id=?", (assignment_id,)
    ).fetchone()
    if current:
        new_val = 0 if current["is_placement_colleague"] else 1
        conn.execute(
            "UPDATE contact_policy_assignments SET is_placement_colleague=? WHERE id=?",
            (new_val, assignment_id),
        )


def merge_contacts(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    """Merge source contact into target: reassign all assignments, delete source."""
    # Reassign client assignments (skip if target already has the same assignment)
    for r in conn.execute(
        "SELECT id, client_id, contact_type FROM contact_client_assignments WHERE contact_id=?", (source_id,)
    ).fetchall():
        existing = conn.execute(
            "SELECT id FROM contact_client_assignments WHERE contact_id=? AND client_id=? AND contact_type=?",
            (target_id, r["client_id"], r["contact_type"]),
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM contact_client_assignments WHERE id=?", (r["id"],))
        else:
            conn.execute("UPDATE contact_client_assignments SET contact_id=? WHERE id=?", (target_id, r["id"]))
    # Reassign policy assignments
    for r in conn.execute(
        "SELECT id, policy_id FROM contact_policy_assignments WHERE contact_id=?", (source_id,)
    ).fetchall():
        existing = conn.execute(
            "SELECT id FROM contact_policy_assignments WHERE contact_id=? AND policy_id=?",
            (target_id, r["policy_id"]),
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM contact_policy_assignments WHERE id=?", (r["id"],))
        else:
            conn.execute("UPDATE contact_policy_assignments SET contact_id=? WHERE id=?", (target_id, r["id"]))
    # Reassign activity_log references
    conn.execute("UPDATE activity_log SET contact_id=? WHERE contact_id=?", (target_id, source_id))
    # Delete source contact
    conn.execute("DELETE FROM contacts WHERE id=?", (source_id,))


def get_followup_count_for_date(conn: sqlite3.Connection, target_date: str) -> int:
    """Count pending follow-ups on a specific date using the same dedup logic as get_all_followups."""
    row = conn.execute("""
        SELECT COUNT(*) AS n FROM (
            SELECT a.id FROM activity_log a
            WHERE a.follow_up_done = 0 AND a.follow_up_date = ?

            UNION ALL

            SELECT p.id FROM policies p
            WHERE p.archived = 0 AND p.follow_up_date = ?
              AND NOT EXISTS (
                  SELECT 1 FROM activity_log a
                  WHERE a.policy_id = p.id AND a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
              )
        )
    """, (target_date, target_date)).fetchone()
    return row["n"]


def get_suggested_followups(
    conn: sqlite3.Connection,
    excluded_statuses: Optional[list] = None,
    client_ids: list[int] | None = None,
) -> list[dict]:
    """Return policies that likely need a follow-up but have none scheduled.

    Criteria: expiring within 90 days, no follow_up_date set, AND either:
    - renewal_status is 'Not Started', or
    - no activity logged in the last 30 days
    """
    excl_clause = ""
    excl_params: list = []
    if excluded_statuses:
        placeholders = ",".join("?" * len(excluded_statuses))
        excl_clause = f"AND (p.renewal_status NOT IN ({placeholders}) OR p.renewal_status IS NULL)"
        excl_params = list(excluded_statuses)

    client_clause = ""
    client_params: list = []
    if client_ids:
        placeholders = ",".join("?" * len(client_ids))
        client_clause = f"AND c.id IN ({placeholders})"
        client_params = list(client_ids)

    sql = f"""
    SELECT p.policy_uid, p.policy_type, p.carrier, p.expiration_date,
           p.renewal_status, p.client_id, p.project_name,
           c.name AS client_name,
           CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
           (SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id) AS last_activity_date
    FROM policies p
    JOIN clients c ON p.client_id = c.id
    WHERE p.archived = 0
      AND p.follow_up_date IS NULL
      AND julianday(p.expiration_date) - julianday('now') <= 90
      AND julianday(p.expiration_date) - julianday('now') > 0
      {excl_clause}
      {client_clause}
      AND (
        p.renewal_status = 'Not Started'
        OR (SELECT COUNT(*) FROM activity_log a
            WHERE a.policy_id = p.id
              AND a.activity_date >= date('now', '-30 days')) = 0
      )
    ORDER BY p.expiration_date ASC
    """
    return [dict(r) for r in conn.execute(sql, excl_params + client_params).fetchall()]


_OPPORTUNITY_SELECT = """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.opportunity_status,
                  p.target_effective_date, p.premium, p.commission_rate,
                  CASE WHEN p.commission_rate > 0
                      THEN ROUND(p.premium * p.commission_rate, 2)
                      ELSE NULL
                  END AS commission_amount,
                  p.project_name, p.project_id, p.description,
                  p.follow_up_date, p.client_id,
                  c.name AS client_name, c.cn_number,
                  COALESCE(
                      (SELECT co_pc.name FROM contact_policy_assignments cpa_pc
                       JOIN contacts co_pc ON cpa_pc.contact_id = co_pc.id
                       WHERE cpa_pc.policy_id = p.id ORDER BY cpa_pc.id LIMIT 1),
                      p.placement_colleague
                  ) AS placement_colleague,
                  COALESCE(
                      (SELECT co_pe.email FROM contact_policy_assignments cpa_pe
                       JOIN contacts co_pe ON cpa_pe.contact_id = co_pe.id
                       WHERE cpa_pe.policy_id = p.id AND co_pe.email IS NOT NULL ORDER BY cpa_pe.id LIMIT 1),
                      p.placement_colleague_email
                  ) AS placement_colleague_email
           FROM policies p
           JOIN clients c ON p.client_id = c.id
           WHERE p.is_opportunity = 1 AND p.archived = 0"""


def get_opportunity_by_uid(conn: sqlite3.Connection, policy_uid: str) -> dict | None:
    """Return a single opportunity row by policy_uid."""
    row = conn.execute(
        _OPPORTUNITY_SELECT + " AND p.policy_uid = ?", (policy_uid.upper(),)
    ).fetchone()
    return dict(row) if row else None


def get_open_opportunities(conn: sqlite3.Connection) -> list[dict]:
    """Return all active (non-archived) opportunities, sorted by status priority then target date."""
    rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.opportunity_status,
                  p.target_effective_date, p.premium, p.commission_rate,
                  CASE WHEN p.commission_rate > 0
                      THEN ROUND(p.premium * p.commission_rate, 2)
                      ELSE NULL
                  END AS commission_amount,
                  p.project_name, p.description,
                  p.follow_up_date, p.client_id,
                  c.name AS client_name,
                  COALESCE(
                      (SELECT co_pc2.name FROM contact_policy_assignments cpa_pc2
                       JOIN contacts co_pc2 ON cpa_pc2.contact_id = co_pc2.id
                       WHERE cpa_pc2.policy_id = p.id ORDER BY cpa_pc2.id LIMIT 1),
                      p.placement_colleague
                  ) AS placement_colleague,
                  COALESCE(
                      (SELECT co_pe2.email FROM contact_policy_assignments cpa_pe2
                       JOIN contacts co_pe2 ON cpa_pe2.contact_id = co_pe2.id
                       WHERE cpa_pe2.policy_id = p.id AND co_pe2.email IS NOT NULL ORDER BY cpa_pe2.id LIMIT 1),
                      p.placement_colleague_email
                  ) AS placement_colleague_email
           FROM policies p
           JOIN clients c ON p.client_id = c.id
           WHERE p.is_opportunity = 1 AND p.archived = 0
           ORDER BY
               CASE p.opportunity_status
                   WHEN 'Pending Bind' THEN 1
                   WHEN 'Submitted'    THEN 2
                   WHEN 'Quoting'      THEN 3
                   WHEN 'Prospecting'  THEN 4
                   ELSE 5
               END,
               p.target_effective_date ASC NULLS LAST,
               c.name ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_overdue_followups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM v_overdue_followups").fetchall()


def get_upcoming_followups(conn: sqlite3.Connection, days: int = 30) -> list[sqlite3.Row]:
    """Return pending follow-ups due within the next `days` days (not yet overdue)."""
    today = date.today().isoformat()
    return conn.execute(
        """SELECT a.id, a.subject, a.follow_up_date, a.activity_type,
                  c.name AS client_name, c.id AS client_id, p.policy_uid
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.follow_up_date >= ?
             AND a.follow_up_date <= date(?, '+' || ? || ' days')
             AND a.follow_up_done = 0
           ORDER BY a.follow_up_date""",
        (today, today, days),
    ).fetchall()


def get_activity_by_id(conn: sqlite3.Connection, activity_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM activity_log WHERE id = ?", (activity_id,)
    ).fetchone()


# ─── PREMIUM HISTORY QUERIES ──────────────────────────────────────────────────

def get_premium_history(
    conn: sqlite3.Connection,
    client_id: int,
    policy_type: Optional[str] = None,
) -> list[sqlite3.Row]:
    sql = """SELECT ph.*, c.name AS client_name
             FROM premium_history ph
             JOIN clients c ON ph.client_id = c.id
             WHERE ph.client_id = ?"""
    params: list = [client_id]
    if policy_type:
        sql += " AND ph.policy_type LIKE ?"
        params.append(f"%{policy_type}%")
    sql += " ORDER BY ph.policy_type, ph.term_effective DESC"
    return conn.execute(sql, params).fetchall()


# ─── SEARCH ───────────────────────────────────────────────────────────────────

def full_text_search(conn: sqlite3.Connection, query: str) -> dict[str, list[sqlite3.Row]]:
    pattern = f"%{query}%"
    clients = conn.execute(
        """SELECT id, name, industry_segment, primary_contact, notes, cn_number
           FROM clients WHERE archived = 0
           AND (name LIKE ? OR notes LIKE ? OR primary_contact LIKE ?
                OR cn_number LIKE ? OR address LIKE ?)""",
        (pattern, pattern, pattern, pattern, pattern),
    ).fetchall()
    policies = conn.execute(
        """SELECT policy_uid, client_name, policy_type, carrier, policy_number,
                  description, notes, project_name
           FROM v_policy_status
           WHERE (client_name LIKE ? OR policy_type LIKE ? OR carrier LIKE ?
                  OR policy_number LIKE ? OR policy_uid LIKE ?
                  OR project_name LIKE ? OR description LIKE ? OR notes LIKE ?)""",
        (pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall()
    activities = conn.execute(
        """SELECT a.id, a.activity_date, c.name AS client_name,
                  a.activity_type, a.subject, a.details, a.contact_person
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           WHERE (a.subject LIKE ? OR a.details LIKE ? OR a.contact_person LIKE ?)""",
        (pattern, pattern, pattern),
    ).fetchall()
    return {"clients": clients, "policies": policies, "activities": activities}


# ─── REVIEW QUERIES ───────────────────────────────────────────────────────────

REVIEW_CYCLE_DAYS: dict[str, int] = {
    "1w": 7,
    "2w": 14,
    "1m": 30,
    "1q": 90,
    "6m": 180,
    "1y": 365,
}

REVIEW_CYCLE_LABELS: dict[str, str] = {
    "1w": "Weekly",
    "2w": "Every 2 Weeks",
    "1m": "Monthly",
    "1q": "Quarterly",
    "6m": "Every 6 Months",
    "1y": "Annually",
}


def get_review_queue(conn: sqlite3.Connection) -> dict:
    """Return records needing review, split into policies, opportunities, and clients."""
    all_rows = [dict(r) for r in conn.execute("SELECT * FROM v_review_queue").fetchall()]
    policies = [r for r in all_rows if not r.get("is_opportunity")]
    opportunities = [r for r in all_rows if r.get("is_opportunity")]
    clients = [dict(r) for r in conn.execute("SELECT * FROM v_review_clients").fetchall()]
    return {"policies": policies, "opportunities": opportunities, "clients": clients}


def get_review_stats(conn: sqlite3.Connection) -> dict:
    """Return counts for the review progress banner."""
    # Needing review = all rows in v_review_queue + v_review_clients
    policy_needing = conn.execute(
        "SELECT COUNT(*) AS n FROM v_review_queue WHERE is_opportunity = 0 OR is_opportunity IS NULL"
    ).fetchone()["n"]
    opp_needing = conn.execute(
        "SELECT COUNT(*) AS n FROM v_review_queue WHERE is_opportunity = 1"
    ).fetchone()["n"]
    client_needing = conn.execute(
        "SELECT COUNT(*) AS n FROM v_review_clients"
    ).fetchone()["n"]

    # Reviewed this week = last_reviewed_at >= Monday of current week
    reviewed_policies = conn.execute(
        """SELECT COUNT(*) AS n FROM policies
           WHERE archived = 0
             AND last_reviewed_at >= date('now', 'weekday 0', '-6 days')"""
    ).fetchone()["n"]
    reviewed_clients = conn.execute(
        """SELECT COUNT(*) AS n FROM clients
           WHERE archived = 0
             AND last_reviewed_at >= date('now', 'weekday 0', '-6 days')"""
    ).fetchone()["n"]

    total_needing = policy_needing + opp_needing + client_needing
    reviewed_this_week = reviewed_policies + reviewed_clients
    return {
        "total_needing": total_needing,
        "reviewed_this_week": reviewed_this_week,
        "policies_needing": policy_needing,
        "opps_needing": opp_needing,
        "clients_needing": client_needing,
    }


def mark_reviewed(
    conn: sqlite3.Connection,
    record_type: str,
    record_id: str | int,
    review_cycle: str | None = None,
) -> None:
    """Set last_reviewed_at = now for a policy (by uid) or client (by id).
    Optionally update review_cycle at the same time.
    """
    if record_type == "policy":
        if review_cycle and review_cycle in REVIEW_CYCLE_DAYS:
            conn.execute(
                "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP, review_cycle = ? WHERE policy_uid = ?",
                (review_cycle, record_id),
            )
        else:
            conn.execute(
                "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP WHERE policy_uid = ?",
                (record_id,),
            )
    elif record_type == "client":
        if review_cycle and review_cycle in REVIEW_CYCLE_DAYS:
            conn.execute(
                "UPDATE clients SET last_reviewed_at = CURRENT_TIMESTAMP, review_cycle = ? WHERE id = ?",
                (review_cycle, record_id),
            )
        else:
            conn.execute(
                "UPDATE clients SET last_reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (record_id,),
            )
    conn.commit()


def set_review_cycle(
    conn: sqlite3.Connection,
    record_type: str,
    record_id: str | int,
    cycle: str,
) -> None:
    """Update review_cycle without marking reviewed."""
    if cycle not in REVIEW_CYCLE_DAYS:
        return
    if record_type == "policy":
        conn.execute(
            "UPDATE policies SET review_cycle = ? WHERE policy_uid = ?",
            (cycle, record_id),
        )
    elif record_type == "client":
        conn.execute(
            "UPDATE clients SET review_cycle = ? WHERE id = ?",
            (cycle, record_id),
        )
    conn.commit()


# ─── AUTO-REVIEW ─────────────────────────────────────────────────────────────


def count_changed_fields(old_row: dict, new_values: dict, fields: list[str]) -> int:
    """Compare old DB values against new form values for a list of field names.

    Normalises None / empty-string / float equivalence to avoid false positives.
    Returns the count of actually-changed fields.
    """
    changed = 0
    for f in fields:
        old = old_row.get(f)
        new = new_values.get(f)
        # Normalise: treat None and '' as equivalent
        if old is None:
            old = ""
        if new is None:
            new = ""
        # Normalise numeric equivalence (e.g. 1000.0 == "1000")
        try:
            if str(float(old)) == str(float(new)):
                continue
        except (ValueError, TypeError):
            pass
        if str(old).strip() != str(new).strip():
            changed += 1
    return changed


def check_auto_review_policy(
    conn: sqlite3.Connection, policy_uid: str, changed_field_count: int = 0
) -> bool:
    """Auto-mark a policy as reviewed if work thresholds are met.

    Returns True if auto-review was triggered.
    """
    from policydb import config as cfg

    if not cfg.get("auto_review_enabled", True):
        return False

    field_thresh = cfg.get("auto_review_field_threshold", 3)
    activity_thresh = cfg.get("auto_review_activity_threshold", 3)

    # Signal 1: enough fields changed in this save
    if changed_field_count >= field_thresh:
        mark_reviewed(conn, "policy", policy_uid)
        return True

    # Signal 2: enough activities since last review
    row = conn.execute(
        "SELECT last_reviewed_at FROM policies WHERE policy_uid = ?",
        (policy_uid,),
    ).fetchone()
    if not row:
        return False
    since = row["last_reviewed_at"] or "2000-01-01"
    pid = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if not pid:
        return False
    count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM activity_log WHERE policy_id = ? AND activity_date >= ?",
        (pid["id"], since),
    ).fetchone()["cnt"]
    if count >= activity_thresh:
        mark_reviewed(conn, "policy", policy_uid)
        return True
    return False


def check_auto_review_client(
    conn: sqlite3.Connection, client_id: int, changed_field_count: int = 0
) -> bool:
    """Auto-mark a client as reviewed if work thresholds are met.

    Returns True if auto-review was triggered.
    """
    from policydb import config as cfg

    if not cfg.get("auto_review_enabled", True):
        return False

    field_thresh = cfg.get("auto_review_field_threshold", 3)
    activity_thresh = cfg.get("auto_review_activity_threshold", 3)

    # Signal 1: enough fields changed in this save
    if changed_field_count >= field_thresh:
        mark_reviewed(conn, "client", client_id)
        return True

    # Signal 2: enough activities since last review
    row = conn.execute(
        "SELECT last_reviewed_at FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()
    if not row:
        return False
    since = row["last_reviewed_at"] or "2000-01-01"
    count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM activity_log WHERE client_id = ? AND activity_date >= ?",
        (client_id, since),
    ).fetchone()["cnt"]
    if count >= activity_thresh:
        mark_reviewed(conn, "client", client_id)
        return True
    return False


# ─── SAVED NOTES ──────────────────────────────────────────────────────────────


def get_saved_notes(
    conn: sqlite3.Connection, scope: str, scope_id: str, limit: int = 50
) -> list[dict]:
    """Return saved notes for a scope (client or policy), newest first."""
    rows = conn.execute(
        "SELECT * FROM saved_notes WHERE scope = ? AND scope_id = ? ORDER BY created_at DESC LIMIT ?",
        (scope, scope_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def save_note(
    conn: sqlite3.Connection, scope: str, scope_id: str, content: str
) -> int:
    """Pin content as a saved note. Returns new note id."""
    cursor = conn.execute(
        "INSERT INTO saved_notes (scope, scope_id, content) VALUES (?, ?, ?)",
        (scope, scope_id, content),
    )
    conn.commit()
    return cursor.lastrowid


def delete_saved_note(conn: sqlite3.Connection, note_id: int) -> None:
    conn.execute("DELETE FROM saved_notes WHERE id = ?", (note_id,))
    conn.commit()


def get_saved_notes_for_client_timeline(
    conn: sqlite3.Connection, client_id: int, limit: int = 50
) -> list[dict]:
    """Return saved notes for a client AND all its policies, for the interleaved timeline."""
    rows = conn.execute(
        """SELECT sn.id, sn.scope, sn.scope_id, sn.content, sn.created_at
           FROM saved_notes sn
           WHERE (sn.scope = 'client' AND sn.scope_id = ?)
              OR (sn.scope = 'policy' AND sn.scope_id IN (
                  SELECT policy_uid FROM policies WHERE client_id = ?
              ))
           ORDER BY sn.created_at DESC LIMIT ?""",
        (str(client_id), client_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_saved_notes(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Return most recent saved notes across all scopes, with client context."""
    rows = conn.execute(
        """SELECT sn.id, sn.scope, sn.scope_id, sn.content, sn.created_at,
                  CASE
                      WHEN sn.scope = 'client' THEN (SELECT name FROM clients WHERE id = CAST(sn.scope_id AS INTEGER))
                      WHEN sn.scope = 'policy' THEN (SELECT c.name FROM policies p JOIN clients c ON p.client_id = c.id WHERE p.policy_uid = sn.scope_id)
                  END AS client_name,
                  CASE
                      WHEN sn.scope = 'client' THEN CAST(sn.scope_id AS INTEGER)
                      WHEN sn.scope = 'policy' THEN (SELECT client_id FROM policies WHERE policy_uid = sn.scope_id)
                  END AS client_id
           FROM saved_notes sn
           ORDER BY sn.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── LINKED ACCOUNTS ──────────────────────────────────────────────────────────


def get_linked_group_for_client(
    conn: sqlite3.Connection, client_id: int
) -> Optional[dict]:
    """Return group info + all member clients for a client's linked group, or None."""
    member = conn.execute(
        "SELECT group_id FROM client_group_members WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if not member:
        return None
    group_id = member["group_id"]
    group = conn.execute(
        "SELECT * FROM client_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if not group:
        return None
    members = conn.execute(
        """SELECT cgm.client_id, c.name, c.cn_number, c.industry_segment,
                  c.is_prospect, c.archived
           FROM client_group_members cgm
           JOIN clients c ON cgm.client_id = c.id
           WHERE cgm.group_id = ?
           ORDER BY c.name""",
        (group_id,),
    ).fetchall()
    # Attach summary stats from v_client_summary
    result_members = []
    for m in members:
        d = dict(m)
        summary = conn.execute(
            "SELECT total_policies, total_premium, total_revenue, next_renewal_days FROM v_client_summary WHERE id = ?",
            (d["client_id"],),
        ).fetchone()
        if summary:
            d.update(dict(summary))
        else:
            d.update({"total_policies": 0, "total_premium": 0, "total_revenue": 0, "next_renewal_days": None})
        result_members.append(d)
    return {
        "group": dict(group),
        "members": result_members,
    }


def get_linked_group_overview(
    conn: sqlite3.Connection, group_id: int
) -> dict:
    """Aggregate program metrics and build coverage matrix across linked group."""
    member_ids = [r["client_id"] for r in conn.execute(
        "SELECT client_id FROM client_group_members WHERE group_id = ?",
        (group_id,),
    ).fetchall()]
    if not member_ids:
        return {"total_premium": 0, "total_revenue": 0, "total_policies": 0,
                "carriers": [], "next_renewal_days": None, "members": [], "coverage_matrix": {}}

    placeholders = ",".join("?" * len(member_ids))

    # Aggregate summary stats
    summaries = conn.execute(
        f"SELECT * FROM v_client_summary WHERE id IN ({placeholders})",
        member_ids,
    ).fetchall()
    total_premium = sum(s["total_premium"] or 0 for s in summaries)
    total_revenue = sum(s["total_revenue"] or 0 for s in summaries)
    total_policies = sum(s["total_policies"] or 0 for s in summaries)
    renewal_days = [s["next_renewal_days"] for s in summaries if s["next_renewal_days"] is not None]
    next_renewal_days = min(renewal_days) if renewal_days else None

    # Distinct carriers
    carrier_rows = conn.execute(
        f"""SELECT DISTINCT carrier FROM policies
            WHERE client_id IN ({placeholders}) AND archived = 0
              AND (is_opportunity = 0 OR is_opportunity IS NULL)
              AND carrier IS NOT NULL AND TRIM(carrier) != ''""",
        member_ids,
    ).fetchall()
    carriers = sorted(r["carrier"] for r in carrier_rows)

    # Coverage matrix: {policy_type: {client_id: [carrier, ...]}}
    policy_rows = conn.execute(
        f"""SELECT client_id, policy_type, carrier FROM policies
            WHERE client_id IN ({placeholders}) AND archived = 0
              AND (is_opportunity = 0 OR is_opportunity IS NULL)""",
        member_ids,
    ).fetchall()
    from collections import defaultdict
    matrix: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for r in policy_rows:
        if r["policy_type"]:
            matrix[r["policy_type"]][r["client_id"]].append(r["carrier"] or "")

    members = [dict(s) for s in summaries]

    return {
        "total_premium": total_premium,
        "total_revenue": total_revenue,
        "total_policies": total_policies,
        "carriers": carriers,
        "next_renewal_days": next_renewal_days,
        "members": members,
        "coverage_matrix": dict(matrix),
    }


def create_linked_group(
    conn: sqlite3.Connection, label: str, relationship: str, client_ids: list[int]
) -> int:
    """Create a linked account group and add initial members. Returns group_id."""
    cursor = conn.execute(
        "INSERT INTO client_groups (label, relationship) VALUES (?, ?)",
        (label or None, relationship or "Related"),
    )
    group_id = cursor.lastrowid
    for cid in client_ids:
        conn.execute(
            "INSERT OR IGNORE INTO client_group_members (group_id, client_id) VALUES (?, ?)",
            (group_id, cid),
        )
    conn.commit()
    return group_id


def add_client_to_group(conn: sqlite3.Connection, group_id: int, client_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO client_group_members (group_id, client_id) VALUES (?, ?)",
        (group_id, client_id),
    )
    conn.commit()


def remove_client_from_group(conn: sqlite3.Connection, client_id: int) -> None:
    """Remove a client from its group. Deletes the group if ≤1 member remains."""
    member = conn.execute(
        "SELECT group_id FROM client_group_members WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if not member:
        return
    group_id = member["group_id"]
    conn.execute("DELETE FROM client_group_members WHERE client_id = ?", (client_id,))
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM client_group_members WHERE group_id = ?",
        (group_id,),
    ).fetchone()["n"]
    if remaining <= 1:
        conn.execute("DELETE FROM client_groups WHERE id = ?", (group_id,))
    conn.commit()


def update_linked_group(
    conn: sqlite3.Connection, group_id: int, label: str, relationship: str
) -> None:
    conn.execute(
        "UPDATE client_groups SET label = ?, relationship = ? WHERE id = ?",
        (label or None, relationship or "Related", group_id),
    )
    conn.commit()


def delete_linked_group(conn: sqlite3.Connection, group_id: int) -> None:
    conn.execute("DELETE FROM client_groups WHERE id = ?", (group_id,))
    conn.commit()


# ─── MANDATED ACTIVITIES ──────────────────────────────────────────────────────

def generate_mandated_activities(conn: sqlite3.Connection) -> int:
    """Create follow-up activities and milestones from mandated_activities config rules.

    Runs on server startup. Uses mandated_activity_log to track which
    (policy_uid, rule_name) combinations have already been created.
    Returns the number of new items created.
    """
    from policydb import config as cfg

    rules = cfg.get("mandated_activities", [])
    if not rules:
        return 0

    today = date.today()
    today_iso = today.isoformat()
    created = 0

    # Get all active, non-archived, non-opportunity policies with dates
    policies = conn.execute(
        """SELECT p.policy_uid, p.id AS policy_id, p.client_id,
                  p.policy_type, p.carrier, p.effective_date, p.expiration_date,
                  c.name AS client_name
           FROM policies p
           JOIN clients c ON p.client_id = c.id
           WHERE p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)"""
    ).fetchall()

    for pol in policies:
        p = dict(pol)
        for rule in rules:
            rule_name = rule.get("name", "")
            trigger = rule.get("trigger", "")
            days_offset = rule.get("days", 0)
            activity_type = rule.get("activity_type", "Meeting")
            subject_tpl = rule.get("subject", rule_name)

            # Check if already created
            existing = conn.execute(
                "SELECT id FROM mandated_activity_log WHERE policy_uid = ? AND rule_name = ?",
                (p["policy_uid"], rule_name),
            ).fetchone()
            if existing:
                continue

            # Calculate target date based on trigger type
            target_date = None
            if trigger == "days_before_expiry" and p.get("expiration_date"):
                try:
                    exp = date.fromisoformat(p["expiration_date"])
                    target_date = exp - timedelta(days=days_offset)
                except (ValueError, TypeError):
                    continue
            elif trigger == "days_after_effective" and p.get("effective_date"):
                try:
                    eff = date.fromisoformat(p["effective_date"])
                    target_date = eff + timedelta(days=days_offset)
                except (ValueError, TypeError):
                    continue
            else:
                continue

            # Skip dates already in the past — record in tracking table so we
            # never revisit, but do NOT create an activity/follow-up the user
            # would just have to abandon.
            if target_date.isoformat() < today_iso:
                conn.execute(
                    "INSERT OR IGNORE INTO mandated_activity_log (policy_uid, rule_name) VALUES (?, ?)",
                    (p["policy_uid"], rule_name),
                )
                continue

            # Render subject template
            subject = subject_tpl.replace("{{policy_type}}", p.get("policy_type") or "")
            subject = subject.replace("{{client_name}}", p.get("client_name") or "")
            subject = subject.replace("{{carrier}}", p.get("carrier") or "")

            # Create milestone
            conn.execute(
                "INSERT OR IGNORE INTO policy_milestones (policy_uid, milestone, completed) VALUES (?, ?, 0)",
                (p["policy_uid"], rule_name),
            )
            milestone = conn.execute(
                "SELECT id FROM policy_milestones WHERE policy_uid = ? AND milestone = ?",
                (p["policy_uid"], rule_name),
            ).fetchone()
            milestone_id = milestone["id"] if milestone else None

            # Create follow-up activity
            account_exec = cfg.get("default_account_exec", "")
            conn.execute(
                """INSERT INTO activity_log
                   (activity_date, client_id, policy_id, activity_type, subject,
                    follow_up_date, account_exec)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    today_iso,
                    p["client_id"],
                    p["policy_id"],
                    activity_type,
                    subject,
                    target_date.isoformat(),
                    account_exec,
                ),
            )
            activity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Track in mandated_activity_log
            conn.execute(
                "INSERT INTO mandated_activity_log (policy_uid, rule_name, activity_id, milestone_id) VALUES (?, ?, ?, ?)",
                (p["policy_uid"], rule_name, activity_id, milestone_id),
            )
            created += 1

    if created:
        conn.commit()
    return created


# ─── DB STATS ─────────────────────────────────────────────────────────────────

def get_db_stats(conn: sqlite3.Connection) -> dict:
    stats = {}
    for table in ["clients", "policies", "activity_log", "premium_history"]:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        stats[table] = row["n"]
    return stats


def build_effort_projection(
    conn: sqlite3.Connection,
    client_id: int,
    months_back: int = 6,
    months_forward: int = 6,
) -> dict:
    """Build effort projection with actuals, forecast, and narrative."""
    from datetime import date, timedelta
    from calendar import month_abbr
    from policydb import config as _cfg

    today = date.today()

    # ── Client info ──
    client = conn.execute(
        "SELECT name, hourly_rate FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client:
        return {}
    client_name = client["name"]
    hourly_rate = client["hourly_rate"] or _cfg.get("default_hourly_rate", 150)
    multiplier = _cfg.get("renewal_effort_multiplier", 1.5)

    # ── Revenue from v_client_summary ──
    _summary = conn.execute(
        "SELECT total_revenue FROM v_client_summary WHERE id = ?", (client_id,)
    ).fetchone()
    total_revenue = float(_summary["total_revenue"] or 0) if _summary else 0

    # ── Historical monthly hours ──
    cutoff = (today.replace(day=1) - timedelta(days=months_back * 30)).strftime("%Y-%m")
    rows = conn.execute(
        """SELECT strftime('%Y-%m', activity_date) AS ym,
                  COALESCE(SUM(duration_hours), 0) AS hours
           FROM activity_log
           WHERE client_id = ? AND duration_hours > 0
             AND strftime('%Y-%m', activity_date) >= ?
             AND strftime('%Y-%m', activity_date) < strftime('%Y-%m', 'now')
           GROUP BY ym ORDER BY ym""",
        (client_id, cutoff),
    ).fetchall()
    monthly_hours = {r["ym"]: round(float(r["hours"]), 1) for r in rows}

    # Build full list of past months (fill gaps with 0)
    actuals = []
    d = today.replace(day=1) - timedelta(days=months_back * 30)
    d = d.replace(day=1)
    while d < today.replace(day=1):
        ym = d.strftime("%Y-%m")
        hours = monthly_hours.get(ym, 0)
        actuals.append({
            "month": ym,
            "label": month_abbr[d.month],
            "hours": hours,
        })
        # Advance to next month
        if d.month == 12:
            d = d.replace(year=d.year + 1, month=1)
        else:
            d = d.replace(month=d.month + 1)

    if not actuals:
        return {"empty": True, "client_name": client_name}

    months_of_data = sum(1 for a in actuals if a["hours"] > 0)
    if months_of_data == 0:
        return {"empty": True, "client_name": client_name}

    # ── Weighted average ──
    total_w = 0.0
    sum_wh = 0.0
    for i, a in enumerate(actuals):
        w = 1 + (i / max(len(actuals) - 1, 1))
        total_w += w
        sum_wh += a["hours"] * w
    weighted_avg = round(sum_wh / total_w, 1) if total_w else 0

    # ── Renewal months ──
    rm_rows = conn.execute(
        """SELECT CAST(strftime('%m', expiration_date) AS INTEGER) AS month,
                  COUNT(*) AS cnt
           FROM policies
           WHERE client_id = ? AND archived = 0
             AND (is_opportunity = 0 OR is_opportunity IS NULL)
             AND expiration_date IS NOT NULL
           GROUP BY month""",
        (client_id,),
    ).fetchall()
    renewal_months = {r["month"]: r["cnt"] for r in rm_rows}

    # ── Projected months ──
    projected = []
    d = today.replace(day=1)
    for _ in range(months_forward):
        is_renewal = d.month in renewal_months
        hours = round(weighted_avg * (multiplier if is_renewal else 1), 1)
        projected.append({
            "month": d.strftime("%Y-%m"),
            "label": month_abbr[d.month],
            "hours": hours,
            "is_renewal": is_renewal,
            "renewal_count": renewal_months.get(d.month, 0),
        })
        if d.month == 12:
            d = d.replace(year=d.year + 1, month=1)
        else:
            d = d.replace(month=d.month + 1)

    # ── Annual estimate ──
    annual_hours = round(sum(a["hours"] for a in actuals) + sum(p["hours"] for p in projected), 1)
    # Scale to 12 months if we have fewer
    total_months = len(actuals) + len(projected)
    if total_months < 12:
        annual_hours = round(annual_hours * 12 / total_months, 1)
    annual_cost = round(annual_hours * hourly_rate)
    revenue_ratio = round(total_revenue / annual_cost, 1) if annual_cost > 0 else 0

    # ── Peak month ──
    peak = max(projected, key=lambda p: p["hours"]) if projected else None
    peak_hours = peak["hours"] if peak else weighted_avg
    peak_label = peak["label"] if peak else "N/A"

    # ── Next renewal info ──
    next_renewal_projs = [p for p in projected if p["is_renewal"]]
    next_renewal_label = next_renewal_projs[0]["label"] if next_renewal_projs else None
    next_renewal_count = next_renewal_projs[0]["renewal_count"] if next_renewal_projs else 0

    # ── Narrative ──
    narrative = (
        f"Based on {months_of_data} months of history, {client_name} requires approximately "
        f"{weighted_avg} hours/month on average."
    )
    if next_renewal_label:
        narrative += (
            f" With {next_renewal_count} polic{'y' if next_renewal_count == 1 else 'ies'} "
            f"renewing in {next_renewal_label}, effort is projected to spike to ~{peak_hours} hours that month."
        )
    narrative += (
        f" Estimated annual effort: ~{annual_hours} hours "
        f"(${annual_cost:,.0f} at ${hourly_rate:,.0f}/hr). "
        f"Current revenue: ${total_revenue:,.0f}"
    )
    if annual_cost > 0:
        narrative += f" ({revenue_ratio}x cost-to-serve ratio)."
    else:
        narrative += "."

    if 0 < revenue_ratio < 5:
        narrative += " This account may warrant a profitability review."
    elif revenue_ratio > 20:
        narrative += " Strong cost-to-serve ratio — current staffing adequate."

    return {
        "empty": False,
        "client_name": client_name,
        "actuals": actuals,
        "projected": projected,
        "weighted_avg": weighted_avg,
        "annual_hours": annual_hours,
        "hourly_rate": hourly_rate,
        "annual_cost": annual_cost,
        "total_revenue": total_revenue,
        "revenue_ratio": revenue_ratio,
        "renewal_months": list(renewal_months.keys()),
        "narrative": narrative,
    }
