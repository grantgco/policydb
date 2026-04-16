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
    lines.append(_field("Contact Mobile", r.get("contact_mobile")))
    if r.get("is_prospect"):
        lines.append(_field("Prospect", "Yes"))
    lines.append(_field("Notes", r.get("notes")))

    # Service block
    service_parts = []
    service_parts.append(_field("Hourly Rate", r.get("hourly_rate"), "currency"))
    service_parts.append(_field("Review Cycle", r.get("review_cycle")))
    service_parts.append(_field("Last Reviewed", r.get("last_reviewed_at"), "date"))
    service_parts.append(_field("Follow-Up Date", r.get("follow_up_date"), "date"))
    service_block = "".join(service_parts)
    if service_block:
        lines.append("\n### Service\n")
        lines.append(service_block)

    # Strategy block — account_priorities, renewal_strategy, growth_opportunities, etc.
    strategy_parts = []
    strategy_parts.append(_field("Account Priorities", r.get("account_priorities")))
    strategy_parts.append(_field("Renewal Strategy", r.get("renewal_strategy")))
    strategy_parts.append(_field("Growth Opportunities", r.get("growth_opportunities")))
    strategy_parts.append(_field("Relationship Risk", r.get("relationship_risk")))
    strategy_parts.append(_field("Service Model", r.get("service_model")))
    strategy_parts.append(_field("Stewardship Date", r.get("stewardship_date"), "date"))
    strategy_block = "".join(strategy_parts)
    if strategy_block:
        lines.append("\n### Strategy\n")
        lines.append(strategy_block)

    # Client scratchpad
    sp_row = conn.execute(
        "SELECT content, updated_at FROM client_scratchpad WHERE client_id = ?",
        (record_id,),
    ).fetchone()
    if sp_row and (sp_row["content"] or "").strip():
        lines.append("\n### Client Scratchpad\n")
        lines.append(sp_row["content"].strip() + "\n")
        if sp_row["updated_at"]:
            lines.append(f"\n*Updated: {_fmt_date(sp_row['updated_at'][:10])}*\n")

    # Location (project) scratchpads for this client
    proj_sps = conn.execute(
        """SELECT pr.name, ps.content, ps.updated_at
           FROM project_scratchpad ps
           JOIN projects pr ON pr.id = ps.project_id
           WHERE pr.client_id = ?
             AND ps.content IS NOT NULL AND TRIM(ps.content) != ''
           ORDER BY pr.name""",
        (record_id,),
    ).fetchall()
    if proj_sps:
        lines.append("\n### Location Scratchpads\n")
        for ps in proj_sps:
            lines.append(f"\n**{ps['name']}:**\n")
            lines.append(ps["content"].strip() + "\n")

    # Recent saved notes (scope='client'). scope_id is TEXT so bind as string.
    notes = conn.execute(
        """SELECT content, created_at FROM saved_notes
           WHERE scope = 'client' AND scope_id = ?
           ORDER BY created_at DESC LIMIT 5""",
        (str(record_id),),
    ).fetchall()
    if notes:
        lines.append("\n### Recent Notes\n")
        for n in notes:
            content = (n["content"] or "").strip()
            if not content:
                continue
            date_str = _fmt_date((n["created_at"] or "")[:10]) if n["created_at"] else ""
            prefix = f"- *{date_str}:* " if date_str else "- "
            lines.append(f"{prefix}{content}\n")

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
    lines.append(_field("Layer Notation", r.get("layer_notation")))
    lines.append(_field("Attachment Point", r.get("attachment_point"), "currency"))
    lines.append(_field("Participation Of", r.get("participation_of"), "currency"))
    lines.append(_field("Renewal Status", r.get("renewal_status")))
    lines.append(_field("Commission Rate", f"{r['commission_rate']}%" if r.get("commission_rate") else None))
    lines.append(_field("Access Point", r.get("access_point")))
    lines.append(_field("Description", r.get("description")))
    lines.append(_field("Notes", r.get("notes")))

    # Exposure block — emits every linked exposure from client_exposures,
    # primary first.  Address comes from the linked location (projects row).
    exposure_rows = conn.execute(
        """SELECT ce.exposure_type, ce.amount, ce.denominator, ce.unit,
                  ce.year, pel.is_primary,
                  COALESCE(pr_pol.address, pr_exp.address) AS addr,
                  COALESCE(pr_pol.city,    pr_exp.city)    AS city,
                  COALESCE(pr_pol.state,   pr_exp.state)   AS state,
                  COALESCE(pr_pol.zip,     pr_exp.zip)     AS zip
           FROM policy_exposure_links pel
           JOIN client_exposures ce ON ce.id = pel.exposure_id
           LEFT JOIN policies pol ON pol.policy_uid = pel.policy_uid
           LEFT JOIN projects pr_pol ON pr_pol.id = pol.project_id
           LEFT JOIN projects pr_exp ON pr_exp.id = ce.project_id
           WHERE pel.policy_uid = ?
           ORDER BY pel.is_primary DESC, ce.exposure_type""",
        (r.get("policy_uid"),),
    ).fetchall()
    if exposure_rows:
        lines.append("\n### Exposure\n")
        for idx, er in enumerate(exposure_rows):
            prefix = "- **Primary**: " if er["is_primary"] else "- "
            amt_parts = []
            if er["amount"] is not None:
                try:
                    amt_parts.append(f"{float(er['amount']):,.0f}")
                except (TypeError, ValueError):
                    amt_parts.append(str(er["amount"]))
            if er["denominator"] and er["denominator"] != 1:
                amt_parts.append(f"per {er['denominator']}")
            if er["unit"]:
                amt_parts.append(f"({er['unit']})")
            amt_str = " ".join(amt_parts) if amt_parts else ""
            lines.append(f"{prefix}{er['exposure_type']}: {amt_str}\n")
            addr_parts = [er["addr"], er["city"], er["state"], er["zip"]]
            addr_line = ", ".join(p for p in addr_parts if p)
            if addr_line:
                lines.append(f"    Location: {addr_line}\n")
            if er["year"]:
                lines.append(f"    Year: {er['year']}\n")

    # Opportunity block (only if this is an opportunity)
    if r.get("is_opportunity"):
        lines.append("\n### Opportunity\n")
        lines.append(_field("Opportunity Status", r.get("opportunity_status")))
        lines.append(_field("Target Effective Date", r.get("target_effective_date"), "date"))

    # Program block
    if r.get("is_program") or r.get("program_id"):
        lines.append("\n### Program\n")
        if r.get("is_program"):
            lines.append(_field("Program Lead", "Yes"))
        if r.get("program_id"):
            prog = conn.execute(
                "SELECT name, program_uid FROM programs WHERE id = ?",
                (r["program_id"],),
            ).fetchone()
            if prog:
                lines.append(_field("Program", prog["name"] or prog["program_uid"]))
        lines.append(_field("Program Carriers", r.get("program_carriers")))
        lines.append(_field("Carrier Count", r.get("program_carrier_count")))

    # Project link
    if r.get("project_id"):
        proj = conn.execute(
            "SELECT name FROM projects WHERE id = ?", (r["project_id"],)
        ).fetchone()
        if proj and proj["name"]:
            lines.append(_field("Project", proj["name"]))

    # Lifecycle block
    lifecycle_parts = []
    lifecycle_parts.append(_field("Bound Date", r.get("bound_date"), "date"))
    lifecycle_parts.append(_field("Milestone Profile", r.get("milestone_profile")))
    lifecycle_parts.append(_field("Follow-Up Date", r.get("follow_up_date"), "date"))
    if r.get("flagged"):
        lifecycle_parts.append(_field("Flagged", "Yes"))
    lifecycle_parts.append(_field("Last Reviewed", r.get("last_reviewed_at"), "date"))
    lifecycle_parts.append(_field("Review Cycle", r.get("review_cycle")))
    lifecycle_block = "".join(lifecycle_parts)
    if lifecycle_block:
        lines.append("\n### Lifecycle\n")
        lines.append(lifecycle_block)

    # Policy scratchpad
    sp_row = conn.execute(
        "SELECT content, updated_at FROM policy_scratchpad WHERE policy_uid = ?",
        (r.get("policy_uid"),),
    ).fetchone()
    if sp_row and (sp_row["content"] or "").strip():
        lines.append("\n### Policy Scratchpad\n")
        lines.append(sp_row["content"].strip() + "\n")
        if sp_row["updated_at"]:
            lines.append(f"\n*Updated: {_fmt_date(sp_row['updated_at'][:10])}*\n")

    # Project (location) scratchpad for this policy's linked location
    if r.get("project_id"):
        proj_sp = conn.execute(
            "SELECT content, updated_at FROM project_scratchpad WHERE project_id = ?",
            (r["project_id"],),
        ).fetchone()
        if proj_sp and (proj_sp["content"] or "").strip():
            lines.append("\n### Location Scratchpad\n")
            lines.append(proj_sp["content"].strip() + "\n")
            if proj_sp["updated_at"]:
                lines.append(f"\n*Updated: {_fmt_date(proj_sp['updated_at'][:10])}*\n")

    # Recent saved notes (scope='policy', scope_id=policy_uid)
    if r.get("policy_uid"):
        notes = conn.execute(
            """SELECT content, created_at FROM saved_notes
               WHERE scope = 'policy' AND scope_id = ?
               ORDER BY created_at DESC LIMIT 5""",
            (r["policy_uid"],),
        ).fetchall()
        if notes:
            lines.append("\n### Recent Notes\n")
            for n in notes:
                content = (n["content"] or "").strip()
                if not content:
                    continue
                date_str = _fmt_date((n["created_at"] or "")[:10]) if n["created_at"] else ""
                prefix = f"- *{date_str}:* " if date_str else "- "
                lines.append(f"{prefix}{content}\n")

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
        """SELECT milestone_name, ideal_date, projected_date, completed_date,
                  health, accountability, waiting_on, prep_alert_date
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
            elif m["projected_date"] and m["projected_date"] != m["ideal_date"]:
                line += f", projected {_fmt_date(m['projected_date'])}"
            line += f" [{status}]"
            if m["accountability"]:
                line += f" [acct: {m['accountability']}]"
            if m["waiting_on"]:
                line += f" (waiting on: {m['waiting_on']})"
            if m["prep_alert_date"] and not m["completed_date"]:
                line += f" (prep alert: {_fmt_date(m['prep_alert_date'])})"
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

    # Days open (computed)
    try:
        created = r.get("activity_date")
        if created:
            start = date.fromisoformat(created[:10])
            if r.get("resolved_date"):
                end = date.fromisoformat(r["resolved_date"][:10])
            else:
                end = date.today()
            days_open = (end - start).days
            lines.append(_field("Days Open", days_open))
    except (ValueError, TypeError):
        pass

    # Auto-close / merge tracking
    if r.get("auto_close_reason"):
        lines.append(_field("Auto-Close Reason", r.get("auto_close_reason")))
        lines.append(_field("Auto-Closed At", r.get("auto_closed_at"), "date"))
    if r.get("merged_into_id"):
        tgt = conn.execute(
            "SELECT issue_uid FROM activity_log WHERE id = ?", (r["merged_into_id"],)
        ).fetchone()
        if tgt:
            lines.append(_field("Merged Into", tgt["issue_uid"]))
    if r.get("merged_from_issue_id"):
        src = conn.execute(
            "SELECT issue_uid FROM activity_log WHERE id = ?", (r["merged_from_issue_id"],)
        ).fetchone()
        if src:
            lines.append(_field("Merged From", src["issue_uid"]))

    # Linked policies — prefer junction table, fall back to single policy_id
    linked = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier, p.expiration_date
           FROM issue_policies ip
           JOIN policies p ON p.id = ip.policy_id
           WHERE ip.issue_id = ?
           ORDER BY p.expiration_date""",
        (record_id,),
    ).fetchall()
    if not linked and r.get("policy_id"):
        linked = conn.execute(
            """SELECT policy_uid, policy_type, carrier, expiration_date
               FROM policies WHERE id = ?""",
            (r["policy_id"],),
        ).fetchall()
    if linked:
        lines.append("\n### Linked Policies\n")
        for p in linked:
            line = f"- **{p['policy_uid']}** — {p['policy_type'] or ''}"
            if p["carrier"]:
                line += f" | {p['carrier']}"
            if p["expiration_date"]:
                line += f" | Exp: {_fmt_date(p['expiration_date'])}"
            lines.append(line + "\n")

    # Linked activities (with email metadata)
    activities = conn.execute(
        """SELECT activity_date, activity_type, subject, details, contact_person,
                  disposition, email_from, email_to, email_snippet
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
            if a["contact_person"]:
                line += f" (contact: {a['contact_person']})"
            line += "\n"
            # Email metadata on a second indented line
            if a["email_from"] or a["email_to"]:
                from_to = []
                if a["email_from"]:
                    from_to.append(f"From: {a['email_from']}")
                if a["email_to"]:
                    from_to.append(f"To: {a['email_to']}")
                line += "  > " + " | ".join(from_to) + "\n"
            if a["email_snippet"]:
                snippet = a["email_snippet"].strip().replace("\r", " ").replace("\n", " ")
                if len(snippet) > 200:
                    snippet = snippet[:200] + "…"
                line += f"  > \"{snippet}\"\n"
            items.append(line)
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

    # Issue scratchpad (working notes)
    sp_row = conn.execute(
        "SELECT content, updated_at FROM issue_scratchpad WHERE issue_id = ?",
        (record_id,),
    ).fetchone()
    if sp_row and (sp_row["content"] or "").strip():
        lines.append("\n### Issue Scratchpad\n")
        lines.append(sp_row["content"].strip() + "\n")
        if sp_row["updated_at"]:
            lines.append(f"\n*Updated: {_fmt_date(sp_row['updated_at'][:10])}*\n")

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

    # Open issues — full detail at DEPTH_FULL, one-liner otherwise
    for r in open_issues:
        if depth == DEPTH_FULL:
            lines.append(assemble_issue(conn, r["id"], DEPTH_FULL))
        else:
            line = f"- **{r['issue_uid']}:** {r['subject']} [{r['issue_status']}]"
            if r["issue_severity"]:
                line += f" — {r['issue_severity']}"
            if r["due_date"]:
                line += f" — Due: {_fmt_date(r['due_date'])}"
            lines.append(line + "\n")

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
            """SELECT activity_date, subject, details, follow_up_date, follow_up_done, disposition,
                      activity_type, contact_person
               FROM activity_log
               WHERE issue_id = ? AND item_kind != 'issue' AND follow_up_date IS NOT NULL
               ORDER BY follow_up_date DESC""",
            (issue_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT activity_date, subject, details, follow_up_date, follow_up_done, disposition,
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
        if r["contact_person"]:
            line += f" — contact: {r['contact_person']}"
        if r["details"] and r["details"].strip():
            details_text = r["details"].strip()
            if len(details_text) > 300:
                details_text = details_text[:300] + "…"
            line += f"\n  > {details_text}"
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
        raw_rows = conn.execute(
            """SELECT co.name, co.email, co.phone, co.mobile, cpa.role, cpa.is_placement_colleague
               FROM contact_policy_assignments cpa
               JOIN contacts co ON cpa.contact_id = co.id
               WHERE cpa.policy_id = ?""",
            (policy_id,),
        ).fetchall()
    else:
        raw_rows = conn.execute(
            """SELECT co.name, co.email, co.phone, co.mobile, cca.role, cca.contact_type, cca.is_primary
               FROM contact_client_assignments cca
               JOIN contacts co ON cca.contact_id = co.id
               WHERE cca.client_id = ?
               ORDER BY cca.is_primary DESC, co.name""",
            (record_id,),
        ).fetchall()

    if not raw_rows:
        return ""

    # Convert to dicts so .get() works and missing columns (which differ between
    # the two branches above) return None instead of raising KeyError on Row.
    rows = [dict(r) for r in raw_rows]

    lines = ["## Contacts\n"]

    def _contact_line(c: dict, bold: bool = True) -> str:
        role = c.get("role") or ("Placement Colleague" if c.get("is_placement_colleague") else "Contact")
        name = f"**{c['name']}**" if bold else c['name']
        parts = []
        if c.get("email"):
            parts.append(c["email"])
        if c.get("phone"):
            parts.append(c["phone"])
        if c.get("mobile") and c.get("mobile") != c.get("phone"):
            parts.append(f"mobile: {c['mobile']}")
        suffix = f" ({' | '.join(parts)})" if parts else ""
        return f"- {name} — {role}{suffix}\n"

    if len(rows) <= 5:
        for c in rows:
            lines.append(_contact_line(c))
    else:
        # Primary at tier 2, rest at tier 3
        primary = [c for c in rows if c.get("is_primary") or c.get("is_placement_colleague")]
        primary_ids = {id(c) for c in primary}
        others = [c for c in rows if id(c) not in primary_ids]
        for c in primary:
            lines.append(_contact_line(c))
        ref_items = [f"- {c['name']} — {c.get('role') or 'Contact'}\n" for c in others]
        ref_items = _truncated_list(ref_items, 5, "contacts")
        for item in ref_items:
            lines.append(item)

    return "".join(lines)


@register("opportunities")
def assemble_opportunities(conn: sqlite3.Connection, record_id: int, depth: int = DEPTH_SUMMARY) -> str:
    """Assemble opportunity-flagged policies for a client. record_id is client_id."""
    rows = conn.execute(
        """SELECT id, policy_uid, policy_type, carrier, premium,
                  target_effective_date, opportunity_status, description, notes
           FROM policies
           WHERE client_id = ? AND is_opportunity = 1 AND archived = 0
           ORDER BY target_effective_date, policy_type""",
        (record_id,),
    ).fetchall()
    if not rows:
        return ""

    lines = ["## Opportunities\n"]
    for p in rows:
        if depth == DEPTH_REFERENCE:
            lines.append(f"- {p['policy_uid']} — {p['policy_type']} [{p['opportunity_status'] or 'Prospect'}]\n")
            continue
        line = f"- **{p['policy_uid']}** — {p['policy_type']}"
        if p["carrier"]:
            line += f" | {p['carrier']}"
        if p["premium"]:
            line += f" | {_fmt_currency(p['premium'])}"
        if p["target_effective_date"]:
            line += f" | Target: {_fmt_date(p['target_effective_date'])}"
        if p["opportunity_status"]:
            line += f" [{p['opportunity_status']}]"
        lines.append(line + "\n")
        if depth == DEPTH_FULL:
            if p["description"]:
                lines.append(f"  Description: {p['description']}\n")
            if p["notes"] and p["notes"].strip():
                notes_text = p["notes"].strip()
                if len(notes_text) > 300:
                    notes_text = notes_text[:300] + "…"
                lines.append(f"  Notes: {notes_text}\n")

    return "".join(lines)


# ── Briefing / "Status & What's Next" assemblers ────────────────────────────
#
# These assemblers are scope-aware — they pick the most specific scope from the
# resolved keys dict (issue > policy > client) and run parallel SQL against the
# underlying tables. They intentionally do NOT call focus_queue.build_focus_queue()
# so that briefing formatting can drift from Action Center scoring.

def _accountability_map() -> dict:
    """Map disposition label → accountability ('waiting_external' | 'my_action' | ...).

    Mirrors the pattern used by queries.get_all_followups(). Imported lazily to
    avoid a hard dependency on the config module at import time.
    """
    try:
        from policydb import config as _cfg
        dispositions = _cfg.get("follow_up_dispositions", []) or []
    except Exception:
        return {}
    result = {}
    for d in dispositions:
        if isinstance(d, dict) and d.get("label"):
            result[d["label"]] = d.get("accountability") or "my_action"
    return result


@register("focus_items")
def assemble_focus_items(
    conn: sqlite3.Connection, record_id: int = 0, depth: int = DEPTH_FULL, **kwargs
) -> str:
    """Assemble a scoped focus queue — overdue / today / this week / waiting / scheduled.

    Scope priority: issue_id > policy_id > client_id. Runs four parallel SQL
    passes and buckets the results in Python. Returns '' when there is nothing
    to show so _assemble_related_block skips the section.
    """
    client_id = kwargs.get("client_id")
    policy_id = kwargs.get("policy_id")
    policy_uid = kwargs.get("policy_uid")
    issue_id = kwargs.get("issue_id")

    if not (client_id or policy_id or issue_id):
        return ""

    acct_map = _accountability_map()
    items: list[dict] = []

    # 1. Open activity follow-ups (scoped to the most specific key)
    fu_clause = ""
    fu_params: tuple = ()
    if issue_id:
        fu_clause = "a.issue_id = ?"
        fu_params = (issue_id,)
    elif policy_id:
        fu_clause = "a.policy_id = ?"
        fu_params = (policy_id,)
    elif client_id:
        fu_clause = "a.client_id = ?"
        fu_params = (client_id,)
    fu_rows = conn.execute(
        f"""SELECT a.id, a.subject, a.follow_up_date, a.disposition,
                   a.contact_person, a.email_from, a.activity_type,
                   CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue
            FROM activity_log a
            WHERE a.follow_up_done = 0
              AND a.follow_up_date IS NOT NULL
              AND a.item_kind != 'issue'
              AND a.auto_close_reason IS NULL
              AND {fu_clause}
            ORDER BY a.follow_up_date ASC""",
        fu_params,
    ).fetchall()
    for r in fu_rows:
        items.append({
            "kind": "followup",
            "date": r["follow_up_date"],
            "days_overdue": r["days_overdue"] or 0,
            "subject": r["subject"] or "(no subject)",
            "disposition": r["disposition"],
            "accountability": acct_map.get(r["disposition"] or "", "my_action"),
            "contact": r["contact_person"],
        })

    # 3. Open issues in scope (for policy scope, UNION direct + junction)
    if issue_id:
        iss_rows = []  # skip — we ARE the issue
    elif policy_id:
        iss_rows = conn.execute(
            """SELECT DISTINCT a.id, a.issue_uid, a.subject, a.issue_status,
                      a.issue_severity, a.due_date, a.activity_date,
                      CAST(julianday('now') - julianday(COALESCE(a.due_date, a.activity_date)) AS INTEGER) AS days_overdue
               FROM activity_log a
               LEFT JOIN issue_policies ip ON ip.issue_id = a.id
               WHERE a.item_kind = 'issue'
                 AND a.issue_status NOT IN ('Closed', 'Resolved')
                 AND (a.policy_id = ? OR ip.policy_id = ?)
               ORDER BY COALESCE(a.due_date, a.activity_date) ASC""",
            (policy_id, policy_id),
        ).fetchall()
    elif client_id:
        iss_rows = conn.execute(
            """SELECT id, issue_uid, subject, issue_status, issue_severity,
                      due_date, activity_date,
                      CAST(julianday('now') - julianday(COALESCE(due_date, activity_date)) AS INTEGER) AS days_overdue
               FROM activity_log
               WHERE item_kind = 'issue'
                 AND issue_status NOT IN ('Closed', 'Resolved')
                 AND client_id = ?
               ORDER BY COALESCE(due_date, activity_date) ASC""",
            (client_id,),
        ).fetchall()
    else:
        iss_rows = []
    for r in iss_rows:
        items.append({
            "kind": "issue",
            "date": r["due_date"] or r["activity_date"],
            "days_overdue": r["days_overdue"] or 0,
            "subject": f"{r['issue_uid']}: {r['subject']}",
            "disposition": r["issue_status"],
            "accountability": "my_action",
            "severity": r["issue_severity"],
            "contact": None,
        })

    # 4. Overdue / upcoming timeline milestones.
    #    At issue scope we deliberately skip the client-wide fallback: issue
    #    focus items should be about the issue itself, not flooded with every
    #    milestone on every policy for the same client.
    if policy_uid:
        ms_rows = conn.execute(
            """SELECT pt.milestone_name, pt.ideal_date, pt.projected_date,
                      pt.prep_alert_date, pt.accountability, pt.waiting_on, p.policy_uid,
                      CAST(julianday('now') - julianday(pt.ideal_date) AS INTEGER) AS days_overdue
               FROM policy_timeline pt
               JOIN policies p ON p.policy_uid = pt.policy_uid
               WHERE pt.completed_date IS NULL
                 AND p.archived = 0
                 AND pt.ideal_date <= date('now','+30 days')
                 AND pt.policy_uid = ?
               ORDER BY pt.ideal_date ASC""",
            (policy_uid,),
        ).fetchall()
    elif client_id and not issue_id:
        ms_rows = conn.execute(
            """SELECT pt.milestone_name, pt.ideal_date, pt.projected_date,
                      pt.prep_alert_date, pt.accountability, pt.waiting_on, p.policy_uid,
                      CAST(julianday('now') - julianday(pt.ideal_date) AS INTEGER) AS days_overdue
               FROM policy_timeline pt
               JOIN policies p ON p.policy_uid = pt.policy_uid
               WHERE pt.completed_date IS NULL
                 AND p.archived = 0
                 AND pt.ideal_date <= date('now','+30 days')
                 AND p.client_id = ?
               ORDER BY pt.ideal_date ASC""",
            (client_id,),
        ).fetchall()
    else:
        ms_rows = []
    for r in ms_rows:
        items.append({
            "kind": "milestone",
            "date": r["ideal_date"],
            "days_overdue": r["days_overdue"] or 0,
            "subject": f"{r['policy_uid']} — {r['milestone_name']}",
            "disposition": None,
            "accountability": r["accountability"] or "my_action",
            "waiting_on": r["waiting_on"],
            "contact": None,
        })

    if not items:
        return ""

    # Bucket in Python
    buckets = {
        "Overdue": [],
        "Today": [],
        "This Week": [],
        "Waiting on External": [],
        "Scheduled": [],
    }
    for it in items:
        d = it["days_overdue"] or 0
        if it["accountability"] == "waiting_external":
            buckets["Waiting on External"].append(it)
        elif d > 0:
            buckets["Overdue"].append(it)
        elif d == 0:
            buckets["Today"].append(it)
        elif -7 <= d < 0:
            buckets["This Week"].append(it)
        else:
            buckets["Scheduled"].append(it)

    # Sort each bucket: most overdue first
    for k in buckets:
        buckets[k].sort(key=lambda x: -1 * (x.get("days_overdue") or 0))

    lines = ["## Focus Items\n"]
    any_shown = False
    for bucket_name in ["Overdue", "Today", "This Week", "Waiting on External", "Scheduled"]:
        bucket = buckets[bucket_name]
        if not bucket:
            continue
        any_shown = True
        lines.append(f"\n### {bucket_name}\n")
        rows_md = []
        for it in bucket:
            date_str = _fmt_date(it["date"]) if it["date"] else ""
            days = it["days_overdue"] or 0
            if days > 0:
                days_str = f"{days}d overdue"
            elif days == 0:
                days_str = "today"
            else:
                days_str = f"in {-days}d"
            tag = f"[{it['kind']}]"
            if it.get("severity"):
                tag += f" [{it['severity']}]"
            line = f"- **{date_str}** — {it['subject']} ({days_str}) {tag}"
            if it.get("waiting_on"):
                line += f" (waiting on: {it['waiting_on']})"
            if it.get("disposition") and it["kind"] not in ("issue",):
                line += f" [{it['disposition']}]"
            if it.get("contact"):
                line += f" (contact: {it['contact']})"
            rows_md.append(line + "\n")
        for r in _truncated_list(rows_md, 15, "items"):
            lines.append(r)

    if not any_shown:
        return ""
    return "".join(lines)


@register("deliverables_due")
def assemble_deliverables_due(
    conn: sqlite3.Connection, record_id: int = 0, depth: int = DEPTH_FULL, **kwargs
) -> str:
    """Assemble deliverables due: upcoming policy_timeline milestones + open RFI bundles.

    Bucketed Overdue / Due This Week / Coming Up. Scoped by policy_uid or
    client_id (or both when policy scope implies a client).
    """
    client_id = kwargs.get("client_id")
    policy_uid = kwargs.get("policy_uid")

    if not (client_id or policy_uid):
        return ""

    milestones: list = []
    if policy_uid:
        milestones = conn.execute(
            """SELECT pt.milestone_name, pt.ideal_date, pt.projected_date,
                      pt.prep_alert_date, pt.accountability, pt.waiting_on,
                      p.policy_uid, p.policy_type, p.carrier,
                      CAST(julianday('now') - julianday(pt.ideal_date) AS INTEGER) AS days_overdue
               FROM policy_timeline pt
               JOIN policies p ON p.policy_uid = pt.policy_uid
               WHERE pt.completed_date IS NULL
                 AND p.archived = 0
                 AND (pt.prep_alert_date <= date('now','+14 days')
                      OR pt.ideal_date <= date('now','+30 days'))
                 AND pt.policy_uid = ?
               ORDER BY pt.ideal_date ASC""",
            (policy_uid,),
        ).fetchall()
    elif client_id:
        milestones = conn.execute(
            """SELECT pt.milestone_name, pt.ideal_date, pt.projected_date,
                      pt.prep_alert_date, pt.accountability, pt.waiting_on,
                      p.policy_uid, p.policy_type, p.carrier,
                      CAST(julianday('now') - julianday(pt.ideal_date) AS INTEGER) AS days_overdue
               FROM policy_timeline pt
               JOIN policies p ON p.policy_uid = pt.policy_uid
               WHERE pt.completed_date IS NULL
                 AND p.archived = 0
                 AND (pt.prep_alert_date <= date('now','+14 days')
                      OR pt.ideal_date <= date('now','+30 days'))
                 AND p.client_id = ?
               ORDER BY pt.ideal_date ASC""",
            (client_id,),
        ).fetchall()

    rfi_rows: list = []
    if client_id:
        rfi_rows = conn.execute(
            """SELECT id, title, status, send_by_date, created_at, rfi_uid,
                      (SELECT COUNT(*) FROM client_request_items
                       WHERE bundle_id = crb.id AND received = 0) AS open_items,
                      CAST(julianday('now') - julianday(send_by_date) AS INTEGER) AS days_overdue
               FROM client_request_bundles crb
               WHERE status IN ('open','sent','partial') AND client_id = ?
               ORDER BY COALESCE(send_by_date, created_at) ASC""",
            (client_id,),
        ).fetchall()

    if not milestones and not rfi_rows:
        return ""

    # Bucket
    buckets: dict[str, list[str]] = {"Overdue": [], "Due This Week": [], "Coming Up": []}

    def _bucket_for(days: int | None) -> str:
        if days is None:
            return "Coming Up"
        if days > 0:
            return "Overdue"
        if -7 <= days <= 0:
            return "Due This Week"
        return "Coming Up"

    for m in milestones:
        days = m["days_overdue"]
        bucket = _bucket_for(days)
        line = f"- {m['policy_uid']} — **{m['milestone_name']}** (ideal {_fmt_date(m['ideal_date'])})"
        if m["accountability"]:
            line += f" [acct: {m['accountability']}]"
        if m["waiting_on"]:
            line += f" (waiting on: {m['waiting_on']})"
        buckets[bucket].append(line + "\n")

    for r in rfi_rows:
        days = r["days_overdue"] if r["send_by_date"] else None
        bucket = _bucket_for(days)
        line = f"- RFI **{r['rfi_uid'] or r['id']}**: {r['title']}"
        if r["open_items"]:
            line += f" — {r['open_items']} items open"
        if r["send_by_date"]:
            line += f", due {_fmt_date(r['send_by_date'])}"
        line += f" [{r['status']}]"
        buckets[bucket].append(line + "\n")

    lines = ["## Deliverables Due\n"]
    any_shown = False
    for bucket_name in ["Overdue", "Due This Week", "Coming Up"]:
        rows = buckets[bucket_name]
        if not rows:
            continue
        any_shown = True
        lines.append(f"\n### {bucket_name}\n")
        for r in _truncated_list(rows, 15, "deliverables"):
            lines.append(r)
    if not any_shown:
        return ""
    return "".join(lines)


@register("recent_activity_log")
def assemble_recent_activity_log(
    conn: sqlite3.Connection, record_id: int = 0, depth: int = DEPTH_FULL, **kwargs
) -> str:
    """Assemble the last 10 activities scoped to issue/policy/client."""
    client_id = kwargs.get("client_id")
    policy_id = kwargs.get("policy_id")
    issue_id = kwargs.get("issue_id")

    if issue_id:
        where = "a.issue_id = ?"
        params: tuple = (issue_id,)
    elif policy_id:
        where = "a.policy_id = ?"
        params = (policy_id,)
    elif client_id:
        where = "a.client_id = ?"
        params = (client_id,)
    else:
        return ""

    rows = conn.execute(
        f"""SELECT a.activity_date, a.activity_type, a.subject, a.details, a.disposition,
                   a.contact_person, a.email_from, a.email_to, a.email_snippet,
                   a.policy_id, p.policy_uid
            FROM activity_log a
            LEFT JOIN policies p ON p.id = a.policy_id
            WHERE a.item_kind != 'issue' AND {where}
            ORDER BY a.activity_date DESC, a.id DESC
            LIMIT 15""",
        params,
    ).fetchall()

    if not rows:
        return ""

    lines = ["## Recent Activity\n"]
    for r in rows:
        line = f"- {_fmt_date(r['activity_date'])} — {r['activity_type'] or 'Activity'}: {r['subject'] or '(no subject)'}"
        if r["policy_uid"] and not issue_id and not policy_id:
            line += f" [{r['policy_uid']}]"
        if r["disposition"]:
            line += f" [{r['disposition']}]"
        if r["contact_person"]:
            line += f" (contact: {r['contact_person']})"
        line += "\n"
        if r["details"] and r["details"].strip():
            details_text = r["details"].strip().replace("\r", " ")
            if len(details_text) > 500:
                details_text = details_text[:500] + "…"
            line += f"  > Notes: {details_text}\n"
        if r["email_from"]:
            line += f"  > From {r['email_from']}"
            if r["email_to"]:
                line += f" → {r['email_to']}"
            line += "\n"
        if r["email_snippet"]:
            snippet = r["email_snippet"].strip().replace("\r", " ").replace("\n", " ")
            if len(snippet) > 400:
                snippet = snippet[:400] + "…"
            line += f"  > \"{snippet}\"\n"
        lines.append(line)

    return "".join(lines)


@register("pending_emails")
def assemble_pending_emails(
    conn: sqlite3.Connection, record_id: int = 0, depth: int = DEPTH_FULL, **kwargs
) -> str:
    """Assemble pending email actions — follow-ups that imply an email needs to go out."""
    client_id = kwargs.get("client_id")
    policy_id = kwargs.get("policy_id")
    issue_id = kwargs.get("issue_id")

    if issue_id:
        where = "a.issue_id = ?"
        params: tuple = (issue_id,)
    elif policy_id:
        where = "a.policy_id = ?"
        params = (policy_id,)
    elif client_id:
        where = "a.client_id = ?"
        params = (client_id,)
    else:
        return ""

    rows = conn.execute(
        f"""SELECT a.id, a.activity_date, a.subject, a.follow_up_date, a.disposition,
                   a.contact_person, a.email_to, a.email_from, a.activity_type
            FROM activity_log a
            WHERE a.item_kind != 'issue'
              AND a.follow_up_done = 0
              AND a.follow_up_date IS NOT NULL
              AND a.auto_close_reason IS NULL
              AND (LOWER(COALESCE(a.disposition,'')) LIKE '%email%'
                   OR LOWER(COALESCE(a.activity_type,'')) = 'email'
                   OR LOWER(COALESCE(a.disposition,'')) IN
                      ('needs_email_sent','awaiting_response','sent email','follow up email'))
              AND {where}
            ORDER BY a.follow_up_date ASC""",
        params,
    ).fetchall()

    if not rows:
        return ""

    lines = ["## Pending Emails\n"]
    for r in rows:
        recipient = r["email_to"] or r["contact_person"] or "(unknown)"
        date_str = _fmt_date(r["follow_up_date"]) if r["follow_up_date"] else ""
        line = f"- **{date_str}** — To {recipient}: {r['subject'] or '(no subject)'}"
        if r["disposition"]:
            line += f" [{r['disposition']}]"
        lines.append(line + "\n")
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
            # Resolve policy_uid so milestone/deliverables queries can scope
            # narrowly instead of falling back to client-wide.
            if row["policy_id"]:
                pol = conn.execute(
                    "SELECT policy_uid FROM policies WHERE id = ?", (row["policy_id"],)
                ).fetchone()
                if pol:
                    keys["policy_uid"] = pol["policy_uid"]

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
    elif record_type in ("policies", "renewals", "opportunities"):
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
    elif record_type == "focus_items":
        return assemble_focus_items(conn, 0, depth, **keys)
    elif record_type == "deliverables_due":
        return assemble_deliverables_due(conn, 0, depth, **keys)
    elif record_type == "recent_activity_log":
        return assemble_recent_activity_log(conn, 0, depth, **keys)
    elif record_type == "pending_emails":
        return assemble_pending_emails(conn, 0, depth, **keys)
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
