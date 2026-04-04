"""Named query functions over the database."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("policydb.queries")

from rapidfuzz import process, fuzz

import policydb.config as cfg

# ─── ISSUE COVERAGE HELPERS ──────────────────────────────────────────────────

# Reusable SQL snippet: activities visible to a given policy via issue coverage.
# Use with positional ? params — pass the policy_id twice.
# Excludes issue header rows (item_kind='issue') so only real activities surface.
_VIA_ISSUE_COVERAGE = """(a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = ?) AND a.item_kind != 'issue')"""

# Same snippet for correlated subqueries where p.id is available.
_VIA_ISSUE_COVERAGE_CORR = """(a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id) AND a.item_kind != 'issue')"""


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
            "SELECT * FROM policies WHERE client_id = ? ORDER BY policy_type, layer_position",
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


def attach_renewal_issues(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Batch-attach active renewal issue UIDs to policy/program row dicts."""
    if not rows:
        return

    # Collect policy_uid and program_uid keys
    policy_uids = []
    program_uids = []
    for r in rows:
        uid = r.get("policy_uid")
        if uid:
            policy_uids.append(uid)
        puid = r.get("program_uid")
        if puid:
            program_uids.append(puid)

    if not policy_uids and not program_uids:
        return

    # Build combined query for both policy and program renewal issues
    all_keys = list(policy_uids)
    for pu in program_uids:
        all_keys.append(f"program:{pu}")

    if not all_keys:
        return

    ph = ",".join("?" * len(all_keys))
    # First: find open issues
    issue_rows = conn.execute(f"""
        SELECT renewal_term_key, issue_uid, issue_severity, subject
        FROM activity_log
        WHERE is_renewal_issue = 1
          AND renewal_term_key IN ({ph})
          AND issue_status NOT IN ('Resolved', 'Closed')
          AND item_kind = 'issue'
    """, all_keys).fetchall()

    lookup = {r["renewal_term_key"]: dict(r) for r in issue_rows}

    # Second: for any term_keys not found, check for merged issues and follow to target
    missing_keys = [k for k in all_keys if k not in lookup]
    if missing_keys:
        mph = ",".join("?" * len(missing_keys))
        merged_rows = conn.execute(f"""
            SELECT id, renewal_term_key, merged_into_id
            FROM activity_log
            WHERE is_renewal_issue = 1
              AND renewal_term_key IN ({mph})
              AND merged_into_id IS NOT NULL
              AND item_kind = 'issue'
        """, missing_keys).fetchall()
        for mr in merged_rows:
            # Follow merge chain to find the target
            cur_id = mr["merged_into_id"]
            for _ in range(10):
                target = conn.execute(
                    "SELECT id, issue_uid, issue_severity, subject, merged_into_id FROM activity_log WHERE id = ?",
                    (cur_id,),
                ).fetchone()
                if not target:
                    break
                if target["merged_into_id"]:
                    cur_id = target["merged_into_id"]
                else:
                    lookup[mr["renewal_term_key"]] = {
                        "issue_uid": target["issue_uid"],
                        "issue_severity": target["issue_severity"],
                        "subject": target["subject"],
                    }
                    break

    for r in rows:
        uid = r.get("policy_uid")
        puid = r.get("program_uid")
        match = None
        if uid and uid in lookup:
            match = lookup[uid]
        elif puid and f"program:{puid}" in lookup:
            match = lookup[f"program:{puid}"]
        if match:
            r["renewal_issue_uid"] = match["issue_uid"]
            r["renewal_issue_severity"] = match["issue_severity"]
            r["renewal_issue_subject"] = match["subject"]
        else:
            r["renewal_issue_uid"] = None
            r["renewal_issue_severity"] = None
            r["renewal_issue_subject"] = None


def attach_open_issues(conn: sqlite3.Connection, rows: list[dict], policy_id_field: str = "id") -> None:
    """
    For each row, find the highest-severity open issue linked via policy_id.
    Sets: issue_uid, issue_severity, issue_subject on each row dict.
    Used for opportunities (which don't have renewal_term_keys).
    """
    if not rows:
        return
    policy_ids = [r.get(policy_id_field) for r in rows if r.get(policy_id_field)]
    if not policy_ids:
        return
    ph = ",".join("?" * len(policy_ids))
    issue_rows = conn.execute(
        f"""SELECT policy_id, issue_uid, issue_severity, subject
            FROM activity_log
            WHERE item_kind = 'issue'
              AND policy_id IN ({ph})
              AND issue_status NOT IN ('Resolved', 'Closed')
            ORDER BY CASE issue_severity
                WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                WHEN 'Normal' THEN 3 ELSE 4 END""",
        policy_ids,
    ).fetchall()
    lookup: dict[int, dict] = {}
    for row in issue_rows:
        pid = row["policy_id"]
        if pid not in lookup:
            lookup[pid] = dict(row)
    for r in rows:
        pid = r.get(policy_id_field)
        if pid and pid in lookup:
            issue = lookup[pid]
            r["issue_uid"] = issue["issue_uid"]
            r["issue_severity"] = issue["issue_severity"]
            r["issue_subject"] = issue["subject"]
        else:
            r.setdefault("issue_uid", None)
            r.setdefault("issue_severity", None)
            r.setdefault("issue_subject", None)


def attach_issue_counts(
    conn: sqlite3.Connection,
    rows: list[dict],
    id_field: str = "id",
    scope: str = "policy",
) -> None:
    """Attach open_issue_count and max_issue_severity to each row dict.

    Args:
        scope: 'policy' matches on policy_id, 'client' matches on client_id.
    """
    if not rows:
        return
    col = "policy_id" if scope == "policy" else "client_id"
    ids = [r.get(id_field) for r in rows if r.get(id_field)]
    if not ids:
        for r in rows:
            r.setdefault("open_issue_count", 0)
            r.setdefault("max_issue_severity", None)
        return
    ph = ",".join("?" * len(ids))
    issue_rows = conn.execute(
        f"""SELECT {col} AS scope_id,
                   COUNT(*) AS cnt,
                   MIN(CASE issue_severity
                       WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                       WHEN 'Normal' THEN 3 ELSE 4 END) AS sev_rank
            FROM activity_log
            WHERE item_kind = 'issue'
              AND {col} IN ({ph})
              AND issue_status NOT IN ('Resolved', 'Closed')
            GROUP BY {col}""",
        ids,
    ).fetchall()
    sev_map = {1: "Critical", 2: "High", 3: "Normal", 4: "Low"}
    lookup = {r["scope_id"]: (r["cnt"], sev_map.get(r["sev_rank"])) for r in issue_rows}
    for r in rows:
        rid = r.get(id_field)
        if rid and rid in lookup:
            r["open_issue_count"] = lookup[rid][0]
            r["max_issue_severity"] = lookup[rid][1]
        else:
            r["open_issue_count"] = 0
            r["max_issue_severity"] = None


def get_dashboard_issues_widget(conn: sqlite3.Connection, limit: int = 3) -> dict:
    """Returns top N open issues by severity + counts for dashboard widget."""
    top = conn.execute(
        """SELECT a.issue_uid, a.subject, a.issue_severity, a.issue_status,
                  a.issue_sla_days, c.name AS client_name,
                  CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open
           FROM activity_log a
           LEFT JOIN clients c ON c.id = a.client_id
           WHERE a.item_kind = 'issue'
             AND a.merged_into_id IS NULL
             AND a.issue_status NOT IN ('Resolved', 'Closed')
           ORDER BY CASE a.issue_severity
               WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
               WHEN 'Normal' THEN 3 ELSE 4 END,
               days_open DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE item_kind='issue' AND merged_into_id IS NULL AND issue_status NOT IN ('Resolved','Closed')"
    ).fetchone()[0]
    sla_count = conn.execute(
        """SELECT COUNT(*) FROM activity_log
           WHERE item_kind = 'issue'
             AND merged_into_id IS NULL
             AND issue_status NOT IN ('Resolved', 'Closed')
             AND issue_sla_days IS NOT NULL
             AND CAST(julianday('now') - julianday(activity_date) AS INTEGER) > issue_sla_days"""
    ).fetchone()[0]
    return {"total": total, "sla_count": sla_count, "top_issues": [dict(r) for r in top]}


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


def get_program_pipeline(
    conn: sqlite3.Connection,
    client_id: int | None = None,
    window_days: int = 180,
) -> list[dict]:
    """Return one row per active program with renewal-relevant aggregated data."""
    sql = """
    SELECT pg.id AS program_id, pg.program_uid, pg.name AS program_name,
           pg.client_id, pg.renewal_status, pg.project_id,
           pg.follow_up_date, pg.bound_date, pg.placement_colleague,
           pg.milestone_profile,
           c.name AS client_name, c.cn_number,
           pr.name AS project_name,
           COUNT(p.id) AS policy_count,
           COUNT(DISTINCT p.carrier) AS carrier_count,
           COALESCE(SUM(p.premium), 0) AS total_premium,
           MIN(p.expiration_date) AS earliest_expiration,
           CAST(julianday(MIN(p.expiration_date)) - julianday('now') AS INTEGER) AS days_to_renewal,
           GROUP_CONCAT(DISTINCT p.carrier) AS carriers_list
    FROM programs pg
    JOIN clients c ON pg.client_id = c.id
    LEFT JOIN projects pr ON pg.project_id = pr.id
    LEFT JOIN policies p ON p.program_id = pg.id
        AND p.archived = 0
        AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
    WHERE pg.archived = 0
      AND c.archived = 0
    """
    params: list = []
    if client_id is not None:
        sql += " AND pg.client_id = ?"
        params.append(client_id)
    sql += " GROUP BY pg.id HAVING MIN(p.expiration_date) IS NOT NULL"
    sql += " AND CAST(julianday(MIN(p.expiration_date)) - julianday('now') AS INTEGER) <= ?"
    params.append(window_days)
    sql += " ORDER BY MIN(p.expiration_date) ASC"
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        dtr = d.get("days_to_renewal")
        if dtr is None:
            dtr = 999
        if dtr <= 30:
            d["urgency"] = "CRITICAL"
        elif dtr <= 60:
            d["urgency"] = "HIGH"
        elif dtr <= 90:
            d["urgency"] = "MEDIUM"
        else:
            d["urgency"] = "LOW"
        d["_is_program"] = True
        # Compute followup_overdue for display
        from datetime import date as _date
        fu = d.get("follow_up_date")
        d["followup_overdue"] = bool(fu and fu < _date.today().isoformat())
        result.append(d)
    return result


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
               (SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id) AND a.item_kind != 'issue')) AS last_activity_date,
               CASE
                   WHEN v.days_to_renewal <= {critical_days}
                        AND v.renewal_status = 'Not Started'
                        AND ((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id) AND a.item_kind != 'issue')) IS NULL
                             OR julianday('now') - julianday((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id) AND a.item_kind != 'issue'))) > {critical_stale})
                   THEN 'CRITICAL'
                   WHEN v.days_to_renewal <= {warning_days} AND v.renewal_status = 'Not Started'
                   THEN 'WARNING'
                   WHEN v.days_to_renewal <= {nudge_days} AND v.follow_up_date IS NULL
                        AND ((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id) AND a.item_kind != 'issue')) IS NULL
                             OR julianday('now') - julianday((SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id) AND a.item_kind != 'issue'))) > {nudge_stale})
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
                   COALESCE(SUM(total_revenue), 0) AS total_revenue,
                   COALESCE(SUM(opportunity_count), 0) AS opportunity_count,
                   COALESCE(SUM(opportunity_premium), 0) AS opportunity_premium,
                   COALESCE(SUM(opportunity_revenue), 0) AS opportunity_revenue
            FROM v_client_summary WHERE id IN ({ph})
        """, client_ids).fetchone()
    else:
        book = conn.execute("""
            SELECT COUNT(*) AS total_clients, COALESCE(SUM(total_policies), 0) AS total_policies,
                   COALESCE(SUM(total_premium), 0) AS total_premium,
                   COALESCE(SUM(total_commission), 0) AS total_commission,
                   COALESCE(SUM(total_fees), 0) AS total_fees,
                   COALESCE(SUM(total_revenue), 0) AS total_revenue,
                   COALESCE(SUM(opportunity_count), 0) AS opportunity_count,
                   COALESCE(SUM(opportunity_premium), 0) AS opportunity_premium,
                   COALESCE(SUM(opportunity_revenue), 0) AS opportunity_revenue
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
        # Feb 29 → Feb 28 in non-leap year (industry standard: stay in month)
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
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
    sql = """SELECT a.*, c.name AS client_name, c.cn_number, p.policy_uid,
                    p.policy_type,
                    COALESCE(a.project_id, p.project_id) AS project_id,
                    pr.name AS project_name,
                    co_ac.name AS contact_name,
                    (SELECT COUNT(*) FROM record_attachments ra WHERE ra.record_type = 'activity' AND ra.record_id = a.id) AS attachment_count
             FROM activity_log a
             JOIN clients c ON a.client_id = c.id
             LEFT JOIN policies p ON a.policy_id = p.id
             LEFT JOIN projects pr ON COALESCE(a.project_id, p.project_id) = pr.id
             LEFT JOIN contacts co_ac ON a.contact_id = co_ac.id
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
    sql += " ORDER BY a.activity_date DESC, a.id DESC"
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
    """Total hours logged for a specific policy (includes issue-sourced activities)."""
    row = conn.execute(
        """SELECT COALESCE(SUM(duration_hours), 0) AS t FROM activity_log a
           WHERE (a.policy_id = ?
                  OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = ?)
                      AND a.item_kind != 'issue'))
             AND a.duration_hours IS NOT NULL""",
        (policy_id, policy_id),
    ).fetchone()
    return float(row["t"])


def auto_close_followups(
    conn,
    *,
    policy_id: int | None = None,
    issue_id: int | None = None,
    reason: str,
    closed_by: str,
    exclude_id: int | None = None,
    before_date: str | None = None,
) -> int:
    """Auto-close open follow-ups matching criteria. Returns count closed.

    At least one of policy_id or issue_id must be provided.
    """
    clauses = [
        "follow_up_done = 0",
        "follow_up_date IS NOT NULL",
        "item_kind = 'followup'",
    ]
    params: list = []
    if policy_id is not None:
        clauses.append("policy_id = ?")
        params.append(policy_id)
    if issue_id is not None:
        clauses.append("issue_id = ?")
        params.append(issue_id)
    if exclude_id is not None:
        clauses.append("id != ?")
        params.append(exclude_id)
    if before_date is not None:
        clauses.append("activity_date < ?")
        params.append(before_date)

    where = " AND ".join(clauses)
    cursor = conn.execute(
        f"""UPDATE activity_log
            SET follow_up_done = 1,
                auto_close_reason = ?,
                auto_closed_at = datetime('now'),
                auto_closed_by = ?
            WHERE {where}""",
        [reason, closed_by] + params,
    )
    return cursor.rowcount


def supersede_followups(conn, policy_id: int, new_date: str) -> None:
    """When logging a new activity with a follow-up, supersede all older follow-ups.

    1. Mark all pending activity follow-ups for this policy as done.
    2. Sync the policy's own follow_up_date to the new date.
    """
    conn.execute(
        """UPDATE activity_log
           SET follow_up_done = 1,
               auto_close_reason = 'superseded',
               auto_closed_at = datetime('now'),
               auto_closed_by = 'supersede_followups'
           WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL""",
        (policy_id,),
    )
    conn.execute(
        "UPDATE policies SET follow_up_date = ? WHERE id = ?",
        (new_date, policy_id),
    )


def auto_close_stale_followups(conn) -> int:
    """Auto-close follow-ups overdue by more than stale_auto_close_days.

    Returns the number of items closed. Called on server startup.
    """
    threshold = cfg.get("stale_auto_close_days", 30)
    cursor = conn.execute("""
        UPDATE activity_log
        SET follow_up_done = 1,
            auto_close_reason = 'stale',
            auto_closed_at = datetime('now'),
            auto_closed_by = 'auto_close_stale'
        WHERE follow_up_done = 0
          AND follow_up_date IS NOT NULL
          AND julianday('now') - julianday(follow_up_date) > ?
          AND auto_close_reason IS NULL
          AND item_kind = 'followup'
    """, (threshold,))
    count = cursor.rowcount
    if count > 0:
        conn.commit()
    return count


def get_all_followups(
    conn: sqlite3.Connection, window: int = 30, client_ids: list[int] | None = None
) -> tuple[list[dict], list[dict]]:
    """Return (overdue, upcoming) follow-ups from both activity_log and policy records.

    Each item is a plain dict enriched with an 'accountability' key derived from
    the row's disposition value (via the follow_up_dispositions config list).
    Unknown or missing dispositions default to 'my_action'.
    """
    # Build disposition → accountability lookup from config
    _disp_accountability: dict[str, str] = {
        d["label"]: d.get("accountability", "my_action")
        for d in cfg.get("follow_up_dispositions", [])
    }
    sql = """
    SELECT 'activity' AS source,
           a.id, a.subject, a.follow_up_date, a.activity_type,
           a.contact_person, a.disposition, a.thread_id,
           c.name AS client_name, c.id AS client_id, c.cn_number, c.industry_segment AS industry,
           p.policy_uid, p.policy_type, p.carrier, p.project_name, p.project_id,
           p.renewal_status, p.expiration_date,
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
           a.activity_date AS note_date,
           a.program_id,
           pg.name AS program_name,
           pg.program_uid,
           a.email_from, a.email_to, a.email_snippet
    FROM activity_log a
    JOIN clients c ON a.client_id = c.id
    LEFT JOIN policies p ON a.policy_id = p.id
    LEFT JOIN contacts co_a ON a.contact_id = co_a.id
    LEFT JOIN programs pg ON a.program_id = pg.id
    WHERE a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
      AND a.item_kind != 'issue'
      AND (a.project_id IS NULL OR a.policy_id IS NOT NULL OR a.program_id IS NOT NULL)

    UNION ALL

    SELECT 'project' AS source,
           a.id, a.subject, a.follow_up_date, a.activity_type,
           a.contact_person, a.disposition, a.thread_id,
           c.name AS client_name, c.id AS client_id, c.cn_number,
           c.industry_segment AS industry,
           NULL AS policy_uid, NULL AS policy_type, NULL AS carrier,
           pr.name AS project_name, a.project_id,
           NULL AS renewal_status, NULL AS expiration_date,
           0 AS is_opportunity,
           CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
           co_a2.email AS contact_email,
           (SELECT GROUP_CONCAT(co_i4.email, ',')
            FROM contact_client_assignments cca_i4
            JOIN contacts co_i4 ON cca_i4.contact_id = co_i4.id
            WHERE cca_i4.client_id = c.id AND cca_i4.contact_type = 'internal'
              AND co_i4.email IS NOT NULL
           ) AS internal_cc,
           a.details AS note_details,
           NULL AS note_subject,
           a.activity_date AS note_date,
           NULL AS program_id,
           NULL AS program_name,
           NULL AS program_uid,
           a.email_from, a.email_to, a.email_snippet
    FROM activity_log a
    JOIN clients c ON a.client_id = c.id
    LEFT JOIN projects pr ON a.project_id = pr.id
    LEFT JOIN contacts co_a2 ON a.contact_id = co_a2.id
    WHERE a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
      AND a.item_kind != 'issue'
      AND a.project_id IS NOT NULL AND a.policy_id IS NULL

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
           c.name AS client_name, c.id AS client_id, c.cn_number, c.industry_segment AS industry,
           p.policy_uid, p.policy_type, p.carrier, p.project_name, p.project_id,
           p.renewal_status, p.expiration_date,
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
            WHERE a2.policy_id = p.id ORDER BY a2.activity_date DESC, a2.id DESC LIMIT 1) AS note_date,
           NULL AS program_id,
           NULL AS program_name,
           NULL AS program_uid,
           NULL AS email_from, NULL AS email_to, NULL AS email_snippet
    FROM policies p
    JOIN clients c ON p.client_id = c.id
    WHERE p.follow_up_date IS NOT NULL AND p.archived = 0
      AND NOT EXISTS (
          SELECT 1 FROM activity_log a
          WHERE (a.policy_id = p.id
                 OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id)
                     AND a.item_kind != 'issue'))
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
           c.name AS client_name, c.id AS client_id, c.cn_number, c.industry_segment AS industry,
           NULL AS policy_uid, NULL AS policy_type, NULL AS carrier,
           NULL AS project_name, NULL AS project_id,
           NULL AS renewal_status, NULL AS expiration_date,
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
           NULL AS note_date,
           NULL AS program_id,
           NULL AS program_name,
           NULL AS program_uid,
           NULL AS email_from, NULL AS email_to, NULL AS email_snippet
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

    all_rows = overdue + upcoming

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

    # Enrich every row with accountability state
    for r in overdue + upcoming:
        disposition = r.get("disposition") or ""
        r["accountability"] = _disp_accountability.get(disposition, "my_action")

    # Enrich every row with source_label
    for r in all_rows:
        src = r.get("source")
        if src == "activity":
            r["source_label"] = "Follow-up"
        elif src == "policy" and r.get("is_opportunity"):
            r["source_label"] = "Opportunity"
        elif src == "policy":
            r["source_label"] = "Renewal"
        elif src == "client":
            r["source_label"] = "Client"
        elif src == "project":
            r["source_label"] = "Project"
        else:
            r["source_label"] = ""

    # Enrich every row with reason_line
    _today = date.today()
    for r in all_rows:
        src = r.get("source")
        if src in ("activity", "project"):
            disp = r.get("disposition")
            if disp:
                days_over = r.get("days_overdue") or 0
                if days_over > 0:
                    r["reason_line"] = f"{disp} — {days_over}d with no response"
                else:
                    r["reason_line"] = disp
            else:
                # Don't repeat the subject — show note details snippet instead
                details = (r.get("note_details") or "").strip()
                subject = (r.get("subject") or "").strip()
                if details and details != subject:
                    r["reason_line"] = details[:80]
                else:
                    r["reason_line"] = ""
        elif src == "policy":
            nd = r.get("note_date")
            if nd:
                try:
                    days_since = (_today - date.fromisoformat(nd)).days
                except (ValueError, TypeError):
                    days_since = 0
                ns = r.get("note_subject")
                if ns:
                    r["reason_line"] = f"{ns} — {days_since}d ago"
                else:
                    r["reason_line"] = f"Last activity {days_since}d ago"
            else:
                r["reason_line"] = "No activity logged"
        elif src == "client":
            details = r.get("note_details") or ""
            r["reason_line"] = details[:80] if details else ""
        else:
            r["reason_line"] = ""

    # Enrich activity-sourced items with prev_disposition and prev_days_ago
    activity_items = [r for r in all_rows if r.get("source") in ("activity", "project") and r.get("id")]
    if activity_items:
        act_ids = [r["id"] for r in activity_items]
        placeholders = ",".join("?" * len(act_ids))
        _prev_rows = conn.execute(f"""
            SELECT a.id,
                   prev.disposition AS prev_disposition,
                   prev.activity_date AS prev_date
            FROM activity_log a
            JOIN activity_log prev
              ON prev.policy_id = a.policy_id
             AND prev.id < a.id
             AND prev.disposition IS NOT NULL
            WHERE a.id IN ({placeholders})
              AND prev.id = (
                  SELECT MAX(p2.id)
                  FROM activity_log p2
                  WHERE p2.policy_id = a.policy_id
                    AND p2.id < a.id
                    AND p2.disposition IS NOT NULL
              )
        """, act_ids).fetchall()
        _prev_map = {r["id"]: r for r in _prev_rows}
        for r in activity_items:
            prev = _prev_map.get(r["id"])
            if prev:
                r["prev_disposition"] = prev["prev_disposition"]
                try:
                    r["prev_days_ago"] = (_today - date.fromisoformat(prev["prev_date"])).days
                except (ValueError, TypeError):
                    r["prev_days_ago"] = None
            else:
                r["prev_disposition"] = None
                r["prev_days_ago"] = None

    return overdue, upcoming


def get_contacts_for_client(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Return deduplicated contacts for a client (from unified contacts + assignments) for autocomplete."""
    rows = conn.execute("""
        SELECT co.id, co.name, COALESCE(cca.role, cca.title, '') AS detail, cca.contact_type AS source
        FROM contacts co
        JOIN contact_client_assignments cca ON co.id = cca.contact_id
        WHERE cca.client_id = ?

        UNION

        SELECT co.id, co.name, COALESCE(cpa.role, cpa.title, '') AS detail, 'placement' AS source
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
        aid = cur.lastrowid
    # Auto-star: if this is now the only contact on the policy, mark as placement colleague
    _auto_star_sole_placement(conn, policy_id)
    return aid


def _auto_star_sole_placement(conn, policy_id: int) -> None:
    """If a policy has exactly one contact and none are starred, auto-star it."""
    rows = conn.execute(
        "SELECT id, is_placement_colleague FROM contact_policy_assignments WHERE policy_id=?",
        (policy_id,),
    ).fetchall()
    if len(rows) == 1 and not rows[0]["is_placement_colleague"]:
        conn.execute(
            "UPDATE contact_policy_assignments SET is_placement_colleague=1 WHERE id=?",
            (rows[0]["id"],),
        )


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


def get_program_contacts(conn: sqlite3.Connection, program_id: int) -> list[dict]:
    """Return contacts assigned to a program via contact_program_assignments."""
    rows = conn.execute(
        """SELECT cpa.id AS assignment_id, co.id AS contact_id,
                  co.name, co.email, co.phone, co.mobile, co.organization,
                  cpa.role, cpa.title, cpa.notes, cpa.is_placement_colleague
           FROM contact_program_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.program_id = ?
           ORDER BY cpa.role, co.name""",
        (program_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["id"] = d["assignment_id"]
        result.append(d)
    return result


def assign_contact_to_program(conn: sqlite3.Connection, contact_id: int, program_id: int, **fields) -> int:
    """Create or update a contact-program assignment. Returns assignment id."""
    existing = conn.execute(
        "SELECT id FROM contact_program_assignments WHERE contact_id=? AND program_id=?",
        (contact_id, program_id),
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
            conn.execute(f"UPDATE contact_program_assignments SET {', '.join(updates)} WHERE id=?", params)
        return existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO contact_program_assignments
               (contact_id, program_id, role, title, notes, is_placement_colleague)
               VALUES (?,?,?,?,?,?)""",
            (contact_id, program_id,
             fields.get("role"), fields.get("title"), fields.get("notes"),
             fields.get("is_placement_colleague", 0)),
        )
        return cur.lastrowid


def remove_contact_from_program(conn: sqlite3.Connection, assignment_id: int) -> None:
    """Delete a contact-program assignment."""
    conn.execute("DELETE FROM contact_program_assignments WHERE id=?", (assignment_id,))


def set_program_placement_colleague(conn: sqlite3.Connection, assignment_id: int) -> None:
    """Toggle is_placement_colleague on a program contact assignment."""
    current = conn.execute(
        "SELECT is_placement_colleague FROM contact_program_assignments WHERE id=?", (assignment_id,)
    ).fetchone()
    if current:
        new_val = 0 if current["is_placement_colleague"] else 1
        conn.execute(
            "UPDATE contact_program_assignments SET is_placement_colleague=? WHERE id=?",
            (new_val, assignment_id),
        )


def get_program_underwriter_rollup(conn: sqlite3.Connection, program_id: int) -> list[dict]:
    """Aggregate underwriter contacts from all child policies of a program."""
    rows = conn.execute(
        """SELECT DISTINCT co.id AS contact_id, co.name, co.email, co.phone, co.mobile,
                  p.carrier, p.policy_uid,
                  cpa.role, cpa.title
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.program_id = ?
             AND p.archived = 0
             AND LOWER(COALESCE(cpa.role, '')) IN ('underwriter', 'uw')
           ORDER BY p.carrier, co.name""",
        (program_id,),
    ).fetchall()
    return [dict(r) for r in rows]


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

    # Build waiting-external disposition labels from config
    waiting_labels = [
        d["label"] for d in cfg.get("follow_up_dispositions", [])
        if d.get("accountability") == "waiting_external"
    ]
    waiting_ph = ",".join("?" * len(waiting_labels)) if waiting_labels else "'__none__'"

    sql = f"""
    SELECT p.policy_uid, p.policy_type, p.carrier, p.expiration_date,
           p.renewal_status, p.client_id, p.project_name,
           c.name AS client_name,
           CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
           (SELECT MAX(a.activity_date) FROM activity_log a WHERE a.policy_id = p.id OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id) AND a.item_kind != 'issue')) AS last_activity_date
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
            WHERE (a.policy_id = p.id
                   OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id)
                       AND a.item_kind != 'issue'))
              AND a.activity_date >= date('now', '-30 days')) = 0
      )
      AND NOT EXISTS (
        SELECT 1 FROM activity_log al
        WHERE (al.policy_id = p.id
               OR (al.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id)
                   AND al.item_kind != 'issue'))
          AND al.follow_up_done = 0
          AND al.follow_up_date IS NOT NULL
      )
      AND NOT EXISTS (
        SELECT 1 FROM activity_log al
        WHERE (al.policy_id = p.id
               OR (al.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc WHERE ipc.policy_id = p.id)
                   AND al.item_kind != 'issue'))
          AND al.follow_up_done = 0
          AND al.disposition IN ({waiting_ph})
      )
    ORDER BY p.expiration_date ASC
    """
    all_params = excl_params + client_params + (waiting_labels if waiting_labels else [])
    return [dict(r) for r in conn.execute(sql, all_params).fetchall()]


def get_insurance_deadline_suggestions(
    conn: sqlite3.Connection,
    client_ids: list[int] | None = None,
) -> list[dict]:
    """Return project pipeline items approaching their insurance_needed_by deadline.

    Returns suggestions for projects where:
    - insurance_needed_by is set and in the future
    - project stage is NOT in insurance_completed_stages
    - deadline is within the largest tier window

    Each result includes a tier label (Normal/High/Urgent) based on days remaining.
    """
    import policydb.config as cfg

    tiers = cfg.get("insurance_reminder_tiers", [30, 14, 7])
    completed = cfg.get("insurance_completed_stages", ["Bound", "Active", "Complete"])
    if not tiers:
        return []

    max_window = max(tiers)
    tiers_sorted = sorted(tiers, reverse=True)  # e.g. [30, 14, 7]

    client_clause = ""
    client_params: list = []
    if client_ids:
        placeholders = ",".join("?" * len(client_ids))
        client_clause = f"AND p.client_id IN ({placeholders})"
        client_params = list(client_ids)

    stage_clause = ""
    stage_params: list = []
    if completed:
        placeholders = ",".join("?" * len(completed))
        stage_clause = f"AND (p.status IS NULL OR p.status NOT IN ({placeholders}))"
        stage_params = list(completed)

    sql = f"""
    SELECT p.id AS project_id, p.name AS project_name, p.insurance_needed_by,
           p.status AS project_stage, p.client_id,
           c.name AS client_name,
           CAST(julianday(p.insurance_needed_by) - julianday('now') AS INTEGER) AS days_remaining
    FROM projects p
    JOIN clients c ON p.client_id = c.id
    WHERE p.insurance_needed_by IS NOT NULL
      AND julianday(p.insurance_needed_by) - julianday('now') > 0
      AND julianday(p.insurance_needed_by) - julianday('now') <= ?
      AND p.project_type != 'Location'
      AND c.archived = 0
      {stage_clause}
      {client_clause}
    ORDER BY p.insurance_needed_by ASC
    """
    params = [max_window] + stage_params + client_params
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # Assign tier label based on days remaining
    for row in rows:
        days = row["days_remaining"]
        if days <= tiers_sorted[-1]:      # e.g. <= 7
            row["tier"] = "Urgent"
        elif len(tiers_sorted) > 1 and days <= tiers_sorted[-2]:  # e.g. <= 14
            row["tier"] = "High"
        else:
            row["tier"] = "Normal"
        row["subject"] = f"Insurance needed in {days}d — {row['project_name']}"

    return rows


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

import re as _re
import time as _time


def rebuild_search_index(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS5 search index from scratch.  Runs on every startup."""
    t0 = _time.perf_counter()
    conn.execute("DELETE FROM search_index")

    # -- Clients --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'client', CAST(id AS TEXT),
            COALESCE(name, ''),
            COALESCE(industry_segment, '') || ' ' || COALESCE(account_exec, ''),
            COALESCE(notes, '') || ' ' || COALESCE(business_description, '')
                || ' ' || COALESCE(account_priorities, '') || ' ' || COALESCE(renewal_strategy, '')
                || ' ' || COALESCE(growth_opportunities, ''),
            COALESCE(cn_number, '') || ' ' || COALESCE(contact_email, '')
                || ' ' || COALESCE(address, '') || ' ' || COALESCE(primary_contact, '')
                || ' ' || COALESCE(fein, '')
        FROM clients WHERE archived = 0
    """)

    # -- Policies --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'policy', p.policy_uid,
            COALESCE(c.name, '') || ' ' || COALESCE(p.policy_type, ''),
            COALESCE(p.carrier, '') || ' ' || COALESCE(p.first_named_insured, ''),
            COALESCE(p.description, '') || ' ' || COALESCE(p.notes, ''),
            COALESCE(p.policy_uid, '') || ' ' || COALESCE(p.policy_number, '')
                || ' ' || COALESCE(p.project_name, '') || ' ' || COALESCE(p.coverage_form, '')
                || ' ' || COALESCE(p.underwriter_name, '') || ' ' || COALESCE(p.placement_colleague, '')
                || ' ' || COALESCE(p.account_exec, '')
        FROM policies p
        JOIN clients c ON c.id = p.client_id
        WHERE p.archived = 0
    """)

    # -- Activities (last 2 years) --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'activity', CAST(a.id AS TEXT),
            COALESCE(a.subject, ''),
            COALESCE(c.name, '') || ' ' || COALESCE(a.activity_type, '')
                || ' ' || COALESCE(a.contact_person, ''),
            COALESCE(a.details, '') || ' ' || COALESCE(a.email_snippet, ''),
            COALESCE(a.email_from, '') || ' ' || COALESCE(a.email_to, '')
        FROM activity_log a
        LEFT JOIN clients c ON a.client_id = c.id
        WHERE a.item_kind IS NULL
          AND a.activity_date >= date('now', '-2 years')
    """)

    # -- Issues (open parent issues only) --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'issue', CAST(a.id AS TEXT),
            COALESCE(a.subject, ''),
            COALESCE(c.name, '') || ' ' || COALESCE(a.issue_severity, '')
                || ' ' || COALESCE(a.issue_status, ''),
            COALESCE(a.details, '') || ' ' || COALESCE(a.resolution_notes, ''),
            COALESCE(a.issue_uid, '') || ' ' || COALESCE(a.root_cause_category, '')
        FROM activity_log a
        LEFT JOIN clients c ON a.client_id = c.id
        WHERE a.item_kind = 'issue' AND a.issue_id IS NULL
          AND a.merged_into_id IS NULL
          AND a.issue_status NOT IN ('Resolved', 'Closed')
    """)

    # -- Contacts --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'contact', CAST(co.id AS TEXT),
            COALESCE(co.name, ''),
            COALESCE(co.organization, ''),
            COALESCE(co.expertise_notes, ''),
            COALESCE(co.email, '') || ' ' || COALESCE(co.phone, '')
                || ' ' || COALESCE(co.mobile, '')
                || ' ' || COALESCE(
                    (SELECT GROUP_CONCAT(cca.role, ' ')
                     FROM contact_client_assignments cca
                     WHERE cca.contact_id = co.id AND cca.role IS NOT NULL), '')
        FROM contacts co
    """)

    # -- Programs --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'program', pg.program_uid,
            COALESCE(pg.name, ''),
            COALESCE(c.name, '') || ' ' || COALESCE(pg.line_of_business, ''),
            COALESCE(pg.notes, '') || ' ' || COALESCE(pg.working_notes, ''),
            COALESCE(pg.program_uid, '') || ' ' || COALESCE(pg.lead_broker, '')
                || ' ' || COALESCE(pg.account_exec, '')
        FROM programs pg
        JOIN clients c ON c.id = pg.client_id
        WHERE pg.archived = 0
    """)

    # -- Meetings --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'meeting', CAST(m.id AS TEXT),
            COALESCE(m.title, ''),
            COALESCE(c.name, '') || ' ' || COALESCE(m.location, ''),
            COALESCE(m.notes, '') || ' ' || COALESCE(m.agenda, ''),
            COALESCE(m.meeting_uid, '')
                || ' ' || COALESCE(
                    (SELECT GROUP_CONCAT(ma.name, ' ')
                     FROM meeting_attendees ma WHERE ma.meeting_id = m.id), '')
        FROM client_meetings m
        JOIN clients c ON c.id = m.client_id
    """)

    # -- Locations / Projects --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'location', CAST(pr.id AS TEXT),
            COALESCE(pr.name, ''),
            COALESCE(c.name, ''),
            COALESCE(pr.notes, '') || ' ' || COALESCE(pr.scope_description, ''),
            COALESCE(pr.address, '') || ' ' || COALESCE(pr.city, '')
                || ' ' || COALESCE(pr.state, '') || ' ' || COALESCE(pr.zip, '')
                || ' ' || COALESCE(pr.general_contractor, '') || ' ' || COALESCE(pr.owner_name, '')
        FROM projects pr
        JOIN clients c ON c.id = pr.client_id
    """)

    # -- Inbox (last 6 months) --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'inbox', CAST(i.id AS TEXT),
            COALESCE(i.email_subject, SUBSTR(COALESCE(i.content, ''), 1, 100)),
            COALESCE(i.email_from, ''),
            COALESCE(i.content, ''),
            COALESCE(i.inbox_uid, '') || ' ' || COALESCE(i.email_to, '')
        FROM inbox i
        WHERE i.created_at >= date('now', '-6 months')
    """)

    # -- Client scratchpads --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'scratchpad', 'client-' || CAST(cs.client_id AS TEXT),
            COALESCE(c.name, '') || ' scratchpad',
            '',
            COALESCE(cs.content, ''),
            ''
        FROM client_scratchpad cs
        JOIN clients c ON c.id = cs.client_id
        WHERE cs.content IS NOT NULL AND cs.content != ''
    """)

    # -- Policy scratchpads --
    conn.execute("""
        INSERT INTO search_index (entity_type, entity_id, title, subtitle, body, metadata)
        SELECT 'scratchpad', 'policy-' || ps.policy_uid,
            ps.policy_uid || ' scratchpad',
            '',
            COALESCE(ps.content, ''),
            ''
        FROM policy_scratchpad ps
        WHERE ps.content IS NOT NULL AND ps.content != ''
    """)

    conn.commit()
    elapsed = (_time.perf_counter() - t0) * 1000
    count = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
    logger.info("Search index rebuilt: %d rows in %.0fms", count, elapsed)


# FTS5 operator characters to strip from user input
_FTS5_SPECIAL = _re.compile(r'["\*\{\}\^\(\)]')
_FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def _sanitize_fts_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression from user input."""
    # Strip special FTS5 characters
    q = _FTS5_SPECIAL.sub(" ", query)
    words = [w for w in q.split() if w.upper() not in _FTS5_KEYWORDS and len(w) >= 1]
    if not words:
        return ""
    # Each word gets prefix matching; multi-word also tries phrase match
    parts = [f'"{w}"*' for w in words]
    expr = " OR ".join(parts)
    if len(words) > 1:
        phrase = " ".join(words)
        expr = f'"{phrase}" OR {expr}'
    return expr


def full_text_search(conn: sqlite3.Connection, query: str) -> dict:
    """Search across all entity types using FTS5 with fuzzy fallback."""
    match_expr = _sanitize_fts_query(query)
    if not match_expr:
        return {
            "clients": [], "policies": [], "activities": [], "issues": [],
            "contacts": [], "programs": [], "meetings": [], "locations": [],
            "inbox": [], "_snippets": {}, "_query_mode": "none",
        }

    # --- Tier 1: FTS5 search ---
    snippets: dict[tuple[str, str], str] = {}
    grouped: dict[str, list[str]] = {}
    try:
        # Columns: 0=entity_type, 1=entity_id, 2=title, 3=subtitle, 4=body, 5=metadata
        rows = conn.execute("""
            SELECT entity_type, entity_id,
                   snippet(search_index, 2, '<mark>', '</mark>', '…', 40),
                   snippet(search_index, 3, '<mark>', '</mark>', '…', 40),
                   snippet(search_index, 4, '<mark>', '</mark>', '…', 60),
                   snippet(search_index, 5, '<mark>', '</mark>', '…', 40)
            FROM search_index
            WHERE search_index MATCH ?
            ORDER BY bm25(search_index, 10.0, 5.0, 1.0, 3.0)
            LIMIT 80
        """, (match_expr,)).fetchall()
        for r in rows:
            etype, eid = r[0], r[1]
            grouped.setdefault(etype, []).append(eid)
            # Pick the best snippet — prefer the one that actually has a highlight
            snip = ""
            for col in (r[4], r[3], r[5], r[2]):  # body, subtitle, metadata, title
                if col and "<mark>" in col:
                    snip = col
                    break
            if snip:
                snippets[(etype, eid)] = snip
    except Exception:
        # FTS5 query syntax error — fall through to fuzzy
        logger.debug("FTS5 MATCH failed for %r, falling back to fuzzy", query)
        rows = []

    # --- Hydrate results from source tables ---
    # FTS5 stores singular entity_type ('client', 'policy', etc.)
    # Results dict uses plural keys ('clients', 'policies', etc.)
    results: dict[str, list] = {}

    def _hydrate(fts_type: str, result_key: str, sql: str):
        ids = grouped.get(fts_type, [])
        if not ids:
            results[result_key] = []
            return
        placeholders = ",".join("?" * len(ids))
        full_sql = sql.replace("__IDS__", placeholders)
        results[result_key] = [dict(r) for r in conn.execute(full_sql, ids).fetchall()]

    _hydrate("client", "clients", """
        SELECT id, name, industry_segment, primary_contact, notes, cn_number
        FROM clients WHERE CAST(id AS TEXT) IN (__IDS__)
    """)
    _hydrate("policy", "policies", """
        SELECT policy_uid, client_name, policy_type, carrier, policy_number,
               description, notes, project_name, client_id
        FROM v_policy_status WHERE policy_uid IN (__IDS__)
    """)
    _hydrate("activity", "activities", """
        SELECT a.id, a.activity_date, c.name AS client_name,
               a.activity_type, a.subject, a.details, a.contact_person
        FROM activity_log a
        LEFT JOIN clients c ON a.client_id = c.id
        WHERE CAST(a.id AS TEXT) IN (__IDS__)
        ORDER BY a.activity_date DESC
    """)
    _hydrate("issue", "issues", """
        SELECT a.id, a.issue_uid, a.subject, a.issue_status, a.issue_severity,
               a.activity_date, c.name AS client_name, p.policy_type
        FROM activity_log a
        LEFT JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE CAST(a.id AS TEXT) IN (__IDS__)
        ORDER BY
            CASE a.issue_severity
              WHEN 'Critical' THEN 0 WHEN 'High' THEN 1
              WHEN 'Normal' THEN 2 ELSE 3
            END, a.activity_date DESC
    """)
    _hydrate("contact", "contacts", """
        SELECT co.id, co.name, co.email, co.phone, co.mobile, co.organization
        FROM contacts co WHERE CAST(co.id AS TEXT) IN (__IDS__)
    """)
    _hydrate("program", "programs", """
        SELECT pg.program_uid, pg.name, c.name AS client_name,
               pg.line_of_business, pg.client_id
        FROM programs pg
        JOIN clients c ON c.id = pg.client_id
        WHERE pg.program_uid IN (__IDS__)
    """)
    _hydrate("meeting", "meetings", """
        SELECT m.id, m.title, c.name AS client_name, m.meeting_date,
               m.location, m.client_id, m.meeting_uid
        FROM client_meetings m
        JOIN clients c ON c.id = m.client_id
        WHERE CAST(m.id AS TEXT) IN (__IDS__)
        ORDER BY m.meeting_date DESC
    """)
    _hydrate("location", "locations", """
        SELECT pr.id, pr.name, pr.address, pr.city, pr.state, pr.zip,
               pr.client_id, c.name AS client_name
        FROM projects pr
        JOIN clients c ON c.id = pr.client_id
        WHERE CAST(pr.id AS TEXT) IN (__IDS__)
        ORDER BY pr.name
    """)
    _hydrate("inbox", "inbox", """
        SELECT i.id, i.inbox_uid, i.email_subject, i.email_from, i.content,
               i.created_at, i.status
        FROM inbox i WHERE CAST(i.id AS TEXT) IN (__IDS__)
        ORDER BY i.created_at DESC
    """)

    # --- Tier 2: Fuzzy fallback ---
    total_fts = sum(len(v) for v in results.values())
    query_mode = "fts5"

    if total_fts < 3:
        _fuzzy_types = {
            "clients": ("SELECT id, name FROM clients WHERE archived = 0", "name", "id"),
            "contacts": ("SELECT id, name || ' ' || COALESCE(email, '') || ' ' || COALESCE(organization, '') AS label FROM contacts", "label", "id"),
            "programs": ("SELECT program_uid AS id, name FROM programs WHERE archived = 0", "name", "id"),
        }
        for etype, (sql, name_col, id_col) in _fuzzy_types.items():
            if results.get(etype):
                continue  # Already have FTS5 results for this type
            candidates = {str(r[id_col]): r[name_col] for r in conn.execute(sql).fetchall()}
            if not candidates:
                continue
            matches = process.extract(query, candidates, scorer=fuzz.WRatio, score_cutoff=65, limit=10)
            if matches:
                matched_ids = [m[2] for m in matches]  # key = id
                existing_ids = {str(r.get("id", r.get("policy_uid", r.get("program_uid", "")))) for r in results.get(etype, [])}
                new_ids = [mid for mid in matched_ids if mid not in existing_ids]
                if new_ids and etype == "clients":
                    ph = ",".join("?" * len(new_ids))
                    fuzzy_rows = conn.execute(f"""
                        SELECT id, name, industry_segment, primary_contact, notes, cn_number
                        FROM clients WHERE CAST(id AS TEXT) IN ({ph})
                    """, new_ids).fetchall()
                    results["clients"].extend(dict(r) for r in fuzzy_rows)
                elif new_ids and etype == "contacts":
                    ph = ",".join("?" * len(new_ids))
                    fuzzy_rows = conn.execute(f"""
                        SELECT id, name, email, phone, mobile, organization
                        FROM contacts WHERE CAST(id AS TEXT) IN ({ph})
                    """, new_ids).fetchall()
                    results["contacts"].extend(dict(r) for r in fuzzy_rows)
                elif new_ids and etype == "programs":
                    ph = ",".join("?" * len(new_ids))
                    fuzzy_rows = conn.execute(f"""
                        SELECT pg.program_uid, pg.name, c.name AS client_name,
                               pg.line_of_business, pg.client_id
                        FROM programs pg
                        JOIN clients c ON c.id = pg.client_id
                        WHERE pg.program_uid IN ({ph})
                    """, new_ids).fetchall()
                    results["programs"].extend(dict(r) for r in fuzzy_rows)
                if new_ids:
                    query_mode = "fuzzy"

    results["_snippets"] = snippets
    results["_query_mode"] = query_mode
    return results


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


def get_review_queue(conn: sqlite3.Connection, client_id: int = 0) -> dict:
    """Return records needing review, split into policies, opportunities, and clients."""
    if client_id:
        all_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM v_review_queue WHERE client_id = ?", (client_id,)
        ).fetchall()]
    else:
        all_rows = [dict(r) for r in conn.execute("SELECT * FROM v_review_queue").fetchall()]
    policies = [r for r in all_rows if not r.get("is_opportunity")]
    opportunities = [r for r in all_rows if r.get("is_opportunity")]
    if client_id:
        clients = [dict(r) for r in conn.execute(
            "SELECT * FROM v_review_clients WHERE id = ?", (client_id,)
        ).fetchall()]
    else:
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

    # Also include sub-coverage entries in the matrix (guard for pre-migration DBs)
    try:
        sub_rows = conn.execute(
            f"SELECT sc.coverage_type AS policy_type, p.client_id, p.carrier "
            f"FROM policy_sub_coverages sc "
            f"JOIN policies p ON p.id = sc.policy_id "
            f"WHERE p.client_id IN ({placeholders}) "
            f"  AND p.archived = 0 "
            f"  AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)",
            member_ids,
        ).fetchall()
    except Exception:
        sub_rows = []
    for r in sub_rows:
        if r["policy_type"]:
            carrier = (r["carrier"] or "") + " [Pkg]"
            matrix[r["policy_type"]][r["client_id"]].append(carrier)

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

            # Skip dates too far in the future (safety net)
            max_horizon = cfg.get("mandated_activity_horizon_days", 180)
            if (target_date - today).days > max_horizon:
                continue

            # Respect prep_days: only fire when the prep window has opened.
            # For a milestone at -90d with 14d prep, fire_date = target - 14.
            # Activities with prep_days=0 fire when target_date <= today
            # (handled by the past-date check above).
            prep_days = rule.get("prep_days", 0)
            fire_date = target_date - timedelta(days=prep_days) if prep_days else target_date
            if fire_date > today:
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
            logger.info(
                "Mandated activity fired: %s / %s — target=%s fire_date=%s prep_days=%d",
                p["policy_uid"], rule_name, target_date.isoformat(),
                fire_date.isoformat(), prep_days,
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


# ─── FOLLOW-UP WORKLOAD BALANCER ─────────────────────────────────────────────


def get_week_followups(
    conn: sqlite3.Connection, week_start: str, pin_days: int = 14
) -> list[dict]:
    """Return all follow-ups for a Mon-Fri week (plus Sat/Sun bucketed into Monday).

    Each item includes a `pinned` flag based on renewal urgency.
    Items from Saturday/Sunday before the week are bucketed into Monday.
    Enriched with timeline_health, milestone_name, accountability, and due_for_review.
    """
    from datetime import date, timedelta
    mon = date.fromisoformat(week_start)
    # Include prior Sat/Sun so they show on Monday
    sat_before = (mon - timedelta(days=2)).isoformat()
    fri = (mon + timedelta(days=4)).isoformat()

    rows = conn.execute("""
        SELECT 'activity' AS source, a.id, a.subject, a.follow_up_date,
               a.activity_type, a.client_id, a.policy_id,
               c.name AS client_name,
               p.policy_type, p.carrier, p.expiration_date, p.renewal_status,
               CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
               p.policy_uid,
               a.disposition,
               COALESCE(th.timeline_health, '') AS timeline_health,
               th.next_milestone AS milestone_name
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        LEFT JOIN (
            SELECT policy_uid,
                MIN(CASE health WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                    WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4 ELSE 5 END) AS health_rank,
                CASE MIN(CASE health WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                    WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4 ELSE 5 END)
                    WHEN 1 THEN 'critical' WHEN 2 THEN 'at_risk'
                    WHEN 3 THEN 'compressed' WHEN 4 THEN 'drifting' ELSE 'on_track' END AS timeline_health,
                (SELECT pt2.milestone_name FROM policy_timeline pt2
                 WHERE pt2.policy_uid = policy_timeline.policy_uid AND pt2.completed_date IS NULL
                 ORDER BY pt2.projected_date LIMIT 1) AS next_milestone
            FROM policy_timeline
            WHERE completed_date IS NULL
            GROUP BY policy_uid
        ) th ON th.policy_uid = p.policy_uid
        WHERE a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
          AND a.follow_up_date BETWEEN ? AND ?

        UNION ALL

        SELECT 'policy' AS source, p.id, ('Renewal: ' || p.policy_type) AS subject,
               p.follow_up_date, 'Policy Reminder' AS activity_type,
               p.client_id, p.id AS policy_id,
               c.name AS client_name,
               p.policy_type, p.carrier, p.expiration_date, p.renewal_status,
               CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
               p.policy_uid,
               NULL AS disposition,
               COALESCE(th.timeline_health, '') AS timeline_health,
               th.next_milestone AS milestone_name
        FROM policies p
        JOIN clients c ON p.client_id = c.id
        LEFT JOIN (
            SELECT policy_uid,
                MIN(CASE health WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                    WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4 ELSE 5 END) AS health_rank,
                CASE MIN(CASE health WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                    WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4 ELSE 5 END)
                    WHEN 1 THEN 'critical' WHEN 2 THEN 'at_risk'
                    WHEN 3 THEN 'compressed' WHEN 4 THEN 'drifting' ELSE 'on_track' END AS timeline_health,
                (SELECT pt2.milestone_name FROM policy_timeline pt2
                 WHERE pt2.policy_uid = policy_timeline.policy_uid AND pt2.completed_date IS NULL
                 ORDER BY pt2.projected_date LIMIT 1) AS next_milestone
            FROM policy_timeline
            WHERE completed_date IS NULL
            GROUP BY policy_uid
        ) th ON th.policy_uid = p.policy_uid
        WHERE p.follow_up_date IS NOT NULL
          AND p.follow_up_date BETWEEN ? AND ?
          AND p.archived = 0
          AND NOT EXISTS (
              SELECT 1 FROM activity_log a2
              WHERE a2.policy_id = p.id AND a2.follow_up_done = 0
              AND a2.follow_up_date IS NOT NULL
              AND a2.follow_up_date BETWEEN ? AND ?
              AND a2.follow_up_date <= p.follow_up_date
          )

        UNION ALL

        SELECT 'client' AS source, c.id, ('Client Follow-Up: ' || c.name) AS subject,
               c.follow_up_date, 'Client Reminder' AS activity_type,
               c.id AS client_id, NULL AS policy_id,
               c.name AS client_name,
               NULL AS policy_type, NULL AS carrier, NULL AS expiration_date,
               NULL AS renewal_status, NULL AS days_to_renewal,
               NULL AS policy_uid, NULL AS disposition,
               '' AS timeline_health, NULL AS milestone_name
        FROM clients c
        WHERE c.follow_up_date IS NOT NULL AND c.archived = 0
          AND c.follow_up_date BETWEEN ? AND ?

        ORDER BY 4
    """, (sat_before, fri, sat_before, fri, sat_before, fri, sat_before, fri)).fetchall()

    # Build accountability map from config dispositions
    disp_map = {
        d["label"]: d.get("accountability", "my_action")
        for d in cfg.get("follow_up_dispositions", [])
    }

    # Build set of policy_uids due for review
    try:
        review_uids = set(
            r["policy_uid"]
            for r in conn.execute("SELECT policy_uid FROM v_review_queue").fetchall()
        )
    except Exception:
        review_uids = set()

    items = []
    for r in rows:
        d = dict(r)
        fu_date = d["follow_up_date"]
        # Bucket Sat/Sun into Monday
        try:
            fu = date.fromisoformat(fu_date)
            if fu.weekday() >= 5:  # Saturday=5, Sunday=6
                d["follow_up_date"] = mon.isoformat()
                d["bucketed_from"] = fu_date
        except (ValueError, TypeError):
            pass
        # Pin logic — based on renewal urgency or critical/at_risk timeline
        dtr = d.get("days_to_renewal")
        status = d.get("renewal_status") or ""
        d["pinned"] = bool(
            (dtr is not None and dtr <= pin_days)
            or status.upper() in ("EXPIRED",)
            or d.get("timeline_health") in ("critical", "at_risk")
        )
        # Accountability from disposition
        d["accountability"] = disp_map.get(d.get("disposition") or "", "my_action")
        # Review status
        d["due_for_review"] = (d.get("policy_uid") or "") in review_uids
        # Composite ID for reschedule (matches bulk-reschedule pattern)
        d["composite_id"] = f"{d['source']}-{d['id']}"
        items.append(d)
    return items


def get_overdue_for_plan_week(
    conn: sqlite3.Connection, week_start: str, pin_days: int = 14
) -> list[dict]:
    """Return all overdue follow-ups (before week_start) for Plan Week backlog.

    Sorted by expiration urgency (closest expiration first).
    Items near expiration are marked as pinned (can't be pushed later).
    """
    from datetime import date

    # Cut-off: anything with follow_up_date < week_start
    cutoff = week_start

    rows = conn.execute("""
        SELECT 'activity' AS source, a.id, a.subject, a.follow_up_date,
               a.activity_type, a.client_id, a.policy_id,
               c.name AS client_name,
               p.policy_type, p.carrier, p.expiration_date, p.renewal_status,
               CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
               p.policy_uid, a.disposition
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
          AND a.follow_up_date < ?

        UNION ALL

        SELECT 'policy' AS source, p.id, ('Renewal: ' || p.policy_type) AS subject,
               p.follow_up_date, 'Policy Reminder' AS activity_type,
               p.client_id, p.id AS policy_id,
               c.name AS client_name,
               p.policy_type, p.carrier, p.expiration_date, p.renewal_status,
               CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
               p.policy_uid, NULL AS disposition
        FROM policies p
        JOIN clients c ON p.client_id = c.id
        WHERE p.follow_up_date IS NOT NULL
          AND p.follow_up_date < ?
          AND p.archived = 0
          AND NOT EXISTS (
              SELECT 1 FROM activity_log a2
              WHERE a2.policy_id = p.id AND a2.follow_up_done = 0
              AND a2.follow_up_date IS NOT NULL
              AND a2.follow_up_date <= p.follow_up_date
          )

        UNION ALL

        SELECT 'client' AS source, c.id, ('Client Follow-Up: ' || c.name) AS subject,
               c.follow_up_date, 'Client Reminder' AS activity_type,
               c.id AS client_id, NULL AS policy_id,
               c.name AS client_name,
               NULL AS policy_type, NULL AS carrier, NULL AS expiration_date,
               NULL AS renewal_status, NULL AS days_to_renewal,
               NULL AS policy_uid, NULL AS disposition
        FROM clients c
        WHERE c.follow_up_date IS NOT NULL AND c.archived = 0
          AND c.follow_up_date < ?

        ORDER BY 4 ASC
    """, (cutoff, cutoff, cutoff)).fetchall()

    disp_map = {
        d["label"]: d.get("accountability", "my_action")
        for d in cfg.get("follow_up_dispositions", [])
    }

    items = []
    today = date.today()
    for r in rows:
        d = dict(r)
        dtr = d.get("days_to_renewal")
        d["pinned"] = bool(dtr is not None and dtr <= pin_days)
        d["accountability"] = disp_map.get(d.get("disposition") or "", "my_action")
        d["composite_id"] = f"{d['source']}-{d['id']}"
        try:
            fu = date.fromisoformat(d["follow_up_date"])
            d["days_overdue"] = (today - fu).days
        except (ValueError, TypeError):
            d["days_overdue"] = 0
        items.append(d)
    # Sort by expiration urgency (closest expiration first), then follow_up_date
    items.sort(key=lambda i: (i.get("expiration_date") or "9999-12-31", i.get("follow_up_date") or ""))
    return items


def _weighted_load(day_items: list[dict]) -> float:
    """Calculate weighted load for a day's follow-ups.

    First item per client = 1.0 (full context switch).
    Each additional item for the same client = 0.25 (incremental work).
    """
    from collections import Counter
    client_counts = Counter(i.get("client_id") for i in day_items)
    load = 0.0
    for count in client_counts.values():
        load += 1.0 + (count - 1) * 0.25
    return load


def spread_followups(
    items: list[dict], daily_target: int, week_days: list[str],
    overdue_items: list[dict] | None = None, buffer_days: int = 3,
) -> list[dict]:
    """Compute proposed redistribution of follow-ups across the week.

    Returns list of {composite_id, old_date, new_date} for items that should move.
    Only moves non-pinned items from days exceeding daily_target (weighted load).
    Fills lightest days first using weighted client-aware load.

    If overdue_items is provided, non-pinned overdue items are also distributed
    into the week with expiration-aware priority (closest expiration -> earliest day).

    Weighting: first item per client = 1.0, additional same-client items = 0.25.
    This means 5 follow-ups for one client = 2.0 weighted load, while 5 follow-ups
    across 5 clients = 5.0 weighted load.
    """
    from collections import defaultdict
    from datetime import date, timedelta

    from policydb.utils import cap_followup_date

    # Group by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_date[item["follow_up_date"]].append(item)

    # Ensure all week days are in the map
    for d in week_days:
        by_date.setdefault(d, [])

    # Identify overloaded days and collect movable items
    movable_pool: list[dict] = []
    for d in week_days:
        day_items = by_date[d]
        load = _weighted_load(day_items)
        if load > daily_target:
            # Collect non-pinned items from this day (keep pinned in place)
            movable = [i for i in day_items if not i.get("pinned")]
            # Move items until weighted load is at or below target
            # Prefer moving items that are the only one for their client (higher weight savings)
            from collections import Counter
            client_counts = Counter(i.get("client_id") for i in day_items)
            # Sort movable: items from clients with fewer items first (moving them saves more weight)
            movable.sort(key=lambda i: client_counts.get(i.get("client_id"), 0))
            for item in movable:
                if _weighted_load(by_date[d]) <= daily_target:
                    break
                by_date[d].remove(item)
                movable_pool.append(item)

    # Add overdue non-pinned items to the movable pool
    overdue_movable: list[dict] = []
    if overdue_items:
        for item in overdue_items:
            if not item.get("pinned"):
                overdue_movable.append(item)

    # Sort overdue by expiration urgency — closest expiration first
    overdue_movable.sort(key=lambda i: i.get("expiration_date") or "9999-12-31")

    # Assign each movable item to the lightest day (by weighted load)
    proposals: list[dict] = []

    # First: distribute overdue items (higher priority)
    for item in overdue_movable:
        # Find lightest day, but respect expiration cap
        exp_date = item.get("expiration_date") or ""
        eligible_days = week_days[:]
        if exp_date:
            cap_date, _ = cap_followup_date(week_days[-1], exp_date, buffer_days)
            eligible_days = [d for d in week_days if d <= cap_date]
        if not eligible_days:
            eligible_days = [week_days[0]]  # Force to Monday if all days past cap

        lightest_day = min(eligible_days, key=lambda d: _weighted_load(by_date[d]))
        by_date[lightest_day].append(item)
        proposals.append({
            "composite_id": item["composite_id"],
            "old_date": item.get("follow_up_date", ""),
            "new_date": lightest_day,
            "subject": item.get("subject", ""),
            "client_name": item.get("client_name", ""),
            "from_backlog": True,
        })

    # Then: redistribute overloaded week items
    for item in movable_pool:
        lightest_day = min(week_days, key=lambda d: _weighted_load(by_date[d]))
        by_date[lightest_day].append(item)
        proposals.append({
            "composite_id": item["composite_id"],
            "old_date": item["follow_up_date"],
            "new_date": lightest_day,
            "subject": item.get("subject", ""),
            "client_name": item.get("client_name", ""),
        })

    return proposals


# ─── EXPOSURE QUERIES ────────────────────────────────────────────────────────

def get_client_exposures(
    conn: sqlite3.Connection,
    client_id: int,
    year: int,
    project_id: Optional[int] = None,
) -> list[dict]:
    """Return exposures for a client/year with prior year values joined."""
    if project_id is None:
        rows = conn.execute(
            """
            SELECT e.*, prior.amount AS prior_amount
            FROM client_exposures e
            LEFT JOIN client_exposures prior
                ON prior.client_id = e.client_id
                AND prior.exposure_type = e.exposure_type
                AND prior.year = e.year - 1
                AND prior.project_id IS NULL
            WHERE e.client_id = ? AND e.year = ? AND e.project_id IS NULL
            ORDER BY e.is_custom, e.exposure_type
            """,
            (client_id, year),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT e.*, prior.amount AS prior_amount
            FROM client_exposures e
            LEFT JOIN client_exposures prior
                ON prior.client_id = e.client_id
                AND prior.project_id = e.project_id
                AND prior.exposure_type = e.exposure_type
                AND prior.year = e.year - 1
            WHERE e.client_id = ? AND e.year = ? AND e.project_id = ?
            ORDER BY e.is_custom, e.exposure_type
            """,
            (client_id, year, project_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_exposure_years(
    conn: sqlite3.Connection,
    client_id: int,
    project_id: Optional[int] = None,
) -> list[int]:
    """Return distinct years with exposure data, sorted descending."""
    if project_id is None:
        rows = conn.execute(
            "SELECT DISTINCT year FROM client_exposures WHERE client_id = ? AND project_id IS NULL ORDER BY year DESC",
            (client_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT year FROM client_exposures WHERE client_id = ? AND project_id = ? ORDER BY year DESC",
            (client_id, project_id),
        ).fetchall()
    return [r["year"] for r in rows]


def get_distinct_custom_exposure_types(conn: sqlite3.Connection) -> list[str]:
    """Return distinct custom exposure types used across all clients."""
    rows = conn.execute(
        "SELECT DISTINCT exposure_type FROM client_exposures WHERE is_custom = 1 ORDER BY exposure_type"
    ).fetchall()
    return [r["exposure_type"] for r in rows]


def get_exposure_observations(
    conn: sqlite3.Connection,
    client_id: int,
    year: int,
    project_id: Optional[int] = None,
) -> list[dict]:
    """Return YoY exposure changes sorted by absolute % change for observations panel."""
    exposures = get_client_exposures(conn, client_id, year, project_id)
    observations = []
    for e in exposures:
        current = e.get("amount")
        prior = e.get("prior_amount")
        if current is None or prior is None or prior == 0:
            continue
        pct_change = ((current - prior) / prior) * 100
        observations.append({
            "exposure_type": e["exposure_type"],
            "current": current,
            "prior": prior,
            "pct_change": round(pct_change, 1),
            "direction": "up" if pct_change > 0 else "down",
            "unit": e["unit"],
            "notes": e.get("notes") or "",
        })
    observations.sort(key=lambda o: abs(o["pct_change"]), reverse=True)
    return observations


def get_exposure_by_id(conn: sqlite3.Connection, exposure_id: int) -> Optional[dict]:
    """Return a single exposure row by ID."""
    row = conn.execute(
        "SELECT * FROM client_exposures WHERE id = ?", (exposure_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Schematic completeness
# ---------------------------------------------------------------------------

def get_schematic_completeness(
    conn: sqlite3.Connection, client_id: int
) -> list[dict]:
    """Per-tower_group completeness scoring for schematic data.

    Returns list of dicts:
      {tower_group, underlying_count, excess_count, pct_complete, missing_fields}
    """
    rows = conn.execute(
        """
        SELECT id, tower_group, layer_position, policy_type, carrier,
               deductible, limit_amount, attachment_point, is_program
        FROM policies
        WHERE client_id = ? AND tower_group IS NOT NULL AND tower_group != ''
          AND archived = 0 AND (is_opportunity = 0 OR is_opportunity IS NULL)
        """,
        (client_id,),
    ).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        tg = r["tower_group"]
        groups.setdefault(tg, []).append(dict(r))

    result = []
    for tg, policies in sorted(groups.items()):
        filled = 0
        total = 0
        missing: list[str] = []
        u_count = 0
        x_count = 0

        for p in policies:
            lp = (p.get("layer_position") or "Primary").strip().lower()
            is_umb = "umbrella" in lp

            if lp == "primary" or (not is_umb and (p.get("attachment_point") or 0) == 0 and lp not in ("excess",)):
                # Underlying: check policy_type, carrier, deductible
                u_count += 1
                checks = [
                    ("policy_type", bool(p.get("policy_type"))),
                    ("carrier", bool(p.get("carrier"))),
                    ("deductible", p.get("deductible") is not None),
                ]
                for label, ok in checks:
                    total += 1
                    if ok:
                        filled += 1
                    else:
                        missing.append(f"{p.get('policy_type') or 'Line'}: {label}")
            elif is_umb:
                # Umbrella: check carrier, limit
                x_count += 1
                checks = [
                    ("carrier", bool(p.get("carrier"))),
                    ("limit", bool(p.get("limit_amount") and p["limit_amount"] > 0)),
                ]
                for label, ok in checks:
                    total += 1
                    if ok:
                        filled += 1
                    else:
                        missing.append(f"Umbrella: {label}")
            else:
                # Excess: check carrier, limit, attachment_point
                x_count += 1
                checks = [
                    ("carrier", bool(p.get("carrier"))),
                    ("limit", bool(p.get("limit_amount") and p["limit_amount"] > 0)),
                    ("attachment_point", bool(p.get("attachment_point") and p["attachment_point"] > 0)),
                ]
                for label, ok in checks:
                    total += 1
                    if ok:
                        filled += 1
                    else:
                        missing.append(f"Layer: {label}")

        pct = round(filled / total * 100) if total > 0 else 0
        result.append({
            "tower_group": tg,
            "underlying_count": u_count,
            "excess_count": x_count,
            "pct_complete": pct,
            "missing_fields": missing,
        })

    return result


# ─── SUB-COVERAGE HELPERS ────────────────────────────────────────────────────

def get_sub_coverages(conn, policy_id: int) -> list[dict]:
    """Return sub-coverages for a policy, ordered by sort_order."""
    try:
        rows = conn.execute(
            "SELECT id, coverage_type, sort_order, limit_amount, deductible, "
            "coverage_form, notes, attachment_point, premium, carrier, "
            "policy_number, participation_of, layer_position, description "
            "FROM policy_sub_coverages WHERE policy_id = ? ORDER BY sort_order, id",
            (policy_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_sub_coverages_by_policy_id(conn, policy_ids: list[int]) -> dict[int, list[str]]:
    """Return {policy_id: [coverage_type, ...]} for policies with sub-coverages."""
    if not policy_ids:
        return {}
    try:
        placeholders = ",".join("?" * len(policy_ids))
        rows = conn.execute(
            f"SELECT policy_id, coverage_type FROM policy_sub_coverages "  # noqa: S608
            f"WHERE policy_id IN ({placeholders}) ORDER BY sort_order, id",
            policy_ids,
        ).fetchall()
        result: dict[int, list[str]] = {}
        for r in rows:
            result.setdefault(r["policy_id"], []).append(r["coverage_type"])
        return result
    except Exception:
        return {}


def get_sub_coverages_full_by_policy_id(conn, policy_ids: list[int]) -> dict[int, list[dict]]:
    """Return {policy_id: [{coverage_type, limit_amount, deductible, ...}, ...]}."""
    if not policy_ids:
        return {}
    try:
        placeholders = ",".join("?" * len(policy_ids))
        rows = conn.execute(
            f"SELECT id, policy_id, coverage_type, sort_order, limit_amount, deductible, "  # noqa: S608
            f"coverage_form, notes, attachment_point, premium, carrier, "
            f"policy_number, participation_of, layer_position, description "
            f"FROM policy_sub_coverages WHERE policy_id IN ({placeholders}) ORDER BY sort_order, id",
            policy_ids,
        ).fetchall()
        result: dict[int, list[dict]] = {}
        for r in rows:
            result.setdefault(r["policy_id"], []).append(dict(r))
        return result
    except Exception:
        return {}


def auto_generate_sub_coverages(conn, policy_id: int, policy_type: str):
    """Insert auto-sub-coverages based on config mapping. Skips duplicates."""
    auto_map = cfg.get("auto_sub_coverages", {})
    sub_types = auto_map.get(policy_type, [])
    for i, ctype in enumerate(sub_types):
        conn.execute(
            "INSERT OR IGNORE INTO policy_sub_coverages (policy_id, coverage_type, sort_order) "
            "VALUES (?, ?, ?)",
            (policy_id, ctype, i),
        )
    if sub_types:
        conn.commit()


# ─── PROGRAM QUERIES (v2 — standalone programs table) ────────────────────────


def get_program_by_uid(conn: sqlite3.Connection, program_uid: str) -> dict | None:
    """Return a program dict by its UID, or None if not found."""
    row = conn.execute(
        """SELECT pg.*, pr.name AS project_name,
                  pr.address AS project_address, pr.city AS project_city,
                  pr.state AS project_state, pr.zip AS project_zip
           FROM programs pg
           LEFT JOIN projects pr ON pg.project_id = pr.id
           WHERE pg.program_uid = ?""",
        (program_uid,),
    ).fetchone()
    return dict(row) if row else None


def get_program_child_policies(conn: sqlite3.Connection, program_id: int) -> list[dict]:
    """Return child policies for a program via program_id FK."""
    rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
                  p.premium, p.limit_amount, p.deductible, p.layer_position,
                  p.renewal_status, p.effective_date, p.expiration_date,
                  p.attachment_point, p.participation_of, p.coverage_form,
                  p.schematic_column
           FROM policies p
           WHERE p.program_id = ?
             AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           ORDER BY p.layer_position, p.policy_type""",
        (program_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_program_aggregates(conn: sqlite3.Connection, program_id: int) -> dict:
    """Compute aggregate stats for a program from its child policies."""
    row = conn.execute(
        """SELECT COUNT(*) AS policy_count,
                  COUNT(DISTINCT carrier) AS carrier_count,
                  COALESCE(SUM(premium), 0) AS total_premium,
                  COALESCE(MAX(limit_amount), 0) AS max_limit
           FROM policies
           WHERE program_id = ? AND archived = 0
             AND (is_opportunity = 0 OR is_opportunity IS NULL)""",
        (program_id,),
    ).fetchone()
    return dict(row) if row else {
        "policy_count": 0, "carrier_count": 0, "total_premium": 0, "max_limit": 0
    }


def get_programs_for_client(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Return all programs for a client with aggregated stats, including project/location info."""
    rows = conn.execute(
        """SELECT pg.*, pr.name AS project_name
           FROM programs pg
           LEFT JOIN projects pr ON pg.project_id = pr.id
           WHERE pg.client_id = ? AND pg.archived = 0
           ORDER BY pr.name NULLS LAST, pg.name""",
        (client_id,),
    ).fetchall()
    programs = []
    for r in rows:
        pgm = dict(r)
        agg = get_program_aggregates(conn, pgm["id"])
        pgm.update(agg)
        programs.append(pgm)
    return programs


def get_programs_for_project(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """Return all programs linked to a specific project/location with aggregated stats."""
    rows = conn.execute(
        "SELECT * FROM programs WHERE project_id = ? AND archived = 0 ORDER BY name",
        (project_id,),
    ).fetchall()
    programs = []
    for r in rows:
        pgm = dict(r)
        agg = get_program_aggregates(conn, pgm["id"])
        pgm.update(agg)
        programs.append(pgm)
    return programs


def get_unassigned_policies(conn: sqlite3.Connection, client_id: int, exclude_program_id: int | None = None) -> list[dict]:
    """Return active policies not assigned to any program (or assigned to archived/missing programs)."""
    rows = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier, p.premium, p.limit_amount,
                  p.program_id, p.policy_number, p.effective_date, p.expiration_date,
                  p.renewal_status, p.deductible,
                  pr.name AS project_name
           FROM policies p
           LEFT JOIN projects pr ON p.project_id = pr.id
           WHERE p.client_id = ? AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
             AND (
                 p.program_id IS NULL
                 OR NOT EXISTS (SELECT 1 FROM programs pg WHERE pg.id = p.program_id AND pg.archived = 0)
             )
           ORDER BY p.policy_type""",
        (client_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_program_timeline_milestones(conn: sqlite3.Connection, program_id: int) -> list[dict]:
    """Return timeline milestones for all child policies of a program."""
    try:
        rows = conn.execute(
            """SELECT pt.policy_uid, pt.milestone_name, pt.ideal_date,
                      pt.projected_date, pt.completed_date, pt.health,
                      pt.accountability, pt.waiting_on,
                      p.policy_type, p.carrier
               FROM policy_timeline pt
               JOIN policies p ON p.policy_uid = pt.policy_uid
               WHERE p.program_id = ?
               ORDER BY pt.ideal_date""",
            (program_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_program_activities(conn: sqlite3.Connection, program_id: int, limit: int = 50) -> list[dict]:
    """Return recent activities from program itself AND all child policies."""
    rows = conn.execute(
        """SELECT a.id, a.activity_type, a.subject, a.details, a.contact_person,
                  a.created_at, a.follow_up_date, a.disposition,
                  p.policy_type, p.carrier, p.policy_uid
           FROM activity_log a
           JOIN policies p ON p.id = a.policy_id
           WHERE p.program_id = ?

           UNION ALL

           SELECT a.id, a.activity_type, a.subject, a.details, a.contact_person,
                  a.created_at, a.follow_up_date, a.disposition,
                  NULL AS policy_type, NULL AS carrier, NULL AS policy_uid
           FROM activity_log a
           WHERE a.program_id = ? AND a.policy_id IS NULL

           ORDER BY created_at DESC
           LIMIT ?""",
        (program_id, program_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── ISSUE / KANBAN BOARD QUERIES ────────────────────────────────────────────


def get_client_activity_board(
    conn: sqlite3.Connection,
    days: Optional[int] = None,
    activity_type: Optional[str] = None,
    q: Optional[str] = None,
    client_id: Optional[int] = None,
) -> list[dict]:
    """Return activities grouped by client with issue nesting for a kanban board view.

    Each client dict contains:
      client_id, client_name, cn_number, activity_count, total_hours,
      has_issues, issues (sorted by severity then date), untracked (activities
      not linked to any issue).
    """
    from collections import defaultdict

    # ── 1. Fetch activities in the date window (exclude issue header rows) ──
    sql = """
        SELECT a.id, a.subject, a.activity_date, a.activity_type, a.duration_hours,
               a.follow_up_date, a.disposition, a.contact_person, a.details,
               a.issue_id, a.item_kind,
               c.id AS client_id, c.name AS client_name, c.cn_number,
               p.id AS policy_id, p.policy_uid, p.policy_type
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE (a.item_kind = 'followup' OR a.item_kind IS NULL)
    """
    params: list = []

    if client_id is not None:
        sql += " AND a.client_id = ?"
        params.append(client_id)
    if days is not None:
        sql += " AND a.activity_date >= date('now', ?)"
        params.append(f"-{days - 1} days")
    if activity_type:
        sql += " AND a.activity_type = ?"
        params.append(activity_type)
    if q:
        sql += " AND a.subject LIKE ?"
        params.append(f"%{q}%")

    sql += " ORDER BY a.activity_date DESC, a.id DESC"

    activity_rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    if not activity_rows:
        return []

    # ── 2. Collect distinct client_ids from the result ──
    client_ids_in_result = list({r["client_id"] for r in activity_rows})
    ph = ",".join("?" * len(client_ids_in_result))

    # ── 3. Fetch open issues for those clients ──
    issue_rows = conn.execute(
        f"""
        SELECT a.id, a.issue_uid, a.subject, a.issue_severity, a.issue_status,
               a.issue_sla_days, a.client_id, a.policy_id,
               p.policy_uid, p.policy_type,
               CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open
        FROM activity_log a
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.item_kind = 'issue'
          AND a.issue_status NOT IN ('Resolved', 'Closed')
          AND a.client_id IN ({ph})
        ORDER BY
            CASE a.issue_severity
                WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                WHEN 'Normal' THEN 3 WHEN 'Low' THEN 4 ELSE 5
            END,
            a.activity_date ASC
        """,
        client_ids_in_result,
    ).fetchall()
    issue_map: dict[int, dict] = {}  # issue_id → issue dict
    issues_by_client: dict[int, list[dict]] = defaultdict(list)
    for row in issue_rows:
        d = dict(row)
        d["activities"] = []
        issue_map[d["id"]] = d
        issues_by_client[d["client_id"]].append(d)

    # ── 4. Group activities into issue buckets or untracked ──
    # Bucket: activity.issue_id → the issue that owns it
    untracked_by_client: dict[int, list[dict]] = defaultdict(list)
    for act in activity_rows:
        issue_id = act.get("issue_id")
        if issue_id and issue_id in issue_map:
            issue_map[issue_id]["activities"].append(act)
        else:
            untracked_by_client[act["client_id"]].append(act)

    # ── 5. Build per-client stats ──
    hours_by_client: dict[int, float] = defaultdict(float)
    count_by_client: dict[int, int] = defaultdict(int)
    last_activity_by_client: dict[int, str] = {}
    client_meta: dict[int, dict] = {}

    for act in activity_rows:
        cid = act["client_id"]
        count_by_client[cid] += 1
        hours_by_client[cid] += float(act.get("duration_hours") or 0)
        act_date = act.get("activity_date") or ""
        if cid not in last_activity_by_client or act_date > last_activity_by_client[cid]:
            last_activity_by_client[cid] = act_date
        if cid not in client_meta:
            client_meta[cid] = {
                "client_id": cid,
                "client_name": act["client_name"],
                "cn_number": act["cn_number"],
            }

    # ── 6. Assemble result sorted: clients with issues first, then by most recent activity desc ──
    result = []
    for cid, meta in client_meta.items():
        client_issues = issues_by_client.get(cid, [])
        result.append({
            "client_id": cid,
            "client_name": meta["client_name"],
            "cn_number": meta["cn_number"],
            "activity_count": count_by_client[cid],
            "total_hours": round(hours_by_client[cid], 2),
            "has_issues": bool(client_issues),
            "issues": client_issues,
            "untracked": untracked_by_client.get(cid, []),
            "_last_activity": last_activity_by_client.get(cid, ""),
        })

    # Sort: clients with issues first (0 < 1), then by most recent activity date descending.
    # For ISO date strings, lexicographic inversion: replace digits with (9 - digit) to reverse sort order.
    result.sort(
        key=lambda c: (
            0 if c["has_issues"] else 1,
            "" if not c["_last_activity"] else "".join(
                str(9 - int(ch)) if ch.isdigit() else ch
                for ch in c["_last_activity"]
            ),
        )
    )

    # Remove internal sort key before returning
    for c in result:
        del c["_last_activity"]

    return result


def get_escalation_suggestions(conn: sqlite3.Connection) -> list[dict]:
    """Return escalation suggestions from 4 trigger types.

    Trigger types:
      stale_followups   — 2+ follow-ups overdue > stale_threshold_days for same client
      timeline_drift    — policy_timeline milestones health='at_risk' or 'critical'
      nudge_escalation  — 3+ waiting_external activities on same policy in last 90 days
      critical_renewal  — CRITICAL tier from get_escalation_alerts()

    Skips policies with an existing open issue. Skips dismissed suggestions
    unless a newer activity/follow-up exists on that policy since dismissal.
    Returns sorted by severity (Critical first) then title.
    """
    from collections import defaultdict

    _SEVERITY_ORDER = {"Critical": 0, "High": 1, "Normal": 2, "Low": 3}

    stale_threshold = cfg.get("stale_threshold_days", 14)
    excluded_statuses = cfg.get("renewal_statuses_excluded", [])

    # ── Build set of policy_ids that already have an open issue ──
    open_issue_policy_ids: set[int] = set()
    try:
        rows = conn.execute(
            """SELECT DISTINCT policy_id FROM activity_log
               WHERE item_kind = 'issue'
                 AND issue_status NOT IN ('Resolved', 'Closed')
                 AND policy_id IS NOT NULL"""
        ).fetchall()
        open_issue_policy_ids = {r["policy_id"] for r in rows}
    except Exception:
        pass

    # ── Load dismissals: {(policy_id, trigger_type): dismissed_at} ──
    dismissals: dict[tuple, str] = {}
    try:
        d_rows = conn.execute(
            "SELECT policy_id, trigger_type, dismissed_at FROM escalation_dismissals"
        ).fetchall()
        for dr in d_rows:
            dismissals[(dr["policy_id"], dr["trigger_type"])] = dr["dismissed_at"]
    except Exception:
        pass

    # ── Helper: check if a dismissal is still active (no newer activity since dismissed_at) ──
    def _is_dismissed(policy_id: Optional[int], trigger_type: str) -> bool:
        if policy_id is None:
            return False
        dismissed_at = dismissals.get((policy_id, trigger_type))
        if not dismissed_at:
            return False
        # Check if there's any activity or follow-up newer than the dismissal
        row = conn.execute(
            """SELECT 1 FROM activity_log
               WHERE policy_id = ?
                 AND (activity_date > ? OR follow_up_date > ?)
               LIMIT 1""",
            (policy_id, dismissed_at[:10], dismissed_at[:10]),
        ).fetchone()
        if row:
            return False  # Dismissal reset by newer activity
        return True

    suggestions: list[dict] = []

    # ── Trigger 1: Stale follow-ups ──
    # Follow-ups overdue > stale_threshold_days, grouped by client
    # Only suggest when 2+ stale items for same client
    try:
        stale_rows = conn.execute(
            """SELECT a.client_id, c.name AS client_name, a.policy_id,
                      COUNT(*) AS stale_count,
                      GROUP_CONCAT(a.id) AS activity_ids,
                      MAX(a.follow_up_date) AS latest_due
               FROM activity_log a
               JOIN clients c ON a.client_id = c.id
               WHERE a.follow_up_done = 0
                 AND a.follow_up_date IS NOT NULL
                 AND a.follow_up_date < date('now', ?)
                 AND (a.item_kind = 'followup' OR a.item_kind IS NULL)
               GROUP BY a.client_id
               HAVING COUNT(*) >= 2""",
            (f"-{stale_threshold} days",),
        ).fetchall()
        for row in stale_rows:
            policy_id = row["policy_id"]
            if policy_id and policy_id in open_issue_policy_ids:
                continue
            if _is_dismissed(policy_id, "stale_followups"):
                continue
            act_ids = [int(x) for x in (row["activity_ids"] or "").split(",") if x.strip().isdigit()]
            suggestions.append({
                "trigger_type": "stale_followups",
                "severity_preset": "High",
                "icon": "stale",
                "client_id": row["client_id"],
                "client_name": row["client_name"],
                "policy_id": policy_id,
                "title": f"{row['stale_count']} stale follow-ups — {row['client_name']}",
                "detail": f"{row['stale_count']} follow-ups overdue more than {stale_threshold} days",
                "source_activity_ids": act_ids,
            })
    except Exception as e:
        logger.warning("get_escalation_suggestions stale_followups error: %s", e)

    # ── Trigger 2: Timeline drift ──
    try:
        drift_rows = conn.execute(
            """SELECT pt.policy_uid, pt.milestone_name, pt.health,
                      pt.projected_date, pt.ideal_date,
                      p.id AS policy_id, p.client_id, p.policy_type,
                      c.name AS client_name
               FROM policy_timeline pt
               JOIN policies p ON p.policy_uid = pt.policy_uid
               JOIN clients c ON c.id = p.client_id
               WHERE pt.health IN ('at_risk', 'critical')
                 AND pt.completed_date IS NULL
                 AND (pt.acknowledged IS NULL OR pt.acknowledged = 0)
                 AND p.archived = 0
               ORDER BY
                 CASE pt.health WHEN 'critical' THEN 1 ELSE 2 END,
                 pt.projected_date ASC"""
        ).fetchall()
        for row in drift_rows:
            policy_id = row["policy_id"]
            if policy_id in open_issue_policy_ids:
                continue
            trigger = "timeline_drift"
            if _is_dismissed(policy_id, trigger):
                continue
            severity = "Critical" if row["health"] == "critical" else "High"
            drift_days = 0
            try:
                from datetime import date as _date
                proj = _date.fromisoformat(row["projected_date"]) if row["projected_date"] else None
                ideal = _date.fromisoformat(row["ideal_date"]) if row["ideal_date"] else None
                if proj and ideal:
                    drift_days = (proj - ideal).days
            except (ValueError, TypeError):
                pass
            suggestions.append({
                "trigger_type": trigger,
                "severity_preset": severity,
                "icon": "drift",
                "client_id": row["client_id"],
                "client_name": row["client_name"],
                "policy_id": policy_id,
                "title": f"{row['milestone_name']} {row['health'].replace('_', ' ')} — {row['client_name']}",
                "detail": f"{row['policy_type']} milestone '{row['milestone_name']}' drifted {drift_days}d past ideal",
                "source_activity_ids": [],
            })
    except Exception as e:
        logger.warning("get_escalation_suggestions timeline_drift error: %s", e)

    # ── Trigger 3: Nudge escalation ──
    # 3+ waiting_external disposition activities on same policy in last 90 days
    waiting_external_labels: list[str] = [
        d["label"]
        for d in cfg.get("follow_up_dispositions", [])
        if d.get("accountability") == "waiting_external"
    ]
    if waiting_external_labels:
        try:
            ph_we = ",".join("?" * len(waiting_external_labels))
            nudge_rows = conn.execute(
                f"""SELECT a.policy_id, p.client_id, c.name AS client_name,
                           p.policy_type,
                           COUNT(*) AS nudge_count,
                           GROUP_CONCAT(a.id) AS activity_ids,
                           MAX(a.activity_date) AS latest_date
                    FROM activity_log a
                    JOIN policies p ON p.id = a.policy_id
                    JOIN clients c ON c.id = p.client_id
                    WHERE a.disposition IN ({ph_we})
                      AND a.activity_date >= date('now', '-90 days')
                      AND (a.item_kind = 'followup' OR a.item_kind IS NULL)
                      AND a.policy_id IS NOT NULL
                    GROUP BY a.policy_id
                    HAVING COUNT(*) >= 3""",
                waiting_external_labels,
            ).fetchall()
            for row in nudge_rows:
                policy_id = row["policy_id"]
                if policy_id in open_issue_policy_ids:
                    continue
                if _is_dismissed(policy_id, "nudge_escalation"):
                    continue
                act_ids = [int(x) for x in (row["activity_ids"] or "").split(",") if x.strip().isdigit()]
                suggestions.append({
                    "trigger_type": "nudge_escalation",
                    "severity_preset": "High",
                    "icon": "nudge",
                    "client_id": row["client_id"],
                    "client_name": row["client_name"],
                    "policy_id": policy_id,
                    "title": f"{row['nudge_count']} unanswered nudges — {row['client_name']}",
                    "detail": f"{row['policy_type']}: {row['nudge_count']} waiting-external follow-ups in last 90 days",
                    "source_activity_ids": act_ids,
                })
        except Exception as e:
            logger.warning("get_escalation_suggestions nudge_escalation error: %s", e)

    # ── Trigger 4: Critical renewal alerts ──
    try:
        alerts = get_escalation_alerts(conn, excluded_statuses=excluded_statuses)
        for alert in alerts:
            if alert.get("escalation_tier") != "CRITICAL":
                continue
            policy_id = alert.get("id")  # policies.id from v_renewal_pipeline
            if policy_id and policy_id in open_issue_policy_ids:
                continue
            if _is_dismissed(policy_id, "critical_renewal"):
                continue
            suggestions.append({
                "trigger_type": "critical_renewal",
                "severity_preset": "Critical",
                "icon": "critical",
                "client_id": alert.get("client_id"),
                "client_name": alert.get("client_name", ""),
                "policy_id": policy_id,
                "title": f"Critical renewal — {alert.get('client_name', '')}",
                "detail": (
                    f"{alert.get('policy_type', '')} expires in {alert.get('days_to_renewal', '?')}d, "
                    f"status: {alert.get('renewal_status', 'Not Started')}"
                ),
                "source_activity_ids": [],
            })
    except Exception as e:
        logger.warning("get_escalation_suggestions critical_renewal error: %s", e)

    # ── Sort: Critical first, then High, Normal, Low; then by title ──
    suggestions.sort(
        key=lambda s: (
            _SEVERITY_ORDER.get(s["severity_preset"], 9),
            s["title"],
        )
    )

    return suggestions


# ─── SPREADSHEET GRID DATA ──────────────────────────────────────────────────


def get_all_policies_for_grid(conn: sqlite3.Connection) -> list[dict]:
    """Return all non-archived policies with all editable fields for the
    spreadsheet grid view.  Includes opportunities."""
    sql = """
    SELECT
        p.policy_uid,
        p.client_id,
        c.name AS client_name,
        p.policy_type,
        p.carrier,
        p.access_point,
        p.policy_number,
        p.effective_date,
        p.expiration_date,
        p.premium,
        p.limit_amount,
        p.deductible,
        p.commission_rate,
        p.prior_premium,
        p.renewal_status,
        p.is_opportunity,
        p.opportunity_status,
        p.follow_up_date,
        p.coverage_form,
        p.layer_position,
        p.project_name,
        p.first_named_insured,
        p.description,
        p.notes,
        p.placement_colleague,
        p.underwriter_name,
        p.exposure_basis,
        p.exposure_amount,
        p.exposure_address,
        p.exposure_city,
        p.exposure_state,
        p.exposure_zip,
        p.attachment_point,
        p.participation_of
    FROM policies p
    JOIN clients c ON p.client_id = c.id
    WHERE p.archived = 0
    ORDER BY c.name, p.policy_type, p.layer_position
    """
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_projects_by_client(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """Return {client_id: [project_name, ...]} for Location column dropdowns."""
    sql = """
    SELECT client_id, name
    FROM projects
    WHERE name IS NOT NULL AND name != ''
    ORDER BY name
    """
    result: dict[int, list[str]] = {}
    for row in conn.execute(sql).fetchall():
        result.setdefault(row["client_id"], []).append(row["name"])
    return result


def get_all_clients_for_grid(conn: sqlite3.Connection) -> list[dict]:
    """Return all non-archived clients with editable fields and aggregate stats
    for the client spreadsheet grid view."""
    sql = """
    SELECT
        c.id,
        c.name,
        c.cn_number,
        c.industry_segment,
        c.account_exec,
        c.date_onboarded,
        c.website,
        c.fein,
        c.broker_fee,
        c.hourly_rate,
        c.follow_up_date,
        c.relationship_risk,
        c.service_model,
        c.business_description,
        c.notes,
        c.stewardship_date,
        c.renewal_strategy,
        c.growth_opportunities,
        c.account_priorities,
        COALESCE(
            (SELECT COUNT(CASE WHEN p.is_opportunity = 0 OR p.is_opportunity IS NULL THEN p.id END)
             FROM policies p WHERE p.client_id = c.id AND p.archived = 0), 0
        ) AS total_policies,
        COALESCE(
            (SELECT SUM(CASE WHEN p.is_opportunity = 0 OR p.is_opportunity IS NULL THEN p.premium ELSE 0 END)
             FROM policies p WHERE p.client_id = c.id AND p.archived = 0), 0
        ) AS total_premium,
        COALESCE(
            (SELECT SUM(CASE WHEN (p.is_opportunity = 0 OR p.is_opportunity IS NULL) AND p.commission_rate > 0
                         THEN ROUND(p.premium * p.commission_rate, 2) ELSE 0 END)
             FROM policies p WHERE p.client_id = c.id AND p.archived = 0), 0
        ) + COALESCE(c.broker_fee, 0) AS total_revenue,
        (SELECT MIN(CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER))
         FROM policies p WHERE p.client_id = c.id AND p.archived = 0
           AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           AND julianday(p.expiration_date) - julianday('now') > 0
        ) AS next_renewal_days
    FROM clients c
    WHERE c.archived = 0
    ORDER BY c.name
    """
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_followups_for_grid(conn: sqlite3.Connection) -> list[dict]:
    """Return all open follow-ups with enriched data for the follow-ups
    spreadsheet grid view."""
    sql = """
    SELECT
        a.id,
        a.activity_date,
        a.subject,
        a.activity_type,
        a.contact_person,
        a.disposition,
        a.details,
        a.duration_hours,
        a.follow_up_date,
        a.follow_up_done,
        c.id AS client_id,
        c.name AS client_name,
        p.policy_uid,
        p.policy_type,
        p.carrier,
        p.expiration_date,
        pr.name AS project_name,
        CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue
    FROM activity_log a
    JOIN clients c ON a.client_id = c.id
    LEFT JOIN policies p ON a.policy_id = p.id
    LEFT JOIN projects pr ON a.project_id = pr.id
    WHERE a.follow_up_done = 0
      AND a.follow_up_date IS NOT NULL
      AND a.merged_into_id IS NULL
    ORDER BY a.follow_up_date ASC
    """
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


# ── Review Session Queries ──────────────────────────────────────────────


def get_or_create_review_session(conn: sqlite3.Connection) -> dict:
    """Return the active (incomplete) review session, or create a new one."""
    import dateparser
    import json
    from datetime import datetime

    row = conn.execute(
        "SELECT * FROM review_sessions WHERE completed_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row:
        session = dict(row)
        started = dateparser.parse(session["started_at"])
        if started and (datetime.now() - started).days > 7:
            session["stale"] = True
        else:
            session["stale"] = False
        return session

    from policydb.review_checks import WALKTHROUGH_SECTIONS

    sections = {}
    for s in WALKTHROUGH_SECTIONS:
        if s.get("conditional"):
            continue
        sections[s["key"]] = {"status": "pending", "completed_at": None, "item_count": 0}

    conn.execute(
        "INSERT INTO review_sessions (sections_json) VALUES (?)",
        (json.dumps(sections),),
    )
    conn.commit()
    new_row = conn.execute(
        "SELECT * FROM review_sessions WHERE completed_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    session = dict(new_row)
    session["stale"] = False
    return session


def archive_stale_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Mark a stale session as completed (partial) so a new one can start."""
    conn.execute(
        "UPDATE review_sessions SET completed_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()


def update_section_status(
    conn: sqlite3.Connection, session_id: int, section_key: str, status: str, item_count: int = 0
) -> dict:
    """Update a section's status in the session's sections_json."""
    import json
    from datetime import datetime

    row = conn.execute("SELECT sections_json FROM review_sessions WHERE id = ?", (session_id,)).fetchone()
    sections = json.loads(row["sections_json"]) if row else {}
    sections[section_key] = {
        "status": status,
        "completed_at": datetime.now().isoformat() if status == "complete" else None,
        "item_count": item_count,
    }
    conn.execute(
        "UPDATE review_sessions SET sections_json = ? WHERE id = ?",
        (json.dumps(sections), session_id),
    )
    conn.commit()
    return sections


def complete_review_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Mark the entire review session as complete."""
    conn.execute(
        "UPDATE review_sessions SET completed_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()


def get_last_completed_review_date(conn: sqlite3.Connection) -> str | None:
    """Return the completed_at timestamp of the most recent finished review session."""
    row = conn.execute(
        "SELECT completed_at FROM review_sessions WHERE completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()
    return row["completed_at"] if row else None


def get_this_week_summary(conn: sqlite3.Connection, since: str | None = None) -> dict:
    """Return activity summary since last completed review for the This Week section."""
    if not since:
        since = "2000-01-01"

    created_policies = conn.execute(
        "SELECT COUNT(*) as cnt FROM audit_log WHERE table_name = 'policies' AND operation = 'INSERT' AND timestamp > ?",
        (since,),
    ).fetchone()["cnt"]

    created_clients = conn.execute(
        "SELECT COUNT(*) as cnt FROM audit_log WHERE table_name = 'clients' AND operation = 'INSERT' AND timestamp > ?",
        (since,),
    ).fetchone()["cnt"]

    issues_closed = conn.execute(
        """SELECT COUNT(*) as cnt FROM activity_log
           WHERE item_kind = 'issue' AND issue_status IN ('Resolved', 'Closed')
             AND activity_date > ?""",
        (since,),
    ).fetchone()["cnt"]

    followups_completed = conn.execute(
        """SELECT COUNT(*) as cnt FROM activity_log
           WHERE follow_up_done = 1 AND activity_date > ?""",
        (since,),
    ).fetchone()["cnt"]

    status_changes = [dict(r) for r in conn.execute(
        """SELECT al.old_value, al.new_value, p.policy_uid, p.policy_type
           FROM audit_log al
           JOIN policies p ON p.id = al.record_id
           WHERE al.table_name = 'policies' AND al.column_name = 'renewal_status'
             AND al.timestamp > ?
           ORDER BY al.timestamp DESC LIMIT 20""",
        (since,),
    ).fetchall()]

    contacts_modified = conn.execute(
        "SELECT COUNT(*) as cnt FROM audit_log WHERE table_name = 'contacts' AND timestamp > ?",
        (since,),
    ).fetchone()["cnt"]

    return {
        "created_policies": created_policies,
        "created_clients": created_clients,
        "issues_closed": issues_closed,
        "followups_completed": followups_completed,
        "status_changes": status_changes,
        "contacts_modified": contacts_modified,
        "since": since,
    }


def get_review_section_items(conn: sqlite3.Connection, section_key: str) -> list[dict]:
    """Return items for a specific walkthrough section."""
    from policydb.config import cfg

    if section_key == "overdue_followups":
        return [dict(r) for r in conn.execute(
            """SELECT al.*, c.name as client_name
               FROM activity_log al
               LEFT JOIN clients c ON al.client_id = c.id
               WHERE al.follow_up_done = 0 AND al.follow_up_date < date('now')
                 AND al.item_kind = 'followup'
               ORDER BY al.follow_up_date ASC"""
        ).fetchall()]

    if section_key == "upcoming_renewals":
        window = cfg.get("review_renewal_window_days", 120)
        return [dict(r) for r in conn.execute(
            """SELECT p.*, c.name as client_name
               FROM policies p
               JOIN clients c ON p.client_id = c.id
               WHERE p.expiration_date IS NOT NULL
                 AND p.expiration_date BETWEEN date('now') AND date('now', '+' || ? || ' days')
                 AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
               ORDER BY p.expiration_date ASC""",
            (window,),
        ).fetchall()]

    if section_key == "open_issues":
        stale_days = cfg.get("review_stale_issue_days", 14)
        return [dict(r) for r in conn.execute(
            """SELECT al.*, c.name as client_name
               FROM activity_log al
               LEFT JOIN clients c ON al.client_id = c.id
               WHERE al.item_kind = 'issue'
                 AND al.issue_status NOT IN ('Resolved', 'Closed')
                 AND (
                   al.activity_date < date('now', '-' || ? || ' days')
                   OR al.due_date IS NULL
                   OR (al.issue_severity = 'Critical' AND al.activity_date < date('now', '-7 days'))
                 )
               ORDER BY
                 CASE WHEN al.issue_severity = 'Critical' THEN 0 ELSE 1 END,
                 al.activity_date ASC""",
            (stale_days,),
        ).fetchall()]

    if section_key == "client_health":
        inactive_days = cfg.get("review_inactive_client_days", 180)
        return [dict(r) for r in conn.execute(
            """SELECT c.*,
                 (SELECT COUNT(*) FROM contact_client_assignments cca
                  JOIN contacts ct ON cca.contact_id = ct.id
                  WHERE cca.client_id = c.id AND cca.is_primary = 1) as has_primary,
                 (SELECT MAX(al.activity_date) FROM activity_log al WHERE al.client_id = c.id) as last_activity
               FROM clients c
               WHERE c.is_active != 0
                 AND (
                   (SELECT COUNT(*) FROM contact_client_assignments cca
                    JOIN contacts ct ON cca.contact_id = ct.id
                    WHERE cca.client_id = c.id AND cca.is_primary = 1) = 0
                   OR (SELECT MAX(al.activity_date) FROM activity_log al WHERE al.client_id = c.id) < date('now', '-' || ? || ' days')
                   OR (SELECT MAX(al.activity_date) FROM activity_log al WHERE al.client_id = c.id) IS NULL
                 )
               ORDER BY c.name""",
            (inactive_days,),
        ).fetchall()]

    if section_key == "policy_audit":
        return [dict(r) for r in conn.execute(
            """SELECT p.*, c.name as client_name
               FROM policies p
               JOIN clients c ON p.client_id = c.id
               WHERE (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
                 AND (
                   p.expiration_date IS NULL
                   OR p.carrier IS NULL OR p.carrier = ''
                   OR p.renewal_status IS NULL OR p.renewal_status = ''
                 )
               ORDER BY c.name, p.policy_type"""
        ).fetchall()]

    if section_key == "inbox":
        return [dict(r) for r in conn.execute(
            """SELECT * FROM inbox_items
               WHERE processed = 0
               ORDER BY received_at DESC"""
        ).fetchall()]

    return []


def should_show_review_reminder(conn: sqlite3.Connection, reminder_day: str) -> bool:
    """Check if the review reminder banner should show on the dashboard."""
    import datetime as dt

    today = dt.date.today()
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if reminder_day.lower() not in day_names:
        return False
    if day_names[today.weekday()] != reminder_day.lower():
        return False

    monday = today - dt.timedelta(days=today.weekday())
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM review_sessions WHERE completed_at >= ?",
        (monday.isoformat(),),
    ).fetchone()
    return row["cnt"] == 0


def get_vacation_checklist(conn: sqlite3.Connection, return_date: str) -> dict:
    """Generate a pre-departure checklist based on vacation return date."""
    from policydb.config import cfg
    pre_marketing_days = cfg.get("review_vacation_pre_marketing_days", 14)

    followups_during = [dict(r) for r in conn.execute(
        """SELECT al.*, c.name as client_name
           FROM activity_log al
           LEFT JOIN clients c ON al.client_id = c.id
           WHERE al.follow_up_done = 0
             AND al.follow_up_date BETWEEN date('now') AND ?
             AND al.item_kind = 'followup'
           ORDER BY al.follow_up_date ASC""",
        (return_date,),
    ).fetchall()]

    issues_during = [dict(r) for r in conn.execute(
        """SELECT al.*, c.name as client_name
           FROM activity_log al
           LEFT JOIN clients c ON al.client_id = c.id
           WHERE al.item_kind = 'issue'
             AND al.issue_status NOT IN ('Resolved', 'Closed')
             AND al.due_date BETWEEN date('now') AND ?
           ORDER BY al.due_date ASC""",
        (return_date,),
    ).fetchall()]

    renewals_near_return = [dict(r) for r in conn.execute(
        """SELECT p.*, c.name as client_name
           FROM policies p
           JOIN clients c ON p.client_id = c.id
           WHERE p.expiration_date IS NOT NULL
             AND p.expiration_date BETWEEN ? AND date(?, '+' || ? || ' days')
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           ORDER BY p.expiration_date ASC""",
        (return_date, return_date, pre_marketing_days),
    ).fetchall()]

    milestones_during = [dict(r) for r in conn.execute(
        """SELECT pt.*, p.policy_uid, p.policy_type, c.name as client_name
           FROM policy_timeline pt
           JOIN policies p ON pt.policy_uid = p.policy_uid
           JOIN clients c ON p.client_id = c.id
           WHERE pt.completed_date IS NULL
             AND pt.projected_date BETWEEN date('now') AND ?
           ORDER BY pt.projected_date ASC""",
        (return_date,),
    ).fetchall()]

    return {
        "return_date": return_date,
        "followups": followups_during,
        "issues": issues_during,
        "renewals": renewals_near_return,
        "milestones": milestones_during,
    }
