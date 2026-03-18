"""Export system: schedule, client, book, renewals, LLM context dumps."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from policydb import config as cfg
from policydb.analysis import build_program_audit
from policydb.display import fmt_currency, fmt_limit, fmt_pct, fmt_days

# Fields excluded from all client-facing (schedule) exports
_INTERNAL_FIELDS = {
    "commission_rate", "commission_amount", "prior_premium", "rate_change",
    "renewal_status", "placement_colleague", "underwriter_name",
    "underwriter_contact", "notes", "account_exec",
}

TODAY = date.today().isoformat()


def _schedule_rows_for_client(conn: sqlite3.Connection, client_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM v_schedule WHERE client_name = (SELECT name FROM clients WHERE id = ?)""",
        (client_id,),
    ).fetchall()


def _row_to_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict:
    d = dict(row)
    if exclude:
        for k in exclude:
            d.pop(k, None)
    return d


# ─── SCHEDULE OF INSURANCE ───────────────────────────────────────────────────

def export_schedule_md(conn: sqlite3.Connection, client_id: int, client_name: str) -> str:
    rows = _schedule_rows_for_client(conn, client_id)
    account_exec = cfg.get("default_account_exec", "Grant")
    total = sum(r["Premium"] or 0 for r in rows)

    lines = [
        "# Schedule of Insurance",
        "",
        f"**Insured:** {client_name}",
        f"**Prepared:** {TODAY}",
        f"**Prepared by:** {account_exec}, Marsh",
        "",
        "| First Named Insured | Line of Business | Carrier | Policy # | Effective | Expiration | Premium | Limit | Deductible | Form | Layer | Comments |",
        "|---------------------|------------------|---------|----------|-----------|------------|---------|-------|------------|------|-------|----------|",
    ]

    for r in rows:
        pnum = r["Policy Number"] or ""
        form = r["Form"] or ""
        layer = r["Layer"] or "Primary"
        comments = (r["Comments"] or "").replace("|", "\\|")
        named = (r["First Named Insured"] or "").replace("|", "\\|")
        lines.append(
            f"| {named} | {r['Line of Business']} | {r['Carrier']} | {pnum} | {r['Effective']} | {r['Expiration']}"
            f" | {fmt_currency(r['Premium'])} | {fmt_limit(r['Limit'])} | {fmt_limit(r['Deductible'])}"
            f" | {form} | {layer} | {comments} |"
        )

    lines += [
        "",
        f"**Total Annual Premium:** {fmt_currency(total)}",
        f"**Policies:** {len(rows)}",
    ]
    return "\n".join(lines)


def export_schedule_csv(conn: sqlite3.Connection, client_id: int) -> str:
    rows = _schedule_rows_for_client(conn, client_id)
    if not rows:
        return ""
    buf = io.StringIO()
    cols = [k for k in rows[0].keys() if k != "client_name"]
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r[k] for k in cols})
    return buf.getvalue()


def export_schedule_json(conn: sqlite3.Connection, client_id: int, client_name: str) -> str:
    rows = _schedule_rows_for_client(conn, client_id)
    policies = []
    for r in rows:
        d = dict(r)
        d.pop("client_name", None)
        policies.append(d)
    return json.dumps({
        "client": client_name,
        "prepared": TODAY,
        "total_premium": sum(r["Premium"] or 0 for r in rows),
        "policies": policies,
    }, indent=2, default=str)


# ─── LLM CLIENT EXPORT ───────────────────────────────────────────────────────

def export_llm_client_md(conn: sqlite3.Connection, client_id: int) -> str:
    from collections import defaultdict
    from policydb.analysis import layer_notation as _ln

    client = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    summary = conn.execute("SELECT * FROM v_client_summary WHERE id = ?", (client_id,)).fetchone()
    policies = conn.execute(
        "SELECT * FROM v_policy_status WHERE client_id = ? ORDER BY policy_type, layer_position",
        (client_id,),
    ).fetchall()
    pipeline = conn.execute(
        "SELECT * FROM v_renewal_pipeline WHERE client_name = ? ORDER BY expiration_date",
        (client["name"],),
    ).fetchall()
    activities = conn.execute(
        """SELECT a.*, c.name AS client_name, p.policy_uid, p.policy_type AS policy_type_ref
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.client_id = ? AND a.activity_date >= date('now', '-180 days')
           ORDER BY a.activity_date DESC""",
        (client_id,),
    ).fetchall()
    overdue = conn.execute(
        "SELECT * FROM v_overdue_followups WHERE client_name = ?",
        (client["name"],),
    ).fetchall()
    upcoming_followups = conn.execute(
        """SELECT a.id, a.activity_date, a.activity_type, a.subject, a.follow_up_date,
                  a.contact_person, p.policy_uid
           FROM activity_log a
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.client_id = ? AND a.follow_up_date >= date('now') AND a.follow_up_done = 0
           ORDER BY a.follow_up_date ASC""",
        (client_id,),
    ).fetchall()
    history = conn.execute(
        "SELECT * FROM premium_history WHERE client_id = ? ORDER BY policy_type, term_effective DESC",
        (client_id,),
    ).fetchall()
    project_notes_rows = conn.execute(
        "SELECT name AS project_name, notes FROM projects WHERE client_id = ? AND notes != '' ORDER BY name",
        (client_id,),
    ).fetchall()
    contacts = conn.execute(
        """SELECT co.name, co.email, co.phone, co.mobile, co.organization,
                  cca.title, cca.role, cca.assignment, cca.contact_type, cca.notes, cca.is_primary, cca.created_at
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id = ?
           ORDER BY cca.is_primary DESC, co.name""",
        (client_id,),
    ).fetchall()
    scratchpad_row = conn.execute(
        "SELECT content FROM client_scratchpad WHERE client_id = ?", (client_id,)
    ).fetchone()
    audit = build_program_audit(conn, client_id)

    policy_contacts_rows = conn.execute(
        """SELECT cpa.policy_id, co.name, co.email, co.phone, cpa.role, p.policy_uid
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? ORDER BY co.name""",
        (client_id,),
    ).fetchall()

    client_dict = dict(client)
    s = summary
    total_premium = s["total_premium"] if s else 0
    total_commission = s["total_commission"] if s else 0
    total_fees = s["total_fees"] if s else 0
    total_revenue = s["total_revenue"] if s else 0
    next_pol = pipeline[0] if pipeline else None

    # Build lookup maps
    project_notes_map = {r["project_name"]: r["notes"] for r in project_notes_rows}

    # Policy contacts grouped by policy_id
    from collections import defaultdict as _dd
    policy_contacts_map: dict = _dd(list)
    for pc in policy_contacts_rows:
        policy_contacts_map[pc["policy_id"]].append(dict(pc))

    # Primary location address per project (from most recent policy)
    project_addresses: dict[str, str] = {}
    for p in sorted([dict(r) for r in policies], key=lambda x: x.get("id", 0)):
        proj = (p.get("project_name") or "").strip()
        if proj not in project_addresses:
            parts = [p.get("exposure_address"), p.get("exposure_city"),
                     p.get("exposure_state"), p.get("exposure_zip")]
            addr = ", ".join(x for x in parts if x)
            if addr:
                project_addresses[proj] = addr

    # Tower layers grouped by project then tower_group
    tower_by_project: dict = defaultdict(lambda: defaultdict(list))
    for p_dict in [dict(p) for p in policies]:
        tg = p_dict.get("tower_group")
        if tg:
            proj = (p_dict.get("project_name") or "").strip()
            tower_by_project[proj][tg].append(p_dict)

    # Group policies by project
    policy_groups: dict[str, list] = defaultdict(list)
    for p in policies:
        proj = (dict(p).get("project_name") or "").strip()
        policy_groups[proj].append(dict(p))

    # Sort projects: named A-Z, blank ("Corporate / Standalone") last
    sorted_projects = sorted(policy_groups.keys(), key=lambda x: "\xff" if not x else x.lower())

    # ─── YAML frontmatter ────────────────────────────────────────────────────
    lines = [
        "---",
        "export_type: client_program_summary",
        f'client: "{client["name"]}"',
        f'industry: "{client["industry_segment"]}"',
        f'account_executive: "{client["account_exec"]}"',
        f'export_date: "{TODAY}"',
        f"total_policies: {len(policies)}",
        f"total_annual_premium: {fmt_currency(total_premium)}",
        f"estimated_annual_revenue: {fmt_currency(total_revenue)}",
    ]
    if next_pol:
        lines.append(
            f'next_renewal: "{next_pol["expiration_date"]} ({next_pol["days_to_renewal"]}d'
            f' — {next_pol["policy_uid"]}, {next_pol["policy_type"]})"'
        )
    lines += ["---", ""]

    # ─── Title + business description ────────────────────────────────────────
    lines += [f"# Client Program Summary: {client['name']}", ""]

    if client_dict.get("business_description"):
        lines += [client_dict["business_description"], ""]

    meta = f"**Industry:** {client['industry_segment']}"
    if client_dict.get("cn_number"):
        meta += f" | **CN:** {client['cn_number']}"
    meta += f" | **Onboarded:** {client['date_onboarded']} | **Account Executive:** {client['account_exec']}"
    if client_dict.get("client_since"):
        meta += f" | **Client Since:** {client_dict['client_since']}"
    if client_dict.get("website"):
        meta += f" | **Web:** {client_dict['website']}"
    if client_dict.get("preferred_contact_method"):
        meta += f" | **Preferred Contact:** {client_dict['preferred_contact_method']}"
    if client_dict.get("referral_source"):
        meta += f" | **Source:** {client_dict['referral_source']}"
    lines += [meta, ""]

    if client_dict.get("renewal_month"):
        import calendar
        month_name = calendar.month_name[client_dict["renewal_month"]]
        lines += [f"*Typical renewal month: {month_name}*", ""]

    # Contacts
    if contacts:
        lines += ["### Contacts", ""]
        for c in contacts:
            c = dict(c)
            primary_marker = " ★" if c.get("is_primary") else ""
            entry = f"- **{c['name']}{primary_marker}**"
            if c.get("title"):
                entry += f", {c['title']}"
            if c.get("role"):
                entry += f" ({c['role']})"
            if c.get("contact_type") == "internal":
                if c.get("assignment"):
                    entry += f" — {c['assignment']}"
                else:
                    entry += " [internal]"
            if c.get("email"):
                entry += f" — {c['email']}"
            if c.get("phone"):
                entry += f" · {c['phone']}"
            lines.append(entry)
        lines.append("")
    elif client_dict.get("primary_contact"):
        contact_line = f"- **{client['primary_contact']}**"
        if client_dict.get("contact_email"):
            contact_line += f" — {client['contact_email']}"
        if client_dict.get("contact_phone"):
            contact_line += f" · {client['contact_phone']}"
        lines += ["### Contacts", "", contact_line, ""]

    if client_dict.get("notes"):
        lines += ["### Internal Notes", "", client_dict["notes"], ""]

    scratchpad_content = (scratchpad_row["content"] if scratchpad_row else "").strip()
    if scratchpad_content:
        lines += ["### Working Notes", "", scratchpad_content, ""]

    # Risk / Exposure Profile
    risk_rows = conn.execute(
        """SELECT r.id, r.category, r.description, r.severity, r.has_coverage, r.policy_uid, r.notes,
                  r.source, r.review_date, r.identified_date
           FROM client_risks r WHERE r.client_id=?
           ORDER BY CASE r.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                    r.category""",
        (client_id,),
    ).fetchall()
    if risk_rows:
        lines += ["### Risk & Exposure Profile", ""]
        lines.append("| Category | Severity | Coverage | Description | Source | Notes |")
        lines.append("|----------|----------|----------|-------------|--------|-------|")
        for rr in risk_rows:
            rr = dict(rr)
            cov = f"Covered ({rr['policy_uid']})" if rr["has_coverage"] and rr["policy_uid"] else "Covered" if rr["has_coverage"] else "**GAP**"
            lines.append(f"| {rr['category']} | {rr['severity']} | {cov} | {rr['description'] or '—'} | {rr['source'] or '—'} | {rr['notes'] or '—'} |")
        lines.append("")
        # Coverage lines per risk
        for rr in risk_rows:
            rr = dict(rr)
            cl_rows = conn.execute(
                "SELECT coverage_line, adequacy, policy_uid, notes FROM risk_coverage_lines WHERE risk_id=? ORDER BY coverage_line",
                (rr["id"],),
            ).fetchall()
            if cl_rows:
                lines.append(f"**{rr['category']} — Coverage Lines:**")
                for cl in cl_rows:
                    cl = dict(cl)
                    pol = f" ({cl['policy_uid']})" if cl["policy_uid"] else ""
                    lines.append(f"- {cl['coverage_line']} — {cl['adequacy']}{pol}{' — ' + cl['notes'] if cl['notes'] else ''}")
                lines.append("")
            ctrl_rows = conn.execute(
                "SELECT control_type, description, status, responsible, target_date FROM risk_controls WHERE risk_id=? ORDER BY created_at",
                (rr["id"],),
            ).fetchall()
            if ctrl_rows:
                lines.append(f"**{rr['category']} — Controls:**")
                for ct in ctrl_rows:
                    ct = dict(ct)
                    resp = f" ({ct['responsible']})" if ct["responsible"] else ""
                    tgt = f" by {ct['target_date']}" if ct["target_date"] else ""
                    lines.append(f"- [{ct['status']}] {ct['description']} — {ct['control_type']}{resp}{tgt}")
                lines.append("")
        gaps = [dict(rr)["category"] for rr in risk_rows if not dict(rr).get("has_coverage")]
        if gaps:
            lines += [f"**Coverage gaps identified:** {', '.join(gaps)}", ""]

    # ─── Program Overview ────────────────────────────────────────────────────
    rev_detail = ""
    if total_commission and total_fees:
        rev_detail = f" ({fmt_currency(total_commission)} commission + {fmt_currency(total_fees)} flat fee)"
    elif total_commission:
        rev_detail = " (commission)"
    elif total_fees:
        rev_detail = " (flat fee)"

    lines += [
        "## Program Overview",
        "",
        f"- **Total annual premium:** {fmt_currency(total_premium)}",
        f"- **Estimated annual revenue:** {fmt_currency(total_revenue)}{rev_detail}",
        f"- **Active policies:** {len(policies)} ({audit['standalone_count']} standalone)",
        f"- **Coverage lines:** {', '.join(audit['coverage_lines'])}",
        f"- **Carriers:** {', '.join(audit['carriers'])}",
        "",
    ]
    if next_pol:
        lines += [
            f"Next renewal: **{next_pol['policy_type']}** ({next_pol['policy_uid']}) with "
            f"{next_pol['carrier']} — expires {next_pol['expiration_date']}, "
            f"{next_pol['days_to_renewal']} days out.",
            "",
        ]

    # ─── Renewal Pipeline ────────────────────────────────────────────────────
    if pipeline:
        lines += [
            "## Renewal Pipeline (Next 180 Days)",
            "",
            "| Policy | Line of Business | Carrier | Expires | Days | Premium | Urgency | Status | Colleague |",
            "|--------|------------------|---------|---------|------|---------|---------|--------|-----------|",
        ]
        for r in pipeline:
            lines.append(
                f"| {r['policy_uid']} | {r['policy_type']} | {r['carrier']}"
                f" | {r['expiration_date']} | {r['days_to_renewal']}"
                f" | {fmt_currency(r['premium'])} | {r['urgency']}"
                f" | {r['renewal_status']} | {r['placement_colleague'] or '—'} |"
            )
        lines.append("")

    # ─── Insurance Program (grouped by project) ───────────────────────────
    lines += ["## Insurance Program", ""]

    for proj in sorted_projects:
        group = policy_groups[proj]
        display_name = proj if proj else "Corporate / Standalone"
        lines += [f"### {display_name}", ""]

        addr = project_addresses.get(proj, "")
        if addr:
            lines += [f"**Location:** {addr}", ""]

        note = project_notes_map.get(proj, "")
        if note:
            lines += [note, ""]

        # Policy summary table
        lines += [
            "| UID | Line of Business | Carrier | Policy # | Effective | Expires | Premium | Limit | Ded. | Status |",
            "|-----|------------------|---------|----------|-----------|---------|---------|-------|------|--------|",
        ]
        for p in group:
            lines.append(
                f"| {p['policy_uid']} | {p['policy_type']} | {p['carrier']}"
                f" | {p.get('policy_number') or '—'} | {p['effective_date']} | {p['expiration_date']}"
                f" | {fmt_currency(p['premium'])} | {fmt_limit(p.get('limit_amount'))}"
                f" | {fmt_limit(p.get('deductible'))} | {p['renewal_status']} |"
            )
        lines.append("")

        # Per-policy narrative detail (description, placement, team, internal notes)
        for p in group:
            details = []
            if p.get("first_named_insured") and p["first_named_insured"] != client["name"]:
                details.append(f"First Named Insured: {p['first_named_insured']}")
            if p.get("access_point"):
                details.append(f"Access Point: {p['access_point']}")
            if p.get("description"):
                details.append(p["description"])
            # Policy team contacts (new system)
            team = policy_contacts_map.get(p.get("id", 0), [])
            if team:
                team_parts = []
                for tm in team:
                    part = tm["name"]
                    if tm.get("role"):
                        part += f" ({tm['role']})"
                    if tm.get("email"):
                        part += f" <{tm['email']}>"
                    team_parts.append(part)
                details.append("Team: " + "; ".join(team_parts))
            elif p.get("placement_colleague"):
                col = f"Placement: {p['placement_colleague']}"
                if p.get("placement_colleague_email"):
                    col += f" ({p['placement_colleague_email']})"
                details.append(col)
            if p.get("underwriter_name"):
                uw = f"Underwriter: {p['underwriter_name']}"
                if p.get("underwriter_contact"):
                    uw += f" ({p['underwriter_contact']})"
                details.append(uw)
            if p.get("notes"):
                details.append(f"Internal notes: {p['notes']}")
            if details:
                lines.append(f"**{p['policy_uid']} — {p['policy_type']}:** " + " | ".join(details))
        has_details = any(
            p.get("description") or p.get("placement_colleague") or p.get("notes")
            or p.get("underwriter_name") or p.get("first_named_insured")
            or p.get("access_point") or policy_contacts_map.get(p.get("id", 0))
            for p in group
        )
        if has_details:
            lines.append("")

        # Tower structure for this project (inline)
        proj_towers = tower_by_project.get(proj, {})
        if proj_towers:
            for tg_name in sorted(proj_towers.keys()):
                layers = sorted(
                    proj_towers[tg_name],
                    key=lambda lp: lp.get("attachment_point") or 0,
                    reverse=True,
                )
                total_tower_premium = sum(lp.get("premium") or 0 for lp in layers)
                lines += [
                    f"**Tower: {tg_name}** ({fmt_currency(total_tower_premium)} total, {len(layers)} carrier{'s' if len(layers) != 1 else ''})",
                    "",
                    "| Layer | Carrier | Limit | Premium | Colleague |",
                    "|-------|---------|-------|---------|-----------|",
                ]
                for lp in layers:
                    notation = _ln(lp.get("limit_amount"), lp.get("attachment_point"), lp.get("participation_of"))
                    lines.append(
                        f"| {notation or lp.get('layer_position', 'Primary')}"
                        f" | {lp['carrier']} | {fmt_limit(lp.get('limit_amount'))}"
                        f" | {fmt_currency(lp['premium'])} | {lp.get('placement_colleague') or '—'} |"
                    )
                lines.append("")

    # ─── Premium History ──────────────────────────────────────────────────────
    if history:
        lines += ["## Premium History", ""]
        current_type = None
        for r in history:
            if r["policy_type"] != current_type:
                current_type = r["policy_type"]
                lines += [
                    f"### {current_type}",
                    "",
                    "| Term | Carrier | Premium | Limit | Notes |",
                    "|------|---------|---------|-------|-------|",
                ]
            lines.append(
                f"| {r['term_effective']} → {r['term_expiration']}"
                f" | {r['carrier'] or '—'} | {fmt_currency(r['premium'])}"
                f" | {fmt_limit(r['limit_amount'])} | {r['notes'] or '—'} |"
            )
        lines.append("")

    # ─── Account Activity ────────────────────────────────────────────────────
    if activities:
        lines += [
            "## Account Activity (Last 180 Days)",
            "",
            "| Date | Type | Policy | Contact | Subject | Follow-Up |",
            "|------|------|--------|---------|---------|-----------|",
        ]
        detail_blocks = []
        for a in activities:
            subject = (a["subject"] or "").replace("|", "\\|")
            policy_ref = (a["policy_uid"] or "")
            lines.append(
                f"| {a['activity_date']} | {a['activity_type']}"
                f" | {policy_ref or '—'} | {a['contact_person'] or '—'} | {subject}"
                f" | {a['follow_up_date'] or '—'} |"
            )
            if a["details"] and a["details"].strip():
                detail_blocks.append((a["activity_date"], a["subject"], a["details"].strip(), policy_ref))
        lines.append("")
        if detail_blocks:
            lines += ["### Activity Notes", ""]
            for date, subject, details, policy_ref in detail_blocks:
                header = f"**{date} — {subject}**"
                if policy_ref:
                    header += f" [{policy_ref}]"
                lines.append(header)
                lines.append(f"{details}")
                lines.append("")

    # ─── Opportunities ────────────────────────────────────────────────────────
    opportunities = [p for p in [dict(r) for r in policies] if p.get("is_opportunity")]
    if opportunities:
        lines += ["## Opportunities / New Business", "", "| UID | Line | Status | Target Effective | Premium |",
                  "|-----|------|--------|-----------------|---------|"]
        for op in opportunities:
            lines.append(
                f"| {op['policy_uid']} | {op['policy_type']} | {op.get('opportunity_status') or '—'}"
                f" | {op.get('target_effective_date') or '—'} | {fmt_currency(op['premium'])} |"
            )
        lines.append("")

    # ─── Open Follow-Ups ─────────────────────────────────────────────────────
    if overdue or upcoming_followups:
        lines += ["## Open Follow-Ups", ""]
        if overdue:
            lines += ["**Overdue:**", ""]
            for o in overdue:
                lines.append(
                    f"- {o['follow_up_date']} ({o['days_overdue']}d overdue) — "
                    f"{o['activity_type']}: {o['subject']}"
                )
            lines.append("")
        if upcoming_followups:
            lines += ["**Upcoming:**", ""]
            for u in upcoming_followups:
                u = dict(u)
                entry = f"- {u['follow_up_date']} — {u['activity_type']}: {u['subject']}"
                if u.get("contact_person"):
                    entry += f" (w/ {u['contact_person']})"
                if u.get("policy_uid"):
                    entry += f" [{u['policy_uid']}]"
                lines.append(entry)
            lines.append("")

    return "\n".join(lines)


def export_llm_client_json(conn: sqlite3.Connection, client_id: int) -> str:
    client = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    summary = conn.execute("SELECT * FROM v_client_summary WHERE id = ?", (client_id,)).fetchone()
    policies = conn.execute(
        "SELECT * FROM v_policy_status WHERE client_id = ?", (client_id,)
    ).fetchall()
    activities = conn.execute(
        """SELECT a.* FROM activity_log a WHERE a.client_id = ?
           AND a.activity_date >= date('now', '-90 days')
           ORDER BY a.activity_date DESC""",
        (client_id,),
    ).fetchall()
    overdue = conn.execute(
        "SELECT * FROM v_overdue_followups WHERE client_name = ?", (client["name"],)
    ).fetchall()
    upcoming_fups = conn.execute(
        """SELECT a.id, a.activity_date, a.activity_type, a.subject, a.follow_up_date,
                  a.contact_person, p.policy_uid
           FROM activity_log a
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.client_id = ? AND a.follow_up_date >= date('now') AND a.follow_up_done = 0
           ORDER BY a.follow_up_date ASC""",
        (client_id,),
    ).fetchall()
    history = conn.execute(
        "SELECT * FROM premium_history WHERE client_id = ? ORDER BY policy_type, term_effective DESC",
        (client_id,),
    ).fetchall()
    proj_notes = conn.execute(
        "SELECT name AS project_name, notes FROM projects WHERE client_id = ? AND notes != '' ORDER BY name",
        (client_id,),
    ).fetchall()
    contacts = conn.execute(
        """SELECT co.name, co.email, co.phone, co.mobile, co.organization,
                  cca.title, cca.role, cca.assignment, cca.contact_type, cca.notes, cca.is_primary, cca.created_at
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id = ?
           ORDER BY cca.is_primary DESC, co.name""",
        (client_id,),
    ).fetchall()
    policy_contacts = conn.execute(
        """SELECT cpa.policy_id, co.name, co.email, co.phone, cpa.role, p.policy_uid
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? ORDER BY p.policy_uid, co.name""",
        (client_id,),
    ).fetchall()
    scratchpad = conn.execute(
        "SELECT content, updated_at FROM client_scratchpad WHERE client_id = ?", (client_id,)
    ).fetchone()
    risks = conn.execute(
        """SELECT id, category, description, severity, has_coverage, policy_uid, notes,
                  source, review_date, identified_date
           FROM client_risks WHERE client_id=?
           ORDER BY CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                    category""",
        (client_id,),
    ).fetchall()
    audit = build_program_audit(conn, client_id)

    # Group policy_contacts by policy_uid
    from collections import defaultdict as _dd2
    pc_by_uid: dict = _dd2(list)
    for pc in policy_contacts:
        pc_by_uid[pc["policy_uid"]].append(dict(pc))

    data = {
        "metadata": {
            "export_type": "client_program",
            "date": TODAY,
            "client": client["name"],
        },
        "client": dict(client),
        "summary": dict(summary) if summary else {},
        "contacts": [dict(c) for c in contacts],
        "working_notes": scratchpad["content"] if scratchpad else "",
        "project_notes": [dict(r) for r in proj_notes],
        "policies": [
            {
                **dict(p),
                "team": pc_by_uid.get(p["policy_uid"], []),
                "computed": {
                    "days_to_renewal": p["days_to_renewal"],
                    "urgency": p["urgency"],
                    "commission_amount": p["commission_amount"],
                    "rate_change": p["rate_change"],
                },
            }
            for p in policies
        ],
        "risk_profile": [
            {
                **dict(r),
                "coverage_lines": [dict(cl) for cl in conn.execute(
                    "SELECT coverage_line, adequacy, policy_uid, notes FROM risk_coverage_lines WHERE risk_id=?",
                    (r["id"],),
                ).fetchall()],
                "controls": [dict(ct) for ct in conn.execute(
                    "SELECT control_type, description, status, responsible, target_date FROM risk_controls WHERE risk_id=?",
                    (r["id"],),
                ).fetchall()],
            }
            for r in risks
        ],
        "coverage_analysis": {
            "gaps": audit["gap_observations"],
            "coverage_gaps_from_risks": [dict(r)["category"] for r in risks if not dict(r).get("has_coverage")],
            "tower_count": audit["tower_count"],
            "standalone_count": audit["standalone_count"],
            "duplicate_count": audit["duplicate_count"],
        },
        "premium_history": [dict(r) for r in history],
        "activities": [dict(a) for a in activities],
        "overdue_followups": [dict(o) for o in overdue],
        "upcoming_followups": [dict(u) for u in upcoming_fups],
    }
    return json.dumps(data, indent=2, default=str)


# ─── LLM BOOK EXPORT ─────────────────────────────────────────────────────────

def export_llm_book_md(conn: sqlite3.Connection) -> str:
    clients = conn.execute("SELECT * FROM v_client_summary ORDER BY name").fetchall()
    all_policies = conn.execute("SELECT * FROM v_policy_status").fetchall()
    pipeline = conn.execute(
        "SELECT * FROM v_renewal_pipeline ORDER BY expiration_date"
    ).fetchall()
    overdue = conn.execute("SELECT * FROM v_overdue_followups").fetchall()

    total_premium = sum(c["total_premium"] for c in clients)
    total_commission = sum(c["total_commission"] for c in clients)

    urgency_counts: dict[str, dict] = {}
    for p in all_policies:
        u = p["urgency"]
        if u not in urgency_counts:
            urgency_counts[u] = {"count": 0, "premium": 0}
        urgency_counts[u]["count"] += 1
        urgency_counts[u]["premium"] += p["premium"] or 0

    lines = [
        "---",
        "export_type: book_of_business",
        f'account_executive: "{cfg.get("default_account_exec", "Grant")}"',
        f'export_date: "{TODAY}"',
        f"total_clients: {len(clients)}",
        f"total_policies: {len(all_policies)}",
        f"total_premium: {fmt_currency(total_premium)}",
        f"total_commission: {fmt_currency(total_commission)}",
        f"urgent_renewals: {urgency_counts.get('URGENT', {}).get('count', 0)}",
        "---",
        "",
        "# Book of Business Summary",
        "",
        "## Key Metrics",
        f"- Clients: {len(clients)}",
        f"- Policies: {len(all_policies)}",
        f"- Total premium: {fmt_currency(total_premium)}",
        f"- Total commission: {fmt_currency(total_commission)}",
        "",
        "## Urgency Summary",
        "",
    ]
    for urgency_level in ["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"]:
        data = urgency_counts.get(urgency_level, {"count": 0, "premium": 0})
        lines.append(f"- {urgency_level}: {data['count']} policies, {fmt_currency(data['premium'])}")
    lines.append("")

    # Client summaries
    lines += ["## Client Summaries", ""]
    lines += [
        "| Client | Segment | Policies | Premium | Next Renewal | Flags |",
        "|--------|---------|----------|---------|--------------|-------|",
    ]
    for c in clients:
        flags = []
        if c["next_renewal_days"] is not None and c["next_renewal_days"] <= 90:
            flags.append("URGENT RENEWAL")
        if c["activity_last_90d"] == 0:
            flags.append("NO RECENT ACTIVITY")
        lines.append(
            f"| {c['name']} | {c['industry_segment']}"
            f" | {c['total_policies']} | {fmt_currency(c['total_premium'])}"
            f" | {fmt_days(c['next_renewal_days'])} | {', '.join(flags) or '—'} |"
        )
    lines.append("")

    # Stale renewals
    stale = [
        p for p in pipeline
        if p["renewal_status"] == "Not Started"
    ]
    if stale:
        lines += ["## Stale Renewals (Not Started — Within 180d)", ""]
        for p in stale:
            lines.append(f"- {p['client_name']} / {p['policy_type']} ({p['policy_uid']}) — expires {p['expiration_date']}, {p['days_to_renewal']}d")
        lines.append("")

    # Overdue follow-ups
    if overdue:
        lines += ["## Overdue Follow-Ups", ""]
        for o in overdue:
            lines.append(f"- {o['client_name']}: {o['subject']} (due {o['follow_up_date']}, {o['days_overdue']}d overdue)")
        lines.append("")

    return "\n".join(lines)


def export_llm_book_json(conn: sqlite3.Connection) -> str:
    clients = conn.execute("SELECT * FROM v_client_summary ORDER BY name").fetchall()
    all_policies = conn.execute("SELECT * FROM v_policy_status").fetchall()
    pipeline = conn.execute("SELECT * FROM v_renewal_pipeline ORDER BY expiration_date").fetchall()
    overdue = conn.execute("SELECT * FROM v_overdue_followups").fetchall()

    return json.dumps({
        "metadata": {"export_type": "book_of_business", "date": TODAY},
        "clients": [dict(c) for c in clients],
        "policies": [dict(p) for p in all_policies],
        "renewal_pipeline": [dict(p) for p in pipeline],
        "overdue_followups": [dict(o) for o in overdue],
    }, indent=2, default=str)


# ─── CLIENT EXPORT ────────────────────────────────────────────────────────────

def export_client_md(conn: sqlite3.Connection, client_id: int) -> str:
    """Full client profile + policies in Markdown."""
    return export_llm_client_md(conn, client_id)


def export_client_json(conn: sqlite3.Connection, client_id: int) -> str:
    return export_llm_client_json(conn, client_id)


def export_client_csv(conn: sqlite3.Connection, client_id: int) -> str:
    rows = conn.execute(
        "SELECT * FROM v_policy_status WHERE client_id = ? ORDER BY policy_type",
        (client_id,),
    ).fetchall()
    if not rows:
        return ""
    buf = io.StringIO()
    cols = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(dict(r))
    return buf.getvalue()


# ─── RENEWAL EXPORT ───────────────────────────────────────────────────────────

def export_renewals_md(conn: sqlite3.Connection, window_days: int = 180) -> str:
    rows = conn.execute(
        """SELECT * FROM v_renewal_pipeline WHERE days_to_renewal <= ?
           ORDER BY expiration_date""",
        (window_days,),
    ).fetchall()

    # Build policy_contacts map keyed by policy_uid
    pc_rows = conn.execute(
        """SELECT p.policy_uid, co.name, cpa.role, co.email
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.policy_uid IN (SELECT policy_uid FROM v_renewal_pipeline WHERE days_to_renewal <= ?)
           ORDER BY co.name""",
        (window_days,),
    ).fetchall()
    from collections import defaultdict as _ddr
    pc_map: dict = _ddr(list)
    for pc in pc_rows:
        pc_map[pc["policy_uid"]].append(pc["name"])

    total = sum(r["premium"] or 0 for r in rows)
    lines = [
        f"# Renewal Pipeline — Next {window_days} Days",
        "",
        f"**As of:** {TODAY}  ",
        f"**Policies:** {len(rows)}  ",
        f"**Premium at Risk:** {fmt_currency(total)}",
        "",
        "| UID | Client | Line | Carrier | Expires | Days | Urgency | Premium | Status | Team |",
        "|-----|--------|------|---------|---------|------|---------|---------|--------|------|",
    ]
    for r in rows:
        team = pc_map.get(r["policy_uid"])
        team_str = "; ".join(team) if team else (r["placement_colleague"] or "—")
        lines.append(
            f"| {r['policy_uid']} | {r['client_name']} | {r['policy_type']}"
            f" | {r['carrier']} | {r['expiration_date']} | {r['days_to_renewal']}"
            f" | {r['urgency']} | {fmt_currency(r['premium'])}"
            f" | {r['renewal_status']} | {team_str} |"
        )
    return "\n".join(lines)


def export_renewals_json(conn: sqlite3.Connection, window_days: int = 180) -> str:
    rows = conn.execute(
        """SELECT * FROM v_renewal_pipeline WHERE days_to_renewal <= ?
           ORDER BY expiration_date""",
        (window_days,),
    ).fetchall()
    return json.dumps({
        "window_days": window_days,
        "export_date": TODAY,
        "renewals": [dict(r) for r in rows],
    }, indent=2, default=str)


def export_renewals_csv(conn: sqlite3.Connection, window_days: int = 180) -> str:
    rows = conn.execute(
        """SELECT * FROM v_renewal_pipeline WHERE days_to_renewal <= ?
           ORDER BY expiration_date""",
        (window_days,),
    ).fetchall()
    if not rows:
        return ""
    buf = io.StringIO()
    cols = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow(dict(r))
    return buf.getvalue()


# ─── XLSX HELPERS ────────────────────────────────────────────────────────────

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_CURRENCY_FMT = '"$"#,##0.00'
_CURRENCY_COLS = {
    "Premium", "Limit", "Deductible", "premium", "limit_amount", "deductible",
    "prior_premium", "commission_amount", "exposure_amount",
}


def _write_sheet(wb: Workbook, title: str, rows: list, *, col_widths: dict[str, int] | None = None) -> None:
    ws = wb.create_sheet(title)
    if not rows:
        ws.append(["No data"])
        return

    headers = list(rows[0].keys())
    ws.append(headers)
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    for row in rows:
        ws.append([row[k] for k in headers])

    # Apply word-wrap and currency formatting to data cells
    _wrap_align = Alignment(wrap_text=True)
    for col_idx, col_name in enumerate(headers, 1):
        is_currency = col_name in _CURRENCY_COLS
        for row_idx in range(2, ws.max_row + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.alignment = _wrap_align
            if is_currency:
                cell.number_format = _CURRENCY_FMT

    # Column widths — use explicit overrides when provided, otherwise auto-size
    for col_idx, col_name in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        if col_widths and col_name in col_widths:
            ws.column_dimensions[col_letter].width = col_widths[col_name]
        else:
            max_len = max(
                len(str(col_name)),
                *(len(str(row[col_name] or "")) for row in rows),
            )
            ws.column_dimensions[col_letter].width = min(max_len + 4, 45)


def _wb_to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_schedule_xlsx(conn: sqlite3.Connection, client_id: int, client_name: str) -> bytes:
    rows = _schedule_rows_for_client(conn, client_id)
    # Strip client_name column — already in the filename
    cleaned = [{k: r[k] for k in r.keys() if k != "client_name"} for r in rows]
    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "Schedule of Insurance", cleaned)
    # Summary row
    ws = wb["Schedule of Insurance"]
    ws.append([])
    total = sum(r["Premium"] or 0 for r in rows)
    ws.append(["Total Annual Premium", fmt_currency(total)])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    # Project Notes sheet (if any)
    notes_rows = conn.execute(
        "SELECT name AS \"Project / Location\", notes AS \"Notes\" FROM projects WHERE client_id = ? AND notes != '' ORDER BY name",
        (client_id,),
    ).fetchall()
    if notes_rows:
        _write_sheet(wb, "Project Notes", [dict(r) for r in notes_rows])

    return _wb_to_bytes(wb)


def export_client_xlsx(conn: sqlite3.Connection, client_id: int) -> bytes:
    policies = conn.execute(
        "SELECT * FROM v_policy_status WHERE client_id = ? ORDER BY policy_type, layer_position",
        (client_id,),
    ).fetchall()
    history = conn.execute(
        "SELECT * FROM premium_history WHERE client_id = ? ORDER BY policy_type, term_effective DESC",
        (client_id,),
    ).fetchall()
    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "Policies", [dict(r) for r in policies])
    _write_sheet(wb, "Premium History", [dict(r) for r in history])
    return _wb_to_bytes(wb)


def export_full_xlsx(conn: sqlite3.Connection, client_id: int, client_name: str) -> bytes:
    """Full internal data export: all policy fields + contacts + notes + activities."""
    policies = conn.execute(
        """SELECT policy_uid, policy_type, carrier, policy_number,
                  effective_date, expiration_date, premium, limit_amount, deductible,
                  description, coverage_form, layer_position, tower_group, is_standalone,
                  project_name, renewal_status, commission_rate, commission_amount,
                  prior_premium, rate_change,
                  placement_colleague, placement_colleague_email,
                  underwriter_name, underwriter_contact,
                  follow_up_date, attachment_point, participation_of,
                  exposure_basis, exposure_amount, exposure_unit,
                  exposure_address, exposure_city, exposure_state, exposure_zip,
                  notes, account_exec, urgency, days_to_renewal,
                  first_named_insured, access_point
           FROM v_policy_status WHERE client_id = ?
           ORDER BY project_name, policy_type, layer_position""",
        (client_id,),
    ).fetchall()

    contacts = conn.execute(
        """SELECT co.name, cca.title, cca.role, cca.assignment, cca.contact_type,
                  co.email, co.phone, co.mobile, co.organization,
                  cca.notes, cca.is_primary, cca.created_at
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id = ?
           ORDER BY cca.is_primary DESC, co.name""",
        (client_id,),
    ).fetchall()

    policy_team = conn.execute(
        """SELECT p.policy_uid, p.policy_type, co.name, cpa.role, co.email, co.phone
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? ORDER BY p.policy_uid, co.name""",
        (client_id,),
    ).fetchall()

    project_notes = conn.execute(
        """SELECT name AS project_name, notes, created_at, updated_at
           FROM projects WHERE client_id = ? ORDER BY name""",
        (client_id,),
    ).fetchall()

    activities = conn.execute(
        """SELECT activity_date, activity_type, contact_person, subject, details,
                  follow_up_date, follow_up_done, account_exec, created_at
           FROM activity_log WHERE client_id = ?
           ORDER BY activity_date DESC""",
        (client_id,),
    ).fetchall()

    history = conn.execute(
        "SELECT policy_type, term_effective, term_expiration, carrier, premium, limit_amount, deductible, notes FROM premium_history WHERE client_id = ? ORDER BY policy_type, term_effective DESC",
        (client_id,),
    ).fetchall()

    scratchpad = conn.execute(
        "SELECT content, updated_at FROM client_scratchpad WHERE client_id = ?",
        (client_id,),
    ).fetchone()

    # Policy-level scratchpad notes (separate table keyed by policy_uid)
    policy_scratchpad_rows = conn.execute(
        """SELECT ps.policy_uid, ps.content AS notes, ps.updated_at
           FROM policy_scratchpad ps
           JOIN policies p ON ps.policy_uid = p.policy_uid
           WHERE p.client_id = ? AND ps.content != ''
           ORDER BY ps.policy_uid""",
        (client_id,),
    ).fetchall()

    # Risks register
    risks = conn.execute(
        """SELECT r.category, r.description, r.severity, r.has_coverage,
                  r.policy_uid, r.notes, r.source, r.review_date, r.identified_date
           FROM client_risks r WHERE r.client_id = ?
           ORDER BY CASE r.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1
                    WHEN 'Medium' THEN 2 ELSE 3 END, r.category""",
        (client_id,),
    ).fetchall()

    # Saved / pinned notes
    saved_notes = conn.execute(
        """SELECT scope, scope_id, content, created_at
           FROM saved_notes
           WHERE scope = 'client' AND scope_id = ?
           ORDER BY created_at DESC""",
        (str(client_id),),
    ).fetchall()

    # Billing accounts
    billing = conn.execute(
        """SELECT billing_id, entity_name, description, is_master
           FROM billing_accounts WHERE client_id = ? ORDER BY is_master DESC, billing_id""",
        (client_id,),
    ).fetchall()

    # Client profile row (FEIN, broker_fee, key metadata)
    client_profile = conn.execute(
        """SELECT name, cn_number, fein, broker_fee, industry_segment,
                  account_exec, date_onboarded, website
           FROM clients WHERE id = ?""",
        (client_id,),
    ).fetchone()

    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "Policies (Full)", [dict(r) for r in policies])
    _write_sheet(wb, "Contacts", [dict(r) for r in contacts])
    _write_sheet(wb, "Policy Team", [dict(r) for r in policy_team])
    _write_sheet(wb, "Project Notes", [dict(r) for r in project_notes])
    if policy_scratchpad_rows:
        _write_sheet(wb, "Policy Notes", [dict(r) for r in policy_scratchpad_rows])
    _write_sheet(wb, "Activities", [dict(r) for r in activities])
    _write_sheet(wb, "Premium History", [dict(r) for r in history])
    if risks:
        _write_sheet(wb, "Risks", [dict(r) for r in risks])
    if saved_notes:
        _write_sheet(wb, "Saved Notes", [dict(r) for r in saved_notes])
    if billing:
        _write_sheet(wb, "Billing Accounts", [dict(r) for r in billing])

    # Client Profile sheet (key metadata including FEIN and broker_fee)
    ws_profile = wb.create_sheet("Client Profile")
    ws_profile.append(["Field", "Value"])
    ws_profile["A1"].font = Font(bold=True)
    ws_profile["B1"].font = Font(bold=True)
    if client_profile:
        for field, val in dict(client_profile).items():
            ws_profile.append([field, val])
    ws_profile.column_dimensions["A"].width = 24
    ws_profile.column_dimensions["B"].width = 40

    # Internal Notes as a simple text sheet
    client_row = conn.execute("SELECT notes FROM clients WHERE id = ?", (client_id,)).fetchone()
    ws_notes = wb.create_sheet("Internal Notes")
    ws_notes.append(["Internal Notes"])
    ws_notes["A1"].font = Font(bold=True)
    if client_row and client_row["notes"]:
        for line in client_row["notes"].splitlines():
            ws_notes.append([line])
    else:
        ws_notes.append(["(no internal notes)"])
    ws_notes.column_dimensions["A"].width = 80

    # Working Notes as a simple text sheet
    ws = wb.create_sheet("Working Notes")
    ws.append(["Working Notes"])
    ws["A1"].font = Font(bold=True)
    if scratchpad and scratchpad["content"]:
        for line in scratchpad["content"].splitlines():
            ws.append([line])
        ws.append([])
        ws.append([f"Last updated: {scratchpad['updated_at']}"])
    else:
        ws.append(["(no working notes)"])
    ws.column_dimensions["A"].width = 80

    return _wb_to_bytes(wb)


def export_renewals_xlsx(conn: sqlite3.Connection, window_days: int = 180) -> bytes:
    rows = conn.execute(
        """SELECT * FROM v_renewal_pipeline WHERE days_to_renewal <= ?
           ORDER BY expiration_date""",
        (window_days,),
    ).fetchall()
    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, f"Renewals — Next {window_days}d", [dict(r) for r in rows])
    return _wb_to_bytes(wb)


# ─── REQUEST BUNDLE EXPORT ────────────────────────────────────────────────────

_RFI_COL_WIDTHS = {
    "Item": 45,
    "Coverage / Location": 35,
    "Category": 18,
    "Status": 14,
    "Received Date": 16,
    "Notes / Response": 45,
}


def _bundle_request_date(bundle: dict) -> str:
    """Return a human-readable request date from a bundle row.

    Prefers ``sent_at`` over ``created_at``.  Returns empty string when
    neither is available.
    """
    raw = bundle.get("sent_at") or bundle.get("created_at")
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw))
        return dt.strftime("%B %d, %Y")
    except (ValueError, TypeError):
        # Already a plain date string — return as-is
        return str(raw)[:10]


def _bundle_date_label(bundle: dict) -> str:
    """Return 'Sent: <date>' or 'Created: <date>' depending on which field is used."""
    fmt = _bundle_request_date(bundle)
    if not fmt:
        return ""
    if bundle.get("sent_at"):
        return f"Sent: {fmt}"
    return f"Created: {fmt}"


def export_request_bundle_xlsx(conn, bundle_id: int) -> bytes:
    """Export a request bundle as an XLSX spreadsheet for the client."""
    bundle = conn.execute(
        "SELECT b.*, c.name AS client_name FROM client_request_bundles b JOIN clients c ON b.client_id = c.id WHERE b.id = ?",
        (bundle_id,),
    ).fetchone()
    items = conn.execute(
        """SELECT cri.*, p.policy_type, p.carrier, p.project_name AS pol_project
           FROM client_request_items cri
           LEFT JOIN policies p ON cri.policy_uid = p.policy_uid
           WHERE cri.bundle_id = ?
           ORDER BY cri.received ASC, cri.sort_order ASC, cri.id ASC""",
        (bundle_id,),
    ).fetchall()

    rows = []
    for item in items:
        i = dict(item)
        # Build a clear coverage/location reference
        ref_parts = []
        if i.get("policy_type"):
            ref_parts.append(i["policy_type"])
        if i.get("carrier"):
            ref_parts.append(i["carrier"])
        if i.get("pol_project") or i.get("project_name"):
            proj = i.get("pol_project") or i.get("project_name")
            ref_parts.append(proj)
        rows.append({
            "Item": i["description"],
            "Coverage / Location": " — ".join(ref_parts) if ref_parts else "",
            "Category": i.get("category") or "",
            "Status": "Received" if i["received"] else "Outstanding",
            "Received Date": (i.get("received_at") or "")[:10] if i["received"] else "",
            "Notes / Response": i.get("notes") or "",
        })

    wb = Workbook()
    wb.remove(wb.active)
    bundle_dict = dict(bundle) if bundle else {}
    client_name = bundle_dict.get("client_name", "Client")
    title = bundle_dict.get("title", "Request")
    _write_sheet(wb, title[:31], rows, col_widths=_RFI_COL_WIDTHS)  # sheet name max 31 chars

    # Add request date metadata above the data table
    date_label = _bundle_date_label(bundle_dict)
    if date_label:
        ws = wb[title[:31]]
        ws.insert_rows(1)
        ws["A1"] = date_label
        ws["A1"].font = Font(bold=True)

    return _wb_to_bytes(wb)


def render_request_compose_text(conn, bundle_id: int) -> str:
    """Generate formatted email body listing outstanding and received items."""
    bundle = conn.execute(
        "SELECT * FROM client_request_bundles WHERE id = ?",
        (bundle_id,),
    ).fetchone()
    items = conn.execute(
        """SELECT cri.*, p.policy_type, p.carrier, p.project_name AS pol_project
           FROM client_request_items cri
           LEFT JOIN policies p ON cri.policy_uid = p.policy_uid
           WHERE cri.bundle_id = ?
           ORDER BY cri.received ASC, cri.sort_order ASC, cri.id ASC""",
        (bundle_id,),
    ).fetchall()

    outstanding = []
    received = []
    for item in items:
        i = dict(item)
        desc = i["description"]
        context_parts = []
        if i.get("policy_type"):
            context_parts.append(i["policy_type"])
        if i.get("carrier"):
            context_parts.append(i["carrier"])
        proj = i.get("pol_project") or i.get("project_name")
        if proj:
            context_parts.append(proj)
        suffix = f" — {', '.join(context_parts)}" if context_parts else ""

        if i["received"]:
            date_str = f" (received {i['received_at'][:10]})" if i.get("received_at") else ""
            received.append(f"  ☑ {desc}{suffix}{date_str}")
        else:
            outstanding.append(f"  □ {desc}{suffix}")

    lines = []

    # Add request date at the top
    if bundle:
        date_label = _bundle_date_label(dict(bundle))
        if date_label:
            lines.append(date_label)
            lines.append("")

    if outstanding:
        lines.append(f"OUTSTANDING ({len(outstanding)} item{'s' if len(outstanding) != 1 else ''}):")
        lines.extend(outstanding)
    if received:
        if outstanding:
            lines.append("")
        lines.append("RECEIVED — thank you:")
        lines.extend(received)

    return "\n".join(lines)


def export_client_requests_xlsx(conn, client_id: int) -> bytes:
    """Export all non-complete request bundles for a client as a multi-sheet XLSX."""
    bundles = conn.execute(
        "SELECT * FROM client_request_bundles WHERE client_id=? AND status != 'complete' ORDER BY updated_at DESC",
        (client_id,),
    ).fetchall()

    wb = Workbook()
    wb.remove(wb.active)

    any_items = False
    for b in bundles:
        b = dict(b)
        items = conn.execute(
            """SELECT cri.*, p.policy_type, p.carrier, p.project_name AS pol_project
               FROM client_request_items cri
               LEFT JOIN policies p ON cri.policy_uid = p.policy_uid
               WHERE cri.bundle_id = ?
               ORDER BY cri.received ASC, cri.sort_order ASC, cri.id ASC""",
            (b["id"],),
        ).fetchall()
        rows = []
        for item in items:
            i = dict(item)
            ref_parts = []
            if i.get("policy_type"):
                ref_parts.append(i["policy_type"])
            if i.get("carrier"):
                ref_parts.append(i["carrier"])
            if i.get("pol_project") or i.get("project_name"):
                proj = i.get("pol_project") or i.get("project_name")
                ref_parts.append(proj)
            rows.append({
                "Item": i["description"],
                "Coverage / Location": " — ".join(ref_parts) if ref_parts else "",
                "Category": i.get("category") or "",
                "Status": "Received" if i["received"] else "Outstanding",
                "Received Date": (i.get("received_at") or "")[:10] if i["received"] else "",
                "Notes / Response": i.get("notes") or "",
            })
        if rows:
            any_items = True
        sheet_name = (b.get("rfi_uid") or b["title"] or "Request")[:31]
        _write_sheet(wb, sheet_name, rows, col_widths=_RFI_COL_WIDTHS)

        # Add request date metadata above the data table
        date_label = _bundle_date_label(b)
        if date_label:
            ws = wb[sheet_name]
            ws.insert_rows(1)
            ws["A1"] = date_label
            ws["A1"].font = Font(bold=True)

    if not any_items and not bundles:
        _write_sheet(wb, "Requests", [{"Item": "No outstanding items"}])

    return _wb_to_bytes(wb)


def render_client_requests_compose_text(conn, client_id: int) -> str:
    """Generate formatted email body listing all outstanding items across all open bundles."""
    bundles = conn.execute(
        "SELECT * FROM client_request_bundles WHERE client_id=? AND status != 'complete' ORDER BY updated_at DESC",
        (client_id,),
    ).fetchall()

    bundle_count = len(bundles)
    all_lines = []
    total_outstanding = 0

    for b in bundles:
        b = dict(b)
        items = conn.execute(
            """SELECT cri.*, p.policy_type, p.carrier, p.project_name AS pol_project
               FROM client_request_items cri
               LEFT JOIN policies p ON cri.policy_uid = p.policy_uid
               WHERE cri.bundle_id = ?
               ORDER BY cri.received ASC, cri.sort_order ASC, cri.id ASC""",
            (b["id"],),
        ).fetchall()

        outstanding = []
        received = []
        for item in items:
            i = dict(item)
            desc = i["description"]
            context_parts = []
            if i.get("policy_type"):
                context_parts.append(i["policy_type"])
            if i.get("carrier"):
                context_parts.append(i["carrier"])
            proj = i.get("pol_project") or i.get("project_name")
            if proj:
                context_parts.append(proj)
            suffix = f" — {', '.join(context_parts)}" if context_parts else ""

            if i["received"]:
                date_str = f" (received {i['received_at'][:10]})" if i.get("received_at") else ""
                received.append(f"  \u2611 {desc}{suffix}{date_str}")
            else:
                outstanding.append(f"  \u25a1 {desc}{suffix}")

        total_outstanding += len(outstanding)
        rfi_label = b.get("rfi_uid") or b["title"] or "Request"
        title_label = b["title"] or "Information Request"
        date_suffix = ""
        date_label = _bundle_date_label(b)
        if date_label:
            date_suffix = f" ({date_label})"
        all_lines.append(f"\u2500\u2500\u2500 {rfi_label} \u2014 {title_label}{date_suffix} \u2500\u2500\u2500")
        if outstanding:
            all_lines.append(f"OUTSTANDING ({len(outstanding)} item{'s' if len(outstanding) != 1 else ''}):")
            all_lines.extend(outstanding)
        if received:
            if outstanding:
                all_lines.append("")
            all_lines.append("RECEIVED \u2014 thank you:")
            all_lines.extend(received)
        all_lines.append("")

    header = f"Outstanding items across {bundle_count} open request bundle{'s' if bundle_count != 1 else ''}:"
    return header + "\n\n" + "\n".join(all_lines).rstrip()


# ─── ACCOUNT SUMMARY ─────────────────────────────────────────────────────────


def build_account_summary(conn: sqlite3.Connection, client_id: int, days: int = 90, include_linked: bool = False) -> dict:
    """Build a structured account summary dict for a client."""
    from policydb.queries import (
        get_client_by_id, get_client_summary, get_activities,
        get_time_summary, get_policies_for_client,
        get_linked_group_for_client, get_all_followups,
    )
    from datetime import datetime

    client = get_client_by_id(conn, client_id)
    if not client:
        return {}
    client = dict(client)

    summary = get_client_summary(conn, client_id)
    summary = dict(summary) if summary else {}

    # Determine which client_ids to include
    client_ids = [client_id]
    linked_members = []
    linked_group = None
    if include_linked:
        linked_group = get_linked_group_for_client(conn, client_id)
        if linked_group:
            for m in linked_group.get("members", []):
                if m["client_id"] != client_id:
                    client_ids.append(m["client_id"])
                    linked_members.append({
                        "name": m["name"],
                        "total_premium": m.get("total_premium") or 0,
                        "total_policies": m.get("total_policies") or 0,
                        "next_renewal_days": m.get("next_renewal_days"),
                    })

    # Renewals (within 180 days)
    renewals = []
    for cid in client_ids:
        rows = conn.execute(
            """SELECT p.policy_uid, p.policy_type, p.carrier, p.expiration_date,
                      p.renewal_status, p.project_name,
                      CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal
               FROM policies p
               WHERE p.client_id = ? AND p.archived = 0
                 AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
                 AND p.expiration_date IS NOT NULL
                 AND julianday(p.expiration_date) - julianday('now') <= 180
               ORDER BY p.expiration_date""",
            (cid,),
        ).fetchall()
        # Attach milestone progress
        for r in rows:
            rd = dict(r)
            ms = conn.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) AS done FROM policy_milestones WHERE policy_uid=?",
                (rd["policy_uid"],),
            ).fetchone()
            rd["milestone_done"] = ms["done"] or 0
            rd["milestone_total"] = ms["total"] or 0
            if cid != client_id:
                c_row = conn.execute("SELECT name FROM clients WHERE id=?", (cid,)).fetchone()
                rd["client_name"] = c_row["name"] if c_row else ""
            renewals.append(rd)
    renewals.sort(key=lambda r: r.get("expiration_date") or "9999")

    # Follow-ups (overdue + upcoming 30d)
    overdue_all, upcoming_all = get_all_followups(conn, window=30)
    # Filter to our client_ids
    overdue_followups = [dict(r) for r in overdue_all if r.get("client_id") in client_ids]
    upcoming_followups = [dict(r) for r in upcoming_all if r.get("client_id") in client_ids]

    # Recent activity
    activities = []
    for cid in client_ids:
        acts = get_activities(conn, client_id=cid, days=days)
        for a in acts:
            ad = dict(a)
            if cid != client_id:
                ad["_from_linked"] = True
            activities.append(ad)
    activities.sort(key=lambda a: a.get("activity_date", ""), reverse=True)
    activities = activities[:20]  # cap at 20 most recent

    # Time summary
    time_data = get_time_summary(conn, client_id=client_id, days=days)

    # Coverage snapshot (all active policies)
    coverage = []
    for cid in client_ids:
        pols = get_policies_for_client(conn, cid)
        for p in pols:
            pd = dict(p)
            if not pd.get("is_opportunity"):
                coverage.append(pd)

    # High risks
    high_risks = [dict(r) for r in conn.execute(
        "SELECT * FROM client_risks WHERE client_id IN ({}) AND severity IN ('High', 'Critical') ORDER BY severity DESC".format(
            ",".join("?" * len(client_ids))
        ),
        client_ids,
    ).fetchall()]

    # Open request bundles
    open_requests = [dict(r) for r in conn.execute(
        """SELECT b.title, b.status,
                  (SELECT COUNT(*) FROM client_request_items WHERE bundle_id=b.id) AS total,
                  (SELECT COUNT(*) FROM client_request_items WHERE bundle_id=b.id AND received=0) AS outstanding
           FROM client_request_bundles b
           WHERE b.client_id IN ({}) AND b.status != 'complete'
           ORDER BY b.updated_at DESC""".format(",".join("?" * len(client_ids))),
        client_ids,
    ).fetchall()]

    # Renewal calendar — all active non-opp policies grouped by expiration month
    ph = ",".join("?" * len(client_ids))
    renewal_cal_rows = conn.execute(
        f"""SELECT strftime('%Y-%m', expiration_date) AS month_iso,
                   COUNT(*) AS count,
                   COALESCE(SUM(premium), 0) AS premium,
                   GROUP_CONCAT(policy_type, '|') AS types
            FROM policies
            WHERE client_id IN ({ph})
              AND archived = 0
              AND (is_opportunity = 0 OR is_opportunity IS NULL)
              AND expiration_date IS NOT NULL
            GROUP BY month_iso
            ORDER BY month_iso""",
        client_ids,
    ).fetchall()
    renewal_calendar = []
    for row in renewal_cal_rows:
        mi = row["month_iso"]
        try:
            from datetime import date as _d
            year, month = int(mi[:4]), int(mi[5:7])
            label = _d(year, month, 1).strftime("%b %Y")
        except (ValueError, TypeError):
            label = mi or ""
        type_counts: dict = {}
        for t in (row["types"] or "").split("|"):
            t = t.strip()
            if t:
                type_counts[t] = type_counts.get(t, 0) + 1
        renewal_calendar.append({
            "month_iso": mi,
            "month_label": label,
            "count": row["count"],
            "premium": row["premium"] or 0,
            "types": type_counts,
        })

    # Time by coverage line — hours logged per policy_type for this client
    time_by_policy = [dict(r) for r in conn.execute(
        f"""SELECT p.policy_type,
                   COALESCE(SUM(a.duration_hours), 0) AS hours,
                   COUNT(*) AS count
            FROM activity_log a
            JOIN policies p ON a.policy_id = p.id
            WHERE a.client_id IN ({ph})
              AND a.duration_hours IS NOT NULL AND a.duration_hours > 0
              AND a.activity_date >= date('now', ?)
            GROUP BY p.policy_type
            ORDER BY hours DESC""",
        client_ids + [f"-{days - 1} days"],
    ).fetchall()]

    # Notes snippet
    notes = (client.get("notes") or "")[:200]

    return {
        "client": {
            "name": client.get("name", ""),
            "cn_number": client.get("cn_number", ""),
            "industry": client.get("industry_segment", ""),
            "account_exec": cfg.get("default_account_exec", ""),
        },
        "financials": {
            "total_premium": summary.get("total_premium") or 0,
            "total_revenue": summary.get("total_revenue") or 0,
            "total_policies": summary.get("total_policies") or 0,
            "carrier_count": summary.get("carrier_count") or 0,
        },
        "renewals": renewals,
        "open_items": {
            "overdue": overdue_followups,
            "upcoming": upcoming_followups,
        },
        "recent_activity": activities,
        "time_summary": time_data,
        "coverage": coverage,
        "high_risks": high_risks,
        "open_requests": open_requests,
        "renewal_calendar": renewal_calendar,
        "time_by_policy": time_by_policy,
        "notes_snippet": notes,
        "linked_members": linked_members,
        "generated_at": datetime.now().strftime("%B %d, %Y %I:%M %p"),
        "days": days,
        "include_linked": include_linked,
    }


def render_account_summary_text(s: dict) -> str:
    """Render account summary dict as plain text for clipboard/email."""
    if not s:
        return ""

    c = s["client"]
    f = s["financials"]
    lines = [
        f"ACCOUNT SUMMARY: {c['name']}" + (f" [{c['cn_number']}]" if c['cn_number'] else ""),
        f"Generated: {s['generated_at']}",
        "\u2500" * 50,
    ]

    # Financials
    parts = []
    if f["total_premium"]:
        parts.append(f"${f['total_premium']:,.0f} Premium")
    if f["total_revenue"]:
        parts.append(f"${f['total_revenue']:,.0f} Revenue")
    if f["total_policies"]:
        parts.append(f"{f['total_policies']} Policies")
    if f["carrier_count"]:
        parts.append(f"{f['carrier_count']} Carriers")
    if parts:
        lines.append(" | ".join(parts))

    # Linked members
    if s.get("linked_members"):
        lines.append("")
        lines.append(f"LINKED ACCOUNTS ({len(s['linked_members'])})")
        for m in s["linked_members"]:
            lines.append(f"  {m['name']} \u2014 ${m['total_premium']:,.0f} premium, {m['total_policies']} policies")

    # Renewals
    if s["renewals"]:
        lines.append("")
        lines.append(f"RENEWAL STATUS (Next 180d) \u2014 {len(s['renewals'])} policies")
        for r in s["renewals"]:
            dtr = r.get("days_to_renewal")
            days_str = f"({dtr}d)" if dtr is not None else ""
            ms_str = f"[{r.get('milestone_done', 0)}/{r.get('milestone_total', 0)}]" if r.get("milestone_total") else ""
            client_prefix = f"{r['client_name']} \u2014 " if r.get("client_name") else ""
            exp = r.get("expiration_date", "")[:10] if r.get("expiration_date") else ""
            lines.append(f"  {client_prefix}{r.get('policy_type', '')} \u2014 {r.get('carrier', '')} \u2014 Exp {exp} {days_str} \u2014 {r.get('renewal_status', '')} {ms_str}")

    # Open items
    overdue = s["open_items"].get("overdue", [])
    upcoming = s["open_items"].get("upcoming", [])
    if overdue or upcoming:
        lines.append("")
        lines.append(f"OPEN ITEMS ({len(overdue) + len(upcoming)})")
        for o in overdue:
            lines.append(f"  \u26a0 {o.get('subject', '')} ({o.get('days_overdue', 0)}d overdue)")
        for u in upcoming:
            lines.append(f"  \u2192 {u.get('subject', '')} (due {u.get('follow_up_date', '')[:10]})")

    # Open requests
    if s.get("open_requests"):
        lines.append("")
        lines.append("OUTSTANDING REQUESTS")
        for req in s["open_requests"]:
            lines.append(f"  {req['title']} ({req['outstanding']} of {req['total']} items pending)")

    # Recent activity
    if s["recent_activity"]:
        total_h = s["time_summary"].get("total_hours", 0) if s.get("time_summary") else 0
        h_str = f" \u2014 {total_h:.1f}h logged" if total_h else ""
        lines.append("")
        lines.append(f"RECENT ACTIVITY (Last {s['days']}d){h_str}")
        for a in s["recent_activity"][:10]:
            dur = f" ({a['duration_hours']}h)" if a.get("duration_hours") else ""
            lines.append(f"  {a.get('activity_date', '')[:10]} {a.get('activity_type', '')} \u2014 {a.get('subject', '')}{dur}")

    # High risks
    if s.get("high_risks"):
        lines.append("")
        lines.append("HIGH RISKS")
        for r in s["high_risks"]:
            has_cov = "Covered" if r.get("has_coverage") else "No coverage"
            lines.append(f"  ! {r.get('category', '')} \u2014 {r.get('severity', '')} \u2014 {has_cov}")

    # Notes
    if s.get("notes_snippet"):
        lines.append("")
        lines.append("NOTES")
        lines.append(f"  {s['notes_snippet']}")

    return "\n".join(lines)


# ─── PROJECT GROUP EXPORT ────────────────────────────────────────────────────

def export_project_group_xlsx(
    conn: sqlite3.Connection,
    client_id: int,
    project_name: str,
    client_name: str,
) -> bytes:
    """Export policies for a single project/location as XLSX."""
    if project_name:
        policies = conn.execute(
            """SELECT policy_uid, policy_type, carrier, policy_number,
                      effective_date, expiration_date, premium, limit_amount, deductible,
                      description, coverage_form, layer_position,
                      project_name, renewal_status, commission_rate, commission_amount,
                      prior_premium, rate_change,
                      exposure_address, exposure_city, exposure_state, exposure_zip,
                      notes, urgency, days_to_renewal,
                      first_named_insured, access_point
               FROM v_policy_status
               WHERE client_id = ? AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?))
               ORDER BY policy_type, layer_position""",
            (client_id, project_name),
        ).fetchall()
    else:
        policies = conn.execute(
            """SELECT policy_uid, policy_type, carrier, policy_number,
                      effective_date, expiration_date, premium, limit_amount, deductible,
                      description, coverage_form, layer_position,
                      project_name, renewal_status, commission_rate, commission_amount,
                      prior_premium, rate_change,
                      exposure_address, exposure_city, exposure_state, exposure_zip,
                      notes, urgency, days_to_renewal,
                      first_named_insured, access_point
               FROM v_policy_status
               WHERE client_id = ? AND COALESCE(project_name, '') = ''
               ORDER BY policy_type, layer_position""",
            (client_id,),
        ).fetchall()

    policy_ids = [
        conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (r["policy_uid"],)).fetchone()["id"]
        for r in policies
    ]

    policy_team = []
    if policy_ids:
        ph = ",".join("?" * len(policy_ids))
        policy_team = conn.execute(
            f"""SELECT p.policy_uid, p.policy_type, co.name, cpa.role, co.email, co.phone
                FROM contact_policy_assignments cpa
                JOIN contacts co ON cpa.contact_id = co.id
                JOIN policies p ON cpa.policy_id = p.id
                WHERE p.id IN ({ph}) ORDER BY p.policy_uid, co.name""",
            policy_ids,
        ).fetchall()

    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "Policies", [dict(r) for r in policies])

    # Total row
    ws = wb["Policies"]
    ws.append([])
    total = sum(r["premium"] or 0 for r in policies)
    ws.append(["Total Premium", fmt_currency(total)])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    if policy_team:
        _write_sheet(wb, "Policy Team", [dict(r) for r in policy_team])

    # Project note
    if project_name:
        note_row = conn.execute(
            "SELECT notes FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
            (client_id, project_name),
        ).fetchone()
        if note_row and note_row["notes"]:
            ws_n = wb.create_sheet("Project Notes")
            ws_n.append(["Project Notes"])
            ws_n["A1"].font = Font(bold=True)
            for line in (note_row["notes"] or "").split("\n"):
                ws_n.append([line])
            ws_n.column_dimensions["A"].width = 80

    return _wb_to_bytes(wb)


# ─── SINGLE POLICY EXPORT ───────────────────────────────────────────────────

def export_single_policy_xlsx(conn: sqlite3.Connection, policy_uid: str) -> bytes:
    """Export a single policy's full details as a multi-sheet XLSX."""
    row = conn.execute(
        "SELECT * FROM v_policy_status WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if not row:
        wb = Workbook()
        wb.active.append(["Policy not found"])
        return _wb_to_bytes(wb)

    d = dict(row)
    policy_id = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()["id"]

    # Sheet 1: Policy Detail — transposed key/value
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Policy Detail")
    ws.append(["Field", "Value"])
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    for key, val in d.items():
        if key in ("id", "client_id"):
            continue
        ws.append([key, val])
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 50

    # Sheet 2: Policy Team
    team = conn.execute(
        """SELECT co.name, cpa.role, co.email, co.phone, co.mobile
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.policy_id = ? ORDER BY co.name""",
        (policy_id,),
    ).fetchall()
    if team:
        _write_sheet(wb, "Policy Team", [dict(r) for r in team])

    # Sheet 3: Milestones
    milestones = conn.execute(
        """SELECT milestone, completed, completed_at, is_critical
           FROM policy_milestones WHERE policy_uid = ? ORDER BY id""",
        (policy_uid,),
    ).fetchall()
    if milestones:
        _write_sheet(wb, "Milestones", [dict(r) for r in milestones])

    # Sheet 4: Activity Log
    activities = conn.execute(
        """SELECT activity_date, activity_type, contact_person, subject, details,
                  follow_up_date, follow_up_done, duration_hours
           FROM activity_log WHERE policy_id = ?
           ORDER BY activity_date DESC""",
        (policy_id,),
    ).fetchall()
    if activities:
        _write_sheet(wb, "Activity Log", [dict(r) for r in activities])

    # Sheet 5: Premium History
    history = conn.execute(
        """SELECT term_effective, term_expiration, carrier, premium,
                  limit_amount, deductible, notes
           FROM premium_history
           WHERE client_id = ? AND policy_type = ?
           ORDER BY term_effective DESC""",
        (d.get("client_id"), d.get("policy_type")),
    ).fetchall()
    if history:
        _write_sheet(wb, "Premium History", [dict(r) for r in history])

    return _wb_to_bytes(wb)


# ─── SAVE HELPER ─────────────────────────────────────────────────────────────

def save_export(content: str, filename: str) -> Path:
    exports_dir = Path(cfg.get("export_dir", str(Path.home() / ".policydb" / "exports")))
    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / filename
    out.write_text(content)
    return out


def save_export_bytes(content: bytes, filename: str) -> Path:
    exports_dir = Path(cfg.get("export_dir", str(Path.home() / ".policydb" / "exports")))
    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / filename
    out.write_bytes(content)
    return out
