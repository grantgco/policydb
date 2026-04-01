"""Outlook email sync engine — scans Sent/Received/Flagged and creates activities.

Matching strategy:
  Tier 1: Ref tag match — parse [PDB:...] tags to find client/policy/issue records
  Tier 2: Fuzzy match — match sender/recipient emails against contacts, then
           subject keywords against client names using RapidFuzz
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta

from policydb import config as cfg
from policydb.outlook import search_emails, search_all_folders, get_flagged_emails

logger = logging.getLogger(__name__)

_REF_TAG_RE = re.compile(r'\[PDB:([^\]]+)\]')
_HTML_TAG_RE = re.compile(r'<[^>]+>')


def _extract_ref_tags(text: str) -> list[str]:
    """Extract all [PDB:...] ref tags from text."""
    return _REF_TAG_RE.findall(text or "")


def _parse_ref_tag(tag: str) -> dict:
    """Parse a ref tag string into components.

    Examples:
        "CN123456789" → {"cn_number": "123456789"}
        "CN123456789-POL042" → {"cn_number": "123456789", "policy_uid": "POL-042"}
        "CN123456789-A7F2C3B1" → {"cn_number": "123456789", "issue_uid": "A7F2C3B1"}
    """
    result: dict = {}

    # Extract CN number
    cn_match = re.match(r'CN(\d+)', tag)
    if cn_match:
        result["cn_number"] = cn_match.group(1)

    # Extract policy UID (POL followed by digits)
    pol_match = re.search(r'(POL\d+)', tag)
    if pol_match:
        # Re-format as POL-NNN
        pol_raw = pol_match.group(1)
        digits = pol_raw[3:]
        result["policy_uid"] = f"POL-{digits}"

    # Extract issue UID (8-char hex, not matching POL/RFI/COR patterns)
    # Must check BEFORE activity ID since issue UIDs like A7F2C3B1 start with 'A'
    hex_match = re.search(r'-([A-Fa-f0-9]{8})(?:-|$)', tag)
    if hex_match:
        candidate = hex_match.group(1).upper()
        # Exclude if it looks like a POL/RFI/COR pattern
        if not re.match(r'^(POL|RFI|COR)', candidate):
            result["issue_uid"] = candidate

    # Extract activity ID (only if no issue UID found — they can conflict)
    if "issue_uid" not in result:
        act_match = re.search(r'-A(\d+)$', tag)
        if act_match:
            result["activity_id"] = int(act_match.group(1))

    return result


def _resolve_ref_tag(conn: sqlite3.Connection, tag: str) -> dict | None:
    """Resolve a ref tag to database records. Returns match info or None.

    Resolution priority (most specific wins):
      issue > policy > CN number
    Each resolved record's client_id/policy_id overwrites less-specific values.
    """
    parsed = _parse_ref_tag(tag)
    if not parsed:
        return None

    result = {"tier": 1, "confidence": 100}

    # Layer 1 (least specific): Resolve client by CN number
    if "cn_number" in parsed:
        client = conn.execute(
            "SELECT id FROM clients WHERE cn_number=?",
            (parsed["cn_number"],),
        ).fetchone()
        if client:
            result["client_id"] = client["id"]

    # Layer 2: Resolve policy — its client_id overwrites CN lookup
    if "policy_uid" in parsed:
        policy = conn.execute(
            "SELECT id, client_id FROM policies WHERE policy_uid=?",
            (parsed["policy_uid"],),
        ).fetchone()
        if policy:
            result["policy_id"] = policy["id"]
            result["client_id"] = policy["client_id"]

    # Layer 3 (most specific): Resolve issue — its client_id/policy_id overwrite all
    if "issue_uid" in parsed:
        issue = conn.execute(
            "SELECT id, client_id, policy_id FROM activity_log WHERE issue_uid=? AND item_kind='issue'",
            (parsed["issue_uid"],),
        ).fetchone()
        if issue:
            result["issue_id"] = issue["id"]
            result["issue_uid"] = parsed["issue_uid"]
            if issue["client_id"]:
                result["client_id"] = issue["client_id"]
            if issue["policy_id"]:
                result["policy_id"] = issue["policy_id"]

    # Must have resolved at least a client
    if "client_id" not in result:
        return None

    return result



def _create_or_enrich_activity(
    conn: sqlite3.Connection,
    email: dict,
    match: dict,
    source_label: str = "outlook_sync",
) -> dict:
    """Create a new activity or enrich an existing one from an email.

    Returns {"action": "created"|"enriched"|"skipped", "activity_id": ...}
    """
    message_id = email.get("message_id", "")

    # Dedup check
    if message_id:
        existing = conn.execute(
            "SELECT id FROM activity_log WHERE outlook_message_id=?",
            (message_id,),
        ).fetchone()
        if existing:
            return {"action": "skipped", "activity_id": existing["id"], "reason": "duplicate"}

    client_id = match.get("client_id") or 0
    policy_id = match.get("policy_id")
    issue_id = match.get("issue_id")

    # Can't create activity without a client
    if not client_id:
        return {"action": "skipped", "activity_id": 0, "reason": "no_client"}
    folder = email.get("folder", "")
    subject = email.get("subject", "")
    sender = email.get("sender", "")
    # Clean and store readable text snippet
    snippet = _clean_email_text(email.get("body_snippet", ""))[:500]
    email_date = email.get("date", "")[:10]  # ISO date portion
    flag_due = email.get("flag_due_date")

    is_sent = folder.lower() in ("sent items", "sent")

    # Check if there's an existing same-day email activity for this policy
    if is_sent and policy_id:
        existing_activity = conn.execute(
            """SELECT id FROM activity_log
               WHERE activity_date=? AND policy_id=? AND activity_type='Email'
                 AND source='manual'
               ORDER BY id DESC LIMIT 1""",
            (email_date, policy_id),
        ).fetchone()
        if existing_activity:
            # Enrich existing activity
            conn.execute(
                """UPDATE activity_log
                   SET outlook_message_id=?, email_snippet=?, source='outlook_sync'
                   WHERE id=?""",
                (message_id, snippet, existing_activity["id"]),
            )
            conn.commit()
            return {"action": "enriched", "activity_id": existing_activity["id"]}

    # Create new activity
    disposition = "Sent Email" if is_sent else ""
    subj_prefix = "" if is_sent else "Received: "

    # Resolve contact from sender email
    contact_id = None
    contact_person = sender
    if sender:
        contact = conn.execute(
            "SELECT id, name FROM contacts WHERE LOWER(TRIM(email))=?",
            (sender.strip().lower(),),
        ).fetchone()
        if contact:
            contact_id = contact["id"]
            contact_person = contact["name"] or sender

    # Flagged items (from get_flagged_emails) are action items — leave follow_up_done=0.
    # Regular sent/received imports are records, not action items — mark follow_up_done=1
    # so they don't clutter the Action Center.
    is_flagged = "flag_due_date" in email  # Only flagged emails have this key

    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            contact_person, contact_id, disposition, source, outlook_message_id,
            email_snippet, issue_id, follow_up_date, follow_up_done)
           VALUES (?, ?, ?, 'Email', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            email_date,
            client_id,
            policy_id,
            f"{subj_prefix}{subject}",
            f"Imported from Outlook ({folder})",
            contact_person,
            contact_id,
            disposition,
            source_label,
            message_id,
            snippet,
            issue_id,
            flag_due,
            0 if is_flagged else 1,  # Only open follow-up for flagged items
        ),
    )
    conn.commit()

    return {"action": "created", "activity_id": cursor.lastrowid}


def sync_outlook(conn: sqlite3.Connection) -> dict:
    """Run the full Outlook sync sweep.

    Returns a results dict for rendering the sync results template.
    """
    # Determine scan window
    last_sync_str = cfg.get("last_outlook_sync")
    if last_sync_str:
        try:
            since = datetime.fromisoformat(last_sync_str)
        except ValueError:
            since = datetime.now() - timedelta(days=cfg.get("outlook_sync_lookback_days", 7))
    else:
        since = datetime.now() - timedelta(days=cfg.get("outlook_sync_lookback_days", 7))

    results = {
        "auto_linked": {"sent": 0, "received": 0, "flagged": 0},
        "suggestions": [],
        "skipped": 0,
        "errors": [],
        "total_scanned": 0,
        "since": since.strftime("%b %d, %Y %H:%M"),
    }

    skip_category = cfg.get("outlook_skip_category", "Personal")
    capture_category = cfg.get("outlook_capture_category", "PDB")

    # ── Scan Sent Items (default-in, skip "Personal" category) ───────
    sent_result = search_emails("Sent Items", since)
    if not sent_result.get("ok"):
        results["errors"].append(sent_result.get("error", "Failed to scan Sent Items"))
    else:
        for email in sent_result.get("emails", []):
            results["total_scanned"] += 1
            cats = email.get("categories", [])
            # Skip emails marked as Personal (or configured skip category)
            if skip_category and skip_category in cats:
                results["skipped"] += 1
                continue
            _process_email(conn, email, results, "sent")

    # ── Scan all folders for "PDB" category or [PDB:] ref tag ────────
    received_result = search_all_folders(since, category_filter=capture_category)
    if not received_result.get("ok"):
        results["errors"].append(received_result.get("error", "Failed to scan received emails"))
    else:
        for email in received_result.get("emails", []):
            results["total_scanned"] += 1
            _process_email(conn, email, results, "received")

    # ── Scan Flagged across all folders → inbox ──────────────────────
    flagged_result = get_flagged_emails(since)
    if not flagged_result.get("ok"):
        results["errors"].append(flagged_result.get("error", "Failed to scan Flagged items"))
    else:
        for email in flagged_result.get("emails", []):
            results["total_scanned"] += 1
            _process_email(conn, email, results, "flagged")

    # ── Update last sync timestamp ───────────────────────────────────
    config_data = dict(cfg.load_config())
    config_data["last_outlook_sync"] = datetime.now().isoformat()
    cfg.save_config(config_data)
    cfg.reload_config()

    logger.info(
        "Outlook sync complete: %d scanned, %d auto-linked (sent=%d recv=%d flag=%d), "
        "%d suggestions, %d skipped",
        results["total_scanned"],
        sum(results["auto_linked"].values()),
        results["auto_linked"]["sent"],
        results["auto_linked"]["received"],
        results["auto_linked"]["flagged"],
        len(results["suggestions"]),
        results["skipped"],
    )

    return results


def _clean_email_text(text: str) -> str:
    """Collapse excessive whitespace in captured email text for readability."""
    # Strip HTML tags
    text = _HTML_TAG_RE.sub('', text)
    # Collapse runs of 3+ newlines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse runs of spaces/tabs on a line (preserve newlines)
    text = re.sub(r'[^\S\n]+', ' ', text)
    # Strip leading/trailing whitespace per line
    text = '\n'.join(line.strip() for line in text.split('\n'))
    return text.strip()


def _process_email(
    conn: sqlite3.Connection,
    email: dict,
    results: dict,
    category: str,  # "sent", "received", "flagged"
) -> None:
    """Process a single email: extract ref tags, match, create/enrich activity."""
    # Strip HTML tags from body before searching for ref tags
    body = _HTML_TAG_RE.sub('', email.get("body_snippet", ""))
    combined_text = email.get("subject", "") + " " + body
    tags = _extract_ref_tags(combined_text)

    # Try ref tag matching first
    match = None
    if tags:
        for tag in tags:
            match = _resolve_ref_tag(conn, tag)
            if match:
                break

    if not match:
        # No ref tag match — send to inbox for triage
        # (all categories: sent, received, flagged — never silently drop)
        message_id = email.get("message_id", "")
        # Dedup: check if already in activity_log or inbox
        if message_id:
            existing = conn.execute(
                "SELECT 1 FROM activity_log WHERE outlook_message_id=?", (message_id,),
            ).fetchone()
            if existing:
                results["skipped"] += 1
                return
            existing_inbox = conn.execute(
                "SELECT 1 FROM inbox WHERE outlook_message_id=?", (message_id,),
            ).fetchone()
            if existing_inbox:
                results["skipped"] += 1
                return
        subject = email.get("subject", "")
        sender = email.get("sender", "")
        folder = email.get("folder", "")
        date_str = (email.get("date", "") or "")[:10]
        snippet = _clean_email_text(email.get("body_snippet", ""))[:1000]
        label_map = {"flagged": "[Outlook Flagged]", "sent": "[Outlook Sent]", "received": "[Outlook Received]"}
        label = label_map.get(category, "[Outlook]")
        recipients = ", ".join(email.get("recipients", [])[:3])
        content = f"{label} {subject}\nFrom: {sender}\nTo: {recipients}\nFolder: {folder}\nDate: {date_str}"
        if snippet:
            content += f"\n\n{snippet}"
        conn.execute(
            """INSERT INTO inbox (content, client_id, contact_id, inbox_uid,
                                  email_subject, email_date, outlook_message_id)
               VALUES (?, NULL, NULL, '', ?, ?, ?)""",
            (content, subject, date_str, message_id),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("UPDATE inbox SET inbox_uid = ? WHERE id = ?", (f"INB-{row_id}", row_id))
        conn.commit()
        results["suggestions"].append({
            "subject": subject, "sender": sender, "folder": folder,
            "date": date_str, "category": category, "inbox_uid": f"INB-{row_id}",
        })
        return

    result = _create_or_enrich_activity(conn, email, match)

    if result["action"] == "skipped":
        results["skipped"] += 1
    elif result["action"] in ("created", "enriched"):
        results["auto_linked"][category] += 1
