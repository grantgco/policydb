"""AppleScript bridge for Legacy Outlook for Mac.

Provides functions to create drafts, search emails, and read flagged items
via osascript subprocess calls. All functions return dicts with an 'ok' key
and gracefully handle Outlook being unavailable.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime

logger = logging.getLogger(__name__)

_TIMEOUT = 30  # default seconds per osascript call — overridable per call

# Per-function timeouts. Folder crawls and discover runs materialize large
# `whose` predicates that can take minutes on big archives; a flat 30s budget
# is too tight and causes silent folder-level sync loss. Values here are
# overridable via the `outlook_script_timeout_seconds` config key (see
# `_resolve_timeout` below).
_DEFAULT_TIMEOUTS = {
    "availability": 5,
    "create_draft": 30,
    "search_emails": 30,
    "search_all_folders": 120,
    "search_folder_since": 120,
    "get_flagged_emails": 120,
    "discover_folders": 300,
}


def _resolve_timeout(op: str) -> int:
    """Look up a per-op timeout; defer config import to avoid cycles."""
    base = _DEFAULT_TIMEOUTS.get(op, _TIMEOUT)
    try:
        from policydb.config import load_config
        cfg = load_config()
        overrides = cfg.get("outlook_script_timeout_seconds", {}) or {}
        if isinstance(overrides, dict):
            val = overrides.get(op)
            if val is not None:
                return max(5, int(val))
    except Exception:
        pass  # never let config lookup block a sync
    return base


def is_outlook_available() -> bool:
    """Check if Legacy Outlook for Mac is running."""
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to '
             '(name of processes) contains "Microsoft Outlook"'],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _run_applescript(script: str, timeout: int | None = None) -> dict:
    """Execute an AppleScript and return parsed JSON output.

    ``timeout`` overrides the default osascript subprocess budget. Callers
    that scan many messages (folder crawl, discover) should pass a longer
    value so large `whose` predicates don't time out silently.
    """
    effective_timeout = timeout if timeout is not None else _TIMEOUT
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=effective_timeout,
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            logger.warning("AppleScript error: %s", err)
            if "not running" in err.lower() or "application can" in err.lower():
                return {"ok": False, "error": "Outlook is not running. Please open Legacy Outlook and try again."}
            return {"ok": False, "error": err or "AppleScript returned an error"}

        stdout = result.stdout.strip()
        if not stdout:
            return {"ok": True}
        # Try parsing as JSON
        try:
            parsed = json.loads(stdout)
            # If parsed is a dict with "ok" key, return as-is
            if isinstance(parsed, dict) and "ok" in parsed:
                return parsed
            # Otherwise wrap it — raw output goes in "raw" for caller to handle
            return {"ok": True, "raw": stdout}
        except json.JSONDecodeError:
            return {"ok": True, "raw": stdout}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Outlook took too long to respond ({effective_timeout}s timeout)"}
    except FileNotFoundError:
        return {"ok": False, "error": "osascript not found — are you on macOS?"}
    except Exception as e:
        logger.exception("Unexpected error calling AppleScript")
        return {"ok": False, "error": str(e)}


def _escape_for_applescript(text: str) -> str:
    """Escape a string for use inside AppleScript double quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_recipient_fields(email: dict) -> None:
    """In-place: split comma-joined recipient strings into lists and populate
    the backward-compatible ``recipients`` field as ``to + cc``.

    AppleScript emits ``to_recipients`` and ``cc_recipients`` as comma-
    separated strings. Older code paths / fallbacks may still carry a
    pre-combined ``recipients`` string — we honor it as the TO list in
    that case.
    """
    def _split(s) -> list[str]:
        if isinstance(s, list):
            return [x.strip() for x in s if x and str(x).strip()]
        if isinstance(s, str):
            return [p.strip() for p in s.split(",") if p.strip()]
        return []

    to_list = _split(email.get("to_recipients"))
    cc_list = _split(email.get("cc_recipients"))
    if not to_list and not cc_list and "recipients" in email:
        # Legacy combined field — everything lands as TO.
        to_list = _split(email.get("recipients"))
    email["to_recipients"] = to_list
    email["cc_recipients"] = cc_list
    email["recipients"] = to_list + cc_list


def create_draft(
    to: str,
    cc: list[str] | None = None,
    subject: str = "",
    html_body: str = "",
) -> dict:
    """Create an Outlook draft with HTML body and open it for review.

    Returns {"ok": True} on success or {"ok": False, "error": "..."}.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running. Please open Legacy Outlook and try again."}

    cc = cc or []
    esc_subject = _escape_for_applescript(subject)
    esc_body = _escape_for_applescript(html_body)
    esc_to = _escape_for_applescript(to)

    # Build recipient lines
    recipient_lines = []
    if to and to.strip():
        recipient_lines.append(
            f'make new to recipient at newMsg with properties '
            f'{{email address:{{address:"{esc_to}"}}}}'
        )
    for addr in cc:
        esc_addr = _escape_for_applescript(addr)
        recipient_lines.append(
            f'make new cc recipient at newMsg with properties '
            f'{{email address:{{address:"{esc_addr}"}}}}'
        )
    recipients_script = "\n".join(recipient_lines)

    script = f'''
tell application "Microsoft Outlook"
    set newMsg to make new outgoing message with properties {{subject:"{esc_subject}", content:"{esc_body}"}}
    {recipients_script}
    open newMsg
    activate
end tell
return "{{\\"ok\\": true}}"
'''

    return _run_applescript(script, timeout=_resolve_timeout("create_draft"))


def search_all_folders(
    since_date: datetime,
    category_filter: str = "",
) -> dict:
    """Search ALL mail folders (including subfolders) for emails since a date.

    Recursively walks the folder tree under the default account up to 8 levels
    deep so emails filed in nested user folders like ``Inbox/Clients/Acme``
    aren't silently dropped. The legacy implementation only iterated
    ``every mail folder of default account`` (top level only) which meant a
    reply filed into a subfolder would never be enumerated and never matched
    against its ``[PDB:]`` ref tag.

    Returns {"ok": True, "emails": [...]} with the slash-delimited folder
    path on each email (e.g. ``"Inbox/Clients/Acme"``).
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running."}

    date_str = since_date.strftime("%m/%d/%Y")
    esc_cat = _escape_for_applescript(category_filter) if category_filter else ""

    # Build per-message category check in the loop (AppleScript 'whose' can't filter by category)
    cat_check = ""
    if esc_cat:
        cat_check = f'''
                set hasCat to false
                try
                    set cats to categories of msg
                    repeat with c in cats
                        if name of c is "{esc_cat}" then set hasCat to true
                    end repeat
                end try
                if not hasCat then
                    -- Also check for [PDB: ref tag in subject/content
                    set hasRef to false
                    try
                        if subject of msg contains "[PDB:" then set hasRef to true
                        if not hasRef and (plain text content of msg) contains "[PDB:" then set hasRef to true
                    end try
                    if not hasRef then set skipMsg to true
                end if'''

    # Recursive walk: a script object carries the JSON accumulator and message
    # counter across the recursion so we don't have to thread them through
    # handler return values (AppleScript handlers can't return structured
    # tuples cleanly). Depth cap of 8 prevents pathological folder trees from
    # blowing the call stack; matches what most users would need (Phase 3D
    # crawl is the path for genuinely deep archives). System folder names are
    # checked at every level — Sent Items at any depth still routes through
    # the dedicated Sent scan.
    script = f'''
script acc
    property out : ""
    property cnt : 0
end script

set acc's out to "[" & return
set acc's cnt to 0
set myDate to date "{date_str}"
set capLimit to 500

tell application "Microsoft Outlook"
    try
        set rootFolders to mail folders of default account
    on error
        set rootFolders to {{}}
    end try
end tell

repeat with rf in rootFolders
    if acc's cnt >= capLimit then exit repeat
    try
        tell application "Microsoft Outlook" to set rfName to name of rf
        my scanTree(contents of rf, rfName, myDate, capLimit, 0)
    end try
end repeat

return (acc's out) & return & "]"

on scanTree(f, folderPath, myDate, capLimit, depth)
    if acc's cnt >= capLimit then return
    if depth > 8 then return
    set leafName to my leafOf(folderPath)
    -- Skip system folders at every level (Sent Items handled separately)
    if leafName is "Deleted Items" or leafName is "Junk Email" or leafName is "Drafts" or leafName is "Trash" or leafName is "Clutter" or leafName is "Sent Items" or leafName is "Outbox" then return

    -- Scan messages in this folder
    tell application "Microsoft Outlook"
        try
            set folderMsgs to (messages of f whose (time sent >= myDate))
            repeat with msg in folderMsgs
                if acc's cnt >= capLimit then exit repeat
                set skipMsg to false
                {cat_check}
                if not skipMsg then
                    set acc's cnt to (acc's cnt) + 1
                    set msgSubject to subject of msg
                    set msgSender to ""
                    try
                        set msgSender to address of sender of msg
                    end try
                    set msgDate to time sent of msg
                    set msgId to id of msg as text
                    set recipTo to ""
                    try
                        repeat with r in to recipients of msg
                            set recipTo to recipTo & address of email address of r & ","
                        end repeat
                    end try
                    set recipCc to ""
                    try
                        repeat with r in cc recipients of msg
                            set recipCc to recipCc & address of email address of r & ","
                        end repeat
                    end try
                    set catList to ""
                    try
                        set cats to categories of msg
                        repeat with c in cats
                            set catList to catList & (name of c) & ","
                        end repeat
                    end try
                    set msgContent to ""
                    try
                        set msgContent to plain text content of msg
                        if length of msgContent > 100000 then
                            set msgContent to text 1 thru 100000 of msgContent
                        end if
                    on error
                        try
                            set msgContent to content of msg
                            if length of msgContent > 100000 then
                                set msgContent to text 1 thru 100000 of msgContent
                            end if
                        end try
                    end try
                    set msgSubject to my escJSON(msgSubject)
                    set msgSender to my escJSON(msgSender)
                    set msgContent to my escJSON(msgContent)
                    set recipTo to my escJSON(recipTo)
                    set recipCc to my escJSON(recipCc)
                    set msgId to my escJSON(msgId)
                    set catList to my escJSON(catList)
                    set escFolder to my escJSON(folderPath)
                    set dateStr to (year of msgDate as text) & "-" & my padNum(month of msgDate as integer) & "-" & my padNum(day of msgDate) & "T" & my padNum(hours of msgDate) & ":" & my padNum(minutes of msgDate) & ":00"
                    set acc's out to (acc's out) & "  {{\\"message_id\\": \\"" & msgId & "\\", \\"subject\\": \\"" & msgSubject & "\\", \\"sender\\": \\"" & msgSender & "\\", \\"to_recipients\\": \\"" & recipTo & "\\", \\"cc_recipients\\": \\"" & recipCc & "\\", \\"date\\": \\"" & dateStr & "\\", \\"body_snippet\\": \\"" & msgContent & "\\", \\"folder\\": \\"" & escFolder & "\\", \\"categories\\": \\"" & catList & "\\"}},"
                end if
            end repeat
        end try
    end tell

    -- Recurse into subfolders
    set subList to {{}}
    try
        tell application "Microsoft Outlook" to set subList to mail folders of f
    end try
    repeat with sub in subList
        if acc's cnt >= capLimit then return
        try
            tell application "Microsoft Outlook" to set subName to name of sub
            my scanTree(contents of sub, folderPath & "/" & subName, myDate, capLimit, depth + 1)
        end try
    end repeat
end scanTree

on leafOf(p)
    set AppleScript's text item delimiters to "/"
    set parts to text items of p
    set AppleScript's text item delimiters to ""
    if (count of parts) is 0 then return p
    return last item of parts
end leafOf

on escJSON(txt)
    set txt to my replaceText(txt, "\\\\", "\\\\\\\\")
    set txt to my replaceText(txt, "\\"", "\\\\\\"")
    set txt to my replaceText(txt, return, "\\\\n")
    set txt to my replaceText(txt, linefeed, "\\\\n")
    set txt to my replaceText(txt, tab, "\\\\t")
    return txt
end escJSON

on padNum(n)
    if n < 10 then return "0" & (n as text)
    return n as text
end padNum

on replaceText(txt, srch, repl)
    set AppleScript's text item delimiters to srch
    set parts to text items of txt
    set AppleScript's text item delimiters to repl
    set txt to parts as text
    set AppleScript's text item delimiters to ""
    return txt
end replaceText
'''

    result = _run_applescript(script, timeout=_resolve_timeout("search_all_folders"))
    if not result.get("ok", True):
        return result

    raw = result.get("raw", "[]")
    try:
        raw = re.sub(r',\s*\]', ']', raw)
        emails = json.loads(raw)
        for email in emails:
            _normalize_recipient_fields(email)
            if isinstance(email.get("categories"), str):
                email["categories"] = [c.strip() for c in email["categories"].split(",") if c.strip()]
            else:
                email["categories"] = []
        # AppleScript caps the scan at 500 messages to keep the script
        # responsive. Surface that to the caller so `sync_outlook` can
        # warn the user instead of silently dropping the overflow — if
        # hit, the lookback window should be shortened.
        resp = {"ok": True, "emails": emails}
        if len(emails) >= 500:
            resp["truncated"] = True
            resp["cap"] = 500
        return resp
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse all-folder results: %s", e)
        return {"ok": True, "emails": [], "parse_warning": str(e)}


def search_emails(
    folder: str,
    since_date: datetime,
    search_pattern: str | None = None,
) -> dict:
    """Search a mail folder for emails since a given date.

    folder: "Sent Items", "Inbox", etc.
    since_date: Only return emails on or after this date.
    search_pattern: Optional text to search in subject + body (e.g. "[PDB:")

    Returns {"ok": True, "emails": [...]} or {"ok": False, "error": "..."}.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running."}

    date_str = since_date.strftime("%m/%d/%Y")
    esc_folder = _escape_for_applescript(folder)

    # Build the search filter
    if search_pattern:
        esc_pattern = _escape_for_applescript(search_pattern)
        filter_clause = (
            f'whose (time sent >= myDate and '
            f'(subject contains "{esc_pattern}" or content contains "{esc_pattern}"))'
        )
    else:
        filter_clause = 'whose (time sent >= myDate)'

    script = f'''
set output to ""
set myDate to date "{date_str}"
tell application "Microsoft Outlook"
    set theFolder to mail folder "{esc_folder}" of default account
    set msgs to messages of theFolder {filter_clause}
    set msgCount to count of msgs
    if msgCount > 500 then set msgCount to 500
    set output to "[" & return
    repeat with i from 1 to msgCount
        set msg to item i of msgs
        set msgSubject to subject of msg
        -- Sender: sent messages may not have a sender property
        set msgSender to ""
        try
            set msgSender to address of sender of msg
        end try
        set msgDate to time sent of msg
        set msgId to id of msg as text
        -- Get recipients, keeping TO and CC separate so downstream can
        -- tag them with distinct roles in activity_contacts.
        set recipTo to ""
        try
            repeat with r in to recipients of msg
                set recipTo to recipTo & address of email address of r & ","
            end repeat
        end try
        set recipCc to ""
        try
            repeat with r in cc recipients of msg
                set recipCc to recipCc & address of email address of r & ","
            end repeat
        end try
        -- Get body as plain text for ref tag matching
        set msgContent to ""
        try
            set msgContent to plain text content of msg
            if length of msgContent > 100000 then
                set msgContent to text 1 thru 100000 of msgContent
            end if
        on error
            try
                set msgContent to content of msg
                if length of msgContent > 100000 then
                    set msgContent to text 1 thru 100000 of msgContent
                end if
            end try
        end try
        -- Get categories
        set catList to ""
        try
            set cats to categories of msg
            repeat with c in cats
                set catList to catList & (name of c) & ","
            end repeat
        end try
        -- Escape JSON strings
        set msgSubject to my escJSON(msgSubject)
        set msgSender to my escJSON(msgSender)
        set msgContent to my escJSON(msgContent)
        set recipTo to my escJSON(recipTo)
        set recipCc to my escJSON(recipCc)
        set msgId to my escJSON(msgId)
        set catList to my escJSON(catList)
        set dateStr to (year of msgDate as text) & "-" & my padNum(month of msgDate as integer) & "-" & my padNum(day of msgDate) & "T" & my padNum(hours of msgDate) & ":" & my padNum(minutes of msgDate) & ":00"
        set output to output & "  {{\\"message_id\\": \\"" & msgId & "\\", \\"subject\\": \\"" & msgSubject & "\\", \\"sender\\": \\"" & msgSender & "\\", \\"to_recipients\\": \\"" & recipTo & "\\", \\"cc_recipients\\": \\"" & recipCc & "\\", \\"date\\": \\"" & dateStr & "\\", \\"body_snippet\\": \\"" & msgContent & "\\", \\"folder\\": \\"{esc_folder}\\", \\"categories\\": \\"" & catList & "\\"}},"
        if i < msgCount then set output to output & return
    end repeat
    set output to output & return & "]"
end tell
return output

on escJSON(txt)
    set txt to my replaceText(txt, "\\\\", "\\\\\\\\")
    set txt to my replaceText(txt, "\\"", "\\\\\\"")
    set txt to my replaceText(txt, return, "\\\\n")
    set txt to my replaceText(txt, linefeed, "\\\\n")
    set txt to my replaceText(txt, tab, "\\\\t")
    return txt
end escJSON

on padNum(n)
    if n < 10 then return "0" & (n as text)
    return n as text
end padNum

on replaceText(txt, srch, repl)
    set AppleScript's text item delimiters to srch
    set parts to text items of txt
    set AppleScript's text item delimiters to repl
    set txt to parts as text
    set AppleScript's text item delimiters to ""
    return txt
end replaceText
'''

    result = _run_applescript(script, timeout=_resolve_timeout("search_emails"))
    if not result.get("ok", True):
        return result

    # Parse the JSON array from raw output
    raw = result.get("raw", "[]")
    try:
        # Clean up trailing commas before closing bracket
        raw = re.sub(r',\s*\]', ']', raw)
        emails = json.loads(raw)
        for email in emails:
            _normalize_recipient_fields(email)
            if isinstance(email.get("categories"), str):
                email["categories"] = [
                    c.strip() for c in email["categories"].split(",")
                    if c.strip()
                ]
            else:
                email["categories"] = []
        return {"ok": True, "emails": emails}
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse Outlook email results: %s", e)
        return {"ok": True, "emails": [], "parse_warning": str(e)}


def discover_folders() -> dict:
    """Walk the Outlook mail folder tree and return a flat list of folders.

    Enumerates the default account's folder tree up to 3 levels deep
    (account -> folder -> subfolder -> subsubfolder). Each entry has a
    slash-delimited path like ``Inbox/Clients/Acme`` and an inferred
    ``kind`` based on the leaf folder name:

      - ``inbox``   — leaf name is "Inbox"
      - ``sent``    — leaf name is "Sent Items" or "Sent"
      - ``drafts``  — leaf name is "Drafts"
      - ``archive`` — leaf name is "Archive"
      - ``system``  — Deleted Items, Junk Email, Outbox, RSS Feeds, Sync Issues, Clutter
      - ``custom``  — everything else

    The caller is expected to persist this list into the
    ``outlook_folder_sync`` table (migration 153). Folders matching the
    user's ``outlook_excluded_folders`` config list get persisted with
    ``include_in_crawl = 0`` so the crawler skips them.

    Returns:
        ``{"ok": True, "folders": [{"path": ..., "kind": ...}, ...]}`` or
        ``{"ok": False, "error": "..."}``.

    Depth limit: 3 levels. Deeper trees need manual folder entry in the
    settings UI until we add configurable depth or a queue-based walk.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running. Please open Legacy Outlook and try again."}

    # AppleScript handler that emits a single folder JSON entry.
    # The inferred kind is name-based rather than class-based because Outlook
    # for Mac's `class of` returns are inconsistent across account types
    # (Exchange vs IMAP). Name-based is pragmatic and the user can override
    # in the Settings UI anyway.
    #
    # Defensive try wraps at every level: a single bad folder (corrupt
    # sync state, special folder type that doesn't respond to `name of`,
    # access denied) is caught and skipped so the rest of the tree still
    # gets discovered. Without these wraps one bad folder could kill the
    # whole walk and we'd silently lose discovery for everything after it.
    #
    # Empty-account case returns a structured error JSON instead of "[]"
    # so the UI can show a useful message (was: silent empty list).
    script = r'''
set output to "[" & return
tell application "Microsoft Outlook"
    try
        set level1 to mail folders of default account
    on error errMsg
        return "{\"ok\": false, \"error\": \"Could not list folders from default account: " & my escJSON(errMsg) & "\"}"
    end try
    repeat with l1 in level1
        try
            set l1Name to name of l1
            set output to output & my emitEntry(l1Name, l1Name) & "," & return
            try
                set level2 to mail folders of l1
                repeat with l2 in level2
                    try
                        set l2Name to name of l2
                        set l2Path to l1Name & "/" & l2Name
                        set output to output & my emitEntry(l2Path, l2Name) & "," & return
                        try
                            set level3 to mail folders of l2
                            repeat with l3 in level3
                                try
                                    set l3Name to name of l3
                                    set l3Path to l2Path & "/" & l3Name
                                    set output to output & my emitEntry(l3Path, l3Name) & "," & return
                                end try
                            end repeat
                        end try
                    end try
                end repeat
            end try
        end try
    end repeat
end tell
set output to output & "]"
return output

on emitEntry(fullPath, leafName)
    set fKind to "custom"
    if leafName is "Inbox" then set fKind to "inbox"
    if leafName is "Sent Items" or leafName is "Sent" then set fKind to "sent"
    if leafName is "Drafts" then set fKind to "drafts"
    if leafName is "Archive" then set fKind to "archive"
    if leafName is "Deleted Items" or leafName is "Junk Email" or leafName is "Outbox" or leafName is "RSS Feeds" or leafName is "Clutter" or leafName is "Sync Issues" then set fKind to "system"
    set escPath to my escJSON(fullPath)
    return "  {\"path\": \"" & escPath & "\", \"kind\": \"" & fKind & "\"}"
end emitEntry

on escJSON(txt)
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

    result = _run_applescript(script, timeout=_resolve_timeout("discover_folders"))
    if not result.get("ok", True):
        return result

    raw = result.get("raw", "[]")
    # Empty-account / list-failure case: AppleScript returns a structured
    # error JSON object (Bug A fix). Detect and surface it before trying
    # to parse the raw output as a folder array.
    raw_stripped = raw.strip()
    if raw_stripped.startswith("{"):
        try:
            err_obj = json.loads(raw_stripped)
            if not err_obj.get("ok", True):
                return err_obj
        except json.JSONDecodeError:
            pass

    try:
        # Strip trailing comma before closing bracket (AppleScript always emits one)
        raw = re.sub(r',\s*\]', ']', raw)
        folders = json.loads(raw)
        return {"ok": True, "folders": folders}
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse folder discovery results: %s", e)
        return {"ok": False, "error": f"Could not parse folder list: {e}", "raw": raw[:500]}


def search_folder_since(folder_path: str, since_date: datetime) -> dict:
    """Path-aware folder search returning conversation_id + internet_message_id.

    Phase 3D crawl entry point. Walks an arbitrarily nested folder path
    (slash-delimited, e.g. ``Archive/2023/Acme``) and returns up to 500
    messages with ``time sent >= since_date``. Same email shape as
    :func:`search_emails` plus two new fields:

      - ``conversation_id`` — Outlook's native thread key (string).
        Falls back to ``""`` if the AppleScript dictionary doesn't expose
        ``conversation id`` on this Outlook version.
      - ``internet_message_id`` — RFC-822 Message-ID header.

    **Known limitation (Bug E):** Microsoft Outlook for Mac's AppleScript
    dictionary doesn't expose ``internet message id`` as a direct
    property on the ``incoming message`` / ``outgoing message`` classes.
    The RFC-822 Message-ID lives inside the ``headers`` string blob and
    has to be parsed out. Until we add header parsing, the
    ``internet_message_id`` field falls back to Outlook's internal
    numeric ``id`` (same value as ``message_id``). This means the new
    ``outlook_internet_message_id`` column is effectively redundant
    with ``outlook_message_id`` for now — true cross-mailbox dedup
    (e.g. detecting that an email re-imported after archive moves is
    the same one) won't work until the headers-parsing fix lands.
    Within-mailbox dedup still works fine via ``outlook_message_id``.

    Path resolution walks ``mail folder "X" of Y`` segment by segment
    starting at the default account. Any segment that doesn't resolve
    raises an AppleScript error which is caught and returned as an
    error dict so the caller can mark the folder bad and move on.

    Args:
        folder_path: Slash-delimited folder path. Single-segment paths
            ("Inbox", "Sent Items") work the same as nested paths.
        since_date: Only messages with ``time sent >= since_date`` are
            returned. The crawler computes this from the folder row's
            ``last_synced_at`` column or the first-run window.

    Returns:
        ``{"ok": True, "emails": [...]}`` on success or
        ``{"ok": False, "error": "..."}`` on AppleScript / parse failure.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running. Please open Legacy Outlook and try again."}

    date_str = since_date.strftime("%m/%d/%Y")
    esc_path = _escape_for_applescript(folder_path)

    # Path-walking: split on "/" and traverse segments. We can't pre-quote
    # each segment as AppleScript because the depth is dynamic, so build
    # a handler that takes the path string and resolves it at runtime.
    script = f'''
set output to ""
set myDate to date "{date_str}"
tell application "Microsoft Outlook"
    try
        set targetFolder to my findFolder("{esc_path}")
    on error errMsg
        return "{{\\"ok\\": false, \\"error\\": \\"Folder not found: " & my escJSON(errMsg) & "\\"}}"
    end try
    try
        set msgs to messages of targetFolder whose (time sent >= myDate)
    on error errMsg
        return "{{\\"ok\\": false, \\"error\\": \\"Query failed: " & my escJSON(errMsg) & "\\"}}"
    end try
    set msgCount to count of msgs
    if msgCount > 500 then set msgCount to 500
    set output to "[" & return
    repeat with i from 1 to msgCount
        set msg to item i of msgs
        set msgSubject to subject of msg
        set msgSender to ""
        try
            set msgSender to address of sender of msg
        end try
        set msgDate to time sent of msg
        set msgId to id of msg as text
        -- Conversation id: try to read native conversation id, fall back to ""
        set convId to ""
        try
            set convId to conversation id of msg as text
        end try
        -- Internet message id: try the RFC-822 header, fall back to msgId.
        -- Outlook for Mac exposes this as `internet message id` on newer
        -- builds; older versions just expose `message id` (the Outlook
        -- internal numeric id), so we use msgId as the safe anchor.
        set internetMsgId to msgId
        try
            set internetMsgId to internet message id of msg as text
        end try
        set recipTo to ""
        try
            repeat with r in to recipients of msg
                set recipTo to recipTo & address of email address of r & ","
            end repeat
        end try
        set recipCc to ""
        try
            repeat with r in cc recipients of msg
                set recipCc to recipCc & address of email address of r & ","
            end repeat
        end try
        set msgContent to ""
        try
            set msgContent to plain text content of msg
            if length of msgContent > 100000 then
                set msgContent to text 1 thru 100000 of msgContent
            end if
        on error
            try
                set msgContent to content of msg
                if length of msgContent > 100000 then
                    set msgContent to text 1 thru 100000 of msgContent
                end if
            end try
        end try
        set catList to ""
        try
            set cats to categories of msg
            repeat with c in cats
                set catList to catList & (name of c) & ","
            end repeat
        end try
        set msgSubject to my escJSON(msgSubject)
        set msgSender to my escJSON(msgSender)
        set msgContent to my escJSON(msgContent)
        set recipTo to my escJSON(recipTo)
        set recipCc to my escJSON(recipCc)
        set msgId to my escJSON(msgId)
        set convId to my escJSON(convId)
        set internetMsgId to my escJSON(internetMsgId)
        set catList to my escJSON(catList)
        set escFolder to my escJSON("{esc_path}")
        set dateStr to (year of msgDate as text) & "-" & my padNum(month of msgDate as integer) & "-" & my padNum(day of msgDate) & "T" & my padNum(hours of msgDate) & ":" & my padNum(minutes of msgDate) & ":00"
        set output to output & "  {{\\"message_id\\": \\"" & msgId & "\\", \\"conversation_id\\": \\"" & convId & "\\", \\"internet_message_id\\": \\"" & internetMsgId & "\\", \\"subject\\": \\"" & msgSubject & "\\", \\"sender\\": \\"" & msgSender & "\\", \\"to_recipients\\": \\"" & recipTo & "\\", \\"cc_recipients\\": \\"" & recipCc & "\\", \\"date\\": \\"" & dateStr & "\\", \\"body_snippet\\": \\"" & msgContent & "\\", \\"folder\\": \\"" & escFolder & "\\", \\"categories\\": \\"" & catList & "\\"}},"
        if i < msgCount then set output to output & return
    end repeat
    set output to output & return & "]"
end tell
return output

on findFolder(pathStr)
    set AppleScript's text item delimiters to "/"
    set segs to text items of pathStr
    set AppleScript's text item delimiters to ""
    tell application "Microsoft Outlook"
        set f to default account
        repeat with seg in segs
            set f to mail folder (seg as text) of f
        end repeat
    end tell
    return f
end findFolder

on escJSON(txt)
    set txt to my replaceText(txt, "\\\\", "\\\\\\\\")
    set txt to my replaceText(txt, "\\"", "\\\\\\"")
    set txt to my replaceText(txt, return, "\\\\n")
    set txt to my replaceText(txt, linefeed, "\\\\n")
    set txt to my replaceText(txt, tab, "\\\\t")
    return txt
end escJSON

on padNum(n)
    if n < 10 then return "0" & (n as text)
    return n as text
end padNum

on replaceText(txt, srch, repl)
    set AppleScript's text item delimiters to srch
    set parts to text items of txt
    set AppleScript's text item delimiters to repl
    set txt to parts as text
    set AppleScript's text item delimiters to ""
    return txt
end replaceText
'''

    result = _run_applescript(script, timeout=_resolve_timeout("search_folder_since"))
    if not result.get("ok", True):
        return result

    raw = result.get("raw", "[]")
    # The script may return an error JSON object instead of an array when
    # the folder lookup fails; handle that case before trying to parse as list.
    raw_stripped = raw.strip()
    if raw_stripped.startswith("{"):
        try:
            err_obj = json.loads(raw_stripped)
            if not err_obj.get("ok", True):
                return err_obj
        except json.JSONDecodeError:
            pass

    try:
        raw = re.sub(r',\s*\]', ']', raw)
        emails = json.loads(raw)
        for email in emails:
            _normalize_recipient_fields(email)
            if isinstance(email.get("categories"), str):
                email["categories"] = [
                    c.strip() for c in email["categories"].split(",")
                    if c.strip()
                ]
            else:
                email["categories"] = []
            # Ensure both new fields are always present (empty string if AppleScript omitted)
            email.setdefault("conversation_id", "")
            email.setdefault("internet_message_id", email.get("message_id", ""))
        return {"ok": True, "emails": emails, "folder": folder_path}
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse folder %s results: %s", folder_path, e)
        return {"ok": False, "error": f"Parse failed for {folder_path}: {e}", "raw": raw[:500]}


def get_flagged_emails(since_date: datetime) -> dict:
    """Get flagged emails from Outlook (recursive across all subfolders).

    Walks the full folder tree under the default account up to 8 levels
    deep so a flagged email filed into a nested user folder is still
    captured. The legacy implementation only iterated top-level folders,
    which silently dropped flags on emails the user had already filed.

    Returns {"ok": True, "emails": [...]} with flag_due_date field and
    slash-delimited folder path.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running."}

    date_str = since_date.strftime("%m/%d/%Y")

    # Same recursive pattern as `search_all_folders`, with a smaller cap (200)
    # because flagged scans should be tighter and the user's flag-list rarely
    # gets that large in practice.
    script = f'''
script acc
    property out : ""
    property cnt : 0
end script

set acc's out to "[" & return
set acc's cnt to 0
set myDate to date "{date_str}"
set capLimit to 200

tell application "Microsoft Outlook"
    try
        set rootFolders to mail folders of default account
    on error
        set rootFolders to {{}}
    end try
end tell

repeat with rf in rootFolders
    if acc's cnt >= capLimit then exit repeat
    try
        tell application "Microsoft Outlook" to set rfName to name of rf
        my scanFlaggedTree(contents of rf, rfName, myDate, capLimit, 0)
    end try
end repeat

return (acc's out) & return & "]"

on scanFlaggedTree(f, folderPath, myDate, capLimit, depth)
    if acc's cnt >= capLimit then return
    if depth > 8 then return
    set leafName to my leafOf(folderPath)
    if leafName is "Deleted Items" or leafName is "Junk Email" or leafName is "Drafts" or leafName is "Trash" or leafName is "Clutter" or leafName is "Outbox" then return

    -- Scan flagged messages in this folder
    tell application "Microsoft Outlook"
        try
            set folderMsgs to (messages of f whose (time sent >= myDate and todo flag of it is not not flagged))
            repeat with msg in folderMsgs
                if acc's cnt >= capLimit then exit repeat
                set acc's cnt to (acc's cnt) + 1
                set msgSubject to subject of msg
                set msgSender to ""
                try
                    set msgSender to address of sender of msg
                end try
                set msgDate to time sent of msg
                set msgId to id of msg as text
                set flagDate to ""
                try
                    set dueDate to due date of todo flag of msg
                    set flagDate to (year of dueDate as text) & "-" & my padNum(month of dueDate as integer) & "-" & my padNum(day of dueDate)
                end try
                set msgContent to ""
                try
                    set msgContent to plain text content of msg
                    if length of msgContent > 100000 then
                        set msgContent to text 1 thru 100000 of msgContent
                    end if
                on error
                    try
                        set msgContent to content of msg
                        if length of msgContent > 100000 then
                            set msgContent to text 1 thru 100000 of msgContent
                        end if
                    end try
                end try
                set msgSubject to my escJSON(msgSubject)
                set msgSender to my escJSON(msgSender)
                set msgContent to my escJSON(msgContent)
                set msgId to my escJSON(msgId)
                set escFolder to my escJSON(folderPath)
                set dateStr to (year of msgDate as text) & "-" & my padNum(month of msgDate as integer) & "-" & my padNum(day of msgDate) & "T" & my padNum(hours of msgDate) & ":" & my padNum(minutes of msgDate) & ":00"
                set acc's out to (acc's out) & "  {{\\"message_id\\": \\"" & msgId & "\\", \\"subject\\": \\"" & msgSubject & "\\", \\"sender\\": \\"" & msgSender & "\\", \\"date\\": \\"" & dateStr & "\\", \\"body_snippet\\": \\"" & msgContent & "\\", \\"folder\\": \\"" & escFolder & "\\", \\"flag_due_date\\": \\"" & flagDate & "\\"}},"
            end repeat
        end try
    end tell

    -- Recurse into subfolders
    set subList to {{}}
    try
        tell application "Microsoft Outlook" to set subList to mail folders of f
    end try
    repeat with sub in subList
        if acc's cnt >= capLimit then return
        try
            tell application "Microsoft Outlook" to set subName to name of sub
            my scanFlaggedTree(contents of sub, folderPath & "/" & subName, myDate, capLimit, depth + 1)
        end try
    end repeat
end scanFlaggedTree

on leafOf(p)
    set AppleScript's text item delimiters to "/"
    set parts to text items of p
    set AppleScript's text item delimiters to ""
    if (count of parts) is 0 then return p
    return last item of parts
end leafOf

on escJSON(txt)
    set txt to my replaceText(txt, "\\\\", "\\\\\\\\")
    set txt to my replaceText(txt, "\\"", "\\\\\\"")
    set txt to my replaceText(txt, return, "\\\\n")
    set txt to my replaceText(txt, linefeed, "\\\\n")
    set txt to my replaceText(txt, tab, "\\\\t")
    return txt
end escJSON

on padNum(n)
    if n < 10 then return "0" & (n as text)
    return n as text
end padNum

on replaceText(txt, srch, repl)
    set AppleScript's text item delimiters to srch
    set parts to text items of txt
    set AppleScript's text item delimiters to repl
    set txt to parts as text
    set AppleScript's text item delimiters to ""
    return txt
end replaceText
'''

    result = _run_applescript(script, timeout=_resolve_timeout("get_flagged_emails"))
    if not result.get("ok", True):
        return result

    raw = result.get("raw", "[]")
    try:
        raw = re.sub(r',\s*\]', ']', raw)
        emails = json.loads(raw)
        # Convert empty flag_due_date to None
        for email in emails:
            if not email.get("flag_due_date"):
                email["flag_due_date"] = None
        # Cap is 200 for flagged scans. Surface truncation so callers
        # can warn the user before silently dropping flagged items.
        resp = {"ok": True, "emails": emails}
        if len(emails) >= 200:
            resp["truncated"] = True
            resp["cap"] = 200
        return resp
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse flagged email results: %s", e)
        return {"ok": True, "emails": [], "parse_warning": str(e)}


def trigger_search(query: str, auto_paste: bool = True) -> dict:
    """Put ``query`` on the clipboard, activate Outlook, optionally UI-script
    the paste + return sequence in the search field.

    Returns a dict with keys:
        status:  "searched" | "clipboard_only" | "unavailable"
        query:   the input query (for UI use)
        message: human-readable, used for toast copy
    """
    escaped = _escape_for_applescript(query)

    if auto_paste:
        script = (
            'set the clipboard to "' + escaped + '"\n'
            'try\n'
            '    tell application "Microsoft Outlook" to activate\n'
            'on error\n'
            '    return "unavailable"\n'
            'end try\n'
            'try\n'
            '    tell application "System Events"\n'
            '        tell process "Microsoft Outlook"\n'
            '            keystroke "f" using {command down, option down}\n'
            '            delay 0.15\n'
            '            keystroke "v" using {command down}\n'
            '            delay 0.05\n'
            '            keystroke return\n'
            '        end tell\n'
            '    end tell\n'
            '    return "searched"\n'
            'on error\n'
            '    return "clipboard_only"\n'
            'end try\n'
        )
    else:
        # Clipboard-only: skip System Events entirely.
        script = (
            'set the clipboard to "' + escaped + '"\n'
            'try\n'
            '    tell application "Microsoft Outlook" to activate\n'
            '    return "clipboard_only"\n'
            'on error\n'
            '    return "unavailable"\n'
            'end try\n'
        )

    result = _run_applescript(script, timeout=_resolve_timeout("availability"))
    status = (result.get("raw") or "").strip() or "unavailable"
    if not result.get("ok", False):
        status = "unavailable"
    if status not in ("searched", "clipboard_only", "unavailable"):
        status = "unavailable"

    messages = {
        "searched": "Searched Outlook.",
        "clipboard_only": "Copied — ⌘V into Outlook search, then Return.",
        "unavailable": "Outlook isn't running. Query copied — paste into search.",
    }
    return {"status": status, "query": query, "message": messages[status]}
