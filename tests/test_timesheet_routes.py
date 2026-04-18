"""Route tests for Phase 4 Timesheet Review."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    from policydb.db import init_db
    init_db(path=db_path)
    from policydb.web.app import app
    return TestClient(app)


def test_timesheet_panel_default_returns_200(client):
    resp = client.get("/timesheet/panel")
    assert resp.status_code == 200
    assert "timesheet-panel" in resp.text


def test_timesheet_panel_accepts_kind_week(client):
    resp = client.get("/timesheet/panel?kind=week")
    assert resp.status_code == 200


def test_timesheet_panel_accepts_explicit_range(client):
    resp = client.get("/timesheet/panel?kind=day&start=2026-04-15&end=2026-04-15")
    assert resp.status_code == 200


def _make_activity(client, *, subject="Test", hours=0.1, source="manual"):
    """Insert an activity via raw SQL (fast path) and return its id."""
    from policydb.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO clients (name, industry_segment, account_exec) VALUES ('Cust', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='Cust'").fetchone()["id"]
    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            duration_hours, source, item_kind)
           VALUES (date('now'), ?, ?, 'Email', ?, ?, 'activity')""",
        (cid, subject, hours, source),
    )
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


# ---------------------------------------------------------------------------
# Task 10: POST /activity/{id}/review
# ---------------------------------------------------------------------------

def test_post_review_stamps_reviewed_at(client):
    aid = _make_activity(client)
    resp = client.post(f"/timesheet/activity/{aid}/review")
    assert resp.status_code in (200, 204)
    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert row["reviewed_at"] is not None
    conn.close()


def test_post_review_is_idempotent(client):
    aid = _make_activity(client)
    client.post(f"/timesheet/activity/{aid}/review")
    from policydb.db import get_connection
    conn = get_connection()
    first = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()["reviewed_at"]
    conn.close()

    resp = client.post(f"/timesheet/activity/{aid}/review")
    assert resp.status_code in (200, 204)
    conn = get_connection()
    second = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()["reviewed_at"]
    assert first == second
    conn.close()


def test_post_review_404_on_missing(client):
    resp = client.post("/timesheet/activity/999999/review")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 11: PATCH /activity/{id}
# ---------------------------------------------------------------------------

def test_patch_activity_updates_duration_hours(client):
    aid = _make_activity(client, hours=0.1)
    resp = client.patch(f"/timesheet/activity/{aid}",
                        data={"duration_hours": "1.25"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "total_hours" in body

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT duration_hours, reviewed_at FROM activity_log WHERE id=?",
        (aid,),
    ).fetchone()
    # round-to-0.1 per feedback_hours_any_numeric: 1.25 -> 1.3
    assert abs(float(row["duration_hours"]) - 1.3) < 0.001
    assert row["reviewed_at"] is not None
    conn.close()


def test_patch_activity_updates_subject_and_type(client):
    aid = _make_activity(client)
    resp = client.patch(f"/timesheet/activity/{aid}",
                        data={"subject": "New subject", "activity_type": "Call"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT subject, activity_type FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert row["subject"] == "New subject"
    assert row["activity_type"] == "Call"
    conn.close()


def test_patch_activity_404_on_missing(client):
    resp = client.patch("/timesheet/activity/999999",
                        data={"duration_hours": "1.0"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 12: POST /activity (create new row)
# ---------------------------------------------------------------------------

def test_post_activity_creates_row_and_stamps_reviewed(client):
    from policydb.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('NewClient', 'Tech', 'Grant')"
    )
    cid = cur.lastrowid
    conn.commit()
    conn.close()

    resp = client.post(
        "/timesheet/activity",
        data={
            "client_id": str(cid),
            "activity_date": "2026-04-15",
            "subject": "Forgotten phone call",
            "activity_type": "Call",
            "duration_hours": "0.5",
        },
    )
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert body["ok"] is True
    new_id = body["id"]

    conn = get_connection()
    row = conn.execute(
        "SELECT subject, duration_hours, reviewed_at, item_kind FROM activity_log WHERE id=?",
        (new_id,),
    ).fetchone()
    assert row["subject"] == "Forgotten phone call"
    assert float(row["duration_hours"]) == 0.5
    assert row["reviewed_at"] is not None
    assert row["item_kind"] == "activity"
    conn.close()


def test_post_activity_requires_client(client):
    resp = client.post(
        "/timesheet/activity",
        data={"activity_date": "2026-04-15", "subject": "nope"},
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Task 13: DELETE /activity/{id}
# ---------------------------------------------------------------------------

def test_delete_activity_removes_row(client):
    aid = _make_activity(client)
    resp = client.delete(f"/timesheet/activity/{aid}")
    assert resp.status_code in (200, 204)

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM activity_log WHERE id=?", (aid,)).fetchone()
    assert row is None
    conn.close()


def test_delete_activity_404_on_missing(client):
    resp = client.delete("/timesheet/activity/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 14: POST /closeout + POST /closeout/{id}/reopen
# ---------------------------------------------------------------------------

def test_post_closeout_creates_row_and_bulk_stamps(client):
    aid1 = _make_activity(client)
    aid2 = _make_activity(client)
    week_start = "2026-04-13"

    resp = client.post("/timesheet/closeout", data={"week_start": week_start})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    from policydb.db import get_connection
    conn = get_connection()
    co = conn.execute(
        "SELECT * FROM timesheet_closeouts WHERE week_start=?", (week_start,)
    ).fetchone()
    assert co is not None

    stamped = conn.execute(
        "SELECT COUNT(*) AS n FROM activity_log WHERE reviewed_at IS NOT NULL"
    ).fetchone()["n"]
    assert stamped >= 2
    conn.close()


def test_post_closeout_rejects_duplicate_week(client):
    week_start = "2026-04-13"
    client.post("/timesheet/closeout", data={"week_start": week_start})
    resp = client.post("/timesheet/closeout", data={"week_start": week_start})
    assert resp.status_code == 409


def test_post_reopen_deletes_closeout(client):
    week_start = "2026-04-13"
    first = client.post("/timesheet/closeout", data={"week_start": week_start})
    co_id = first.json()["id"]
    resp = client.post(f"/timesheet/closeout/{co_id}/reopen")
    assert resp.status_code == 200

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM timesheet_closeouts WHERE id=?", (co_id,)).fetchone()
    assert row is None
    conn.close()


def test_post_closeout_rejects_non_monday(client):
    resp = client.post("/timesheet/closeout", data={"week_start": "2026-04-15"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Task 15: Range cap behavior lock
# ---------------------------------------------------------------------------

def test_range_exceeding_cap_returns_400(client):
    resp = client.get("/timesheet/panel?kind=range&start=2025-01-01&end=2026-04-15")
    assert resp.status_code == 400


def test_range_below_cap_returns_200(client):
    resp = client.get("/timesheet/panel?kind=range&start=2026-04-01&end=2026-04-30")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Task 16: GET /timesheet full-page wrapper
# ---------------------------------------------------------------------------

def test_full_page_renders(client):
    resp = client.get("/timesheet")
    assert resp.status_code == 200
    assert "timesheet-panel" in resp.text
    assert "<html" in resp.text.lower()


# ---------------------------------------------------------------------------
# Task 17: _activity_row.html + day-card rendering in _panel.html
# ---------------------------------------------------------------------------

def test_activity_row_appears_in_panel(client):
    aid = _make_activity(client, subject="Loss run for Acme", hours=0.25)
    resp = client.get("/timesheet/panel")
    assert resp.status_code == 200
    assert "Loss run for Acme" in resp.text
    assert f'data-activity-id="{aid}"' in resp.text
    assert "contenteditable" in resp.text


# ---------------------------------------------------------------------------
# Task 18: _flag_strip.html
# ---------------------------------------------------------------------------

def test_flag_strip_appears_when_silent_clients_present(client):
    from policydb.db import get_connection, next_policy_uid
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Silent Corp', 'Tech', 'Grant')"
    )
    cid = cur.lastrowid
    from datetime import date, timedelta
    exp = (date.today() + timedelta(days=10)).isoformat()
    puid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (client_id, policy_uid, first_named_insured, policy_type,
                                 expiration_date, is_opportunity, renewal_status)
           VALUES (?, ?, 'Test', 'GL', ?, 0, 'In Progress')""",
        (cid, puid, exp),
    )
    conn.commit()
    conn.close()

    resp = client.get("/timesheet/panel")
    assert resp.status_code == 200
    assert "Silent Corp" in resp.text
    assert "silent" in resp.text.lower()


def test_flag_strip_absent_when_no_silent_clients(client):
    resp = client.get("/timesheet/panel")
    assert "flag-strip" not in resp.text


# ---------------------------------------------------------------------------
# Task 19: _closeout_badge + _add_activity_form + GET /activity/new
# ---------------------------------------------------------------------------

def test_closeout_badge_renders_when_week_closed(client):
    from policydb.db import get_connection
    conn = get_connection()
    conn.execute(
        """INSERT INTO timesheet_closeouts
           (week_start, week_end, total_hours, activity_count, flag_count)
           VALUES ('2026-04-13', '2026-04-19', 28.5, 20, 2)"""
    )
    conn.commit()
    conn.close()

    resp = client.get("/timesheet/panel?kind=week&start=2026-04-13&end=2026-04-19")
    assert resp.status_code == 200
    assert "Closed" in resp.text
    assert "28.5" in resp.text


def test_add_activity_fragment(client):
    resp = client.get("/timesheet/activity/new?date=2026-04-15")
    assert resp.status_code == 200
    assert "activity_date" in resp.text
    assert "2026-04-15" in resp.text


# ---------------------------------------------------------------------------
# Task 20: Range toggle + close-out button in panel header
# ---------------------------------------------------------------------------

def test_panel_includes_range_toggle(client):
    resp = client.get("/timesheet/panel")
    assert "data-range-toggle" in resp.text
    assert "Day" in resp.text
    assert "Week" in resp.text
    assert "Range" in resp.text


def test_panel_includes_closeout_button_on_week(client):
    resp = client.get("/timesheet/panel?kind=week")
    assert "Close out week" in resp.text


def test_panel_hides_closeout_button_on_day(client):
    resp = client.get("/timesheet/panel?kind=day&start=2026-04-15&end=2026-04-15")

    assert "Close out week" not in resp.text


# ── Task 21: Action Center tab integration ───────────────────────────────────


def test_action_center_timesheet_tab_renders(client):
    resp = client.get("/action-center?tab=timesheet")
    assert resp.status_code == 200
    assert "timesheet-panel" in resp.text


def test_action_center_more_menu_includes_timesheet(client):
    resp = client.get("/action-center")
    assert resp.status_code == 200
    assert "Timesheet" in resp.text


# ── Task 22: Dashboard card integration ─────────────────────────────────────


def test_dashboard_hides_timesheet_card_with_zero_flags(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "timesheet-card" not in resp.text


def test_dashboard_shows_timesheet_card_when_unreviewed(client):
    from policydb.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('X', 'T', 'G')"
    )
    cid = cur.lastrowid
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=today.weekday())
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            duration_hours, source, item_kind, reviewed_at)
           VALUES (?, ?, 'Email', 'Email', 0.1, 'outlook_sync', 'activity', NULL)""",
        (start.isoformat(), cid),
    )
    conn.commit()
    conn.close()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "timesheet-card" in resp.text
    assert "Review this week" in resp.text


# ── Task 23: Settings UI — Timesheet Thresholds ──────────────────────────────


def test_save_timesheet_thresholds(client):
    resp = client.post(
        "/settings/timesheet-thresholds",
        data={
            "low_day_threshold_hours": "3.5",
            "silence_renewal_window_days": "45",
            "range_cap_days": "60",
        },
    )
    assert resp.status_code == 200

    from policydb import config as cfg
    cfg.reload_config()
    thresholds = cfg.get("timesheet_thresholds", {})
    assert float(thresholds["low_day_threshold_hours"]) == 3.5
    assert int(thresholds["silence_renewal_window_days"]) == 45
    assert int(thresholds["range_cap_days"]) == 60


# ---------------------------------------------------------------------------
# Task 9: GET /timesheet/options/all — cascade picker options
# ---------------------------------------------------------------------------

def test_options_endpoint_returns_client_scoped_lists(client):
    from policydb.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('OptCust', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='OptCust'").fetchone()["id"]
    # Seed a policy + project + issue under the same client.
    from policydb.db import next_policy_uid
    uid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, first_named_insured,
                                 policy_type, expiration_date)
           VALUES (?, ?, 'OptCust', 'GL', '2026-12-31')""",
        (uid, cid),
    )
    conn.execute("INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')", (cid,))
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            item_kind, issue_uid, follow_up_done)
           VALUES (date('now'), ?, 'Audit dispute', 'Issue',
                   'issue', 'ISS-99', 0)""",
        (cid,),
    )
    conn.commit()
    conn.close()

    resp = client.get(f"/timesheet/options/all?client_id={cid}")
    assert resp.status_code == 200
    data = resp.json()
    assert any(p["label"].startswith("POL-") for p in data["policies"])
    assert any(p["label"] == "Plant 3" for p in data["projects"])
    assert any(i["label"].startswith("ISS-99") for i in data["issues"])
    # Each row must carry an integer id.
    for k in ("policies", "projects", "issues"):
        for row in data[k]:
            assert isinstance(row["id"], int)


def test_options_endpoint_requires_client_id(client):
    resp = client.get("/timesheet/options/all")
    assert resp.status_code == 422  # FastAPI validation — missing query param


# ---------------------------------------------------------------------------
# Task 11: POST /activity accepts + validates project_id / issue_id
# ---------------------------------------------------------------------------

def _seed_client_with_extras(client):
    from policydb.db import get_connection, next_policy_uid
    conn = get_connection()
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('PCust', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='PCust'").fetchone()["id"]
    uid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, first_named_insured,
                                 policy_type, expiration_date)
           VALUES (?, ?, 'PCust', 'GL', '2026-12-31')""",
        (uid, cid),
    )
    pol_id = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (uid,)).fetchone()["id"]
    conn.execute("INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')", (cid,))
    prj_id = conn.execute("SELECT id FROM projects WHERE client_id=?", (cid,)).fetchone()["id"]
    iss_id = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            item_kind, issue_uid, follow_up_done)
           VALUES (date('now'), ?, 'Issue Q1', 'Issue',
                   'issue', 'ISS-10', 0)""",
        (cid,),
    ).lastrowid
    conn.commit()
    conn.close()
    return cid, pol_id, prj_id, iss_id


def test_post_activity_accepts_project_and_issue(client):
    cid, pol_id, prj_id, iss_id = _seed_client_with_extras(client)
    resp = client.post("/timesheet/activity", data={
        "client_id": cid,
        "activity_date": "2026-04-15",
        "subject": "Follow up",
        "activity_type": "Call",
        "duration_hours": "0.5",
        "policy_id": pol_id,
        "project_id": prj_id,
        "issue_id": iss_id,
    })
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT client_id, policy_id, project_id, issue_id FROM activity_log WHERE id=?",
        (new_id,),
    ).fetchone()
    assert row["client_id"] == cid
    assert row["policy_id"] == pol_id
    assert row["project_id"] == prj_id
    assert row["issue_id"] == iss_id
    conn.close()


def test_post_activity_rejects_cross_client_project(client):
    cid, _, prj_id, _ = _seed_client_with_extras(client)
    # Another client with no projects.
    from policydb.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('Other', 'X', 'Grant')"
    )
    other_cid = conn.execute("SELECT id FROM clients WHERE name='Other'").fetchone()["id"]
    conn.commit()
    conn.close()
    resp = client.post("/timesheet/activity", data={
        "client_id": other_cid,
        "activity_date": "2026-04-15",
        "subject": "X",
        "activity_type": "Note",
        "project_id": prj_id,  # belongs to PCust, not Other
    })
    assert resp.status_code == 400


def test_post_activity_rejects_non_issue_row_as_issue(client):
    cid, _, _, _ = _seed_client_with_extras(client)
    # A plain activity row — not an issue.
    from policydb.db import get_connection
    conn = get_connection()
    aid = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type, item_kind)
           VALUES (date('now'), ?, 'plain', 'Note', 'activity')""",
        (cid,),
    ).lastrowid
    conn.commit()
    conn.close()
    resp = client.post("/timesheet/activity", data={
        "client_id": cid,
        "activity_date": "2026-04-15",
        "subject": "X",
        "activity_type": "Note",
        "issue_id": aid,
    })
    assert resp.status_code == 400
