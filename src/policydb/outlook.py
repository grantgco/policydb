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

_TIMEOUT = 30  # seconds per osascript call


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


def _run_applescript(script: str) -> dict:
    """Execute an AppleScript and return parsed JSON output."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=_TIMEOUT,
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
        return {"ok": False, "error": "Outlook took too long to respond (30s timeout)"}
    except FileNotFoundError:
        return {"ok": False, "error": "osascript not found — are you on macOS?"}
    except Exception as e:
        logger.exception("Unexpected error calling AppleScript")
        return {"ok": False, "error": str(e)}


def _escape_for_applescript(text: str) -> str:
    """Escape a string for use inside AppleScript double quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


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

    return _run_applescript(script)


def search_all_folders(
    since_date: datetime,
    category_filter: str = "",
) -> dict:
    """Search ALL mail folders for emails with a specific category since a date.

    Returns {"ok": True, "emails": [...]} with folder name on each email.
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

    script = f'''
set output to "[" & return
set totalCount to 0
set myDate to date "{date_str}"
tell application "Microsoft Outlook"
    set allFolders to every mail folder of default account
    repeat with f in allFolders
        set folderName to name of f
        -- Skip system folders we don't care about
        if folderName is not "Deleted Items" and folderName is not "Junk Email" and folderName is not "Drafts" and folderName is not "Trash" and folderName is not "Clutter" and folderName is not "Sent Items" then
            try
                set folderMsgs to messages of f whose (time sent >= myDate)
                repeat with msg in folderMsgs
                    if totalCount >= 500 then exit repeat
                    set skipMsg to false
                    {cat_check}
                    if not skipMsg then
                        set totalCount to totalCount + 1
                        set msgSubject to subject of msg
                        set msgSender to ""
                        try
                            set msgSender to address of sender of msg
                        end try
                        set msgDate to time sent of msg
                        set msgId to id of msg as text
                        set recipList to ""
                        try
                            repeat with r in to recipients of msg
                                set recipList to recipList & address of email address of r & ","
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
                        set recipList to my escJSON(recipList)
                        set msgId to my escJSON(msgId)
                        set catList to my escJSON(catList)
                        set escFolder to my escJSON(folderName)
                        set dateStr to (year of msgDate as text) & "-" & my padNum(month of msgDate as integer) & "-" & my padNum(day of msgDate) & "T" & my padNum(hours of msgDate) & ":" & my padNum(minutes of msgDate) & ":00"
                        set output to output & "  {{\\"message_id\\": \\"" & msgId & "\\", \\"subject\\": \\"" & msgSubject & "\\", \\"sender\\": \\"" & msgSender & "\\", \\"recipients\\": \\"" & recipList & "\\", \\"date\\": \\"" & dateStr & "\\", \\"body_snippet\\": \\"" & msgContent & "\\", \\"folder\\": \\"" & escFolder & "\\", \\"categories\\": \\"" & catList & "\\"}},"
                    end if
                end repeat
            end try
        end if
        if totalCount >= 500 then exit repeat
    end repeat
end tell
set output to output & return & "]"
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

    result = _run_applescript(script)
    if not result.get("ok", True):
        return result

    raw = result.get("raw", "[]")
    try:
        raw = re.sub(r',\s*\]', ']', raw)
        emails = json.loads(raw)
        for email in emails:
            if isinstance(email.get("recipients"), str):
                email["recipients"] = [r.strip() for r in email["recipients"].split(",") if r.strip()]
            if isinstance(email.get("categories"), str):
                email["categories"] = [c.strip() for c in email["categories"].split(",") if c.strip()]
            else:
                email["categories"] = []
        return {"ok": True, "emails": emails}
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
        -- Get recipients
        set recipList to ""
        try
            repeat with r in to recipients of msg
                set recipList to recipList & address of email address of r & ","
            end repeat
            repeat with r in cc recipients of msg
                set recipList to recipList & address of email address of r & ","
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
        set recipList to my escJSON(recipList)
        set msgId to my escJSON(msgId)
        set catList to my escJSON(catList)
        set dateStr to (year of msgDate as text) & "-" & my padNum(month of msgDate as integer) & "-" & my padNum(day of msgDate) & "T" & my padNum(hours of msgDate) & ":" & my padNum(minutes of msgDate) & ":00"
        set output to output & "  {{\\"message_id\\": \\"" & msgId & "\\", \\"subject\\": \\"" & msgSubject & "\\", \\"sender\\": \\"" & msgSender & "\\", \\"recipients\\": \\"" & recipList & "\\", \\"date\\": \\"" & dateStr & "\\", \\"body_snippet\\": \\"" & msgContent & "\\", \\"folder\\": \\"{esc_folder}\\", \\"categories\\": \\"" & catList & "\\"}},"
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

    result = _run_applescript(script)
    if not result.get("ok", True):
        return result

    # Parse the JSON array from raw output
    raw = result.get("raw", "[]")
    try:
        # Clean up trailing commas before closing bracket
        raw = re.sub(r',\s*\]', ']', raw)
        emails = json.loads(raw)
        # Split comma-separated strings into lists
        for email in emails:
            if isinstance(email.get("recipients"), str):
                email["recipients"] = [
                    r.strip() for r in email["recipients"].split(",")
                    if r.strip()
                ]
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
    script = r'''
set output to "[" & return
tell application "Microsoft Outlook"
    try
        set level1 to mail folders of default account
    on error
        return "[]"
    end try
    repeat with l1 in level1
        set l1Name to name of l1
        set output to output & my emitEntry(l1Name, l1Name) & "," & return
        try
            set level2 to mail folders of l1
            repeat with l2 in level2
                set l2Name to name of l2
                set l2Path to l1Name & "/" & l2Name
                set output to output & my emitEntry(l2Path, l2Name) & "," & return
                try
                    set level3 to mail folders of l2
                    repeat with l3 in level3
                        set l3Name to name of l3
                        set l3Path to l2Path & "/" & l3Name
                        set output to output & my emitEntry(l3Path, l3Name) & "," & return
                    end repeat
                end try
            end repeat
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

    result = _run_applescript(script)
    if not result.get("ok", True):
        return result

    raw = result.get("raw", "[]")
    try:
        # Strip trailing comma before closing bracket (AppleScript always emits one)
        raw = re.sub(r',\s*\]', ']', raw)
        folders = json.loads(raw)
        return {"ok": True, "folders": folders}
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse folder discovery results: %s", e)
        return {"ok": False, "error": f"Could not parse folder list: {e}", "raw": raw[:500]}


def get_flagged_emails(since_date: datetime) -> dict:
    """Get flagged emails from Outlook.

    Returns {"ok": True, "emails": [...]} with flag_due_date field.
    """
    if not is_outlook_available():
        return {"ok": False, "error": "Outlook is not running."}

    date_str = since_date.strftime("%m/%d/%Y")

    script = f'''
set output to ""
set myDate to date "{date_str}"
tell application "Microsoft Outlook"
    -- Search all folders in default account for flagged items
    set output to "[" & return
    set totalCount to 0
    set allFolders to every mail folder of default account
    repeat with f in allFolders
        try
            set folderName to name of f
            set folderMsgs to messages of f whose (time sent >= myDate and todo flag of it is not not flagged)
            repeat with msg in folderMsgs
                if totalCount >= 200 then exit repeat
                set totalCount to totalCount + 1
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
                set escFolder to my escJSON(folderName)
                set dateStr to (year of msgDate as text) & "-" & my padNum(month of msgDate as integer) & "-" & my padNum(day of msgDate) & "T" & my padNum(hours of msgDate) & ":" & my padNum(minutes of msgDate) & ":00"
                set output to output & "  {{\\"message_id\\": \\"" & msgId & "\\", \\"subject\\": \\"" & msgSubject & "\\", \\"sender\\": \\"" & msgSender & "\\", \\"date\\": \\"" & dateStr & "\\", \\"body_snippet\\": \\"" & msgContent & "\\", \\"folder\\": \\"" & escFolder & "\\", \\"flag_due_date\\": \\"" & flagDate & "\\"}},"
            end repeat
        end try
        if totalCount >= 200 then exit repeat
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

    result = _run_applescript(script)
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
        return {"ok": True, "emails": emails}
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse flagged email results: %s", e)
        return {"ok": True, "emails": [], "parse_warning": str(e)}
