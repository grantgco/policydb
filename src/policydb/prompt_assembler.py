"""Prompt data assembler — builds markdown context blocks for LLM prompts.

Schema audit findings (2026-04-03):
- "Renewals" = policies with renewal_status/expiration_date. View: v_renewal_pipeline.
- "Issues" = activity_log rows where item_kind='issue'. Fields: issue_uid, issue_status,
  issue_severity, issue_sla_days, resolution_type, resolution_notes, root_cause_category.
- "Follow-ups" = activity_log rows with follow_up_date set.
- "Milestones" = policy_milestones (checklist) + policy_timeline (health tracking).
- Contacts = unified contacts table + junction tables (contact_client_assignments,
  contact_policy_assignments, contact_program_assignments).
- No separate renewals, issues, or follow-ups tables.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Callable

# ── Depth tiers ──────────────────────────────────────────────────────────────

DEPTH_FULL = 1
DEPTH_SUMMARY = 2
DEPTH_REFERENCE = 3

# ── Registry ─────────────────────────────────────────────────────────────────

_ASSEMBLERS: dict[str, Callable[[sqlite3.Connection, int, int], str]] = {}

# Primary record types the user can select in the UI
PRIMARY_RECORD_TYPES = ["client", "policy", "renewal", "issue"]


def register(record_type: str):
    """Decorator to register an assembler function."""
    def wrapper(fn: Callable) -> Callable:
        _ASSEMBLERS[record_type] = fn
        return fn
    return wrapper


def get_assembler(record_type: str) -> Callable | None:
    return _ASSEMBLERS.get(record_type)


# ── Formatting helpers ───────────────────────────────────────────────────────

def _fmt_currency(v) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return ""


def _fmt_date(d) -> str:
    """Format date as 'Month DD, YYYY'. Accepts ISO string or date object."""
    if not d:
        return ""
    try:
        if isinstance(d, str):
            d = date.fromisoformat(d)
        return d.strftime("%B %d, %Y")
    except (ValueError, TypeError):
        return str(d)


def _field(label: str, value, fmt: str | None = None) -> str:
    """Return '**Label:** value\\n' or '' if value is None/empty."""
    if value is None or value == "" or value == 0 and fmt == "currency":
        return ""
    if fmt == "currency":
        formatted = _fmt_currency(value)
        if not formatted:
            return ""
        return f"**{label}:** {formatted}\n"
    if fmt == "date":
        formatted = _fmt_date(value)
        if not formatted:
            return ""
        return f"**{label}:** {formatted}\n"
    return f"**{label}:** {value}\n"


def _truncated_list(items: list[str], limit: int, noun: str = "items") -> list[str]:
    """Return items[:limit] plus a truncation notice if needed."""
    if limit <= 0 or len(items) <= limit:
        return items
    omitted = len(items) - limit
    return items[:limit] + [f"[{omitted} additional {noun} not shown]"]


def _row_to_dict(row: sqlite3.Row | None) -> dict:
    """Convert sqlite3.Row to dict, or return empty dict."""
    if row is None:
        return {}
    return dict(row)


# ── Primary record assemblers ────────────────────────────────────────────────

@register("client")
def assemble_client(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_FULL) -> str:
    """Assemble client data as markdown block."""
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return f"[Client #{record_id} not found]\n"
    r = _row_to_dict(row)

    if depth == DEPTH_REFERENCE:
        name = r.get("name", "")
        cn = r.get("cn_number", "")
        return f"- {name}" + (f" (CN: {cn})" if cn else "") + "\n"

    if depth == DEPTH_SUMMARY:
        # Summary stats
        stats = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(premium),0) AS total_premium "
            "FROM policies WHERE client_id = ? AND archived = 0 AND (is_opportunity = 0 OR is_opportunity IS NULL)",
            (record_id,),
        ).fetchone()
        lines = ["## Client\n"]
        lines.append(_field("Name", r.get("name")))
        lines.append(_field("Industry", r.get("industry_segment")))
        lines.append(_field("Business Description", r.get("business_description")))
        lines.append(_field("Active Policies", stats["cnt"] if stats else 0))
        lines.append(_field("Total Premium", stats["total_premium"] if stats else 0, "currency"))
        return "".join(lines)

    # DEPTH_FULL
    lines = ["## Client\n"]
    lines.append(_field("Name", r.get("name")))
    lines.append(_field("Account Number (CN)", r.get("cn_number")))
    lines.append(_field("FEIN", r.get("fein")))
    lines.append(_field("Industry", r.get("industry_segment")))
    lines.append(_field("Business Description", r.get("business_description")))
    lines.append(_field("Address", r.get("address")))
    lines.append(_field("Website", r.get("website")))
    lines.append(_field("Client Since", r.get("client_since"), "date"))
    lines.append(_field("Date Onboarded", r.get("date_onboarded"), "date"))
    lines.append(_field("Referral Source", r.get("referral_source")))
    lines.append(_field("Account Executive", r.get("account_exec")))
    lines.append(_field("Broker Fee", r.get("broker_fee"), "currency"))
    lines.append(_field("Renewal Month", r.get("renewal_month")))
    lines.append(_field("Preferred Contact Method", r.get("preferred_contact_method")))
    lines.append(_field("Notes", r.get("notes")))
    return "".join(lines)


@register("policy")
def assemble_policy(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_FULL) -> str:
    """Assemble policy data as markdown block. record_id is policies.id."""
    row = conn.execute("SELECT * FROM policies WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return f"[Policy #{record_id} not found]\n"
    r = _row_to_dict(row)

    if depth == DEPTH_REFERENCE:
        uid = r.get("policy_uid", "")
        ptype = r.get("policy_type", "")
        carrier = r.get("carrier", "")
        parts = [uid, ptype]
        if carrier:
            parts.append(carrier)
        return "- " + " | ".join(parts) + "\n"

    if depth == DEPTH_SUMMARY:
        lines = ["## Policy\n"]
        lines.append(_field("Policy UID", r.get("policy_uid")))
        lines.append(_field("Line of Business", r.get("policy_type")))
        lines.append(_field("Carrier", r.get("carrier")))
        lines.append(_field("Premium", r.get("premium"), "currency"))
        lines.append(_field("Expiration Date", r.get("expiration_date"), "date"))
        lines.append(_field("Renewal Status", r.get("renewal_status")))
        return "".join(lines)

    # DEPTH_FULL
    lines = ["## Policy\n"]
    lines.append(_field("Policy UID", r.get("policy_uid")))
    lines.append(_field("Line of Business", r.get("policy_type")))
    lines.append(_field("Carrier", r.get("carrier")))
    lines.append(_field("Policy Number", r.get("policy_number")))
    lines.append(_field("First Named Insured", r.get("first_named_insured")))
    lines.append(_field("Effective Date", r.get("effective_date"), "date"))
    lines.append(_field("Expiration Date", r.get("expiration_date"), "date"))
    lines.append(_field("Premium", r.get("premium"), "currency"))
    lines.append(_field("Prior Premium", r.get("prior_premium"), "currency"))
    if r.get("premium") and r.get("prior_premium"):
        try:
            change = ((float(r["premium"]) - float(r["prior_premium"])) / float(r["prior_premium"])) * 100
            lines.append(_field("Rate Change", f"{change:+.1f}%"))
        except (ValueError, ZeroDivisionError):
            pass
    lines.append(_field("Limit", r.get("limit_amount"), "currency"))
    lines.append(_field("Deductible", r.get("deductible"), "currency"))
    lines.append(_field("Coverage Form", r.get("coverage_form")))
    lines.append(_field("Layer Position", r.get("layer_position")))
    lines.append(_field("Renewal Status", r.get("renewal_status")))
    lines.append(_field("Commission Rate", f"{r['commission_rate']}%" if r.get("commission_rate") else None))
    lines.append(_field("Access Point", r.get("access_point")))
    lines.append(_field("Description", r.get("description")))
    lines.append(_field("Notes", r.get("notes")))

    # Sub-coverages
    subs = conn.execute(
        "SELECT coverage_type FROM policy_sub_coverages WHERE policy_id = ? ORDER BY sort_order",
        (record_id,),
    ).fetchall()
    if subs:
        lines.append(_field("Sub-Coverages", ", ".join(s["coverage_type"] for s in subs)))

    # Policy contacts
    contacts = conn.execute(
        """SELECT co.name, co.email, cpa.role, cpa.is_placement_colleague
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.policy_id = ?""",
        (record_id,),
    ).fetchall()
    if contacts:
        lines.append("\n### Policy Contacts\n")
        for c in contacts:
            role = c["role"] or ("Placement Colleague" if c["is_placement_colleague"] else "Contact")
            line = f"- **{role}:** {c['name']}"
            if c["email"]:
                line += f" ({c['email']})"
            lines.append(line + "\n")

    return "".join(lines)


@register("renewal")
def assemble_renewal(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_FULL) -> str:
    """Assemble renewal data. record_id is policies.id. Includes timeline and milestone data."""
    # Start with policy assembly
    policy_block = assemble_policy(conn, record_id, depth)
    if depth != DEPTH_FULL:
        return policy_block

    r = _row_to_dict(conn.execute("SELECT * FROM policies WHERE id = ?", (record_id,)).fetchone() or {})
    policy_uid = r.get("policy_uid", "")
    lines = [policy_block]

    # Days to renewal
    exp = r.get("expiration_date")
    if exp:
        try:
            days = (date.fromisoformat(exp) - date.today()).days
            lines.append(f"\n**Days to Renewal:** {days}\n")
            if days <= 0:
                lines.append("**Urgency:** EXPIRED\n")
            elif days <= 90:
                lines.append("**Urgency:** URGENT\n")
            elif days <= 120:
                lines.append("**Urgency:** WARNING\n")
            elif days <= 180:
                lines.append("**Urgency:** UPCOMING\n")
        except ValueError:
            pass

    # Timeline milestones
    milestones = conn.execute(
        """SELECT milestone_name, ideal_date, projected_date, completed_date, health, accountability, waiting_on
           FROM policy_timeline WHERE policy_uid = ? ORDER BY ideal_date""",
        (policy_uid,),
    ).fetchall()
    if milestones:
        lines.append("\n### Renewal Timeline\n")
        for m in milestones:
            status = "Done" if m["completed_date"] else m["health"] or "on_track"
            line = f"- **{m['milestone_name']}:** target {_fmt_date(m['ideal_date'])}"
            if m["completed_date"]:
                line += f", completed {_fmt_date(m['completed_date'])}"
            elif m["projected_date"] != m["ideal_date"]:
                line += f", projected {_fmt_date(m['projected_date'])}"
            line += f" [{status}]"
            if m["waiting_on"]:
                line += f" (waiting on: {m['waiting_on']})"
            lines.append(line + "\n")

    # Milestone checklist
    checklist = conn.execute(
        "SELECT milestone, completed FROM policy_milestones WHERE policy_uid = ? ORDER BY id",
        (policy_uid,),
    ).fetchall()
    if checklist:
        lines.append("\n### Milestone Checklist\n")
        for item in checklist:
            mark = "x" if item["completed"] else " "
            lines.append(f"- [{mark}] {item['milestone']}\n")

    return "".join(lines)


@register("issue")
def assemble_issue(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_FULL) -> str:
    """Assemble issue data. record_id is activity_log.id where item_kind='issue'."""
    row = conn.execute(
        "SELECT * FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (record_id,),
    ).fetchone()
    if not row:
        return f"[Issue #{record_id} not found]\n"
    r = _row_to_dict(row)

    if depth == DEPTH_REFERENCE:
        uid = r.get("issue_uid", "")
        subj = r.get("subject", "")
        status = r.get("issue_status", "")
        return f"- {uid}: {subj} [{status}]\n"

    if depth == DEPTH_SUMMARY:
        lines = ["## Issue\n"]
        lines.append(_field("Issue ID", r.get("issue_uid")))
        lines.append(_field("Subject", r.get("subject")))
        lines.append(_field("Status", r.get("issue_status")))
        lines.append(_field("Severity", r.get("issue_severity")))
        lines.append(_field("Due Date", r.get("due_date"), "date"))
        return "".join(lines)

    # DEPTH_FULL
    lines = ["## Issue\n"]
    lines.append(_field("Issue ID", r.get("issue_uid")))
    lines.append(_field("Subject", r.get("subject")))
    lines.append(_field("Details", r.get("details")))
    lines.append(_field("Status", r.get("issue_status")))
    lines.append(_field("Severity", r.get("issue_severity")))
    lines.append(_field("SLA Days", r.get("issue_sla_days")))
    lines.append(_field("Due Date", r.get("due_date"), "date"))
    lines.append(_field("Created", r.get("activity_date"), "date"))
    lines.append(_field("Resolution Type", r.get("resolution_type")))
    lines.append(_field("Resolution Notes", r.get("resolution_notes")))
    lines.append(_field("Root Cause", r.get("root_cause_category")))

    # Linked activities
    activities = conn.execute(
        """SELECT activity_date, activity_type, subject, details, contact_person, disposition
           FROM activity_log WHERE issue_id = ? AND item_kind != 'issue'
           ORDER BY activity_date DESC""",
        (record_id,),
    ).fetchall()
    if activities:
        lines.append("\n### Activity History\n")
        items = []
        for a in activities:
            line = f"- {_fmt_date(a['activity_date'])} — {a['activity_type']}: {a['subject']}"
            if a["disposition"]:
                line += f" [{a['disposition']}]"
            items.append(line + "\n")
        for item in _truncated_list(items, 10, "activities"):
            lines.append(item)

    # Checklist
    checklist = conn.execute(
        "SELECT label, completed FROM issue_checklist WHERE issue_id = ? ORDER BY sort_order",
        (record_id,),
    ).fetchall()
    if checklist:
        lines.append("\n### Checklist\n")
        for item in checklist:
            mark = "x" if item["completed"] else " "
            lines.append(f"- [{mark}] {item['label']}\n")

    return "".join(lines)


# ── Related data assemblers ──────────────────────────────────────────────────

@register("policies")
def assemble_policies_for_client(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_SUMMARY) -> str:
    """Assemble policies for a client. record_id is client_id."""
    active = conn.execute(
        """SELECT id, policy_uid, policy_type, carrier, premium, expiration_date, renewal_status
           FROM policies WHERE client_id = ? AND archived = 0
           AND (is_opportunity = 0 OR is_opportunity IS NULL)
           ORDER BY expiration_date""",
        (record_id,),
    ).fetchall()
    expired = [p for p in active if p["expiration_date"] and p["expiration_date"] < date.today().isoformat()]
    current = [p for p in active if p not in expired]

    if not active:
        return ""

    lines = ["## Policies\n"]

    for p in current:
        if depth <= DEPTH_SUMMARY:
            lines.append(f"- **{p['policy_uid']}** — {p['policy_type']}")
            if p["carrier"]:
                lines.append(f" | {p['carrier']}")
            if p["premium"]:
                lines.append(f" | {_fmt_currency(p['premium'])}")
            if p["expiration_date"]:
                lines.append(f" | Exp: {_fmt_date(p['expiration_date'])}")
            if p["renewal_status"]:
                lines.append(f" [{p['renewal_status']}]")
            lines.append("\n")
        else:
            lines.append(f"- {p['policy_uid']} | {p['policy_type']}\n")

    if expired:
        exp_items = []
        for p in expired:
            exp_items.append(f"- {p['policy_uid']} | {p['policy_type']} | Expired {_fmt_date(p['expiration_date'])}\n")
        exp_items = _truncated_list(exp_items, 3, "expired policies")
        lines.append("\n### Expired Policies\n")
        for item in exp_items:
            lines.append(item)

    return "".join(lines)


@register("renewals")
def assemble_renewals_for_client(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_SUMMARY) -> str:
    """Assemble renewal-state policies for a client. record_id is client_id."""
    rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.premium,
                  p.expiration_date, p.renewal_status,
                  CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal
           FROM policies p
           WHERE p.client_id = ? AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
             AND p.renewal_status IS NOT NULL AND p.renewal_status != ''
             AND julianday(p.expiration_date) - julianday('now') <= 180
           ORDER BY p.expiration_date""",
        (record_id,),
    ).fetchall()
    if not rows:
        return ""

    lines = ["## Renewals in Pipeline\n"]
    for r in rows:
        line = f"- **{r['policy_uid']}** — {r['policy_type']}"
        if r["carrier"]:
            line += f" | {r['carrier']}"
        if r["premium"]:
            line += f" | {_fmt_currency(r['premium'])}"
        line += f" | Exp: {_fmt_date(r['expiration_date'])}"
        line += f" | {r['days_to_renewal']}d"
        if r["renewal_status"]:
            line += f" [{r['renewal_status']}]"
        lines.append(line + "\n")

    return "".join(lines)


@register("issues")
def assemble_issues_for_record(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_SUMMARY, **kwargs) -> str:
    """Assemble issues. record_id is client_id. Pass policy_id=N in kwargs to filter by policy."""
    policy_id = kwargs.get("policy_id")
    if policy_id:
        # Issues linked to a specific policy
        rows = conn.execute(
            """SELECT a.id, a.issue_uid, a.subject, a.issue_status, a.issue_severity,
                      a.due_date, a.activity_date, a.resolved_date
               FROM activity_log a
               JOIN issue_policies ip ON ip.issue_id = a.id
               WHERE ip.policy_id = ? AND a.item_kind = 'issue'
               ORDER BY CASE WHEN a.issue_status IN ('Closed','Resolved') THEN 1 ELSE 0 END,
                        a.due_date""",
            (policy_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, issue_uid, subject, issue_status, issue_severity,
                      due_date, activity_date, resolved_date
               FROM activity_log
               WHERE client_id = ? AND item_kind = 'issue'
               ORDER BY CASE WHEN issue_status IN ('Closed','Resolved') THEN 1 ELSE 0 END,
                        due_date""",
            (record_id,),
        ).fetchall()

    if not rows:
        return ""

    open_issues = [r for r in rows if r["issue_status"] not in ("Closed", "Resolved")]
    closed_issues = [r for r in rows if r["issue_status"] in ("Closed", "Resolved")]

    lines = ["## Issues\n"]

    # Open issues at tier 2
    for r in open_issues:
        if depth <= DEPTH_SUMMARY:
            line = f"- **{r['issue_uid']}:** {r['subject']} [{r['issue_status']}]"
            if r["issue_severity"]:
                line += f" — {r['issue_severity']}"
            if r["due_date"]:
                line += f" — Due: {_fmt_date(r['due_date'])}"
            lines.append(line + "\n")
        elif depth == DEPTH_FULL:
            lines.append(assemble_issue(conn, r["id"], DEPTH_SUMMARY))

    # Closed issues at tier 3 (reference only, capped)
    if closed_issues:
        closed_items = []
        for r in closed_issues:
            closed_items.append(
                f"- {r['issue_uid']}: {r['subject']} [Resolved {_fmt_date(r['resolved_date'] or r['activity_date'])}]\n"
            )
        closed_items = _truncated_list(closed_items, 5, "closed issues")
        lines.append("\n### Resolved Issues\n")
        for item in closed_items:
            lines.append(item)

    return "".join(lines)


@register("follow_ups")
def assemble_followups(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_SUMMARY, **kwargs) -> str:
    """Assemble follow-ups. record_id is client_id. Pass issue_id=N for issue-specific."""
    issue_id = kwargs.get("issue_id")
    if issue_id:
        rows = conn.execute(
            """SELECT activity_date, subject, follow_up_date, follow_up_done, disposition,
                      activity_type, contact_person
               FROM activity_log
               WHERE issue_id = ? AND item_kind != 'issue' AND follow_up_date IS NOT NULL
               ORDER BY follow_up_date DESC""",
            (issue_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT activity_date, subject, follow_up_date, follow_up_done, disposition,
                      activity_type, contact_person
               FROM activity_log
               WHERE client_id = ? AND follow_up_date IS NOT NULL AND item_kind != 'issue'
               ORDER BY follow_up_date DESC""",
            (record_id,),
        ).fetchall()

    if not rows:
        return ""

    lines = ["## Follow-Ups\n"]
    items = []
    for r in rows:
        status = "Done" if r["follow_up_done"] else "Pending"
        line = f"- {_fmt_date(r['follow_up_date'])} — {r['subject']} [{status}]"
        if r["disposition"]:
            line += f" ({r['disposition']})"
        items.append(line + "\n")

    total = len(items)
    items = _truncated_list(items, 5, "follow-ups")
    for item in items:
        lines.append(item)

    return "".join(lines)


@register("milestones")
def assemble_milestones(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_SUMMARY, **kwargs) -> str:
    """Assemble milestones. record_id is policy_id. Needs policy_uid passed via kwargs or looked up."""
    policy_uid = kwargs.get("policy_uid")
    if not policy_uid:
        row = conn.execute("SELECT policy_uid FROM policies WHERE id = ?", (record_id,)).fetchone()
        if not row:
            return ""
        policy_uid = row["policy_uid"]

    # Timeline milestones (detailed)
    timeline = conn.execute(
        """SELECT milestone_name, ideal_date, projected_date, completed_date, health
           FROM policy_timeline WHERE policy_uid = ? ORDER BY ideal_date""",
        (policy_uid,),
    ).fetchall()

    # Checklist milestones
    checklist = conn.execute(
        "SELECT milestone, completed FROM policy_milestones WHERE policy_uid = ? ORDER BY id",
        (policy_uid,),
    ).fetchall()

    if not timeline and not checklist:
        return ""

    lines = ["## Milestones\n"]

    if timeline:
        all_items = list(timeline)
        if len(all_items) >= 10:
            incomplete = [m for m in all_items if not m["completed_date"]]
            completed = [m for m in all_items if m["completed_date"]]
            show_full = incomplete
            show_ref = _truncated_list(
                [f"- {m['milestone_name']} [completed {_fmt_date(m['completed_date'])}]\n" for m in completed],
                5, "completed milestones"
            )
        else:
            show_full = all_items
            show_ref = []

        for m in show_full:
            status = "Done" if m["completed_date"] else m["health"] or "on_track"
            line = f"- **{m['milestone_name']}:** target {_fmt_date(m['ideal_date'])}"
            if m["completed_date"]:
                line += f", completed {_fmt_date(m['completed_date'])}"
            line += f" [{status}]"
            lines.append(line + "\n")

        if show_ref:
            lines.append("\n### Completed\n")
            for item in show_ref:
                lines.append(item)

    if checklist:
        lines.append("\n### Checklist\n")
        for item in checklist:
            mark = "x" if item["completed"] else " "
            lines.append(f"- [{mark}] {item['milestone']}\n")

    return "".join(lines)


@register("contacts")
def assemble_contacts(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_SUMMARY, **kwargs) -> str:
    """Assemble contacts. record_id is client_id. Pass policy_id=N for policy contacts."""
    policy_id = kwargs.get("policy_id")
    if policy_id:
        rows = conn.execute(
            """SELECT co.name, co.email, co.phone, cpa.role, cpa.is_placement_colleague
               FROM contact_policy_assignments cpa
               JOIN contacts co ON cpa.contact_id = co.id
               WHERE cpa.policy_id = ?""",
            (policy_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT co.name, co.email, co.phone, cca.role, cca.contact_type, cca.is_primary
               FROM contact_client_assignments cca
               JOIN contacts co ON cca.contact_id = co.id
               WHERE cca.client_id = ?
               ORDER BY cca.is_primary DESC, co.name""",
            (record_id,),
        ).fetchall()

    if not rows:
        return ""

    lines = ["## Contacts\n"]

    if len(rows) <= 5:
        for c in rows:
            role = c["role"] or ("Placement Colleague" if c.get("is_placement_colleague") else "Contact")
            line = f"- **{c['name']}** — {role}"
            if c["email"]:
                line += f" ({c['email']})"
            lines.append(line + "\n")
    else:
        # Primary at tier 2, rest at tier 3
        primary = [c for c in rows if c.get("is_primary") or c.get("is_placement_colleague")]
        others = [c for c in rows if c not in primary]
        for c in primary:
            role = c["role"] or "Primary Contact"
            line = f"- **{c['name']}** — {role}"
            if c["email"]:
                line += f" ({c['email']})"
            lines.append(line + "\n")
        ref_items = [f"- {c['name']} — {c['role'] or 'Contact'}\n" for c in others]
        ref_items = _truncated_list(ref_items, 5, "contacts")
        for item in ref_items:
            lines.append(item)

    return "".join(lines)


# ── Relationship key resolution ──────────────────────────────────────────────

def _resolve_keys(conn: sqlite3.Connection, primary_type: str, record_id: int) -> dict:
    """Given a primary record type and ID, return relationship keys for related assemblers."""
    keys = {"client_id": None, "policy_id": None, "policy_uid": None, "issue_id": None}

    if primary_type == "client":
        keys["client_id"] = record_id

    elif primary_type in ("policy", "renewal"):
        keys["policy_id"] = record_id
        row = conn.execute("SELECT client_id, policy_uid FROM policies WHERE id = ?", (record_id,)).fetchone()
        if row:
            keys["client_id"] = row["client_id"]
            keys["policy_uid"] = row["policy_uid"]

    elif primary_type == "issue":
        keys["issue_id"] = record_id
        row = conn.execute(
            "SELECT client_id, policy_id FROM activity_log WHERE id = ?", (record_id,)
        ).fetchone()
        if row:
            keys["client_id"] = row["client_id"]
            keys["policy_id"] = row["policy_id"]

    return keys


def _assemble_related_block(
    conn: sqlite3.Connection,
    record_type: str,
    keys: dict,
    depth: int,
) -> str:
    """Assemble a single related data block using the appropriate assembler and keys."""
    assembler = get_assembler(record_type)
    if not assembler:
        return ""

    # Determine the record_id to pass based on the record_type and available keys
    if record_type == "client":
        rid = keys.get("client_id")
    elif record_type == "policy":
        rid = keys.get("policy_id")
    elif record_type == "renewal":
        rid = keys.get("policy_id")
    elif record_type == "issue":
        rid = keys.get("issue_id")
    elif record_type in ("policies", "renewals"):
        rid = keys.get("client_id")
    elif record_type == "issues":
        rid = keys.get("client_id")
        if not rid:
            return ""
        return assemble_issues_for_record(conn, rid, depth, policy_id=keys.get("policy_id"))
    elif record_type == "follow_ups":
        rid = keys.get("client_id")
        if not rid:
            return ""
        return assemble_followups(conn, rid, depth, issue_id=keys.get("issue_id"))
    elif record_type == "milestones":
        rid = keys.get("policy_id")
        if not rid:
            return ""
        return assemble_milestones(conn, rid, depth, policy_uid=keys.get("policy_uid"))
    elif record_type == "contacts":
        rid = keys.get("client_id")
        if not rid:
            return ""
        return assemble_contacts(conn, rid, depth, policy_id=keys.get("policy_id"))
    else:
        return ""

    if not rid:
        return ""

    return assembler(conn, rid, depth)


# ── Public API ───────────────────────────────────────────────────────────────

def assemble_prompt(
    conn: sqlite3.Connection,
    template: dict,
    record_type: str,
    record_id: int,
) -> dict:
    """Assemble the full prompt. Returns {"full": str, "data_only": str}."""
    required = json.loads(template.get("required_record_types") or "[]")
    depth_overrides = json.loads(template.get("depth_overrides") or "null") or {}
    system_prompt = template.get("system_prompt", "")
    closing = template.get("closing_instruction", "")

    # Resolve relationship keys from primary record
    keys = _resolve_keys(conn, record_type, record_id)

    # Assemble primary record at full depth
    primary_assembler = get_assembler(record_type)
    if not primary_assembler:
        data_block = f"[Unknown record type: {record_type}]\n"
    else:
        data_block = primary_assembler(conn, record_id, DEPTH_FULL)

    # Assemble related blocks
    related_parts = []
    for rtype in required:
        # Skip the primary type (already assembled)
        if rtype == record_type:
            continue
        depth = depth_overrides.get(rtype, DEPTH_SUMMARY)
        block = _assemble_related_block(conn, rtype, keys, depth)
        if block.strip():
            related_parts.append(block)

    # Compose data section
    data_sections = [data_block]
    if related_parts:
        data_sections.extend(related_parts)

    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    data_sections.append(f"\n*Data as of: {timestamp}*\n")

    data_only = "\n".join(data_sections)

    # Compose full prompt
    parts = []
    if system_prompt:
        parts.append(system_prompt)
        parts.append("\n---\n")
    parts.append(data_only)
    if closing:
        parts.append("\n---\n")
        parts.append(closing)

    full = "\n".join(parts)

    return {"full": full, "data_only": data_only}
