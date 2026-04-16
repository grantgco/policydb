"""Policy Bind — per-policy 'binder received' action.

Extracted from bind_order.py as part of the Bind Order → Renew Policies
refactor (see .claude/plans/snappy-strolling-fountain.md). This module owns
the lifecycle transition that happens when a carrier issues a binder for an
already-existing (but not-yet-bound) policy term:

    mark_policy_bound(conn, policy_uid, bind_date, note)
        - Set renewal_status = 'Bound', bound_date = <date>
        - Log "Renewal bound" activity
        - Complete any remaining timeline milestones (unless program child)
        - Close all open follow-ups on the policy (auto_close_reason='renewal_bound')
        - Cascade to program renewal close (if program child)
        - Auto-resolve the renewal issue at policy scope
        - Generate the config-driven post-bind follow-ups

This action is now SEPARATE from creating a new renewal term. See
renew_policies.py for the term-creation flow.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from policydb import config as cfg
from policydb.renewal_issues import (
    auto_resolve_renewal_issue,
    cascade_program_renewal_close,
)

logger = logging.getLogger("policydb.policy_bind")


def mark_policy_bound(
    conn: sqlite3.Connection,
    policy_uid: str,
    bind_date: str,
    note: str | None = None,
    bind_event_id: int | None = None,
    *,
    generate_followups: bool = True,
) -> None:
    """Mark a single policy bound. Idempotent — if the policy already has a
    `bound_date`, returns early without duplicating the 'Renewal bound'
    activity or regenerating post-bind follow-ups. Opportunity rows are
    skipped (they must be converted before they can be bound).

    Raises:
        ValueError if the policy_uid doesn't exist.
    """
    uid = policy_uid.upper()
    pol = conn.execute(
        """SELECT id, client_id, policy_uid, is_opportunity, program_id, bound_date
           FROM policies WHERE policy_uid = ?""",
        (uid,),
    ).fetchone()
    if not pol:
        raise ValueError(f"Policy {uid} not found")
    if pol["is_opportunity"]:
        logger.info("Skipping bind for %s: is_opportunity=1", uid)
        return
    if pol["bound_date"]:
        # Already bound — don't duplicate the activity log row or regenerate
        # post-bind follow-ups on a double-click, retry, or concurrent call.
        logger.info("Skipping bind for %s: bound_date=%s already set", uid, pol["bound_date"])
        return

    # 1. Status + bound_date + updated_at
    conn.execute(
        """UPDATE policies
           SET renewal_status = 'Bound',
               bound_date = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE policy_uid = ?""",
        (bind_date, uid),
    )

    # 2. Log "Renewal bound" activity on the policy
    bind_event_note = f" [bind_event_id={bind_event_id}]" if bind_event_id else ""
    full_details = ((note or "") + bind_event_note).strip() or None
    conn.execute(
        """INSERT INTO activity_log
           (client_id, policy_id, activity_type, subject, details, activity_date, created_at)
           VALUES (?, ?, 'Milestone', 'Renewal bound', ?, ?, datetime('now'))""",
        (pol["client_id"], pol["id"], full_details, bind_date),
    )

    # 3. Complete remaining timeline milestones. Skip if this is a program
    # child — those milestones live at program level and would otherwise
    # double-mark when the program cascade fires.
    if not pol["program_id"]:
        from policydb.timeline_engine import complete_timeline_milestone
        incomplete = conn.execute(
            "SELECT milestone_name FROM policy_timeline WHERE policy_uid = ? AND completed_date IS NULL",
            (uid,),
        ).fetchall()
        for m in incomplete:
            try:
                complete_timeline_milestone(conn, uid, m["milestone_name"])
            except Exception as exc:
                logger.warning("Timeline milestone completion failed for %s/%s: %s",
                               uid, m["milestone_name"], exc)

    # 4. Close all open follow-ups on this policy
    conn.execute(
        """UPDATE activity_log
           SET follow_up_done = 1,
               auto_close_reason = 'renewal_bound',
               auto_closed_at = datetime('now'),
               auto_closed_by = 'policy_bind'
           WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL""",
        (pol["id"],),
    )

    # 5. Cascade to program (if any) + auto-resolve renewal issue at policy scope
    if pol["program_id"]:
        cascade_program_renewal_close(conn, uid)
    auto_resolve_renewal_issue(conn, policy_uid=uid)

    # 6. Generate post-bind follow-ups (config-driven)
    if generate_followups:
        generate_post_bind_followups(
            conn,
            client_id=pol["client_id"],
            bind_date=bind_date,
            policy_id=pol["id"],
            program_id=pol["program_id"],
        )


def generate_post_bind_followups(
    conn: sqlite3.Connection,
    *,
    client_id: int,
    bind_date: str,
    program_id: int | None = None,
    policy_id: int | None = None,
) -> int:
    """Insert one follow-up activity_log row per item in
    config.post_bind_activities. Returns count inserted.

    At least one of program_id / policy_id must be set. Both may be passed
    simultaneously — the follow-up row then carries both FKs, which puts the
    item on both the program's and the policy's action centers.
    """
    if program_id is None and policy_id is None:
        raise ValueError("Must provide program_id or policy_id")

    items = cfg.get("post_bind_activities", []) or []
    if not items:
        return 0

    try:
        bind_dt = date.fromisoformat(bind_date)
    except ValueError:
        bind_dt = date.today()

    inserted = 0
    for item in items:
        try:
            offset_days = int(item.get("days_after_bind", 0))
        except (TypeError, ValueError):
            offset_days = 0
        fu_date = (bind_dt + timedelta(days=offset_days)).isoformat()
        subject = item.get("subject") or item.get("name") or "Post-bind follow-up"
        activity_type = item.get("activity_type") or "Follow-up"

        conn.execute(
            """INSERT INTO activity_log
               (client_id, policy_id, program_id, activity_type, subject,
                activity_date, follow_up_date, follow_up_done, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))""",
            (
                client_id,
                policy_id,
                program_id,
                activity_type,
                subject,
                bind_date,
                fu_date,
            ),
        )
        inserted += 1
    return inserted
