"""Regression tests for issue SLA calculation with manual due_date override."""
from datetime import date, timedelta

import pytest

import policydb.web.app  # noqa: F401 — boot FastAPI app to satisfy route imports

from policydb.db import get_connection, init_db
from policydb.queries import (
    attach_issue_sla_state,
    compute_issue_sla_state,
    get_dashboard_issues_widget,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


TODAY = date(2026, 4, 15)


def test_manual_due_date_future_is_not_breached():
    """Manual due_date in the future wins over ancient activity_date."""
    state = compute_issue_sla_state(
        {
            "due_date": "2026-05-01",
            "activity_date": "2026-03-01",  # 45d ago — severity would be breached
            "issue_sla_days": 7,
            "issue_severity": "Normal",
        },
        today=TODAY,
    )
    assert state["over_sla"] is False
    assert state["deadline_source"] == "manual"
    assert state["deadline_date"] == "2026-05-01"
    assert state["days_to_deadline"] == 16
    assert state["days_past"] == 0


def test_manual_due_date_past_is_breached():
    """Manual due_date in the past is breached even if activity is recent."""
    state = compute_issue_sla_state(
        {
            "due_date": "2026-04-10",
            "activity_date": "2026-04-14",  # severity would NOT be breached
            "issue_sla_days": 7,
            "issue_severity": "Normal",
        },
        today=TODAY,
    )
    assert state["over_sla"] is True
    assert state["deadline_source"] == "manual"
    assert state["days_past"] == 5


def test_no_due_date_falls_back_to_severity_breached():
    """Without a manual due_date, the severity-derived deadline applies."""
    state = compute_issue_sla_state(
        {
            "due_date": None,
            "activity_date": "2026-04-05",  # 10d ago, SLA 7d — breached
            "issue_sla_days": 7,
            "issue_severity": "Normal",
        },
        today=TODAY,
    )
    assert state["over_sla"] is True
    assert state["deadline_source"] == "severity"
    assert state["deadline_date"] == "2026-04-12"
    assert state["days_past"] == 3


def test_no_due_date_within_severity_budget():
    state = compute_issue_sla_state(
        {
            "due_date": None,
            "activity_date": "2026-04-12",  # 3d ago, SLA 7d — within budget
            "issue_sla_days": 7,
            "issue_severity": "Normal",
        },
        today=TODAY,
    )
    assert state["over_sla"] is False
    assert state["deadline_source"] == "severity"
    assert state["days_past"] == 0


def test_manual_due_date_overrides_severity_breach():
    """The critical regression case: severity says breached, manual says not."""
    state = compute_issue_sla_state(
        {
            "due_date": "2026-04-22",  # next week
            "activity_date": "2026-03-26",  # 20d ago, severity says 13d overdue
            "issue_sla_days": 7,
            "issue_severity": "Normal",
        },
        today=TODAY,
    )
    assert state["over_sla"] is False
    assert state["deadline_source"] == "manual"
    assert state["days_to_deadline"] == 7


def test_empty_due_date_string_treated_as_none():
    state = compute_issue_sla_state(
        {
            "due_date": "",
            "activity_date": "2026-04-12",
            "issue_sla_days": 7,
        },
        today=TODAY,
    )
    assert state["deadline_source"] == "severity"


def test_severity_fallback_when_sla_days_missing():
    """issue_sla_days can be NULL — helper pulls from issue_severities config."""
    state = compute_issue_sla_state(
        {
            "due_date": None,
            "activity_date": "2026-04-01",
            "issue_severity": "Critical",  # default config: 1d SLA
            "issue_sla_days": None,
        },
        today=TODAY,
    )
    # Exact severity budget depends on config, but it must be set (>0) and
    # the issue is clearly over because activity is 14d old.
    assert state["sla_days"] > 0
    assert state["over_sla"] is True


def test_attach_issue_sla_state_mutates_in_place():
    rows = [
        {"due_date": "2026-04-20", "activity_date": "2026-04-01", "issue_sla_days": 7},
        {"due_date": None, "activity_date": "2026-04-01", "issue_sla_days": 7},
    ]
    attach_issue_sla_state(rows)
    assert rows[0]["deadline_source"] == "manual"
    assert rows[0]["over_sla"] is False
    assert rows[1]["deadline_source"] == "severity"
    assert rows[1]["over_sla"] is True


# ── End-to-end: dashboard widget breach count honors manual due_date ────────

def _seed_issue(conn, client_id, *, due_date, activity_date, sla_days, subject):
    conn.execute(
        """INSERT INTO activity_log
             (activity_date, client_id, activity_type, subject, item_kind,
              issue_uid, issue_status, issue_severity, issue_sla_days,
              due_date, account_exec)
           VALUES (?, ?, 'Note', ?, 'issue', ?, 'Open', 'Normal', ?, ?, 'Grant')""",
        (activity_date, client_id, subject, f"ISS-{subject}", sla_days, due_date),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_client_simple(conn):
    conn.execute(
        "INSERT INTO clients (name, cn_number, industry_segment) "
        "VALUES ('Test Client SLA', 'CN-SLA', 'Test')"
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_dashboard_widget_sla_count_respects_manual_due_date(tmp_db):
    """sla_count must not flag an old issue whose manual due_date is in the future."""
    conn = get_connection()
    cid = _seed_client_simple(conn)

    # Issue 1: Old (activity 30d ago, SLA 7d) but manual due_date is 2 weeks out
    # → should NOT count as breached.
    today = date.today()
    old_act = (today - timedelta(days=30)).isoformat()
    future_due = (today + timedelta(days=14)).isoformat()
    _seed_issue(conn, cid, due_date=future_due, activity_date=old_act,
                sla_days=7, subject="old-with-future-due")

    # Issue 2: Manual due_date 5 days ago → SHOULD count as breached.
    past_due = (today - timedelta(days=5)).isoformat()
    _seed_issue(conn, cid, due_date=past_due, activity_date=today.isoformat(),
                sla_days=7, subject="manual-past-due")

    # Issue 3: No manual due_date, activity 14d ago, SLA 7d → breached via severity.
    ancient_act = (today - timedelta(days=14)).isoformat()
    _seed_issue(conn, cid, due_date=None, activity_date=ancient_act,
                sla_days=7, subject="severity-breach")

    # Issue 4: No manual due_date, activity 2d ago → within severity budget.
    recent_act = (today - timedelta(days=2)).isoformat()
    _seed_issue(conn, cid, due_date=None, activity_date=recent_act,
                sla_days=7, subject="within-budget")

    conn.commit()

    widget = get_dashboard_issues_widget(conn, limit=10)
    assert widget["total"] == 4
    # Only Issue 2 (manual past due) and Issue 3 (severity breach) should count.
    assert widget["sla_count"] == 2

    # Spot-check that top_issues have SLA state attached.
    by_subject = {i["subject"]: i for i in widget["top_issues"]}
    assert by_subject["old-with-future-due"]["over_sla"] is False
    assert by_subject["old-with-future-due"]["deadline_source"] == "manual"
    assert by_subject["manual-past-due"]["over_sla"] is True
    assert by_subject["severity-breach"]["over_sla"] is True
    assert by_subject["within-budget"]["over_sla"] is False
