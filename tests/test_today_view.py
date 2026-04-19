"""Tests for v_today_tasks view."""
from __future__ import annotations

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


@pytest.fixture
def seeded(tmp_db):
    """Three tasks with varying due dates + one done task + one merged task."""
    conn = get_connection()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    conn.execute(
        "INSERT INTO clients (name, industry_segment) VALUES ('Acme Co', 'Manufacturing')"
    )
    client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Overdue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec, disposition) "
        "VALUES (?, ?, 'Call', 'Overdue task', ?, 0, 'followup', 'Grant', 'My action')",
        (today, client_id, yesterday),
    )
    # Today
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec, disposition) "
        "VALUES (?, ?, 'Call', 'Today task', ?, 0, 'followup', 'Grant', 'Waiting — client')",
        (today, client_id, today),
    )
    # Tomorrow + standalone (NULL client_id)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, NULL, 'Task', 'Standalone task', ?, 0, 'followup', 'Grant')",
        (today, tomorrow),
    )
    # Done — must be excluded
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, 'Call', 'Done task', ?, 1, 'followup', 'Grant')",
        (today, client_id, today),
    )
    # Merged / auto-closed — must be excluded
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec, auto_closed_at) "
        "VALUES (?, ?, 'Call', 'Auto-closed task', ?, 0, 'followup', 'Grant', '2026-04-15')",
        (today, client_id, today),
    )
    conn.commit()
    return {"client_id": client_id}


def test_v_today_tasks_includes_open_followups(seeded):
    conn = get_connection()
    rows = conn.execute("SELECT subject FROM v_today_tasks ORDER BY subject").fetchall()
    subjects = [r["subject"] for r in rows]
    assert "Overdue task" in subjects
    assert "Today task" in subjects
    assert "Standalone task" in subjects


def test_v_today_tasks_excludes_done_and_auto_closed(seeded):
    conn = get_connection()
    rows = conn.execute("SELECT subject FROM v_today_tasks").fetchall()
    subjects = [r["subject"] for r in rows]
    assert "Done task" not in subjects
    assert "Auto-closed task" not in subjects


def test_v_today_tasks_standalone_has_null_client(seeded):
    conn = get_connection()
    row = conn.execute(
        "SELECT client_id, client_name FROM v_today_tasks WHERE subject = 'Standalone task'"
    ).fetchone()
    assert row["client_id"] is None
    assert row["client_name"] is None


def test_v_today_tasks_priority_ordering(seeded):
    """priority: overdue=3, today=2, tomorrow=1, later=0."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT subject, priority FROM v_today_tasks ORDER BY priority DESC, follow_up_date ASC"
    ).fetchall()
    mapping = {r["subject"]: r["priority"] for r in rows}
    assert mapping["Overdue task"] == 3
    assert mapping["Today task"] == 2
    assert mapping["Standalone task"] == 1


def test_v_today_tasks_is_waiting_flag(seeded):
    conn = get_connection()
    rows = conn.execute("SELECT subject, is_waiting FROM v_today_tasks").fetchall()
    mapping = {r["subject"]: r["is_waiting"] for r in rows}
    assert mapping["Overdue task"] == 0
    assert mapping["Today task"] == 1           # disposition = 'Waiting — client'
    assert mapping["Standalone task"] == 0      # no disposition set
