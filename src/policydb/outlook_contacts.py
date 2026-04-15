"""AppleScript bridge for Legacy Outlook for Mac contacts.

Companion to outlook.py (which handles email). Provides the four operations
needed by contact_sync.py to push PolicyDB contacts into Outlook's address
book, fenced by the "PDB" category so we never touch the user's personal
contacts.

All functions return dicts with an "ok" key and gracefully handle Outlook
being unavailable or on the wrong version (New Outlook for Mac dropped
AppleScript entirely — is_outlook_available() returns False there).
"""

from __future__ import annotations

import json
import logging
import re

from policydb.outlook import (
    _TIMEOUT,  # noqa: F401  (kept for symmetry with outlook.py)
    _escape_for_applescript,
    _run_applescript,
    is_outlook_available,
)

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY = "PDB"


# ─── Shared AppleScript helpers (escJSON / replaceText / padNum) ────────────

_APPLESCRIPT_HELPERS = r'''
on escJSON(txt)
    try
        set txt to txt as text
    on error
        set txt to ""
    end try
    set txt to my replaceText(txt, "\\", "\\\\")
    set txt to my replaceText(txt, "\"", "\\\"")
    set txt to my replaceText(txt, return, "\\n")
    set txt to my replaceText(txt, linefeed, "\\n")
    set txt to my replaceText(txt, tab, "\\t")
    return txt
end escJSON

on replaceText(txt, srch, repl)
    set AppleScript's text item delimiters to srch
    set parts to text items of txt
    set AppleScript's text item delimiters to repl
    set txt to parts as text
    set AppleScript's text item delimiters to ""
    return txt
end replaceText
'''


# ─── ensure_pdb_category ────────────────────────────────────────────────────


def ensure_pdb_category(category_name: str = DEFAULT_CATEGORY) -> dict:
    """Create the PDB category in Outlook if it does not already exist.

    Idempotent — safe to call at the top of every sync. Outlook refuses to
    inline-create categories when assigning them to contacts, so this has to
    run first.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running."}

    esc_name = _escape_for_applescript(category_name)

    script = f'''
tell application "Microsoft Outlook"
    set catName to "{esc_name}"
    set foundCat to false
    try
        repeat with c in categories
            if name of c is catName then
                set foundCat to true
                exit repeat
            end if
        end repeat
    end try
    if not foundCat then
        try
            make new category with properties {{name:catName, color:category color 7}}
        on error errMsg
            return "{{\\"ok\\": false, \\"error\\": \\"" & my escJSON(errMsg) & "\\"}}"
        end try
    end if
end tell
return "{{\\"ok\\": true}}"
{_APPLESCRIPT_HELPERS}
'''
    return _run_applescript(script)


# ─── list_pdb_contacts ──────────────────────────────────────────────────────


def list_pdb_contacts(category_name: str = DEFAULT_CATEGORY) -> dict:
    """Return every Outlook contact carrying the PDB category.

    Shape: {"ok": True, "contacts": [{"outlook_id": "...", "display_name": ...,
    "first_name": ..., "last_name": ..., "company": ..., "job_title": ...,
    "email": ..., "business_phone": ..., "mobile_phone": ..., "notes": ...,
    "business_address_street": ...}, ...]}

    The cap of 500 mirrors the email-side search cap (30s osascript timeout).
    Anything beyond 500 is dropped with a warning — in practice books of
    business rarely have that many contacts, and the orchestrator can page
    later if it becomes a real constraint.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running.", "contacts": []}

    esc_name = _escape_for_applescript(category_name)

    script = f'''
set output to "["
set matchCount to 0
tell application "Microsoft Outlook"
    set allContacts to every contact
    repeat with c in allContacts
        if matchCount >= 500 then exit repeat
        set hasPdb to false
        try
            repeat with cat in (categories of c)
                if name of cat is "{esc_name}" then
                    set hasPdb to true
                    exit repeat
                end if
            end repeat
        end try
        if hasPdb then
            set cId to ""
            set firstName to ""
            set lastName to ""
            set displayName to ""
            set company to ""
            set jobTitle to ""
            set emailAddr to ""
            set bizPhone to ""
            set mobPhone to ""
            set notesText to ""
            set addrStreet to ""
            try
                set cId to (id of c) as text
            end try
            try
                set firstName to first name of c
            end try
            try
                set lastName to last name of c
            end try
            try
                set displayName to display name of c
            end try
            try
                set company to company of c
            end try
            try
                set jobTitle to job title of c
            end try
            try
                set emails to email addresses of c
                if (count of emails) > 0 then
                    try
                        set emailAddr to address of item 1 of emails
                    end try
                end if
            end try
            try
                set bizPhone to business phone of c
            end try
            try
                set mobPhone to mobile phone of c
            end try
            try
                set notesText to plain text content of c
            on error
                try
                    set notesText to notes of c
                end try
            end try
            try
                set ba to business address of c
                try
                    set addrStreet to street of ba
                end try
            end try
            set cId to my escJSON(cId)
            set firstName to my escJSON(firstName)
            set lastName to my escJSON(lastName)
            set displayName to my escJSON(displayName)
            set company to my escJSON(company)
            set jobTitle to my escJSON(jobTitle)
            set emailAddr to my escJSON(emailAddr)
            set bizPhone to my escJSON(bizPhone)
            set mobPhone to my escJSON(mobPhone)
            set notesText to my escJSON(notesText)
            set addrStreet to my escJSON(addrStreet)
            if matchCount > 0 then set output to output & ","
            set output to output & "{{\\"outlook_id\\":\\"" & cId & "\\",\\"first_name\\":\\"" & firstName & "\\",\\"last_name\\":\\"" & lastName & "\\",\\"display_name\\":\\"" & displayName & "\\",\\"company\\":\\"" & company & "\\",\\"job_title\\":\\"" & jobTitle & "\\",\\"email\\":\\"" & emailAddr & "\\",\\"business_phone\\":\\"" & bizPhone & "\\",\\"mobile_phone\\":\\"" & mobPhone & "\\",\\"notes\\":\\"" & notesText & "\\",\\"business_address_street\\":\\"" & addrStreet & "\\"}}"
            set matchCount to matchCount + 1
        end if
    end repeat
end tell
set output to output & "]"
return output
{_APPLESCRIPT_HELPERS}
'''

    result = _run_applescript(script)
    if not result.get("ok", True):
        return {"ok": False, "error": result.get("error", "AppleScript failed"), "contacts": []}

    raw = result.get("raw", "[]")
    try:
        contacts = json.loads(raw)
        if not isinstance(contacts, list):
            return {"ok": False, "error": "Unexpected AppleScript output", "contacts": []}
        return {"ok": True, "contacts": contacts}
    except json.JSONDecodeError as e:
        logger.warning("list_pdb_contacts JSON decode failed: %s", e)
        return {"ok": False, "error": f"Could not parse Outlook output: {e}", "contacts": []}


# ─── upsert_contact ─────────────────────────────────────────────────────────


# Outlook's `notes` field accepts newlines but the AppleScript source cannot
# contain a raw newline inside a quoted string. The bridge uses `return & `
# concatenation for multi-line notes instead of embedding \n directly.
_NOTES_TRUNCATE = 5000


def _notes_to_applescript_expr(text: str) -> str:
    """Build an AppleScript expression that concatenates quoted lines with `return`.

    Example: "hello\nworld" -> "\"hello\" & return & \"world\""
    Empty text returns "\"\"" for safe assignment.
    """
    if not text:
        return '""'
    text = text[:_NOTES_TRUNCATE]
    lines = text.splitlines() or [""]
    parts = [f'"{_escape_for_applescript(line)}"' for line in lines]
    return " & return & ".join(parts)


def upsert_contact(
    payload: dict,
    *,
    outlook_id: str | None = None,
    category_name: str = DEFAULT_CATEGORY,
) -> dict:
    """Create or update a single Outlook contact.

    Payload keys (all optional, strings only):
        display_name, first_name, last_name, company, job_title,
        email, business_phone, mobile_phone, notes, business_address_street

    On create, returns {"ok": True, "outlook_id": "<new id>", "created": True}.
    On modify, returns {"ok": True, "outlook_id": "<same id>", "created": False}.

    The function writes fields individually with try/end try wrappers so that
    user-managed fields we don't know about (birthday, spouse, custom fields)
    are preserved. We never use a wholesale `set properties of contact`
    assignment. Always re-asserts the PDB category.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running."}

    esc_cat = _escape_for_applescript(category_name)
    esc_first = _escape_for_applescript(payload.get("first_name", "") or "")
    esc_last = _escape_for_applescript(payload.get("last_name", "") or "")
    esc_display = _escape_for_applescript(payload.get("display_name", "") or "")
    esc_company = _escape_for_applescript(payload.get("company", "") or "")
    esc_title = _escape_for_applescript(payload.get("job_title", "") or "")
    esc_email = _escape_for_applescript(payload.get("email", "") or "")
    esc_biz_phone = _escape_for_applescript(payload.get("business_phone", "") or "")
    esc_mob_phone = _escape_for_applescript(payload.get("mobile_phone", "") or "")
    esc_street = _escape_for_applescript(payload.get("business_address_street", "") or "")
    notes_expr = _notes_to_applescript_expr(payload.get("notes", "") or "")

    # Locate or create
    if outlook_id:
        esc_id = _escape_for_applescript(str(outlook_id))
        # Look up contact by stored id. If not found, fall through to create.
        locator = f'''
    set targetContact to missing value
    try
        repeat with c in (every contact)
            try
                if ((id of c) as text) is "{esc_id}" then
                    set targetContact to c
                    exit repeat
                end if
            end try
        end repeat
    end try
    if targetContact is missing value then
        set targetContact to make new contact with properties {{first name:"{esc_first}", last name:"{esc_last}"}}
        set wasCreated to true
    else
        set wasCreated to false
    end if
'''
    else:
        locator = f'''
    set targetContact to make new contact with properties {{first name:"{esc_first}", last name:"{esc_last}"}}
    set wasCreated to true
'''

    script = f'''
tell application "Microsoft Outlook"
{locator}
    try
        set first name of targetContact to "{esc_first}"
    end try
    try
        set last name of targetContact to "{esc_last}"
    end try
    if "{esc_display}" is not "" then
        try
            set display name of targetContact to "{esc_display}"
        end try
    end if
    try
        set company of targetContact to "{esc_company}"
    end try
    try
        set job title of targetContact to "{esc_title}"
    end try
    if "{esc_email}" is not "" then
        try
            set email addresses of targetContact to {{{{address:"{esc_email}"}}}}
        on error
            try
                set email address of targetContact to "{esc_email}"
            end try
        end try
    else
        try
            set email addresses of targetContact to {{}}
        end try
    end if
    try
        set business phone of targetContact to "{esc_biz_phone}"
    end try
    try
        set mobile phone of targetContact to "{esc_mob_phone}"
    end try
    try
        set notes of targetContact to ({notes_expr})
    end try
    if "{esc_street}" is not "" then
        try
            set business address of targetContact to {{{{street:"{esc_street}"}}}}
        end try
    end if
    try
        set pdbCat to missing value
        repeat with cat in categories
            if name of cat is "{esc_cat}" then
                set pdbCat to cat
                exit repeat
            end if
        end repeat
        if pdbCat is not missing value then
            set existingCats to categories of targetContact
            set alreadyTagged to false
            repeat with ec in existingCats
                if name of ec is "{esc_cat}" then
                    set alreadyTagged to true
                    exit repeat
                end if
            end repeat
            if not alreadyTagged then
                set categories of targetContact to (existingCats & pdbCat)
            end if
        end if
    end try
    set newId to ((id of targetContact) as text)
    set newId to my escJSON(newId)
    if wasCreated then
        return "{{\\"ok\\":true,\\"outlook_id\\":\\"" & newId & "\\",\\"created\\":true}}"
    else
        return "{{\\"ok\\":true,\\"outlook_id\\":\\"" & newId & "\\",\\"created\\":false}}"
    end if
end tell
{_APPLESCRIPT_HELPERS}
'''

    return _run_applescript(script)


# ─── delete_contact ─────────────────────────────────────────────────────────


def delete_contact(outlook_id: str, category_name: str = DEFAULT_CATEGORY) -> dict:
    """Delete a contact by Outlook id. No-op if not found.

    Safety: will only delete contacts that currently carry the PDB category.
    If the user has untagged a contact between syncs, delete_contact leaves
    it alone and returns {"ok": True, "deleted": False, "reason": "untagged"}.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running."}

    esc_id = _escape_for_applescript(str(outlook_id))
    esc_cat = _escape_for_applescript(category_name)

    script = f'''
tell application "Microsoft Outlook"
    set targetContact to missing value
    try
        repeat with c in (every contact)
            try
                if ((id of c) as text) is "{esc_id}" then
                    set targetContact to c
                    exit repeat
                end if
            end try
        end repeat
    end try
    if targetContact is missing value then
        return "{{\\"ok\\":true,\\"deleted\\":false,\\"reason\\":\\"not_found\\"}}"
    end if
    set hasPdb to false
    try
        repeat with cat in (categories of targetContact)
            if name of cat is "{esc_cat}" then
                set hasPdb to true
                exit repeat
            end if
        end repeat
    end try
    if not hasPdb then
        return "{{\\"ok\\":true,\\"deleted\\":false,\\"reason\\":\\"untagged\\"}}"
    end if
    try
        delete targetContact
        return "{{\\"ok\\":true,\\"deleted\\":true}}"
    on error errMsg
        return "{{\\"ok\\":false,\\"error\\":\\"" & my escJSON(errMsg) & "\\"}}"
    end try
end tell
{_APPLESCRIPT_HELPERS}
'''
    return _run_applescript(script)


# ─── Utilities consumed by contact_sync.py ──────────────────────────────────


_HONORIFICS = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam", "fr"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq", "cpa", "cpcu", "arm", "cic"}


def split_name(full_name: str) -> tuple[str, str]:
    """Best-effort split of a single-string name into (first, last).

    Drops honorifics ("Dr.", "Mr.", etc.) and trailing suffixes ("Jr", "III").
    The full original string is always the source of truth — this is only
    used to populate Outlook's first/last fields as derived values.

    Single-word names go to `first`; `last` stays empty.
    """
    name = (full_name or "").strip()
    if not name:
        return ("", "")
    tokens = re.split(r"\s+", name)
    cleaned: list[str] = []
    for tok in tokens:
        stripped = tok.rstrip(".,").lower()
        if stripped in _HONORIFICS:
            continue
        cleaned.append(tok.rstrip(",").rstrip("."))
    # Drop trailing suffix tokens
    while cleaned and cleaned[-1].rstrip(".,").lower() in _SUFFIXES:
        cleaned.pop()
    if not cleaned:
        return (name, "")
    if len(cleaned) == 1:
        return (cleaned[0], "")
    first = cleaned[0]
    last = " ".join(cleaned[1:])
    return (first, last)
