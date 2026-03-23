"""Tests for follow-ups urgency tier bucketing logic."""
import pytest
from datetime import date, timedelta


def _get_classify_item():
    """Import _classify_item while avoiding circular import.

    action_center.py imports from app.py which imports action_center at
    module level to register routes.  We break the cycle by pre-populating
    sys.modules with a stub for policydb.web.app before importing.
    """
    import sys
    import types

    # If already imported successfully, just return it
    mod = sys.modules.get("policydb.web.routes.action_center")
    if mod and hasattr(mod, "_classify_item"):
        return mod._classify_item

    # Create a minimal stub for policydb.web.app to break the circular import
    stub_key = "policydb.web.app"
    had_stub = stub_key in sys.modules
    old_mod = sys.modules.get(stub_key)

    if not had_stub:
        stub = types.ModuleType(stub_key)
        stub.get_db = None
        stub.templates = None
        sys.modules[stub_key] = stub

    try:
        from policydb.web.routes.action_center import _classify_item
        return _classify_item
    finally:
        # Restore original state
        if not had_stub:
            if stub_key in sys.modules:
                del sys.modules[stub_key]
        elif old_mod is not None:
            sys.modules[stub_key] = old_mod


_classify_item = _get_classify_item()

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
