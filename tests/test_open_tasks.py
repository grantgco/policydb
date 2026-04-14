"""Tests for Open Tasks panel: sync helpers, creation helper, get_open_tasks."""
from datetime import date, timedelta

import pytest

from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def _seed_client(conn, name="Sync Test Co"):
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES (?, 'Test')", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_policy(conn, client_id, uid="POL-001"):
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date) "
        "VALUES (?, ?, 'GL', 'Test Carrier', '2026-01-01', '2027-01-01')",
        (uid, client_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_followup(conn, client_id, policy_id, subject, fu_date, done=0):
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', ?, ?, ?, 'followup', 'Grant')",
        (date.today().isoformat(), client_id, policy_id, subject, fu_date, done),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── sync_policy_follow_up_date ────────────────────────────────────────────────

def test_sync_policy_fu_date_sets_earliest_open(tmp_db):
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    _insert_followup(conn, cid, pid, "later", "2026-05-10")
    _insert_followup(conn, cid, pid, "earlier", "2026-05-01")
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] == "2026-05-01"


def test_sync_policy_fu_date_clears_when_no_open(tmp_db):
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    _insert_followup(conn, cid, pid, "done", "2026-05-01", done=1)
    conn.execute("UPDATE policies SET follow_up_date='2026-05-01' WHERE id=?", (pid,))
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] is None


def test_sync_client_fu_date_sets_earliest_open(tmp_db):
    from policydb.queries import sync_client_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    # Client-level follow-ups have policy_id = NULL
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'direct1', '2026-06-10', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'direct2', '2026-06-01', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.commit()

    sync_client_follow_up_date(conn, cid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM clients WHERE id=?", (cid,)).fetchone()
    assert row["follow_up_date"] == "2026-06-01"


def test_sync_policy_fu_date_ignores_done_rows(tmp_db):
    """Core invariant: a done row must not be picked as the earliest date
    when an open row exists with a later date."""
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    _insert_followup(conn, cid, pid, "closed earlier", "2026-05-01", done=1)
    _insert_followup(conn, cid, pid, "still open", "2026-05-10")
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] == "2026-05-10"


def test_sync_policy_fu_date_picks_up_null_item_kind(tmp_db):
    """Pre-migration rows with item_kind IS NULL must still be synced.
    Migration 104 added item_kind with DEFAULT 'followup' but did not
    backfill existing rows — the helper must accept both forms."""
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'legacy row', '2026-04-15', 0, NULL, 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] == "2026-04-15"


def test_sync_client_fu_date_clears_when_no_open(tmp_db):
    """Parity with the policy helper: closing the only open client-level
    follow-up should clear clients.follow_up_date."""
    from policydb.queries import sync_client_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'already done', '2026-06-01', 1, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.execute("UPDATE clients SET follow_up_date='2026-06-01' WHERE id=?", (cid,))
    conn.commit()

    sync_client_follow_up_date(conn, cid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM clients WHERE id=?", (cid,)).fetchone()
    assert row["follow_up_date"] is None


# ── create_followup_activity ─────────────────────────────────────────────────

def test_create_followup_activity_inserts_and_supersedes(tmp_db):
    from policydb.queries import create_followup_activity
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    # Pre-existing older follow-up that should be superseded
    old_id = _insert_followup(conn, cid, pid, "old", "2026-03-01")
    conn.commit()

    new_id = create_followup_activity(
        conn,
        client_id=cid,
        policy_id=pid,
        issue_id=None,
        subject="New task",
        activity_type="Task",
        follow_up_date="2026-04-20",
        follow_up_done=False,
        disposition="",
    )
    conn.commit()

    assert new_id is not None and new_id != old_id
    row = conn.execute("SELECT subject, follow_up_done FROM activity_log WHERE id=?", (new_id,)).fetchone()
    assert row["subject"] == "New task"
    assert row["follow_up_done"] == 0

    # Supersession fired: old row should be closed
    old_row = conn.execute("SELECT follow_up_done, auto_close_reason FROM activity_log WHERE id=?", (old_id,)).fetchone()
    assert old_row["follow_up_done"] == 1
    assert old_row["auto_close_reason"] == "superseded"

    # policies.follow_up_date should be synced to the new date
    pol = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert pol["follow_up_date"] == "2026-04-20"


def test_create_followup_activity_note_mode_no_supersede(tmp_db):
    from policydb.queries import create_followup_activity
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    old_id = _insert_followup(conn, cid, pid, "still open", "2026-03-01")
    conn.commit()

    # A note: done=True, date=None → should NOT trigger supersede
    create_followup_activity(
        conn,
        client_id=cid,
        policy_id=pid,
        issue_id=None,
        subject="FYI note",
        activity_type="Note",
        follow_up_date=None,
        follow_up_done=True,
        disposition="",
    )
    conn.commit()

    old_row = conn.execute("SELECT follow_up_done FROM activity_log WHERE id=?", (old_id,)).fetchone()
    assert old_row["follow_up_done"] == 0  # untouched


# ── filter_thread_for_history ────────────────────────────────────────────────

def test_filter_thread_drops_open_followups():
    from policydb.queries import filter_thread_for_history
    rows = [
        {"id": 1, "item_kind": "followup", "follow_up_done": 0, "follow_up_date": "2026-05-01", "subject": "open"},
        {"id": 2, "item_kind": "followup", "follow_up_done": 1, "follow_up_date": "2026-04-01", "subject": "closed"},
        {"id": 3, "item_kind": "followup", "follow_up_done": 0, "follow_up_date": None, "subject": "note-ish"},
        {"id": 4, "item_kind": "issue", "follow_up_done": 0, "follow_up_date": "2026-05-01", "subject": "issue header"},
    ]
    kept = filter_thread_for_history(rows)
    ids = [r["id"] for r in kept]
    assert 1 not in ids  # open followup with date — panel owns it
    assert 2 in ids      # closed followup — history
    assert 3 in ids      # followup with no date — history (note-like)
    assert 4 in ids      # issue header — history


# ── get_open_tasks (issue scope) ─────────────────────────────────────────────

def test_get_open_tasks_issue_scope_splits_on_issue_and_loose(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid, "POL-100")

    # Create an issue header
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Renewal POL-100', 'issue', 'ISS-1', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Open task attached to the issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'on-issue task', '2026-04-15', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid, issue_id),
    )

    # Loose task on same policy, no issue_id
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose task', '2026-04-20', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    conn.commit()

    result = get_open_tasks(conn, "issue", issue_id)
    groups = {g["key"]: g for g in result["groups"]}
    assert "on_issue" in groups
    assert "loose" in groups
    assert len(groups["on_issue"]["rows"]) == 1
    assert groups["on_issue"]["rows"][0]["subject"] == "on-issue task"
    assert len(groups["loose"]["rows"]) == 1
    assert groups["loose"]["rows"][0]["subject"] == "loose task"
    assert result["total"] == 2


# ── get_open_tasks (client scope) ────────────────────────────────────────────

def test_get_open_tasks_client_scope_groups_by_issue(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid1 = _seed_policy(conn, cid, "POL-200")
    pid2 = _seed_policy(conn, cid, "POL-201")

    # Issue touching POL-200 only
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'POL-200 renewal', 'issue', 'ISS-200', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid1),
    )
    issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Task on the issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'iss-200 task', '2026-04-18', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid1, issue_id),
    )
    # Task loose on POL-201 (not covered by any open issue)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose on POL-201', '2026-04-22', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid, pid2),
    )
    # Direct client follow-up (policy_id NULL)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'client-direct', '2026-04-25', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.commit()

    result = get_open_tasks(conn, "client", cid)
    keys = [g["key"] for g in result["groups"]]
    assert "direct_client" in keys
    assert f"issue:{issue_id}" in keys
    assert "loose_policies" in keys
    assert result["total"] == 3


def test_get_open_tasks_program_scope(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)

    conn.execute(
        "INSERT INTO programs (program_uid, client_id, name, effective_date, expiration_date) "
        "VALUES ('PGM-1', ?, 'Test Program', '2026-01-01', '2027-01-01')",
        (cid,),
    )
    pgm_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, program_id, policy_type, carrier, "
        "effective_date, expiration_date) "
        "VALUES ('POL-P1', ?, ?, 'GL', 'Test', '2026-01-01', '2027-01-01')",
        (cid, pgm_id),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Program-level renewal issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, program_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Program renewal', 'issue', 'ISS-PGM', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pgm_id),
    )
    iss_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Task attached to program issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'on program issue', '2026-05-01', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid, iss_id),
    )
    # Loose task on child policy
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose on child', '2026-05-05', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    conn.commit()

    result = get_open_tasks(conn, "program", pgm_id)
    keys = [g["key"] for g in result["groups"]]
    assert "on_program_issue" in keys
    assert "loose" in keys
    assert result["total"] == 2


def test_get_open_tasks_policy_scope_single_group(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid, "POL-SOLO")
    _insert_followup(conn, cid, pid, "task-a", "2026-04-10")
    _insert_followup(conn, cid, pid, "task-b", "2026-04-20")
    conn.commit()

    result = get_open_tasks(conn, "policy", pid)
    assert len(result["groups"]) == 1
    assert result["groups"][0]["key"] == "on_policy"
    assert result["total"] == 2
    subjects = [r["subject"] for r in result["groups"][0]["rows"]]
    assert subjects == ["task-a", "task-b"]  # sort: earlier date first
