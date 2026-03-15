"""Email template token rendering and context builders."""

from __future__ import annotations

import sqlite3
from datetime import date


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
        "SELECT name, email FROM client_contacts WHERE client_id=? AND is_primary=1 AND contact_type='client'",
        (client_id,),
    ).fetchone()
    if primary:
        return primary["name"] or fallback_name, primary["email"] or fallback_email
    return fallback_name, fallback_email


def policy_context(conn: sqlite3.Connection, policy_uid: str) -> dict:
    """Build token context dict from a policy + its client."""
    row = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier, p.policy_number,
                  p.effective_date, p.expiration_date, p.premium, p.limit_amount,
                  p.deductible, p.project_name, p.renewal_status, p.account_exec,
                  p.access_point, p.last_reviewed_at, p.review_cycle,
                  c.id AS client_id, c.name AS client_name, c.industry_segment AS industry,
                  c.primary_contact, c.contact_email AS client_email,
                  c.website, c.client_since, c.referral_source
           FROM policies p
           JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (policy_uid.upper(),),
    ).fetchone()
    if not row:
        return {}

    def _fmt_currency(v) -> str:
        if not v:
            return ""
        try:
            return f"${float(v):,.0f}"
        except (TypeError, ValueError):
            return ""

    primary_name, primary_email = _resolve_primary_contact(
        conn, row["client_id"], row["primary_contact"] or "", row["client_email"] or ""
    )
    proj = row["project_name"] or ""
    return {
        "client_name": row["client_name"] or "",
        "policy_type": row["policy_type"] or "",
        "carrier": row["carrier"] or "",
        "policy_uid": row["policy_uid"] or "",
        "policy_number": row["policy_number"] or "",
        "effective_date": row["effective_date"] or "",
        "expiration_date": row["expiration_date"] or "",
        "premium": _fmt_currency(row["premium"]),
        "limit": _fmt_currency(row["limit_amount"]),
        "deductible": _fmt_currency(row["deductible"]),
        "project_name": proj,
        "project_name_sep": f" \u2014 {proj}" if proj else "",
        "renewal_status": row["renewal_status"] or "",
        "account_exec": row["account_exec"] or "",
        "primary_contact": primary_name,
        "primary_email": primary_email,
        "industry": row["industry"] or "",
        "access_point": row["access_point"] or "",
        "website": row["website"] or "",
        "client_since": row["client_since"] or "",
        "last_reviewed_at": row["last_reviewed_at"] or "",
        "review_cycle": _cycle_label(row["review_cycle"]),
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
    }


def client_context(conn: sqlite3.Connection, client_id: int) -> dict:
    """Build token context dict from a client record."""
    row = conn.execute(
        """SELECT id, name, industry_segment, primary_contact, contact_email, account_exec,
                  website, client_since, referral_source
           FROM clients WHERE id = ?""",
        (client_id,),
    ).fetchone()
    if not row:
        return {}
    primary_name, primary_email = _resolve_primary_contact(
        conn, row["id"], row["primary_contact"] or "", row["contact_email"] or ""
    )
    return {
        "client_name": row["name"] or "",
        "industry": row["industry_segment"] or "",
        "primary_contact": primary_name,
        "primary_email": primary_email,
        "account_exec": row["account_exec"] or "",
        "website": row["website"] or "",
        "client_since": row["client_since"] or "",
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
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
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
    }


# Tokens available per context — used to build the pill toolbar UI
CONTEXT_TOKENS: dict[str, list[tuple[str, str]]] = {
    "policy": [
        ("client_name", "Client Name"),
        ("policy_type", "Policy Type"),
        ("carrier", "Carrier"),
        ("policy_uid", "Policy ID"),
        ("policy_number", "Policy Number"),
        ("effective_date", "Effective Date"),
        ("expiration_date", "Expiration Date"),
        ("premium", "Premium"),
        ("limit", "Limit"),
        ("deductible", "Deductible"),
        ("project_name", "Project / Location"),
        ("project_name_sep", "Project (with separator)"),
        ("renewal_status", "Renewal Status"),
        ("access_point", "Access Point"),
        ("account_exec", "Account Exec"),
        ("primary_contact", "Primary Contact"),
        ("primary_email", "Primary Email"),
        ("industry", "Industry"),
        ("website", "Client Website"),
        ("client_since", "Client Since"),
        ("last_reviewed_at", "Last Reviewed Date"),
        ("review_cycle", "Review Cycle"),
        ("today", "Today's Date"),
    ],
    "client": [
        ("client_name", "Client Name"),
        ("industry", "Industry"),
        ("primary_contact", "Primary Contact"),
        ("primary_email", "Primary Email"),
        ("account_exec", "Account Exec"),
        ("website", "Client Website"),
        ("client_since", "Client Since"),
        ("today", "Today's Date"),
    ],
    "general": [
        ("account_exec", "Account Exec"),
        ("today", "Today's Date"),
    ],
    "followup": [
        ("client_name", "Client Name"),
        ("policy_type", "Policy Type"),
        ("carrier", "Carrier"),
        ("policy_uid", "Policy ID"),
        ("project_name", "Project / Location"),
        ("project_name_sep", "Project (with separator)"),
        ("subject", "Follow-Up Subject"),
        ("contact_person", "Contact Person"),
        ("today", "Today's Date"),
    ],
}
