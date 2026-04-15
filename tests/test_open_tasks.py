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


# ── sync_policy_follow_up_date / sync_client_follow_up_date ─────────────────
# Removed in PR #244 (commit 32593a02) — record-level follow_up_date cache
# columns were dropped from policies/clients/programs via migration 150.
# activity_log is now the sole source of truth and the "next follow-up" is
# derived via grouped LEFT JOIN on activity_log, so the sync helpers are gone
# and the old tests that asserted cache freshness no longer apply.


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

    # After PR #244, the "next follow-up" for a policy is derived from the
    # earliest open activity_log row via MIN(follow_up_date). Verify directly.
    derived = conn.execute(
        """SELECT MIN(follow_up_date) AS next_fu FROM activity_log
           WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL
             AND (item_kind = 'followup' OR item_kind IS NULL)""",
        (pid,),
    ).fetchone()
    assert derived["next_fu"] == "2026-04-20"


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


def test_get_open_tasks_client_scope_no_issue_task_duplication(tmp_db):
    """Regression: issue-linked follow-ups must not also appear under
    'Direct client follow-ups' (policy_id NULL) or 'Loose on other policies'
    (policy_id set).  The task belongs to exactly one group — its issue.
    """
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid, "POL-300")

    # Pure client-level issue (no policy)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, 'Note', 'Client-level issue', 'issue', 'ISS-300', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid),
    )
    client_issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Policy-scoped issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Policy issue', 'issue', 'ISS-301', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    policy_issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Follow-up on the client-level issue (policy_id NULL).  Without the fix
    # this leaks into "Direct client follow-ups" because it has no policy.
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'client-issue task', '2026-04-20', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, client_issue_id),
    )
    # Plus a genuine direct-client follow-up so the group isn't empty
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'real direct task', '2026-04-25', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.commit()

    result = get_open_tasks(conn, "client", cid)
    groups = {g["key"]: g for g in result["groups"]}

    # Direct client group shows only the task with no issue_id
    direct_subjects = [r["subject"] for r in groups["direct_client"]["rows"]]
    assert "real direct task" in direct_subjects
    assert "client-issue task" not in direct_subjects

    # Client-level issue group owns the issue-linked task
    client_issue_subjects = [
        r["subject"] for r in groups[f"issue:{client_issue_id}"]["rows"]
    ]
    assert "client-issue task" in client_issue_subjects

    # Policy issue group is empty (no follow-ups seeded on it) so shouldn't
    # exist at all — confirm no duplication into it
    assert f"issue:{policy_issue_id}" not in groups

    # Total = 2 (one direct + one on client issue), not 3 (no double-count)
    assert result["total"] == 2


def test_get_open_tasks_client_scope_no_loose_issue_duplication(tmp_db):
    """Regression: a policy follow-up attached to one of the client's open
    issues must not also surface in 'Loose on other policies'.
    """
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid_covered = _seed_policy(conn, cid, "POL-400")
    pid_loose = _seed_policy(conn, cid, "POL-401")

    # Issue on POL-400 (covers it)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'POL-400 issue', 'issue', 'ISS-400', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid_covered),
    )
    issue_400 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Follow-up attached to issue_400 but pointing at the OTHER (uncovered)
    # policy.  Without the fix it would show both in the issue group AND
    # in loose_policies (since POL-401 is uncovered).
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'cross-policy issue task', '2026-04-22', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid_loose, issue_400),
    )
    conn.commit()

    result = get_open_tasks(conn, "client", cid)
    groups = {g["key"]: g for g in result["groups"]}

    loose_subjects = [r["subject"] for r in groups.get("loose_policies", {}).get("rows", [])]
    assert "cross-policy issue task" not in loose_subjects

    on_issue_subjects = [r["subject"] for r in groups[f"issue:{issue_400}"]["rows"]]
    assert "cross-policy issue task" in on_issue_subjects

    assert result["total"] == 1


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
