# Outlook integration — one-time setup

PolicyDB talks to Legacy Outlook for Mac via `osascript` subprocess calls. Most features (compose, sync, suggested contacts) work without any extra setup. A single feature — **"Search Outlook" auto-paste** — requires macOS Accessibility permission.

## Search Outlook — Accessibility permission

The "Search Outlook" button (on issue / policy / project / program / client pages) attempts to focus Outlook's search field and paste the generated query via macOS System Events. This requires **Accessibility** permission for whatever process is running `osascript` — usually your terminal app (Terminal.app, iTerm.app) or the editor that launched `policydb serve` (VS Code, etc.).

### To grant it (one time)

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Click the **+** button and add your terminal app (e.g., Terminal.app or iTerm.app).
3. Toggle the switch on.
4. Restart the `policydb serve` process so the child `osascript` calls inherit the new permission.

### If you haven't granted it

The button still works — it falls back to clipboard-only mode. You'll see a toast that says "Copied — ⌘V into Outlook search, then Return." Paste manually into Outlook's search bar; the result is the same.

### Forcing clipboard-only mode

If UI scripting gets flaky after an Outlook update, flip `outlook_search_auto_paste` to **off** in **Settings → Email & Contacts**. Every click then copies-only, skipping System Events entirely.

## What "Search Outlook" actually does

When you click the button, PolicyDB:

1. Builds a wide Outlook search string that covers the record plus all its relatives (e.g., clicking the button on an issue searches for the issue UID **and** the policy UID **and** the client CN number — all OR'd together). This solves the "I tagged it with the wrong UID type and can't find the thread" problem structurally.
2. Copies the query to your clipboard.
3. Activates Outlook and (if permission granted) focuses the search field and pastes it. Otherwise shows a toast reminding you to paste manually.
