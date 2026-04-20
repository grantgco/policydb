"""Tests for standalone tasks (activity_log rows with NULL client_id)."""
from __future__ import annotations

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


def test_activity_log_client_id_is_nullable(tmp_db):
    """After migration 163, activity_log.client_id must be NULL-allowed."""
    conn = get_connection()
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(activity_log)").fetchall()}
    assert cols["client_id"]["notnull"] == 0, (
        "activity_log.client_id must be NULL-allowed for standalone tasks"
    )


def test_can_insert_standalone_task(tmp_db):
    """An activity_log row with NULL client_id + follow_up_date is a standalone task."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, item_kind, account_exec) "
        "VALUES ('2026-04-18', NULL, 'Task', 'Standalone item', '2026-04-18', 'followup', 'Grant')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, client_id, subject FROM activity_log WHERE subject = 'Standalone item'"
    ).fetchone()
    assert row is not None
    assert row["client_id"] is None
    assert row["subject"] == "Standalone item"
