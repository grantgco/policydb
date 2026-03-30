"""Renewal issues — standing issues auto-created per renewal or program.

Auto-creates one issue per renewal (or per program) to serve as the hub for
all renewal-related activities. Severity tracks timeline health. Auto-resolves
when the renewal reaches a terminal status (e.g., Bound).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import policydb.config as cfg
from policydb.db import generate_issue_uid

logger = logging.getLogger("policydb.renewal_issues")

# ── Health → Severity mapping ────────────────────────────────────────────────

_HEALTH_SEVERITY = {
    "critical": "Critical",
    "at_risk": "High",
    "compressed": "Normal",
    "drifting": "Normal",
    "on_track": "Low",
}


def _severity_sla(severity: str) -> int:
    """Look up SLA days for a severity label from config."""
    for sev in cfg.get("issue_severities", []):
        if sev["label"] == severity:
            return sev.get("sla_days", 7)
    return 7


def _worst_health_severity(conn, policy_uid: str) -> str:
    """Derive issue severity from the worst incomplete milestone health."""
    row = conn.execute("""
        SELECT health FROM policy_timeline
        WHERE policy_uid = ? AND completed_date IS NULL
        ORDER BY CASE health
            WHEN 'critical' THEN 1
            WHEN 'at_risk' THEN 2
            WHEN 'compressed' THEN 3
            WHEN 'drifting' THEN 4
            ELSE 5
        END
        LIMIT 1
    """, (policy_uid,)).fetchone()
    if row:
        return _HEALTH_SEVERITY.get(row["health"], "Low")
    return "Low"


def _worst_program_health_severity(conn, program_id: int) -> str:
    """Derive severity from worst health across all child policy timelines."""
    row = conn.execute("""
        SELECT pt.health
        FROM policy_timeline pt
        JOIN policies p ON p.policy_uid = pt.policy_uid
        WHERE p.program_id = ?
          AND pt.completed_date IS NULL
        ORDER BY CASE pt.health
            WHEN 'critical' THEN 1
            WHEN 'at_risk' THEN 2
            WHEN 'compressed' THEN 3
            WHEN 'drifting' THEN 4
            ELSE 5
        END
        LIMIT 1
    """, (program_id,)).fetchone()
    if row:
        return _HEALTH_SEVERITY.get(row["health"], "Low")
    return "Low"


# ── ensure_renewal_issues ────────────────────────────────────────────────────


def ensure_renewal_issues(conn, policy_uid: str | None = None) -> None:
    """Create renewal issues for eligible policies/programs within the window.

    Called from init_db() after generate_policy_timelines().
    Idempotent — skips policies/programs that already have an open renewal issue.
    """
    if not cfg.get("renewal_issue_auto_create", True):
        return

    window_days = cfg.get("renewal_issue_window_days", 120)
    today = date.today()
    horizon = today + timedelta(days=window_days)

    # ── Standalone policies (no program) ─────────────────────────────
    if policy_uid:
        policy_rows = conn.execute("""
            SELECT p.policy_uid, p.expiration_date, p.policy_type, p.id AS policy_id,
                   c.name AS client_name, p.client_id, p.program_id
            FROM policies p
            JOIN clients c ON c.id = p.client_id
            WHERE p.policy_uid = ?
        """, (policy_uid,)).fetchall()
    else:
        policy_rows = conn.execute("""
            SELECT p.policy_uid, p.expiration_date, p.policy_type, p.id AS policy_id,
                   c.name AS client_name, p.client_id, p.program_id
            FROM policies p
            JOIN clients c ON c.id = p.client_id
            WHERE (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
              AND (p.archived = 0 OR p.archived IS NULL)
              AND p.expiration_date IS NOT NULL
              AND p.expiration_date >= ?
              AND p.expiration_date <= ?
        """, (today.isoformat(), horizon.isoformat())).fetchall()

    for pol in policy_rows:
        # Skip child policies in active programs — they roll up to program issue
        if pol["program_id"] is not None:
            continue

        term_key = pol["policy_uid"]
        _create_renewal_issue_if_needed(
            conn, term_key,
            client_id=pol["client_id"],
            policy_id=pol["policy_id"],
            program_id=None,
            subject=_build_subject(pol["expiration_date"], pol["policy_type"], pol["client_name"]),
            severity_fn=lambda: _worst_health_severity(conn, pol["policy_uid"]),
        )

    # ── Programs ─────────────────────────────────────────────────────
    if not policy_uid:
        program_rows = conn.execute("""
            SELECT pg.id, pg.program_uid, pg.expiration_date, pg.line_of_business,
                   pg.name AS program_name, c.name AS client_name, pg.client_id
            FROM programs pg
            JOIN clients c ON c.id = pg.client_id
            WHERE (pg.archived = 0 OR pg.archived IS NULL)
              AND pg.expiration_date IS NOT NULL
              AND pg.expiration_date >= ?
              AND pg.expiration_date <= ?
        """, (today.isoformat(), horizon.isoformat())).fetchall()

        for pgm in program_rows:
            term_key = f"program:{pgm['program_uid']}"
            label = pgm["line_of_business"] or pgm["program_name"] or "Program"
            _create_renewal_issue_if_needed(
                conn, term_key,
                client_id=pgm["client_id"],
                policy_id=None,
                program_id=pgm["id"],
                subject=_build_subject(pgm["expiration_date"], f"{label} Program", pgm["client_name"]),
                severity_fn=lambda pgm_id=pgm["id"]: _worst_program_health_severity(conn, pgm_id),
            )

    conn.commit()


def _create_renewal_issue_if_needed(
    conn, term_key: str, *, client_id: int, policy_id: int | None,
    program_id: int | None, subject: str, severity_fn,
) -> None:
    """Create a renewal issue if one doesn't already exist for this term key."""
    existing = conn.execute("""
        SELECT id FROM activity_log
        WHERE is_renewal_issue = 1
          AND renewal_term_key = ?
          AND issue_status NOT IN ('Resolved', 'Closed')
    """, (term_key,)).fetchone()
    if existing:
        return

    severity = severity_fn()
    sla_days = _severity_sla(severity)
    uid = generate_issue_uid()
    today_str = date.today().isoformat()

    cur = conn.execute("""
        INSERT INTO activity_log (
            activity_date, client_id, policy_id, activity_type, subject,
            item_kind, issue_uid, issue_status, issue_severity, issue_sla_days,
            program_id, is_renewal_issue, renewal_term_key, created_at
        ) VALUES (?, ?, ?, 'Issue', ?, 'issue', ?, 'Open', ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
    """, (
        today_str, client_id, policy_id, subject,
        uid, severity, sla_days, program_id, term_key,
    ))
    new_issue_id = cur.lastrowid

    # Backfill-link recent unlinked activities on this policy/program
    _backfill_link(conn, new_issue_id, client_id, policy_id, program_id)
    logger.info("Created renewal issue %s for %s (severity=%s)", uid, term_key, severity)


def _backfill_link(conn, issue_id: int, client_id: int, policy_id: int | None, program_id: int | None) -> None:
    """Link recent unlinked activities to a newly created renewal issue."""
    window_days = cfg.get("renewal_issue_window_days", 120)
    cutoff = (date.today() - timedelta(days=window_days)).isoformat()

    if program_id:
        # Link activities on program or any child policy
        conn.execute("""
            UPDATE activity_log
            SET issue_id = ?
            WHERE issue_id IS NULL
              AND item_kind = 'followup'
              AND activity_date >= ?
              AND (
                  program_id = ?
                  OR policy_id IN (SELECT id FROM policies WHERE program_id = ?)
              )
        """, (issue_id, cutoff, program_id, program_id))
    elif policy_id:
        conn.execute("""
            UPDATE activity_log
            SET issue_id = ?
            WHERE issue_id IS NULL
              AND item_kind = 'followup'
              AND activity_date >= ?
              AND policy_id = ?
        """, (issue_id, cutoff, policy_id))


def _build_subject(expiration_date: str | None, policy_type: str | None, client_name: str) -> str:
    """Build renewal issue subject: '2026 GL Renewal — Acme Corp'."""
    year = ""
    if expiration_date:
        try:
            year = expiration_date[:4]
        except (TypeError, IndexError):
            pass
    ptype = policy_type or "Renewal"
    parts = []
    if year:
        parts.append(year)
    parts.append(f"{ptype} Renewal")
    return f"{' '.join(parts)} — {client_name}"


# ── sync_renewal_issue_severity ──────────────────────────────────────────────


def sync_renewal_issue_severity(conn, policy_uid: str) -> None:
    """Update the renewal issue severity based on current timeline health.

    Only touches auto-created renewal issues (is_renewal_issue=1).
    """
    # Check if this policy is part of a program
    pol = conn.execute(
        "SELECT program_id FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if not pol:
        return

    if pol["program_id"]:
        # Sync the program's renewal issue instead
        pgm = conn.execute(
            "SELECT program_uid FROM programs WHERE id = ?", (pol["program_id"],)
        ).fetchone()
        if not pgm:
            return
        term_key = f"program:{pgm['program_uid']}"
        severity = _worst_program_health_severity(conn, pol["program_id"])
    else:
        term_key = policy_uid
        severity = _worst_health_severity(conn, policy_uid)

    sla_days = _severity_sla(severity)
    conn.execute("""
        UPDATE activity_log
        SET issue_severity = ?, issue_sla_days = ?
        WHERE is_renewal_issue = 1
          AND renewal_term_key = ?
          AND issue_status NOT IN ('Resolved', 'Closed')
    """, (severity, sla_days, term_key))


# ── auto_resolve_renewal_issue ───────────────────────────────────────────────


def auto_resolve_renewal_issue(conn, policy_uid: str | None = None, program_uid: str | None = None) -> None:
    """Resolve the renewal issue when renewal reaches a terminal status.

    Call with policy_uid for standalone policies, program_uid for programs.
    """
    if policy_uid:
        term_key = policy_uid
    elif program_uid:
        term_key = f"program:{program_uid}"
    else:
        return

    today_str = date.today().isoformat()
    conn.execute("""
        UPDATE activity_log
        SET issue_status = 'Resolved',
            resolution_type = 'Completed',
            resolution_notes = 'Auto-resolved: renewal bound',
            resolved_date = ?
        WHERE is_renewal_issue = 1
          AND renewal_term_key = ?
          AND issue_status NOT IN ('Resolved', 'Closed')
    """, (today_str, term_key))


# ── auto_link_to_renewal_issue ───────────────────────────────────────────────


def auto_link_to_renewal_issue(conn, policy_id: int, activity_id: int) -> None:
    """Link a newly created activity to the open renewal issue for its policy.

    Checks the policy itself first, then falls back to the policy's program.
    Only links if renewal_issue_auto_link config is True.
    """
    if not cfg.get("renewal_issue_auto_link", True):
        return

    # Look up the policy's uid and program
    pol = conn.execute(
        "SELECT policy_uid, program_id FROM policies WHERE id = ?", (policy_id,)
    ).fetchone()
    if not pol:
        return

    # Try standalone policy first
    issue = conn.execute("""
        SELECT id FROM activity_log
        WHERE is_renewal_issue = 1
          AND renewal_term_key = ?
          AND issue_status NOT IN ('Resolved', 'Closed')
        LIMIT 1
    """, (pol["policy_uid"],)).fetchone()

    # Fall back to program renewal issue
    if not issue and pol["program_id"]:
        pgm = conn.execute(
            "SELECT program_uid FROM programs WHERE id = ?", (pol["program_id"],)
        ).fetchone()
        if pgm:
            issue = conn.execute("""
                SELECT id FROM activity_log
                WHERE is_renewal_issue = 1
                  AND renewal_term_key = ?
                  AND issue_status NOT IN ('Resolved', 'Closed')
                LIMIT 1
            """, (f"program:{pgm['program_uid']}",)).fetchone()

    if issue:
        conn.execute(
            "UPDATE activity_log SET issue_id = ? WHERE id = ?",
            (issue["id"], activity_id),
        )
