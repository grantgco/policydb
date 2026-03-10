"""Export system: schedule, client, book, renewals, LLM context dumps."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

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
        "| Line of Business | Carrier | Policy # | Effective | Expiration | Premium | Limit | Deductible | Form | Layer | Comments |",
        "|------------------|---------|----------|-----------|------------|---------|-------|------------|------|-------|----------|",
    ]

    for r in rows:
        pnum = r["Policy Number"] or ""
        form = r["Form"] or ""
        layer = r["Layer"] or "Primary"
        comments = (r["Comments"] or "").replace("|", "\\|")
        lines.append(
            f"| {r['Line of Business']} | {r['Carrier']} | {pnum} | {r['Effective']} | {r['Expiration']}"
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
        """SELECT a.*, c.name AS client_name FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           WHERE a.client_id = ? AND a.activity_date >= date('now', '-90 days')
           ORDER BY a.activity_date DESC""",
        (client_id,),
    ).fetchall()
    overdue = conn.execute(
        "SELECT * FROM v_overdue_followups WHERE client_name = ?",
        (client["name"],),
    ).fetchall()
    history = conn.execute(
        """SELECT * FROM premium_history WHERE client_id = ?
           ORDER BY policy_type, term_effective DESC""",
        (client_id,),
    ).fetchall()
    audit = build_program_audit(conn, client_id)

    s = summary
    total_premium = s["total_premium"] if s else 0
    total_commission = s["total_commission"] if s else 0
    next_renewal_days = s["next_renewal_days"] if s else None

    # Find next renewing policy
    next_pol = None
    if pipeline:
        next_pol = pipeline[0]

    lines = [
        "---",
        "export_type: client_program_summary",
        f'client: "{client["name"]}"',
        f'industry: "{client["industry_segment"]}"',
        f'account_executive: "{client["account_exec"]}"',
        f'export_date: "{TODAY}"',
        f"total_policies: {len(policies)}",
        f"standalone_policies: {audit['standalone_count']}",
        f"total_annual_premium: {fmt_currency(total_premium)}",
        f"total_annual_commission: {fmt_currency(total_commission)}",
    ]
    if next_pol:
        lines.append(
            f'next_renewal: "{next_pol["expiration_date"]} ({next_pol["days_to_renewal"]} days — {next_pol["policy_uid"]}, {next_pol["policy_type"]})"'
        )
    lines += ["---", ""]

    lines += [
        f"# Client Program Summary: {client['name']}",
        "",
        "## Client Profile",
        f"- **Industry:** {client['industry_segment']}",
        f"- **Primary Contact:** {client['primary_contact'] or '—'} ({client['contact_email'] or ''}, {client['contact_phone'] or ''})",
        f"- **Address:** {client['address'] or '—'}",
        f"- **Onboarded:** {client['date_onboarded']}",
        f"- **Notes:** {client['notes'] or '—'}",
        "",
        "## Program Overview",
        "",
        f"- Total annual premium: {fmt_currency(total_premium)}",
        f"- Total annual commission: {fmt_currency(total_commission)}",
        f"- Active policies: {len(policies)} ({audit['standalone_count']} standalone)",
        f"- Coverage lines: {', '.join(audit['coverage_lines'])}",
        f"- Carriers on account: {', '.join(audit['carriers'])}",
        "",
    ]

    # Renewal pipeline
    if pipeline:
        lines += [
            "## Renewal Pipeline (Next 180 Days)",
            "",
            "| Policy | Line | Carrier | Expires | Days | Premium | Urgency | Renewal Status | Colleague |",
            "|--------|------|---------|---------|------|---------|---------|----------------|-----------|",
        ]
        for r in pipeline:
            lines.append(
                f"| {r['policy_uid']} | {r['policy_type']} | {r['carrier']}"
                f" | {r['expiration_date']} | {r['days_to_renewal']}"
                f" | {fmt_currency(r['premium'])} | {r['urgency']}"
                f" | {r['renewal_status']} | {r['placement_colleague'] or '—'} |"
            )
        lines.append("")

    # Complete policy schedule
    lines += [
        "## Complete Policy Schedule",
        "",
        "| UID | Line of Business | Carrier | Policy # | Effective | Expires | Premium | Limit | Ded. | Description | Layer | Standalone | Renewal Status | Colleague |",
        "|-----|------------------|---------|----------|-----------|---------|---------|-------|------|-------------|-------|------------|----------------|-----------|",
    ]
    for p in policies:
        desc = (p["description"] or "").replace("|", "\\|")
        lines.append(
            f"| {p['policy_uid']} | {p['policy_type']} | {p['carrier']}"
            f" | {p['policy_number'] or ''} | {p['effective_date']} | {p['expiration_date']}"
            f" | {fmt_currency(p['premium'])} | {fmt_limit(p['limit_amount'])}"
            f" | {fmt_limit(p['deductible'])} | {desc}"
            f" | {p['layer_position'] or 'Primary'} | {'Yes' if p['is_standalone'] else 'No'}"
            f" | {p['renewal_status']} | {p['placement_colleague'] or '—'} |"
        )
    lines.append("")

    # Tower structure
    towers = audit["towers"]
    if towers:
        lines += ["## Tower / Layer Structure", ""]
        for group_name, group_policies in towers.items():
            lines += [
                f"### {group_name}",
                "",
                "| Layer | Carrier | Limit | Premium | Description |",
                "|-------|---------|-------|---------|-------------|",
            ]
            for p in group_policies:
                desc = (p.get("description") or "").replace("|", "\\|")
                lines.append(
                    f"| {p.get('layer_position', 'Primary')} | {p['carrier']}"
                    f" | {fmt_limit(p.get('limit_amount'))} | {fmt_currency(p['premium'])} | {desc} |"
                )
            lines.append("")

    # Coverage gap analysis
    gaps = audit["gap_observations"]
    lines += ["## Coverage Gap Analysis", ""]
    if gaps:
        for obs in gaps:
            lines.append(f"- {obs}")
    else:
        lines.append("- No coverage gaps detected based on configured rules.")
    lines.append("")

    # Premium history
    if history:
        lines += ["## Premium Trending", ""]
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

    # Recent activity
    if activities:
        lines += [
            "## Recent Activity (Last 90 Days)",
            "",
            "| Date | Type | Contact | Subject | Follow-Up |",
            "|------|------|---------|---------|-----------|",
        ]
        for a in activities:
            lines.append(
                f"| {a['activity_date']} | {a['activity_type']}"
                f" | {a['contact_person'] or '—'} | {a['subject']}"
                f" | {a['follow_up_date'] or '—'} |"
            )
        lines.append("")

    # Overdue follow-ups
    if overdue:
        lines += [
            "## Open Follow-Ups",
            "",
            "| Due | Overdue By | Subject | Activity Date |",
            "|-----|------------|---------|---------------|",
        ]
        for o in overdue:
            lines.append(
                f"| {o['follow_up_date']} | {o['days_overdue']}d"
                f" | {o['subject']} | {o['activity_date']} |"
            )
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
    history = conn.execute(
        "SELECT * FROM premium_history WHERE client_id = ? ORDER BY policy_type, term_effective DESC",
        (client_id,),
    ).fetchall()
    audit = build_program_audit(conn, client_id)

    data = {
        "metadata": {
            "export_type": "client_program",
            "date": TODAY,
            "client": client["name"],
        },
        "client": dict(client),
        "summary": dict(summary) if summary else {},
        "policies": [
            {
                **dict(p),
                "computed": {
                    "days_to_renewal": p["days_to_renewal"],
                    "urgency": p["urgency"],
                    "commission_amount": p["commission_amount"],
                    "rate_change": p["rate_change"],
                },
            }
            for p in policies
        ],
        "coverage_analysis": {
            "gaps": audit["gap_observations"],
            "tower_count": audit["tower_count"],
            "standalone_count": audit["standalone_count"],
            "duplicate_count": audit["duplicate_count"],
        },
        "premium_history": [dict(r) for r in history],
        "activities": [dict(a) for a in activities],
        "overdue_followups": [dict(o) for o in overdue],
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
    total = sum(r["premium"] or 0 for r in rows)
    lines = [
        f"# Renewal Pipeline — Next {window_days} Days",
        "",
        f"**As of:** {TODAY}  ",
        f"**Policies:** {len(rows)}  ",
        f"**Premium at Risk:** {fmt_currency(total)}",
        "",
        "| UID | Client | Line | Carrier | Expires | Days | Urgency | Premium | Status | Colleague |",
        "|-----|--------|------|---------|---------|------|---------|---------|--------|-----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['policy_uid']} | {r['client_name']} | {r['policy_type']}"
            f" | {r['carrier']} | {r['expiration_date']} | {r['days_to_renewal']}"
            f" | {r['urgency']} | {fmt_currency(r['premium'])}"
            f" | {r['renewal_status']} | {r['placement_colleague'] or '—'} |"
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


# ─── SAVE HELPER ─────────────────────────────────────────────────────────────

def save_export(content: str, filename: str) -> Path:
    exports_dir = Path(cfg.get("export_dir", str(Path.home() / ".policydb" / "exports")))
    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / filename
    out.write_text(content)
    return out
