"""PolicyDB -> Outlook contact push sync.

Builds the push set (every contact with ≥1 assignment to a non-archived
client), fetches the current PDB-tagged state from Outlook, and diffs to
produce create / update / delete operations.

The sync is one-way: PolicyDB is always the source of truth. The PDB
category on the Outlook side is the safety fence — contacts that do not
carry the category are never touched, even if email matches. Untagging a
contact in Outlook is the user's escape hatch.

See the plan at .claude/plans/lazy-seeking-rivest.md for full rationale,
the 30-case edge-case table, and verification checklist.
"""

from __future__ import annotations

import logging
import sqlite3

from policydb import config as cfg
from policydb.outlook import is_outlook_available
from policydb.outlook_contacts import (
    DEFAULT_CATEGORY,
    delete_contact,
    ensure_pdb_category,
    list_pdb_contacts,
    split_name,
    upsert_contact,
)
from policydb.utils import clean_email, format_phone

logger = logging.getLogger(__name__)

_NOTES_TRUNCATE = 5000


def _empty_result(**overrides) -> dict:
    result = {
        "ok": True,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped_orphan": 0,
        "skipped_archived": 0,
        "skipped_email": 0,
        "skipped_unavailable": False,
        "errors": [],
        "ambiguous_bootstrap": [],
        "pushed_internal": 0,
        "push_set_size": 0,
    }
    result.update(overrides)
    return result


def _build_push_set(conn: sqlite3.Connection) -> list[dict]:
    """Return the list of PolicyDB contacts eligible for push.

    A contact is eligible if it has at least one row in
    contact_client_assignments where the referenced client is not archived.
    The chosen client assignment (primary if any, otherwise oldest) drives
    the title and business address.
    """
    rows = conn.execute(
        """
        SELECT
            c.id                AS contact_id,
            c.name              AS name,
            c.email             AS email,
            c.phone             AS phone,
            c.mobile            AS mobile,
            c.organization      AS organization,
            c.expertise_notes   AS expertise_notes,
            c.outlook_contact_id AS outlook_contact_id,
            cca.title           AS title,
            cl.address          AS client_address,
            cl.name             AS client_name
        FROM contacts c
        JOIN contact_client_assignments cca ON cca.contact_id = c.id
        JOIN clients cl ON cl.id = cca.client_id
        WHERE cl.archived = 0
        ORDER BY
            c.id,
            cca.is_primary DESC,
            cca.id ASC
        """
    ).fetchall()

    # Collapse to one row per contact — the ORDER BY puts the preferred
    # assignment first for each contact, so keep the first occurrence.
    seen: set[int] = set()
    push_set: list[dict] = []
    for row in rows:
        cid = row["contact_id"]
        if cid in seen:
            continue
        seen.add(cid)
        push_set.append(dict(row))
    return push_set


def _row_to_payload(row: dict) -> dict:
    """Build the Outlook payload dict from a push-set row.

    Normalizes email and phones before push. Truncates notes to 5000 chars
    (mirrors the email-snippet cap). Single-line client address becomes the
    business address street.
    """
    name = (row.get("name") or "").strip()
    first, last = split_name(name)

    raw_email = row.get("email") or ""
    email = clean_email(raw_email) if raw_email else ""
    # clean_email never fails, but defensively strip if somehow malformed
    if email and "@" not in email:
        email = ""

    phone = format_phone(row.get("phone") or "")
    mobile = format_phone(row.get("mobile") or "")

    notes = (row.get("expertise_notes") or "").strip()[:_NOTES_TRUNCATE]

    street = (row.get("client_address") or "").strip()

    return {
        "display_name": name,
        "first_name": first,
        "last_name": last,
        "company": (row.get("organization") or "").strip(),
        "job_title": (row.get("title") or "").strip(),
        "email": email,
        "business_phone": phone,
        "mobile_phone": mobile,
        "notes": notes,
        "business_address_street": street,
    }


def _needs_update(payload: dict, remote: dict) -> bool:
    """True if any tracked field on the Outlook side differs from the push payload.

    Kept liberal on purpose — an extra no-op `set` call is cheap and Outlook
    silently ignores redundant writes. This just avoids round-tripping every
    contact on every sweep when nothing has changed.
    """
    fields = (
        "first_name",
        "last_name",
        "display_name",
        "company",
        "job_title",
        "email",
        "business_phone",
        "mobile_phone",
        "business_address_street",
    )
    for key in fields:
        if (payload.get(key) or "").strip() != (remote.get(key) or "").strip():
            return True
    # Notes comparison — whitespace-normalized so AppleScript's return/linefeed
    # round-trip differences don't force spurious updates
    local_notes = " ".join((payload.get("notes") or "").split())
    remote_notes = " ".join((remote.get("notes") or "").split())
    if local_notes != remote_notes:
        return True
    return False


def _internal_domain_set() -> set[str]:
    internal = cfg.get("internal_email_domains", [])
    if isinstance(internal, str):
        internal = [internal]
    return {d.strip().lower() for d in internal if d and d.strip()}


def _is_internal(email: str, internal_domains: set[str]) -> bool:
    if not email or "@" not in email:
        return False
    return email.rsplit("@", 1)[-1].lower() in internal_domains


def sync_contacts_to_outlook(conn: sqlite3.Connection) -> dict:
    """Top-level entry point. Push PolicyDB contacts to Outlook.

    Safe to call when Outlook is unavailable — returns
    {"ok": True, "skipped_unavailable": True, ...} and does nothing.
    Idempotent — re-running the sweep when nothing has changed creates
    no writes.
    """
    if not cfg.get("outlook_contact_sync_enabled", True):
        return _empty_result(ok=True, skipped_unavailable=True,
                             errors=["Contact sync disabled in Settings."])

    if not is_outlook_available():
        return _empty_result(ok=True, skipped_unavailable=True)

    category_name = cfg.get("outlook_contact_category", DEFAULT_CATEGORY) or DEFAULT_CATEGORY
    allow_deletes = bool(cfg.get("outlook_contact_allow_deletes", True))

    # Make sure the category exists before we try to tag anything with it
    cat_result = ensure_pdb_category(category_name)
    if not cat_result.get("ok"):
        return _empty_result(ok=False, errors=[
            f"Could not create '{category_name}' category in Outlook: {cat_result.get('error', 'unknown error')}"
        ])

    push_set = _build_push_set(conn)
    result = _empty_result(push_set_size=len(push_set))

    # Fetch current PDB-tagged contacts from Outlook
    list_result = list_pdb_contacts(category_name)
    if not list_result.get("ok"):
        result["ok"] = False
        result["errors"].append(
            f"Could not list Outlook contacts: {list_result.get('error', 'unknown error')}"
        )
        return result

    remote_contacts = list_result.get("contacts", []) or []
    remote_by_id: dict[str, dict] = {
        c["outlook_id"]: c for c in remote_contacts if c.get("outlook_id")
    }
    remote_by_email: dict[str, dict] = {}
    for c in remote_contacts:
        em = (c.get("email") or "").strip().lower()
        if em:
            remote_by_email.setdefault(em, []).append(c)

    # Safety check for edge case #12: if DB has tracked ids but Outlook
    # returned zero PDB-tagged contacts, the category was probably renamed
    # or deleted. Abort rather than re-create every row.
    tracked_in_db = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE outlook_contact_id IS NOT NULL"
    ).fetchone()[0]
    if tracked_in_db > 0 and not remote_contacts:
        result["ok"] = False
        result["errors"].append(
            f"Found {tracked_in_db} tracked contacts in PolicyDB but no '{category_name}' "
            f"contacts in Outlook. The category may have been renamed or deleted. "
            f"Rename it back, or reset contact sync state in Settings."
        )
        return result

    internal_domains = _internal_domain_set()
    push_set_ids: set[str] = set()

    for row in push_set:
        payload = _row_to_payload(row)
        email = payload.get("email", "")
        if row.get("email") and not email:
            result["skipped_email"] += 1

        if _is_internal(email, internal_domains):
            result["pushed_internal"] += 1

        tracked_id = row.get("outlook_contact_id")
        remote = remote_by_id.get(tracked_id) if tracked_id else None

        # Bootstrap path — no tracked id yet
        if not tracked_id:
            candidates: list[dict] = []
            if email:
                candidates = list(remote_by_email.get(email, []))
            if len(candidates) == 1:
                remote = candidates[0]
                tracked_id = remote.get("outlook_id") or None
            elif len(candidates) > 1:
                result["ambiguous_bootstrap"].append(email)
                remote = None
                tracked_id = None

        try:
            if remote is None:
                upsert = upsert_contact(payload, outlook_id=None, category_name=category_name)
                if not upsert.get("ok"):
                    result["errors"].append(
                        f"Create failed for {row.get('name')}: {upsert.get('error', 'unknown')}"
                    )
                    continue
                new_id = upsert.get("outlook_id") or upsert.get("raw")
                if new_id:
                    conn.execute(
                        "UPDATE contacts SET outlook_contact_id=? WHERE id=?",
                        (new_id, row["contact_id"]),
                    )
                    conn.commit()
                    push_set_ids.add(new_id)
                result["created"] += 1
            else:
                if _needs_update(payload, remote):
                    upsert = upsert_contact(
                        payload,
                        outlook_id=tracked_id,
                        category_name=category_name,
                    )
                    if not upsert.get("ok"):
                        result["errors"].append(
                            f"Update failed for {row.get('name')}: {upsert.get('error', 'unknown')}"
                        )
                        continue
                    result["updated"] += 1
                # Even if no update, adopt the id so delete phase doesn't
                # think this row is orphaned
                if tracked_id and not row.get("outlook_contact_id"):
                    conn.execute(
                        "UPDATE contacts SET outlook_contact_id=? WHERE id=?",
                        (tracked_id, row["contact_id"]),
                    )
                    conn.commit()
                push_set_ids.add(tracked_id)
        except Exception as e:
            logger.exception("contact_sync: unexpected error for contact %s", row.get("contact_id"))
            result["errors"].append(f"Unexpected error for {row.get('name')}: {e}")

    # Delete phase — any PDB-tagged Outlook contact whose id is not in the
    # push set is orphaned and should be removed (if deletes are allowed).
    if allow_deletes:
        for remote_id, remote in remote_by_id.items():
            if remote_id in push_set_ids:
                continue
            # Make sure we actually track this id in PDB; if not, leave it
            # alone — it's a PDB-tagged contact we didn't create
            tracked = conn.execute(
                "SELECT id FROM contacts WHERE outlook_contact_id=?",
                (remote_id,),
            ).fetchone()
            if tracked is None:
                continue
            del_result = delete_contact(remote_id, category_name=category_name)
            if not del_result.get("ok"):
                result["errors"].append(
                    f"Delete failed for Outlook id {remote_id}: {del_result.get('error', 'unknown')}"
                )
                continue
            if del_result.get("deleted") is False:
                # Untagged between list and delete — leave the pointer in DB
                # alone so the next sync reconsiders it
                continue
            conn.execute(
                "UPDATE contacts SET outlook_contact_id=NULL WHERE outlook_contact_id=?",
                (remote_id,),
            )
            conn.commit()
            result["deleted"] += 1

    # Orphan count — contacts with no non-archived client assignment.
    # Not pushed; just reported for transparency.
    orphan_row = conn.execute(
        """
        SELECT COUNT(*) FROM contacts c
        WHERE NOT EXISTS (
            SELECT 1 FROM contact_client_assignments cca
            JOIN clients cl ON cl.id = cca.client_id
            WHERE cca.contact_id = c.id AND cl.archived = 0
        )
        """
    ).fetchone()
    result["skipped_orphan"] = int(orphan_row[0] or 0)

    return result
