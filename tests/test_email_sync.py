"""Tests for the Outlook email sync engine.

Covers pure helpers (no Outlook needed) plus DB-backed resolution and
the create / enrich / inbox-routing paths in `email_sync.py`.

The tests reuse the same `tmp_db` pattern as `test_db.py`: a fresh sqlite
database under tmp_path, with `policydb.db.DB_PATH` monkeypatched.
"""

from __future__ import annotations

import sqlite3

import pytest

from policydb.db import get_connection, init_db
from policydb import config as cfg
from policydb.email_sync import (
    _build_domain_index,
    _capture_unknown_contacts,
    _create_or_enrich_activity,
    _extract_ref_tags,
    _is_automated_sender,
    _match_by_domain,
    _normalize_subject,
    _parse_ref_tag,
    _process_email,
    _resolve_ref_tag,
    _run_thread_inheritance,
    sync_outlook,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh sqlite DB on disk with all migrations applied."""
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


@pytest.fixture
def conn(tmp_db):
    c = get_connection(tmp_db)
    yield c
    c.close()


def _insert_client(conn, name, cn, website, archived=0):
    """Helper: insert a client filling NOT NULL columns."""
    cur = conn.execute(
        """INSERT INTO clients (name, cn_number, website, archived, industry_segment)
           VALUES (?, ?, ?, ?, '')""",
        (name, cn, website, archived),
    )
    return cur.lastrowid


@pytest.fixture
def seed_client(conn):
    """Insert a basic client with website + contact for matching tests."""
    client_id = _insert_client(conn, "Acme Corp", "999000111", "https://www.acme.com")
    conn.execute(
        """INSERT INTO contacts (name, email)
           VALUES ('Jane Doe', 'jane@acme.com')"""
    )
    contact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO contact_client_assignments (contact_id, client_id, role)
           VALUES (?, ?, 'Risk Manager')""",
        (contact_id, client_id),
    )
    conn.commit()
    return client_id


@pytest.fixture
def seed_archived_client(conn):
    """Archived client with the same domain as a real concern — should be ignored."""
    return _insert_client(
        conn, "Old Acme", "111222333", "https://www.archived-acme.com", archived=1,
    )


@pytest.fixture
def seed_policy(conn, seed_client):
    cur = conn.execute(
        """INSERT INTO policies
              (policy_uid, client_id, policy_type, carrier, archived)
           VALUES ('POL-042', ?, 'GL', 'Travelers', 0)""",
        (seed_client,),
    )
    conn.commit()
    return cur.lastrowid


# ── _parse_ref_tag ──────────────────────────────────────────────────────────


def test_parse_ref_tag_cn_only():
    assert _parse_ref_tag("CN999000111") == {"cn_number": "999000111"}


def test_parse_ref_tag_cn_with_policy():
    parsed = _parse_ref_tag("CN999000111-POL042")
    assert parsed["cn_number"] == "999000111"
    assert parsed["policy_uid"] == "POL-042"


def test_parse_ref_tag_full_compound():
    parsed = _parse_ref_tag("CN999000111-L5-POL042")
    assert parsed["cn_number"] == "999000111"
    assert parsed["project_id"] == 5
    assert parsed["policy_uid"] == "POL-042"


def test_parse_ref_tag_with_issue_uid():
    parsed = _parse_ref_tag("CN999000111-A7F2C3B1")
    assert parsed["cn_number"] == "999000111"
    assert parsed["issue_uid"] == "A7F2C3B1"


def test_parse_ref_tag_program_uid():
    parsed = _parse_ref_tag("CN999000111-PGM7")
    assert parsed["cn_number"] == "999000111"
    assert parsed["program_uid"] == "PGM-7"


def test_parse_ref_tag_garbage_returns_empty_dict():
    assert _parse_ref_tag("not-a-real-tag") == {}


# ── _normalize_subject ──────────────────────────────────────────────────────


def test_normalize_subject_strips_re_fwd():
    assert _normalize_subject("Re: Fwd: GL Renewal") == "gl renewal"


def test_normalize_subject_strips_external_warning():
    assert _normalize_subject("[EXTERNAL] Re: Quote") == "quote"


def test_normalize_subject_strips_legacy_received_prefix():
    # Backward-compat for rows imported before migration 144 added email_direction
    assert _normalize_subject("Received: GL Renewal") == "gl renewal"


def test_normalize_subject_collapses_whitespace_and_lowercases():
    assert _normalize_subject("   GL    Renewal\tDiscussion  ") == "gl renewal discussion"


def test_normalize_subject_handles_nested_re():
    assert _normalize_subject("RE: Re: re: Re: Quote") == "quote"


def test_normalize_subject_empty_string():
    assert _normalize_subject("") == ""
    assert _normalize_subject(None) == ""


# ── _extract_ref_tags ───────────────────────────────────────────────────────


def test_extract_ref_tags_single():
    assert _extract_ref_tags("Hello [PDB:CN12345-POL042] world") == ["CN12345-POL042"]


def test_extract_ref_tags_multiple():
    text = "[PDB:CN1] body [PDB:CN2-POL5] footer"
    assert _extract_ref_tags(text) == ["CN1", "CN2-POL5"]


def test_extract_ref_tags_none_in_empty():
    assert _extract_ref_tags("") == []
    assert _extract_ref_tags(None) == []


# ── _is_automated_sender ────────────────────────────────────────────────────


@pytest.mark.parametrize("addr,expected", [
    ("noreply@carrier.com", True),
    ("no-reply@example.com", True),
    ("donotreply@portal.com", True),
    ("mailer-daemon@host.com", True),
    ("bounces+abc123@list.com", True),
    ("notifications@app.io", True),
    ("postmaster@x.com", True),
    ("alerts@status.io", True),
    ("jane.doe@acme.com", False),
    ("john@acme.com", False),
    ("", False),
    ("not-an-email", False),
])
def test_is_automated_sender(addr, expected):
    assert _is_automated_sender(addr) is expected


# ── _build_domain_index + _match_by_domain ──────────────────────────────────


def test_domain_index_includes_active_clients(conn, seed_client):
    idx = _build_domain_index(conn)
    assert "acme.com" in idx
    assert seed_client in idx["acme.com"]


def test_domain_index_excludes_archived_clients(conn, seed_client, seed_archived_client):
    idx = _build_domain_index(conn)
    # Active client present
    assert seed_client in idx.get("acme.com", set())
    # Archived client's domain is not indexed
    assert seed_archived_client not in idx.get("archived-acme.com", set())


def test_match_by_domain_unique_match(conn, seed_client):
    match = _match_by_domain(conn, ["jane@acme.com"])
    assert match is not None
    assert match["tier"] == 2
    assert match["client_id"] == seed_client


def test_match_by_domain_skips_freemail(conn, seed_client, monkeypatch):
    # Pre-populate freemail
    monkeypatch.setattr(cfg, "get", lambda k, default=None: {
        "freemail_domains": ["gmail.com"],
        "internal_email_domains": ["marsh.com"],
    }.get(k, default if default is not None else []))
    assert _match_by_domain(conn, ["someone@gmail.com"]) is None


def test_match_by_domain_ambiguous_returns_none(conn, seed_client):
    # Create a second client also using acme.com → ambiguous
    _insert_client(conn, "Acme East", "888777666", "https://www.acme.com")
    conn.commit()
    match = _match_by_domain(conn, ["jane@acme.com"])
    assert match is None


# ── _resolve_ref_tag ────────────────────────────────────────────────────────


def test_resolve_ref_tag_by_cn(conn, seed_client):
    match = _resolve_ref_tag(conn, "CN999000111")
    assert match is not None
    assert match["client_id"] == seed_client
    assert match["tier"] == 1


def test_resolve_ref_tag_by_policy(conn, seed_policy, seed_client):
    match = _resolve_ref_tag(conn, "CN999000111-POL042")
    assert match is not None
    assert match["client_id"] == seed_client
    assert match["policy_id"] == seed_policy


def test_resolve_ref_tag_garbage_returns_none(conn):
    assert _resolve_ref_tag(conn, "TOTAL-GARBAGE") is None


def test_resolve_ref_tag_direct_policy_lookup(conn, seed_policy):
    match = _resolve_ref_tag(conn, "POL042")
    assert match is not None
    assert match["policy_id"] == seed_policy


# ── _create_or_enrich_activity ──────────────────────────────────────────────


def _email_fixture(**overrides):
    base = {
        "message_id": "MSG-1",
        "subject": "Quote",
        "sender": "jane@acme.com",
        "recipients": ["me@marsh.com"],
        "date": "2026-04-13T10:30:00",
        "body_snippet": "Body text",
        "folder": "Inbox",
    }
    base.update(overrides)
    return base


def test_create_activity_received_uses_email_direction_column(conn, seed_client):
    match = {"tier": 1, "client_id": seed_client}
    result = _create_or_enrich_activity(conn, _email_fixture(), match)
    assert result["action"] == "created"
    row = conn.execute(
        "SELECT subject, email_direction FROM activity_log WHERE id = ?",
        (result["activity_id"],),
    ).fetchone()
    # No more "Received: " subject prefix munging (#10)
    assert row["subject"] == "Quote"
    assert row["email_direction"] == "received"


def test_create_activity_sent_marked_as_sent(conn, seed_client):
    match = {"tier": 1, "client_id": seed_client}
    email = _email_fixture(folder="Sent Items", message_id="MSG-2")
    result = _create_or_enrich_activity(conn, email, match)
    row = conn.execute(
        "SELECT email_direction, disposition FROM activity_log WHERE id = ?",
        (result["activity_id"],),
    ).fetchone()
    assert row["email_direction"] == "sent"
    assert row["disposition"] == "Sent Email"


def test_create_activity_flagged_marked_and_open(conn, seed_client):
    match = {"tier": 1, "client_id": seed_client}
    email = _email_fixture(message_id="MSG-3", flag_due_date="2026-04-20")
    result = _create_or_enrich_activity(conn, email, match)
    row = conn.execute(
        "SELECT email_direction, follow_up_done, follow_up_date FROM activity_log WHERE id = ?",
        (result["activity_id"],),
    ).fetchone()
    assert row["email_direction"] == "flagged"
    assert row["follow_up_done"] == 0
    assert row["follow_up_date"] == "2026-04-20"


def test_create_activity_dedups_on_message_id(conn, seed_client):
    match = {"tier": 1, "client_id": seed_client}
    _create_or_enrich_activity(conn, _email_fixture(message_id="MSG-DUP"), match)
    second = _create_or_enrich_activity(conn, _email_fixture(message_id="MSG-DUP"), match)
    assert second["action"] == "skipped"


def test_create_activity_skips_dismissed_message(conn, seed_client):
    conn.execute(
        "INSERT INTO dismissed_outlook_messages (message_id) VALUES ('MSG-DISMISSED')"
    )
    conn.commit()
    match = {"tier": 1, "client_id": seed_client}
    result = _create_or_enrich_activity(
        conn, _email_fixture(message_id="MSG-DISMISSED"), match,
    )
    assert result["action"] == "skipped"
    assert result["reason"] == "dismissed"


def test_create_activity_no_client_returns_skipped(conn):
    match = {"tier": 1}  # no client_id
    result = _create_or_enrich_activity(conn, _email_fixture(message_id="MSG-X"), match)
    assert result["action"] == "skipped"
    assert result["reason"] == "no_client"


def test_enrichment_path_updates_existing_same_day_email(conn, seed_client, seed_policy):
    # Pre-existing manually logged email on same day + policy
    conn.execute(
        """INSERT INTO activity_log
              (activity_date, client_id, policy_id, activity_type, subject, source)
           VALUES ('2026-04-13', ?, ?, 'Email', 'Quote', 'manual')""",
        (seed_client, seed_policy),
    )
    existing_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    match = {"tier": 1, "client_id": seed_client, "policy_id": seed_policy}
    email = _email_fixture(folder="Sent Items", message_id="MSG-ENRICH")
    result = _create_or_enrich_activity(conn, email, match)
    assert result["action"] == "enriched"
    assert result["activity_id"] == existing_id

    row = conn.execute(
        "SELECT outlook_message_id, source, email_direction FROM activity_log WHERE id = ?",
        (existing_id,),
    ).fetchone()
    assert row["outlook_message_id"] == "MSG-ENRICH"
    assert row["source"] == "outlook_sync"
    assert row["email_direction"] == "sent"


# ── _capture_unknown_contacts ───────────────────────────────────────────────


def test_capture_unknown_contacts_skips_noreply(conn):
    email = {
        "sender": "noreply@carrier.com",
        "recipients": ["me@marsh.com"],
        "subject": "Quote",
    }
    _capture_unknown_contacts(conn, email, client_id=None)
    count = conn.execute(
        "SELECT COUNT(*) FROM suggested_contacts WHERE email = 'noreply@carrier.com'"
    ).fetchone()[0]
    assert count == 0


def test_capture_unknown_contacts_skips_archived_client(conn, seed_archived_client):
    email = {
        "sender": "real.person@vendor.com",
        "recipients": [],
        "subject": "Hi",
    }
    _capture_unknown_contacts(conn, email, client_id=seed_archived_client)
    count = conn.execute(
        "SELECT COUNT(*) FROM suggested_contacts WHERE email = 'real.person@vendor.com'"
    ).fetchone()[0]
    assert count == 0


def test_capture_unknown_contacts_inserts_new(conn, seed_client):
    email = {
        "sender": "new.person@vendor.com",
        "recipients": [],
        "subject": "Hi there",
    }
    _capture_unknown_contacts(conn, email, client_id=seed_client)
    row = conn.execute(
        "SELECT email, parsed_name FROM suggested_contacts WHERE email = ?",
        ("new.person@vendor.com",),
    ).fetchone()
    assert row is not None
    assert row["parsed_name"] == "New Person"


# ── _process_email routing ──────────────────────────────────────────────────


def test_process_email_unmatched_routed_to_inbox_with_direction(conn):
    """An email with no ref tag and no domain match goes to the inbox."""
    results = {
        "auto_linked": {"sent": 0, "received": 0, "flagged": 0},
        "suggestions": [],
        "skipped": 0,
        "errors": [],
    }
    email = _email_fixture(
        message_id="MSG-INBOX-1",
        sender="stranger@unknownco.example",
        recipients=["me@marsh.com"],
        subject="Cold pitch",
    )
    _process_email(conn, email, results, "received")
    row = conn.execute(
        "SELECT email_direction, content FROM inbox WHERE outlook_message_id = ?",
        ("MSG-INBOX-1",),
    ).fetchone()
    assert row is not None
    assert row["email_direction"] == "received"


def test_process_email_with_ref_tag_creates_activity(conn, seed_client, seed_policy):
    results = {
        "auto_linked": {"sent": 0, "received": 0, "flagged": 0},
        "suggestions": [],
        "skipped": 0,
        "errors": [],
    }
    email = _email_fixture(
        message_id="MSG-TAG-1",
        subject="Update [PDB:CN999000111-POL042]",
    )
    _process_email(conn, email, results, "received")
    row = conn.execute(
        "SELECT client_id, policy_id, email_direction FROM activity_log WHERE outlook_message_id = ?",
        ("MSG-TAG-1",),
    ).fetchone()
    assert row is not None
    assert row["client_id"] == seed_client
    assert row["policy_id"] == seed_policy
    assert row["email_direction"] == "received"


# ── sync_outlook empty-category guard ───────────────────────────────────────


def test_sync_outlook_refuses_empty_capture_category(conn, monkeypatch):
    """Migration 144 + #5 — empty category must not silently scan everything."""
    # Stub the AppleScript bridges so we don't need Outlook
    monkeypatch.setattr("policydb.email_sync.search_emails",
                        lambda *a, **k: {"ok": True, "emails": []})
    monkeypatch.setattr("policydb.email_sync.get_flagged_emails",
                        lambda *a, **k: {"ok": True, "emails": []})

    called = {"count": 0}

    def _fail_if_called(*args, **kwargs):
        called["count"] += 1
        return {"ok": True, "emails": []}

    monkeypatch.setattr("policydb.email_sync.search_all_folders", _fail_if_called)

    # Force capture category to empty via cfg.get monkeypatch
    real_get = cfg.get
    def _get(k, default=None):
        if k == "outlook_capture_category":
            return ""
        return real_get(k, default)
    monkeypatch.setattr(cfg, "get", _get)
    monkeypatch.setattr(cfg, "save_config", lambda *a, **k: None)
    monkeypatch.setattr(cfg, "reload_config", lambda *a, **k: None)
    monkeypatch.setattr(cfg, "load_config", lambda: {})

    results = sync_outlook(conn)
    assert called["count"] == 0
    assert any("capture category is empty" in e.lower() for e in results["errors"])
