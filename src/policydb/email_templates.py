"""Email template token rendering and context builders."""

from __future__ import annotations

import sqlite3
from datetime import date

from policydb.utils import build_ref_tag


_REVIEW_CYCLE_LABELS = {
    "1w": "Weekly", "2w": "Every 2 Weeks", "1m": "Monthly",
    "1q": "Quarterly", "6m": "Every 6 Months", "1y": "Annually",
}


def _cycle_label(cycle: str | None) -> str:
    return _REVIEW_CYCLE_LABELS.get(cycle or "1w", cycle or "Weekly")


def render_tokens(template_text: str, context: dict) -> str:
    """Replace {{token}} placeholders. Missing/None values render as empty string.

    Any remaining {{...}} placeholders not found in context are stripped to avoid
    raw tokens appearing in composed emails.
    """
    import re
    for key, value in context.items():
        placeholder = "{{" + key + "}}"
        template_text = template_text.replace(placeholder, str(value) if value else "")
    # Strip any remaining unreplaced {{token}} placeholders
    template_text = re.sub(r"\{\{[^}]+\}\}", "", template_text)
    return template_text


def _resolve_primary_contact(conn: sqlite3.Connection, client_id: int, fallback_name: str = "", fallback_email: str = "") -> tuple[str, str]:
    """Return (name, email) of the flagged primary contact, falling back to legacy client fields."""
    primary = conn.execute(
        """SELECT co.name, co.email FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id=? AND cca.is_primary=1 AND cca.contact_type='client'""",
        (client_id,),
    ).fetchone()
    if primary:
        return primary["name"] or fallback_name, primary["email"] or fallback_email
    return fallback_name, fallback_email


def _fmt_currency(v) -> str:
    if not v:
        return ""
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return ""


def _fmt_date(d: str | None) -> str:
    """Format ISO date string as MM/DD/YYYY for email readability."""
    if not d:
        return ""
    try:
        return date.fromisoformat(d).strftime("%m/%d/%Y")
    except (ValueError, TypeError):
        return d


def _linked_account_names(conn: sqlite3.Connection, client_id: int) -> str:
    """Return comma-separated names of other clients in this client's linked group."""
    try:
        member = conn.execute(
            "SELECT group_id FROM client_group_members WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if not member:
            return ""
        names = conn.execute(
            """SELECT c.name FROM client_group_members cgm
               JOIN clients c ON cgm.client_id = c.id
               WHERE cgm.group_id = ? AND cgm.client_id != ?
               ORDER BY c.name""",
            (member["group_id"], client_id),
        ).fetchall()
        return ", ".join(r["name"] for r in names) if names else ""
    except Exception:
        return ""


def _build_policy_list_tokens(conn: sqlite3.Connection, client_id: int, project_name: str | None = None) -> dict:
    """Build formatted policy list and coverage summary tokens.

    If *project_name* is given the results are scoped to that location,
    otherwise all active policies for the client are included.
    """
    from collections import defaultdict

    params: list = [client_id]
    location_filter = ""
    if project_name is not None:
        location_filter = "AND LOWER(TRIM(COALESCE(p.project_name, ''))) = LOWER(TRIM(?))"
        params.append(project_name)

    policies = conn.execute(
        f"""SELECT p.policy_uid, p.policy_type, p.carrier, p.premium,
                   p.effective_date, p.expiration_date
            FROM policies p
            WHERE p.client_id = ? AND p.archived = 0
              AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
              {location_filter}
            ORDER BY p.policy_type, p.policy_uid""",
        params,
    ).fetchall()

    # -- policy_list: one bullet per policy --
    lines: list[str] = []
    for r in policies:
        parts = [r["policy_type"] or "Unknown"]
        if r["policy_uid"]:
            parts[0] += f" ({r['policy_uid']})"
        if r["carrier"]:
            parts.append(r["carrier"])
        prem = _fmt_currency(r["premium"])
        if prem:
            parts.append(prem)
        eff = _fmt_date(r["effective_date"])
        exp = _fmt_date(r["expiration_date"])
        if eff and exp:
            parts.append(f"{eff} to {exp}")
        elif eff:
            parts.append(f"Eff: {eff}")
        elif exp:
            parts.append(f"Exp: {exp}")
        lines.append("  - " + " \u2014 ".join(parts))

    # -- coverage_summary: grouped by policy_type --
    by_type: dict[str, dict] = defaultdict(lambda: {"count": 0, "premium": 0.0, "carriers": set()})
    for r in policies:
        t = r["policy_type"] or "Other"
        by_type[t]["count"] += 1
        by_type[t]["premium"] += float(r["premium"] or 0)
        if r["carrier"]:
            by_type[t]["carriers"].add(r["carrier"])
    summary_lines: list[str] = []
    for ptype in sorted(by_type):
        info = by_type[ptype]
        word = "policy" if info["count"] == 1 else "policies"
        summary_lines.append(f"{ptype} \u2014 {info['count']} {word} \u2014 {_fmt_currency(info['premium'])}")
        if info["carriers"]:
            summary_lines.append(f"  Carriers: {', '.join(sorted(info['carriers']))}")

    total = sum(float(r["premium"] or 0) for r in policies)

    return {
        "policy_list": "\n".join(lines),
        "coverage_summary": "\n".join(summary_lines),
        "client_total_premium": _fmt_currency(total),
        "client_policy_count": str(len(policies)),
    }


def _build_rfi_tokens(conn: sqlite3.Connection, client_id: int) -> dict:
    """Build RFI due-date list token for non-complete bundles."""
    try:
        bundles = conn.execute(
            """SELECT b.rfi_uid, b.title, b.send_by_date, b.status,
                      (SELECT COUNT(*) FROM client_request_items WHERE bundle_id = b.id) AS total_items,
                      (SELECT COUNT(*) FROM client_request_items WHERE bundle_id = b.id AND received = 0) AS outstanding
               FROM client_request_bundles b
               WHERE b.client_id = ? AND b.status != 'complete'
               ORDER BY b.send_by_date, b.rfi_uid""",
            (client_id,),
        ).fetchall()
    except Exception:
        return {"rfi_due_dates": "", "rfi_outstanding_count": "0"}

    lines: list[str] = []
    for b in bundles:
        parts = [b["rfi_uid"] or "RFI"]
        if b["title"]:
            parts[0] += f" {b['title']}"
        due = _fmt_date(b["send_by_date"])
        if due:
            parts.append(f"Due: {due}")
        parts.append(f"{b['outstanding']} of {b['total_items']} items outstanding")
        lines.append("  - " + " \u2014 ".join(parts))

    return {
        "rfi_due_dates": "\n".join(lines),
        "rfi_outstanding_count": str(len(bundles)),
    }


def _build_compliance_tokens(conn: sqlite3.Connection, client_id: int) -> dict:
    """Build compliance summary tokens for email templates."""
    try:
        from policydb.compliance import compute_compliance_summary, get_client_compliance_data
        data = get_client_compliance_data(conn, client_id)
        s = data["overall_summary"]
        gap_lines: list[str] = []
        for loc in data["locations"]:
            for line, gov in loc.get("governing", {}).items():
                if (gov.get("compliance_status") or "Needs Review").lower() == "gap":
                    gap_lines.append(line)
        return {
            "compliance_pct": str(s.get("compliance_pct", 0)),
            "compliance_gaps": str(s.get("gap", 0)),
            "compliance_gap_lines": ", ".join(sorted(set(gap_lines))) if gap_lines else "None",
        }
    except Exception:
        return {
            "compliance_pct": "0",
            "compliance_gaps": "0",
            "compliance_gap_lines": "",
        }


def _project_tokens(conn: sqlite3.Connection, project_id: int) -> dict:
    """Build token dict from a projects row.

    Reusable by location_context() and policy_context().  All keys use the
    ``location_`` prefix to avoid collision with ``address`` in _client_tokens.
    """
    if not project_id:
        return {}
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        return {}
    row = dict(row)
    # Build full address from components
    addr = row.get("address") or ""
    city = row.get("city") or ""
    state_zip = " ".join(filter(None, [row.get("state") or "", row.get("zip") or ""]))
    full_parts = [p for p in [addr, city, state_zip] if p]
    full_address = ", ".join(full_parts)
    return {
        "location_name": row.get("name") or "",
        "location_description": row.get("notes") or "",
        "location_type": row.get("project_type") or "",
        "location_status": row.get("status") or "",
        "location_value": _fmt_currency(row.get("project_value")),
        "location_start_date": _fmt_date(row.get("start_date")),
        "location_target_completion": _fmt_date(row.get("target_completion")),
        "location_insurance_needed_by": _fmt_date(row.get("insurance_needed_by")),
        "location_scope": row.get("scope_description") or "",
        "location_general_contractor": row.get("general_contractor") or "",
        "location_owner": row.get("owner_name") or "",
        "location_address": addr,
        "location_city": city,
        "location_state": row.get("state") or "",
        "location_zip": row.get("zip") or "",
        "location_full_address": full_address,
    }


def _client_tokens(conn: sqlite3.Connection, client_id: int, row) -> dict:
    """Build the shared client-field token dict used by policy, client, and location contexts."""
    primary_name, primary_email = _resolve_primary_contact(
        conn, client_id, row.get("primary_contact") or "", row.get("contact_email") or "",
    )
    # Coverage gaps from client_risks — a risk is a gap if no coverage line has adequacy='Adequate'
    try:
        gap_rows = conn.execute(
            """SELECT DISTINCT r.category FROM client_risks r
               WHERE r.client_id=? AND r.has_coverage=0
               ORDER BY r.category""",
            (client_id,),
        ).fetchall()
        coverage_gaps = ", ".join(sorted({r["category"] for r in gap_rows})) if gap_rows else ""
    except Exception:
        coverage_gaps = ""

    # Risk summary: high/critical risk categories
    try:
        high_risk_rows = conn.execute(
            "SELECT DISTINCT category FROM client_risks WHERE client_id=? AND severity IN ('High','Critical') ORDER BY category",
            (client_id,),
        ).fetchall()
        risk_summary = ", ".join(r["category"] for r in high_risk_rows) if high_risk_rows else ""
    except Exception:
        risk_summary = ""

    return {
        "client_name": row.get("name") or row.get("client_name") or "",
        "cn_number": row.get("cn_number") or "",
        "industry": row.get("industry_segment") or row.get("industry") or "",
        "address": row.get("address") or "",
        "business_description": row.get("business_description") or "",
        "coverage_gaps": coverage_gaps,
        "risk_summary": risk_summary,
        "primary_contact": primary_name,
        "primary_email": primary_email,
        "contact_phone": row.get("contact_phone") or "",
        "contact_mobile": row.get("contact_mobile") or "",
        "contact_organization": "",  # Populated per-contact if needed
        "preferred_contact_method": row.get("preferred_contact_method") or "",
        "account_exec": row.get("account_exec") or "",
        "website": row.get("website") or "",
        "client_since": row.get("client_since") or "",
        "date_onboarded": row.get("date_onboarded") or "",
        "referral_source": row.get("referral_source") or "",
        "fein": row.get("fein") or "",
        "internal_notes": row.get("notes") or "",
        "linked_accounts": _linked_account_names(conn, client_id),
    }


def policy_context(conn: sqlite3.Connection, policy_uid: str) -> dict:
    """Build token context dict from a policy + its client."""
    row = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
                  p.effective_date, p.expiration_date, p.premium, p.limit_amount,
                  p.deductible, p.project_name, p.project_id, p.renewal_status, p.account_exec,
                  p.access_point, p.last_reviewed_at, p.review_cycle,
                  p.first_named_insured, p.is_program,
                  c.id AS client_id, c.name AS client_name, c.industry_segment AS industry,
                  c.primary_contact, c.contact_email,
                  c.cn_number, c.address, c.business_description, c.notes,
                  c.contact_phone, c.contact_mobile,
                  c.preferred_contact_method, c.date_onboarded, c.referral_source,
                  c.website, c.client_since
           FROM policies p
           JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (policy_uid.upper(),),
    ).fetchone()
    if not row:
        return {}

    ctx = _client_tokens(conn, row["client_id"], dict(row))
    proj = row["project_name"] or ""
    # Placement colleague from contact_policy_assignments (is_placement_colleague flag)
    _pc_row = conn.execute(
        """SELECT co.name, co.email, co.phone FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.policy_id = (SELECT id FROM policies WHERE policy_uid = ?) AND cpa.is_placement_colleague = 1 LIMIT 1""",
        (policy_uid.upper(),),
    ).fetchone()
    pc_name = _pc_row["name"] if _pc_row else ""
    pc_email = _pc_row["email"] if _pc_row else ""
    pc_phone = _pc_row["phone"] if _pc_row else ""
    # Program carrier info (from program_carriers table)
    if row["is_program"]:
        carrier_rows = conn.execute(
            "SELECT carrier FROM program_carriers WHERE program_id = ? ORDER BY sort_order",
            (row["id"],),
        ).fetchall()
        ctx["program_carriers"] = ", ".join(r["carrier"] for r in carrier_rows)
        ctx["program_carrier_count"] = str(len(carrier_rows))
    else:
        ctx["program_carriers"] = ""
        ctx["program_carrier_count"] = ""

    # COPE data from linked location
    cope_data: dict = {}
    if row["project_id"]:
        _cope_row = conn.execute(
            "SELECT * FROM cope_data WHERE project_id = ?", (row["project_id"],)
        ).fetchone()
        if _cope_row:
            cope_data = dict(_cope_row)

    # Project/location fields from linked project
    proj_tokens = _project_tokens(conn, row["project_id"]) if row["project_id"] else {}

    ctx.update({
        "policy_type": row["policy_type"] or "",
        "carrier": row["carrier"] or "",
        "policy_uid": row["policy_uid"] or "",
        "policy_number": row["policy_number"] or "",
        "first_named_insured": row["first_named_insured"] or "",
        "effective_date": row["effective_date"] or "",
        "expiration_date": row["expiration_date"] or "",
        "premium": _fmt_currency(row["premium"]),
        "limit": _fmt_currency(row["limit_amount"]),
        "deductible": _fmt_currency(row["deductible"]),
        "project_name": proj,
        "project_name_sep": f" \u2014 {proj}" if proj else "",
        "renewal_status": row["renewal_status"] or "",
        "access_point": row["access_point"] or "",
        "placement_colleague": pc_name,
        "placement_colleague_name": pc_name,
        "placement_colleague_email": pc_email,
        "placement_colleague_phone": pc_phone,
        "last_reviewed_at": row["last_reviewed_at"] or "",
        "review_cycle": _cycle_label(row["review_cycle"]),
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
        "ref_tag": build_ref_tag(
            cn_number=ctx.get("cn_number") or "",
            client_id=row["client_id"],
            policy_uid=row["policy_uid"] or "",
            project_id=row["project_id"] or 0,
        ),
        # COPE tokens
        "construction_type": cope_data.get("construction_type", ""),
        "year_built": str(cope_data.get("year_built", "")) if cope_data.get("year_built") else "",
        "stories": str(cope_data.get("stories", "")) if cope_data.get("stories") else "",
        "sq_footage": str(int(cope_data["sq_footage"])) if cope_data.get("sq_footage") else "",
        "sprinklered": cope_data.get("sprinklered", ""),
        "roof_type": cope_data.get("roof_type", ""),
        "occupancy_description": cope_data.get("occupancy_description", ""),
        "protection_class": cope_data.get("protection_class", ""),
        "total_insurable_value": _fmt_currency(cope_data.get("total_insurable_value")),
    })
    # Overlay project tokens — fill gaps only so policy fields take precedence
    for k, v in proj_tokens.items():
        if k not in ctx or not ctx[k]:
            ctx[k] = v

    # Sub-coverages (table may not exist on older DBs before migration 090)
    try:
        sub_rows = conn.execute(
            "SELECT coverage_type FROM policy_sub_coverages "
            "WHERE policy_id = ? ORDER BY sort_order, id",
            (row["id"],),
        ).fetchall()
        ctx["sub_coverages"] = ", ".join(r["coverage_type"] for r in sub_rows) if sub_rows else ""
    except Exception:
        ctx["sub_coverages"] = ""

    # Exposure/rate from linked exposures
    from policydb.exposures import get_policy_exposures
    exp_links = get_policy_exposures(conn, row["policy_uid"] if "policy_uid" in row.keys() else "")
    primary = next((e for e in exp_links if e["is_primary"]), None)
    ctx["exposure_type"] = primary["exposure_type"] if primary else ""
    ctx["exposure_amount"] = "${:,.0f}".format(primary["amount"]) if primary and primary["amount"] else ""
    ctx["exposure_denominator"] = str(primary["denominator"]) if primary else ""
    ctx["exposure_rate"] = "${:,.2f}".format(primary["rate"]) if primary and primary["rate"] is not None else ""
    ctx["exposure_rate_label"] = (
        f"${primary['rate']:,.2f} per ${primary['denominator']:,} of {primary['exposure_type'].lower()}"
        if primary and primary["rate"] is not None else ""
    )

    return ctx


def client_context(conn: sqlite3.Connection, client_id: int) -> dict:
    """Build token context dict from a client record."""
    raw = conn.execute(
        "SELECT * FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()
    if not raw:
        return {}
    row = dict(raw)
    ctx = _client_tokens(conn, row["id"], row)
    ctx.update({
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
        "ref_tag": build_ref_tag(
            cn_number=row.get("cn_number") or "",
            client_id=row["id"],
        ),
    })
    ctx.update(_build_policy_list_tokens(conn, row["id"]))
    ctx.update(_build_rfi_tokens(conn, row["id"]))
    ctx.update(_build_compliance_tokens(conn, row["id"]))
    return ctx


def location_context(conn: sqlite3.Connection, client_id: int, project_name: str) -> dict:
    """Build token context dict aggregated from all policies at a location."""
    # Client info
    _client_row = conn.execute(
        "SELECT * FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()
    if not _client_row:
        return {}
    client = dict(_client_row)

    ctx = _client_tokens(conn, client_id, client)

    # Aggregate policy data for this location
    policies = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier, p.premium,
                  p.effective_date, p.expiration_date
           FROM policies p
           WHERE p.client_id = ? AND p.archived = 0
             AND LOWER(TRIM(COALESCE(p.project_name, ''))) = LOWER(TRIM(?))
           ORDER BY p.policy_type""",
        (client_id, project_name),
    ).fetchall()

    policy_types = sorted({r["policy_type"] for r in policies if r["policy_type"]})
    carriers = sorted({r["carrier"] for r in policies if r["carrier"]})
    uids = [r["policy_uid"] for r in policies if r["policy_uid"]]
    total_premium = sum(float(r["premium"] or 0) for r in policies)
    eff_dates = [r["effective_date"] for r in policies if r["effective_date"]]
    exp_dates = [r["expiration_date"] for r in policies if r["expiration_date"]]

    # Team contacts from contact_policy_assignments
    team_rows = conn.execute(
        """SELECT DISTINCT co.name, co.email, cpa.is_placement_colleague
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? AND p.archived = 0
             AND LOWER(TRIM(COALESCE(p.project_name, ''))) = LOWER(TRIM(?))
             AND co.name IS NOT NULL AND co.name != ''""",
        (client_id, project_name),
    ).fetchall()

    team_names = sorted({r["name"] for r in team_rows if r["name"]})
    team_emails = sorted({r["email"] for r in team_rows if r["email"]})
    pc_rows = [r for r in team_rows if r["is_placement_colleague"]]
    pc_names = sorted({r["name"] for r in pc_rows if r["name"]})
    pc_emails = sorted({r["email"] for r in pc_rows if r["email"]})

    # Full project fields from the projects table
    proj_row = conn.execute(
        "SELECT id FROM projects WHERE client_id=? AND LOWER(TRIM(name))=LOWER(TRIM(?)) LIMIT 1",
        (client_id, project_name),
    ).fetchone()
    location_project_id = proj_row["id"] if proj_row else 0
    proj_tokens = _project_tokens(conn, location_project_id) if location_project_id else {}

    ctx.update(proj_tokens)
    ctx.update({
        "location_name": project_name or proj_tokens.get("location_name", ""),
        "policy_count": str(len(policies)),
        "policy_types": ", ".join(policy_types),
        "carriers": ", ".join(carriers),
        "policy_uids": ", ".join(uids),
        "total_premium": _fmt_currency(total_premium),
        "earliest_effective": min(eff_dates) if eff_dates else "",
        "latest_expiration": max(exp_dates) if exp_dates else "",
        "team_names": ", ".join(team_names),
        "team_emails": ", ".join(team_emails),
        "placement_colleagues": ", ".join(pc_names),
        "placement_emails": ", ".join(pc_emails),
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
        "ref_tag": build_ref_tag(
            cn_number=client.get("cn_number") or "",
            client_id=client_id,
            project_id=location_project_id,
        ),
    })
    ctx.update(_build_policy_list_tokens(conn, client_id, project_name))
    return ctx


def meeting_context(conn: sqlite3.Connection, meeting_id: int) -> dict:
    """Build token dict for meeting email templates."""
    meeting = conn.execute(
        """SELECT cm.*, c.name as client_name
           FROM client_meetings cm
           JOIN clients c ON c.id = cm.client_id
           WHERE cm.id = ?""",
        (meeting_id,),
    ).fetchone()
    if not meeting:
        return {}
    meeting = dict(meeting)

    attendees = conn.execute(
        "SELECT name, role FROM meeting_attendees WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchall()
    attendee_names = ", ".join(a["name"] for a in attendees)

    decisions = conn.execute(
        "SELECT description FROM meeting_decisions WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchall()
    decisions_text = "\n".join(f"- {d['description']}" for d in decisions)

    actions = conn.execute(
        "SELECT description, assignee, due_date FROM meeting_action_items WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchall()
    actions_text = "\n".join(
        f"- {a['description']} ({a['assignee'] or 'TBD'}, {a['due_date'] or 'No date'})"
        for a in actions
    )

    return {
        "meeting_title": meeting.get("title", ""),
        "meeting_date": meeting.get("meeting_date", ""),
        "meeting_time": meeting.get("meeting_time", ""),
        "meeting_type": meeting.get("meeting_type", ""),
        "meeting_location": meeting.get("location", ""),
        "meeting_duration": str(meeting.get("duration_hours", "") or ""),
        "client_name": meeting.get("client_name", ""),
        "attendees": attendee_names,
        "decisions": decisions_text,
        "action_items": actions_text,
        "meeting_notes": (meeting.get("notes", "") or "")[:500],
    }


def timeline_context(conn, policy_uid: str) -> dict:
    """Build token dict from policy_timeline data."""
    from policydb.timeline_engine import get_policy_timeline

    timeline = get_policy_timeline(conn, policy_uid)
    if not timeline:
        return {}

    # Get policy info for expiration
    policy = conn.execute(
        "SELECT expiration_date, policy_type FROM policies WHERE policy_uid = ?",
        (policy_uid,)
    ).fetchone()

    completed = [r for r in timeline if r.get("completed_date")]
    incomplete = [r for r in timeline if not r.get("completed_date")]

    # Find current active milestone (first incomplete)
    active = incomplete[0] if incomplete else None

    # Compute drift for active milestone
    drift_days = 0
    if active:
        ideal = date.fromisoformat(active["ideal_date"])
        projected = date.fromisoformat(active["projected_date"])
        drift_days = (ideal - projected).days  # negative = slipped

    # Days to expiry
    days_to_expiry = ""
    if policy and policy["expiration_date"]:
        exp = date.fromisoformat(policy["expiration_date"])
        days_to_expiry = str((exp - date.today()).days)

    # Build blocking reason from active milestone
    blocking_reason = ""
    if active and active.get("waiting_on"):
        blocking_reason = f"Waiting on {active['waiting_on']}"
    elif active and active.get("accountability") == "waiting_external":
        blocking_reason = f"Awaiting external response for {active['milestone_name']}"

    return {
        "days_to_expiry": days_to_expiry,
        "drift_days": str(abs(drift_days)) if drift_days else "0",
        "blocking_reason": blocking_reason,
        "current_status": active["accountability"].replace("_", " ").title() if active else "",
        "milestones_complete": f"{len(completed)} of {len(timeline)}",
        "milestones_remaining": ", ".join(r["milestone_name"] for r in incomplete),
        "contact_first_name": "",  # Filled from contact context when available
        "nudge_count": "",  # Filled from follow-up thread context when available
        "meeting_date": "",  # Filled from scheduled follow-up date when available
    }


def rfi_notify_context(conn, bundle_id: int) -> dict:
    """Build token dict for RFI receipt notification."""
    bundle = conn.execute(
        """SELECT b.id, b.client_id, b.title, b.status, b.rfi_uid, b.sent_at,
                  c.name AS client_name, c.cn_number
           FROM client_request_bundles b
           JOIN clients c ON c.id = b.client_id
           WHERE b.id = ?""",
        (bundle_id,),
    ).fetchone()
    if not bundle:
        return {}

    items = conn.execute(
        """SELECT description, received
           FROM client_request_items
           WHERE bundle_id = ?
           ORDER BY sort_order, id""",
        (bundle_id,),
    ).fetchall()

    received = [r["description"] for r in items if r["received"]]
    outstanding = [r["description"] for r in items if not r["received"]]

    ref_tag = bundle["rfi_uid"] or ""

    return {
        "rfi_uid": bundle["rfi_uid"] or "",
        "request_title": bundle["title"] or "",
        "client_name": bundle["client_name"] or "",
        "cn_number": bundle["cn_number"] or "",
        "bundle_status": bundle["status"] or "",
        "sent_at": bundle["sent_at"] or "",
        "received_items": received,
        "outstanding_items": outstanding,
        "ref_tag": ref_tag,
    }


def followup_context(row: dict) -> dict:
    """Build token context dict from a follow-up row dict."""
    proj = row.get("project_name") or ""
    return {
        "client_name": row.get("client_name") or "",
        "policy_type": row.get("policy_type") or "",
        "carrier": row.get("carrier") or "",
        "policy_uid": row.get("policy_uid") or "",
        "project_name": proj,
        "project_name_sep": f" \u2014 {proj}" if proj else "",
        "subject": row.get("subject") or "",
        "contact_person": row.get("contact_person") or "",
        "duration_hours": str(row["duration_hours"]) if row.get("duration_hours") else "",
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
        "disposition": row.get("disposition") or "",
        "thread_ref": f"COR-{row['thread_id']}" if row.get("thread_id") else "",
        "ref_tag": build_ref_tag(
            cn_number=row.get("cn_number") or "",
            client_id=row.get("client_id") or 0,
            policy_uid=row.get("policy_uid") or "",
            project_id=row.get("project_id") or 0,
            activity_id=row.get("id") or 0,
        ),
    }


# Reusable client token groups
_CLIENT_GROUP: list[tuple[str, str]] = [
    ("client_name", "Client Name"),
    ("cn_number", "CN Number"),
    ("fein", "FEIN"),
    ("industry", "Industry"),
    ("address", "Address"),
    ("business_description", "Business Description"),
    ("coverage_gaps", "Coverage Gaps"),
    ("risk_summary", "Risk Summary (High/Critical)"),
    ("date_onboarded", "Date Onboarded"),
    ("client_since", "Client Since"),
    ("referral_source", "Referral Source"),
    ("internal_notes", "Internal Notes"),
    ("linked_accounts", "Linked Accounts"),
]

_CLIENT_CONTACT_GROUP: list[tuple[str, str]] = [
    ("primary_contact", "Primary Contact"),
    ("primary_email", "Primary Email"),
    ("contact_phone", "Contact Phone"),
    ("contact_mobile", "Contact Mobile"),
    ("contact_organization", "Contact Organization"),
    ("preferred_contact_method", "Preferred Contact Method"),
    ("account_exec", "Account Exec"),
    ("website", "Website"),
]

# Grouped tokens per context — list of (group_name, [(key, label), ...])
# Consolidated to 2 contexts: policy (includes location, followup, timeline)
# and client (includes meeting).
CONTEXT_TOKEN_GROUPS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "policy": [
        ("Policy", [
            ("policy_type", "Policy Type"),
            ("sub_coverages", "Sub-Coverages"),
            ("carrier", "Carrier"),
            ("policy_uid", "Policy ID"),
            ("policy_number", "Policy Number"),
            ("first_named_insured", "First Named Insured"),
            ("project_name", "Project / Location"),
            ("project_name_sep", "Project (with separator)"),
            ("access_point", "Access Point"),
            ("placement_colleague", "Placement Colleague"),
            ("placement_colleague_name", "Placement Colleague Name"),
            ("placement_colleague_email", "Colleague Email"),
            ("placement_colleague_phone", "Colleague Phone"),
            ("renewal_status", "Renewal Status"),
            ("program_carriers", "Program Carriers"),
            ("program_carrier_count", "Program Carrier Count"),
        ]),
        ("Dates", [
            ("effective_date", "Effective Date"),
            ("expiration_date", "Expiration Date"),
            ("last_reviewed_at", "Last Reviewed"),
            ("review_cycle", "Review Cycle"),
            ("today", "Today's Date"),
        ]),
        ("Financials", [
            ("premium", "Premium"),
            ("limit", "Limit"),
            ("deductible", "Deductible"),
        ]),
        ("Exposure", [
            ("exposure_type", "Exposure Type"),
            ("exposure_amount", "Exposure Amount"),
            ("exposure_denominator", "Exposure Denominator"),
            ("exposure_rate", "Exposure Rate"),
            ("exposure_rate_label", "Rate Label (e.g. $0.50 per $100 of payroll)"),
        ]),
        ("Client", _CLIENT_GROUP),
        ("Contact", _CLIENT_CONTACT_GROUP),
        ("Location", [
            ("location_name", "Location / Project"),
            ("location_description", "Location Description"),
            ("location_type", "Location Type"),
            ("location_status", "Location Status"),
            ("location_value", "Project Value"),
            ("location_start_date", "Start Date"),
            ("location_target_completion", "Target Completion"),
            ("location_insurance_needed_by", "Insurance Needed By"),
            ("location_scope", "Scope Description"),
            ("location_general_contractor", "General Contractor"),
            ("location_owner", "Owner Name"),
            ("location_address", "Location Address"),
            ("location_city", "Location City"),
            ("location_state", "Location State"),
            ("location_zip", "Location ZIP"),
            ("location_full_address", "Full Location Address"),
            ("policy_count", "# of Policies"),
            ("total_premium", "Total Premium (sum)"),
            ("team_names", "Team Names (list)"),
            ("team_emails", "Team Emails (list)"),
            ("placement_colleagues", "Placement Colleagues (list)"),
            ("placement_emails", "Placement Emails (list)"),
            ("policy_list", "Policy List (location)"),
            ("coverage_summary", "Coverage Summary (location)"),
        ]),
        ("COPE", [
            ("construction_type", "Construction Type"),
            ("year_built", "Year Built"),
            ("stories", "Stories"),
            ("sq_footage", "Square Footage"),
            ("sprinklered", "Sprinklered"),
            ("roof_type", "Roof Type"),
            ("occupancy_description", "Occupancy"),
            ("protection_class", "Protection Class"),
            ("total_insurable_value", "Total Insurable Value"),
        ]),
        ("Follow-up", [
            ("subject", "Follow-Up Subject"),
            ("contact_person", "Contact Person"),
            ("duration_hours", "Duration (hrs)"),
            ("disposition", "Disposition"),
            ("thread_ref", "Thread Reference"),
        ]),
        ("Timeline", [
            ("days_to_expiry", "Days to Expiry"),
            ("drift_days", "Timeline Drift (days)"),
            ("blocking_reason", "Blocking Reason"),
            ("current_status", "Current Status"),
            ("milestones_complete", "Milestones Complete"),
            ("milestones_remaining", "Milestones Remaining"),
        ]),
        ("Tracking", [("ref_tag", "Email Ref Tag")]),
    ],
    "client": [
        ("Client", _CLIENT_GROUP),
        ("Contact", _CLIENT_CONTACT_GROUP),
        ("Book of Business", [
            ("policy_list", "Policy List"),
            ("coverage_summary", "Coverage Summary"),
            ("client_total_premium", "Total Premium"),
            ("client_policy_count", "Policy Count"),
            ("rfi_due_dates", "RFI Due Dates"),
            ("rfi_outstanding_count", "Outstanding RFIs"),
        ]),
        ("Meeting", [
            ("meeting_title", "Meeting Title"),
            ("meeting_date", "Meeting Date"),
            ("meeting_time", "Meeting Time"),
            ("meeting_type", "Meeting Type"),
            ("meeting_location", "Location"),
            ("meeting_duration", "Duration"),
            ("attendees", "Attendees"),
            ("decisions", "Decisions"),
            ("action_items", "Action Items"),
            ("meeting_notes", "Notes (first 500 chars)"),
        ]),
        ("Compliance", [
            ("compliance_pct", "Compliance %"),
            ("compliance_gaps", "Gap Count"),
            ("compliance_gap_lines", "Gap Coverage Lines"),
        ]),
        ("Other", [
            ("today", "Today's Date"),
        ]),
        ("Tracking", [("ref_tag", "Email Ref Tag")]),
    ],
}

# Flat token list per context — derived from groups for backward compat
CONTEXT_TOKENS: dict[str, list[tuple[str, str]]] = {
    ctx: [tok for _, tokens in groups for tok in tokens]
    for ctx, groups in CONTEXT_TOKEN_GROUPS.items()
}
