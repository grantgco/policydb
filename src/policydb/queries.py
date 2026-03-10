"""Named query functions over the database."""

from __future__ import annotations

import sqlite3
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
    conn: sqlite3.Connection, window_days: int = 180
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM v_renewal_pipeline
           WHERE days_to_renewal <= ?
           ORDER BY expiration_date ASC""",
        (window_days,),
    ).fetchall()


def get_stale_renewals(
    conn: sqlite3.Connection,
    window_days: int = 180,
    stale_days: int = 14,
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT v.*, p.created_at AS policy_created
           FROM v_renewal_pipeline v
           JOIN policies p ON p.policy_uid = v.policy_uid
           WHERE v.days_to_renewal <= ?
             AND v.renewal_status = 'Not Started'
             AND julianday('now') - julianday(p.created_at) > ?
           ORDER BY v.expiration_date ASC""",
        (window_days, stale_days),
    ).fetchall()


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
            COUNT(DISTINCT c.id) AS total_clients,
            COUNT(p.id) AS total_policies,
            COALESCE(SUM(p.premium), 0) AS total_premium
        FROM clients c
        LEFT JOIN policies p ON p.client_id = c.id AND p.archived = 0
        WHERE c.archived = 0
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


# ─── ACTIVITY QUERIES ─────────────────────────────────────────────────────────

def get_activities(
    conn: sqlite3.Connection,
    client_id: Optional[int] = None,
    days: Optional[int] = None,
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
    sql += " ORDER BY a.activity_date DESC, a.id DESC"
    return conn.execute(sql, params).fetchall()


def get_overdue_followups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM v_overdue_followups").fetchall()


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
