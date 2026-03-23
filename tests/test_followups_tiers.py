"""Tests for follow-ups urgency tier bucketing logic.

Tests _classify_item() — a pure function that buckets follow-up items
by disposition and date. The function is duplicated here to avoid
circular import issues with the action_center module (which triggers
FastAPI app initialization).
"""
from datetime import date, timedelta


def _classify_item(item: dict, today: date, stale_threshold: int, dispositions: list[dict]) -> str:
    """Classify a follow-up item into a bucket.
    Returns one of: triage, today, overdue, stale, nudge_due, watching, scheduled
    """
    source = item.get("source", "activity")
    disposition = item.get("disposition") or ""
    fu_date_str = item.get("follow_up_date", "")

    if source == "activity" and not disposition.strip():
        return "triage"

    accountability = "my_action"
    for d in dispositions:
        if d.get("label", "").lower() == disposition.lower():
            accountability = d.get("accountability", "my_action")
            break

    if accountability == "scheduled":
        return "scheduled"

    try:
        fu_date = date.fromisoformat(fu_date_str)
    except (ValueError, TypeError):
        return "triage"

    days_overdue = (today - fu_date).days

    if accountability == "waiting_external":
        return "nudge_due" if days_overdue >= 0 else "watching"

    if days_overdue == 0:
        return "today"
    elif days_overdue > stale_threshold:
        return "stale"
    elif days_overdue > 0:
        return "overdue"
    else:
        return "watching"

DISPOSITIONS = [
    {"label": "Left VM", "default_days": 3, "accountability": "waiting_external"},
    {"label": "No Answer", "default_days": 1, "accountability": "my_action"},
    {"label": "Sent Email", "default_days": 7, "accountability": "waiting_external"},
    {"label": "Waiting on Client", "default_days": 7, "accountability": "waiting_external"},
    {"label": "Waiting on Carrier", "default_days": 7, "accountability": "waiting_external"},
    {"label": "Connected", "default_days": 0, "accountability": "my_action"},
    {"label": "Received Response", "default_days": 0, "accountability": "my_action"},
    {"label": "Meeting Scheduled", "default_days": 0, "accountability": "scheduled"},
    {"label": "Escalated", "default_days": 3, "accountability": "my_action"},
]


def test_activity_without_disposition_goes_to_triage():
    item = {"source": "activity", "disposition": None, "follow_up_date": date.today().isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "triage"


def test_activity_empty_disposition_goes_to_triage():
    item = {"source": "activity", "disposition": "", "follow_up_date": date.today().isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "triage"


def test_activity_with_disposition_skips_triage():
    item = {"source": "activity", "disposition": "Left VM", "follow_up_date": date.today().isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) != "triage"


def test_policy_reminder_skips_triage():
    item = {"source": "policy", "disposition": None, "follow_up_date": date.today().isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "today"


def test_client_reminder_skips_triage():
    item = {"source": "client", "disposition": None, "follow_up_date": date.today().isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "today"


def test_today_bucket():
    item = {"source": "activity", "disposition": "Connected", "follow_up_date": date.today().isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "today"


def test_overdue_bucket():
    item = {"source": "activity", "disposition": "No Answer", "follow_up_date": (date.today() - timedelta(days=5)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "overdue"


def test_stale_bucket():
    item = {"source": "activity", "disposition": "No Answer", "follow_up_date": (date.today() - timedelta(days=20)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "stale"


def test_boundary_overdue_not_stale():
    """Exactly 14 days overdue is Overdue, not Stale."""
    item = {"source": "activity", "disposition": "No Answer", "follow_up_date": (date.today() - timedelta(days=14)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "overdue"


def test_boundary_stale():
    """15 days overdue is Stale."""
    item = {"source": "activity", "disposition": "No Answer", "follow_up_date": (date.today() - timedelta(days=15)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "stale"


def test_future_my_action_goes_to_watching():
    item = {"source": "activity", "disposition": "Connected", "follow_up_date": (date.today() + timedelta(days=3)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "watching"


def test_waiting_external_overdue_goes_to_nudge():
    item = {"source": "activity", "disposition": "Waiting on Client", "follow_up_date": (date.today() - timedelta(days=2)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "nudge_due"


def test_waiting_external_today_goes_to_nudge():
    item = {"source": "activity", "disposition": "Waiting on Client", "follow_up_date": date.today().isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "nudge_due"


def test_waiting_external_future_goes_to_watching():
    item = {"source": "activity", "disposition": "Waiting on Client", "follow_up_date": (date.today() + timedelta(days=5)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "watching"


def test_scheduled_goes_to_scheduled():
    item = {"source": "activity", "disposition": "Meeting Scheduled", "follow_up_date": (date.today() + timedelta(days=1)).isoformat()}
    assert _classify_item(item, date.today(), 14, DISPOSITIONS) == "scheduled"
