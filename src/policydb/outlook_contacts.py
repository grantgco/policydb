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
        set allCats to get categories
        repeat with c in allCats
            if (name of c) as text is catName then
                set foundCat to true
                exit repeat
            end if
        end repeat
    end try
    if not foundCat then
        try
            -- Outlook's color property is an RGB triple, not an enum.
            -- Omitting it lets Outlook pick the default swatch; the user can
            -- recolor the PDB category in Outlook if they want a specific hue.
            make new category with properties {{name:catName}}
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
            -- `get ... of (contents of c)` forces reference resolution so the
            -- inner repeat sees concrete category objects. Without the dual
            -- dereference, `name of cat` silently returns empty strings when
            -- iterating `every contact`.
            set catList to get categories of (contents of c)
            repeat with cat in catList
                if (name of cat) as text is "{esc_name}" then
                    set hasPdb to true
                    exit repeat
                end if
            end repeat
        end try
        if hasPdb then
            -- Local var names are prefixed `v` to avoid clashing with Outlook's
            -- AppleScript vocabulary inside the `tell` block. Plain names like
            -- `company` or `note` get resolved against the app's property
            -- namespace and cause -10006 "Can't set X to ..." errors.
            set vId to ""
            set vFirst to ""
            set vLast to ""
            set vDisplay to ""
            set vCompany to ""
            set vTitle to ""
            set vEmail to ""
            set vBizPhone to ""
            set vMobPhone to ""
            set vNote to ""
            set vStreet to ""
            try
                set vId to (id of c) as text
            end try
            try
                set vFirst to first name of c
            end try
            try
                set vLast to last name of c
            end try
            try
                set vDisplay to display name of c
            end try
            try
                set vCompany to company of c
            end try
            try
                set vTitle to job title of c
            end try
            try
                set emails to email addresses of c
                if (count of emails) > 0 then
                    try
                        set vEmail to address of item 1 of emails
                    end try
                end if
            end try
            try
                set vBizPhone to business phone number of c
            end try
            try
                set vMobPhone to mobile number of c
            end try
            try
                -- `note` preserves newlines; `plain text note` strips them.
                -- Prefer `note` and fall back to `plain text note` if the
                -- field holds rich formatting we can't round-trip.
                set vNote to note of c
            on error
                try
                    set vNote to plain text note of c
                end try
            end try
            try
                set vStreet to business street address of c
            end try
            set vId to my escJSON(vId)
            set vFirst to my escJSON(vFirst)
            set vLast to my escJSON(vLast)
            set vDisplay to my escJSON(vDisplay)
            set vCompany to my escJSON(vCompany)
            set vTitle to my escJSON(vTitle)
            set vEmail to my escJSON(vEmail)
            set vBizPhone to my escJSON(vBizPhone)
            set vMobPhone to my escJSON(vMobPhone)
            set vNote to my escJSON(vNote)
            set vStreet to my escJSON(vStreet)
            if matchCount > 0 then set output to output & ","
            set output to output & "{{\\"outlook_id\\":\\"" & vId & "\\",\\"first_name\\":\\"" & vFirst & "\\",\\"last_name\\":\\"" & vLast & "\\",\\"display_name\\":\\"" & vDisplay & "\\",\\"company\\":\\"" & vCompany & "\\",\\"job_title\\":\\"" & vTitle & "\\",\\"email\\":\\"" & vEmail & "\\",\\"business_phone\\":\\"" & vBizPhone & "\\",\\"mobile_phone\\":\\"" & vMobPhone & "\\",\\"notes\\":\\"" & vNote & "\\",\\"business_address_street\\":\\"" & vStreet & "\\"}}"
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


# Outlook's `note` field accepts newlines but the AppleScript source cannot
# contain a raw newline inside a quoted string. The bridge uses `linefeed &`
# concatenation (LF, matching modern macOS line endings) for multi-line notes
# instead of embedding \n directly. Using `return` (CR) here causes some mac
# apps to render the whole note as one long line.
_NOTES_TRUNCATE = 5000


def _notes_to_applescript_expr(text: str) -> str:
    """Build an AppleScript expression that concatenates quoted lines with `linefeed`.

    Example: "hello\nworld" -> "\"hello\" & linefeed & \"world\""
    Empty text returns "\"\"" for safe assignment.
    """
    if not text:
        return '""'
    text = text[:_NOTES_TRUNCATE]
    lines = text.splitlines() or [""]
    parts = [f'"{_escape_for_applescript(line)}"' for line in lines]
    return " & linefeed & ".join(parts)


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

    # Locate or create. When a tracked id is provided AND the contact still
    # exists but no longer carries the category, honor the user's "untag to
    # remove from sync" escape hatch: return {skipped_untagged: true} so the
    # orchestrator can clear the DB pointer instead of creating a duplicate.
    if outlook_id:
        esc_id = _escape_for_applescript(str(outlook_id))
        locator = f'''
    set targetContact to missing value
    set allContacts to every contact
    repeat with c in allContacts
        try
            if ((id of (contents of c)) as text) is "{esc_id}" then
                set targetContact to contents of c
                exit repeat
            end if
        end try
    end repeat
    if targetContact is not missing value then
        set hasPdbCat to false
        try
            set catList to get categories of (contents of targetContact)
            repeat with cat in catList
                if (name of cat) as text is "{esc_cat}" then
                    set hasPdbCat to true
                    exit repeat
                end if
            end repeat
        end try
        if not hasPdbCat then
            return "{{\\"ok\\":true,\\"outlook_id\\":\\"{esc_id}\\",\\"skipped_untagged\\":true}}"
        end if
        set wasCreated to false
    else
        set targetContact to make new contact with properties {{first name:"{esc_first}", last name:"{esc_last}"}}
        set wasCreated to true
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
            -- Outlook requires both `address` and `type class` in the record;
            -- providing only `address` fails with -1700 "Contact e-mail address
            -- is incorrect (one or more fields may be missing)."
            set email addresses of targetContact to {{{{address:"{esc_email}", type class:work}}}}
        end try
    else
        try
            set email addresses of targetContact to {{}}
        end try
    end if
    try
        set business phone number of targetContact to "{esc_biz_phone}"
    end try
    try
        set mobile number of targetContact to "{esc_mob_phone}"
    end try
    try
        set note of targetContact to ({notes_expr})
    end try
    if "{esc_street}" is not "" then
        try
            set business street address of targetContact to "{esc_street}"
        end try
    end if
    try
        set pdbCat to missing value
        set allCats to get categories
        repeat with cat in allCats
            if (name of cat) as text is "{esc_cat}" then
                set pdbCat to contents of cat
                exit repeat
            end if
        end repeat
        if pdbCat is not missing value then
            set existingCats to get categories of (contents of targetContact)
            set alreadyTagged to false
            repeat with ec in existingCats
                if (name of ec) as text is "{esc_cat}" then
                    set alreadyTagged to true
                    exit repeat
                end if
            end repeat
            if not alreadyTagged then
                -- Concatenate as list-of-one; `existingCats & pdbCat` fails
                -- with a coercion error when existingCats is empty.
                set categories of targetContact to (existingCats & {{pdbCat}})
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
    set allContacts to every contact
    repeat with c in allContacts
        try
            if ((id of (contents of c)) as text) is "{esc_id}" then
                set targetContact to contents of c
                exit repeat
            end if
        end try
    end repeat
    if targetContact is missing value then
        return "{{\\"ok\\":true,\\"deleted\\":false,\\"reason\\":\\"not_found\\"}}"
    end if
    set hasPdb to false
    try
        set catList to get categories of targetContact
        repeat with cat in catList
            if (name of cat) as text is "{esc_cat}" then
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
