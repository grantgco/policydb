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


def get_client_by_id(conn: sqlite3.Connection, client_id: int) -> Optional[sqlite3.Row]:
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
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM v_renewal_pipeline WHERE days_to_renewal <= ?"
    params: list = [window_days]
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
) -> list[sqlite3.Row]:
    sql = """SELECT v.*, p.created_at AS policy_created
           FROM v_renewal_pipeline v
           JOIN policies p ON p.policy_uid = v.policy_uid
           WHERE v.days_to_renewal <= ?
             AND v.renewal_status = 'Not Started'
             AND julianday('now') - julianday(p.created_at) > ?"""
    params: list = [window_days, stale_days]
    if excluded_statuses:
        placeholders = ",".join("?" * len(excluded_statuses))
        sql += f" AND (v.renewal_status NOT IN ({placeholders}) OR v.renewal_status IS NULL)"
        params.extend(excluded_statuses)
    sql += " ORDER BY v.expiration_date ASC"
    return conn.execute(sql, params).fetchall()


def get_renewal_metrics(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("""
        SELECT
            urgency,
            COUNT(*) AS policy_count,
            COALESCE(SUM(premium), 0) AS total_premium
        FROM v_policy_status
        GROUP BY urgency
    """).fetchall()
    metrics = {r["urgency"]: {"count": r["policy_count"], "premium": r["total_premium"]} for r in rows}
    book = conn.execute("""
        SELECT
            COUNT(*) AS total_clients,
            COALESCE(SUM(total_policies), 0) AS total_policies,
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
            placement_colleague, underwriter_name, underwriter_contact,
            renewal_status, commission_rate, account_exec, notes,
            project_name, exposure_basis, exposure_amount, exposure_unit,
            exposure_address, exposure_city, exposure_state, exposure_zip,
            prior_policy_uid)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            new_uid, old["client_id"], old["policy_type"], old["carrier"], None,
            new_eff.isoformat(), new_exp.isoformat(),
            old["premium"], old["premium"],  # premium carries over; prior_premium = old premium
            old["limit_amount"], old["deductible"], old["description"], old["coverage_form"],
            old["layer_position"] or "Primary", old["tower_group"], old["is_standalone"],
            old["placement_colleague"], old["underwriter_name"], old["underwriter_contact"],
            "Not Started", old["commission_rate"], old["account_exec"], None,
            old["project_name"], old["exposure_basis"], old["exposure_amount"], old["exposure_unit"],
            old["exposure_address"], old["exposure_city"], old["exposure_state"], old["exposure_zip"],
            uid,
        ),
    )

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
) -> list[sqlite3.Row]:
    sql = """SELECT a.*, c.name AS client_name, p.policy_uid
             FROM activity_log a
             JOIN clients c ON a.client_id = c.id
             LEFT JOIN policies p ON a.policy_id = p.id
             WHERE 1=1"""
    params: list = []
    if client_id is not None:
        sql += " AND a.client_id = ?"
        params.append(client_id)
    if days is not None:
        sql += " AND a.activity_date >= date('now', ?)"
        params.append(f"-{days} days")
    if activity_type:
        sql += " AND a.activity_type = ?"
        params.append(activity_type)
    sql += " ORDER BY a.activity_date DESC, a.id DESC"
    return conn.execute(sql, params).fetchall()


def get_all_followups(
    conn: sqlite3.Connection, window: int = 30
) -> tuple[list[dict], list[dict]]:
    """Return (overdue, upcoming) follow-ups from both activity_log and policy records."""
    sql = """
    SELECT 'activity' AS source,
           a.id, a.subject, a.follow_up_date, a.activity_type,
           a.contact_person,
           c.name AS client_name, c.id AS client_id,
           p.policy_uid, p.policy_type, p.carrier, p.project_name,
           CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
           cc.email AS contact_email,
           (SELECT GROUP_CONCAT(ic.email, ',')
            FROM client_contacts ic
            WHERE ic.client_id = c.id AND ic.contact_type = 'internal' AND ic.email IS NOT NULL
           ) AS internal_cc
    FROM activity_log a
    JOIN clients c ON a.client_id = c.id
    LEFT JOIN policies p ON a.policy_id = p.id
    LEFT JOIN client_contacts cc ON cc.client_id = c.id
      AND cc.name = a.contact_person
      AND cc.contact_type = 'client'
      AND cc.email IS NOT NULL
    WHERE a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL

    UNION ALL

    SELECT 'policy' AS source,
           p.id,
           p.policy_type || ' – ' || p.carrier AS subject,
           p.follow_up_date,
           'Policy Reminder' AS activity_type,
           COALESCE(
               (SELECT pc.name FROM policy_contacts pc
                WHERE pc.policy_id = p.id ORDER BY pc.id LIMIT 1),
               p.placement_colleague
           ) AS contact_person,
           c.name AS client_name, c.id AS client_id,
           p.policy_uid, p.policy_type, p.carrier, p.project_name,
           CAST(julianday('now') - julianday(p.follow_up_date) AS INTEGER) AS days_overdue,
           COALESCE(
               (SELECT pc.email FROM policy_contacts pc
                WHERE pc.policy_id = p.id AND pc.email IS NOT NULL ORDER BY pc.id LIMIT 1),
               p.placement_colleague_email
           ) AS contact_email,
           (SELECT GROUP_CONCAT(ic.email, ',')
            FROM client_contacts ic
            WHERE ic.client_id = c.id AND ic.contact_type = 'internal' AND ic.email IS NOT NULL
           ) AS internal_cc
    FROM policies p
    JOIN clients c ON p.client_id = c.id
    WHERE p.follow_up_date IS NOT NULL AND p.archived = 0
      AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)

    ORDER BY follow_up_date ASC
    """
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=window)).isoformat()
    overdue = [r for r in rows if r["follow_up_date"] < today]
    upcoming = [r for r in rows if today <= r["follow_up_date"] <= cutoff]
    return overdue, upcoming


def get_suggested_followups(
    conn: sqlite3.Connection,
    excluded_statuses: Optional[list] = None,
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
      AND (
        p.renewal_status = 'Not Started'
        OR (SELECT COUNT(*) FROM activity_log a
            WHERE a.policy_id = p.id
              AND a.activity_date >= date('now', '-30 days')) = 0
      )
    ORDER BY p.expiration_date ASC
    """
    return [dict(r) for r in conn.execute(sql, excl_params).fetchall()]


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
        """SELECT id, name, industry_segment, primary_contact, notes
           FROM clients WHERE archived = 0
           AND (name LIKE ? OR notes LIKE ? OR primary_contact LIKE ?)""",
        (pattern, pattern, pattern),
    ).fetchall()
    policies = conn.execute(
        """SELECT policy_uid, client_name, policy_type, carrier, policy_number,
                  description, notes
           FROM v_policy_status
           WHERE (policy_type LIKE ? OR carrier LIKE ? OR policy_number LIKE ?
                  OR description LIKE ? OR notes LIKE ?)""",
        (pattern, pattern, pattern, pattern, pattern),
    ).fetchall()
    activities = conn.execute(
        """SELECT a.id, a.activity_date, c.name AS client_name,
                  a.activity_type, a.subject, a.details
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           WHERE (a.subject LIKE ? OR a.details LIKE ?)""",
        (pattern, pattern),
    ).fetchall()
    return {"clients": clients, "policies": policies, "activities": activities}


# ─── DB STATS ─────────────────────────────────────────────────────────────────

def get_db_stats(conn: sqlite3.Connection) -> dict:
    stats = {}
    for table in ["clients", "policies", "activity_log", "premium_history"]:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        stats[table] = row["n"]
    return stats
