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
    """Replace {{token}} placeholders. Missing/None values render as empty string."""
    for key, value in context.items():
        placeholder = "{{" + key + "}}"
        template_text = template_text.replace(placeholder, str(value) if value else "")
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
    })
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

    # Location description/notes from the projects table
    proj_row = conn.execute(
        "SELECT id, notes FROM projects WHERE client_id=? AND LOWER(TRIM(name))=LOWER(TRIM(?)) LIMIT 1",
        (client_id, project_name),
    ).fetchone()
    location_description = (proj_row["notes"] if proj_row else "") or ""
    location_project_id = proj_row["id"] if proj_row else 0

    ctx.update({
        "location_name": project_name or "",
        "location_description": location_description,
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
CONTEXT_TOKEN_GROUPS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "policy": [
        ("Policy", [
            ("policy_type", "Policy Type"),
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
        ("Client", _CLIENT_GROUP),
        ("Contact", _CLIENT_CONTACT_GROUP),
        ("Tracking", [("ref_tag", "Email Ref Tag")]),
    ],
    "client": [
        ("Client", _CLIENT_GROUP),
        ("Contact", _CLIENT_CONTACT_GROUP),
        ("Other", [
            ("today", "Today's Date"),
        ]),
        ("Tracking", [("ref_tag", "Email Ref Tag")]),
    ],
    "location": [
        ("Location", [
            ("location_name", "Location / Project"),
            ("location_description", "Location Description"),
            ("policy_count", "# of Policies"),
            ("policy_types", "Policy Types (list)"),
            ("carriers", "Carriers (list)"),
            ("policy_uids", "Policy IDs (list)"),
            ("total_premium", "Total Premium (sum)"),
            ("earliest_effective", "Earliest Effective"),
            ("latest_expiration", "Latest Expiration"),
        ]),
        ("Team", [
            ("team_names", "Team Names (list)"),
            ("team_emails", "Team Emails (list)"),
            ("placement_colleagues", "Placement Colleagues (list)"),
            ("placement_emails", "Placement Emails (list)"),
        ]),
        ("Client", _CLIENT_GROUP),
        ("Contact", _CLIENT_CONTACT_GROUP),
        ("Other", [
            ("today", "Today's Date"),
        ]),
        ("Tracking", [("ref_tag", "Email Ref Tag")]),
    ],
    "general": [
        ("General", [
            ("account_exec", "Account Exec"),
            ("today", "Today's Date"),
        ]),
    ],
    "followup": [
        ("Follow-Up", [
            ("subject", "Follow-Up Subject"),
            ("contact_person", "Contact Person"),
            ("duration_hours", "Duration (hrs)"),
            ("disposition", "Disposition"),
            ("thread_ref", "Thread Reference"),
        ]),
        ("Policy", [
            ("client_name", "Client Name"),
            ("policy_type", "Policy Type"),
            ("carrier", "Carrier"),
            ("policy_uid", "Policy ID"),
            ("project_name", "Project / Location"),
            ("project_name_sep", "Project (with separator)"),
        ]),
        ("Other", [
            ("today", "Today's Date"),
        ]),
        ("Tracking", [("ref_tag", "Email Ref Tag")]),
    ],
    "meeting": [
        ("Meeting", [
            ("meeting_title", "Meeting Title"),
            ("meeting_date", "Meeting Date"),
            ("meeting_time", "Meeting Time"),
            ("meeting_type", "Meeting Type"),
            ("meeting_location", "Location"),
            ("meeting_duration", "Duration"),
            ("client_name", "Client Name"),
            ("attendees", "Attendees"),
            ("decisions", "Decisions"),
            ("action_items", "Action Items"),
            ("meeting_notes", "Notes (first 500 chars)"),
        ]),
    ],
    "timeline": [
        ("Timeline", [
            ("days_to_expiry", "Days to Expiry"),
            ("drift_days", "Timeline Drift (days)"),
            ("blocking_reason", "Blocking Reason"),
            ("current_status", "Current Status"),
            ("milestones_complete", "Milestones Complete"),
            ("milestones_remaining", "Milestones Remaining"),
            ("contact_first_name", "Contact First Name"),
            ("nudge_count", "Nudge Count"),
            ("meeting_date", "Meeting Date"),
        ]),
    ],
}

# Flat token list per context — derived from groups for backward compat
CONTEXT_TOKENS: dict[str, list[tuple[str, str]]] = {
    ctx: [tok for _, tokens in groups for tok in tokens]
    for ctx, groups in CONTEXT_TOKEN_GROUPS.items()
}
