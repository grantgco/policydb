"""Settings routes — manage configurable dropdown lists and email subjects."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import policydb.config as cfg
from policydb.config import reorder_list_item
from policydb.db import DB_PATH, _HEALTH_STATUS
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/settings")

EDITABLE_LISTS: dict[str, str] = {
    "policy_types": "Lines of Business",
    "coverage_forms": "Coverage Forms",
    "renewal_statuses": "Renewal Statuses",
    "opportunity_statuses": "Opportunity Statuses",
    "industry_segments": "Industry Segments",
    "activity_types": "Activity Types",
    "meeting_types": "Meeting Types",
    "renewal_milestones": "Renewal Checklist",
    "critical_milestones": "Critical Milestones",
    "risk_categories": "Risk / Exposure Categories",
    "risk_severities": "Risk Severity Levels",
    "risk_sources": "Risk Sources",
    "risk_control_types": "Risk Control Types",
    "risk_control_statuses": "Risk Control Statuses",
    "risk_adequacy_levels": "Coverage Adequacy Levels",
    "linked_account_relationships": "Account Relationship Types",
    "project_stages": "Project Stages",
    "project_types": "Project Types",
    "expertise_lines": "Contact Expertise — Lines",
    "expertise_industries": "Contact Expertise — Industries",
    "endorsement_types": "Required Endorsement Types",
    "compliance_statuses": "Compliance Statuses",
    "deductible_types": "Deductible Types",
    "construction_types": "Construction Types (ISO)",
    "sprinkler_options": "Sprinkler Options",
    "roof_types": "Roof Types",
    "protection_classes": "Protection Classes (ISO)",
    "carriers": "Carriers",
    "exposure_basis_options": "Exposure Basis Options",
    "exposure_unit_options": "Exposure Unit Options",
    "exposure_denominators": "Exposure Denominators",
    "contact_roles": "Contact Roles",
    "request_categories": "Request Categories",
    "issue_lifecycle_states": "Issue Lifecycle States",
    "issue_resolution_types": "Issue Resolution Types",
    "issue_root_cause_categories": "Issue Root Cause Categories",
    "account_priority_options": "Account Priority Options",
    "service_model_options": "Service Model Options",
    "kb_categories": "KB Categories",
}

TAB_LISTS: dict[str, dict[str, str]] = {
    "workflow": {
        "renewal_statuses": "Renewal Statuses",
        "renewal_milestones": "Renewal Checklist",
        "critical_milestones": "Critical Milestones",
        "activity_types": "Activity Types",
        "meeting_types": "Meeting Types",
        "request_categories": "Request Categories",
    },
    "readiness": {},
    "carriers": {
        "carriers": "Carriers",
        "policy_types": "Lines of Business",
        "coverage_forms": "Coverage Forms",
        "deductible_types": "Deductible Types",
        "endorsement_types": "Required Endorsement Types",
        "exposure_basis_options": "Exposure Basis Options",
        "exposure_unit_options": "Exposure Unit Options",
        "exposure_denominators": "Exposure Denominators",
    },
    "property-risk": {
        "construction_types": "Construction Types (ISO)",
        "sprinkler_options": "Sprinkler Options",
        "roof_types": "Roof Types",
        "protection_classes": "Protection Classes (ISO)",
        "risk_categories": "Risk / Exposure Categories",
        "risk_severities": "Risk Severity Levels",
        "risk_sources": "Risk Sources",
        "risk_control_types": "Risk Control Types",
        "risk_control_statuses": "Risk Control Statuses",
        "risk_adequacy_levels": "Coverage Adequacy Levels",
        "compliance_statuses": "Compliance Statuses",
    },
    "email-contacts": {
        "industry_segments": "Industry Segments",
        "opportunity_statuses": "Opportunity Statuses",
        "expertise_lines": "Contact Expertise — Lines",
        "expertise_industries": "Contact Expertise — Industries",
        "linked_account_relationships": "Account Relationship Types",
        "contact_roles": "Contact Roles",
        "account_priority_options": "Account Priority Options",
        "service_model_options": "Service Model Options",
    },
    "issues": {
        "issue_lifecycle_states": "Issue Lifecycle States",
        "issue_resolution_types": "Issue Resolution Types",
        "issue_root_cause_categories": "Issue Root Cause Categories",
    },
    "database": {
        "project_stages": "Project Stages",
        "project_types": "Project Types",
        "kb_categories": "KB Categories",
    },
    "data-health": {},
    "anomalies": {},
}

TAB_LABELS = {
    "workflow": "Renewal Workflow",
    "readiness": "Readiness & Alerts",
    "carriers": "Carriers & Coverage",
    "property-risk": "Property & Risk",
    "issues": "Issue Tracking",
    "email-contacts": "Email & Contacts",
    "database": "Database & Admin",
    "data-health": "Data Health",
    "anomalies": "Anomaly Thresholds",
}

SEARCH_INDEX = [
    {"label": label, "tab": tab, "anchor": f"list-{key}"}
    for tab, lists in TAB_LISTS.items()
    for key, label in lists.items()
] + [
    {"label": "Mandated Activities", "tab": "workflow", "anchor": "section-mandated-activities"},
    {"label": "Milestone Profiles", "tab": "workflow", "anchor": "section-milestone-profiles"},
    {"label": "Timeline Engine", "tab": "workflow", "anchor": "section-timeline-engine"},
    {"label": "Follow-Up Dispositions", "tab": "workflow", "anchor": "section-dispositions"},
    {"label": "Follow-Up Urgency Tiers", "tab": "workflow", "anchor": "section-stale-threshold"},
    {"label": "Expiration Buffer", "tab": "workflow", "anchor": "section-expiration-buffer"},
    {"label": "Alert & Readiness Thresholds", "tab": "readiness", "anchor": "section-thresholds"},
    {"label": "Readiness Score Weights", "tab": "readiness", "anchor": "section-readiness-weights"},
    {"label": "Carrier Aliases", "tab": "carriers", "anchor": "section-carrier-aliases"},
    {"label": "Issue Severity Levels", "tab": "issues", "anchor": "section-issue-severities"},
    {"label": "Renewal Issue Window", "tab": "issues", "anchor": "section-renewal-issue-window"},
    {"label": "Email Subject Lines", "tab": "email-contacts", "anchor": "section-email-subjects"},
    {"label": "Google Places API", "tab": "database", "anchor": "section-google-places"},
    {"label": "Report Logo", "tab": "database", "anchor": "section-report-logo"},
    {"label": "Database Health", "tab": "database", "anchor": "section-db-health"},
    {"label": "SQL Console", "tab": "database", "anchor": "section-sql-console"},
    {"label": "Schema Reference", "tab": "database", "anchor": "section-schema-ref"},
    {"label": "Anomaly Detection Thresholds", "tab": "anomalies", "anchor": "section-anomaly-thresholds"},
]


# ── Tab context builder ──────────────────────────────────────────────────────

def _build_tab_context(tab: str, conn) -> dict:
    """Build template context for a specific settings tab."""
    ctx: dict = {}

    tab_lists = TAB_LISTS.get(tab, {})
    if tab_lists:
        ctx["lists"] = {key: cfg.get(key, []) for key in tab_lists}
        ctx["tab_lists"] = tab_lists

    if tab == "workflow":
        ctx["mandated_activities"] = cfg.get("mandated_activities", [])
        ctx["milestone_profiles"] = cfg.get("milestone_profiles", [])
        ctx["milestone_profile_rules"] = cfg.get("milestone_profile_rules", [])
        ctx["timeline_engine"] = cfg.get("timeline_engine", {})
        ctx["risk_alert_thresholds"] = cfg.get("risk_alert_thresholds", {})
        ctx["dispositions"] = cfg.get("follow_up_dispositions", [])
        ctx["excluded_statuses"] = cfg.get("renewal_statuses_excluded", [])
        ctx["client_facing_milestones"] = cfg.get("client_facing_milestones", [])
        ctx["renewal_statuses"] = cfg.get("renewal_statuses", [])
        ctx["renewal_milestones"] = cfg.get("renewal_milestones", [])
        ctx["stale_threshold_days"] = cfg.get("stale_threshold_days", 14)
        ctx["followup_expiration_buffer_days"] = cfg.get("followup_expiration_buffer_days", 3)
        # activity_types needed by mandated activities editor
        if "lists" not in ctx:
            ctx["lists"] = {}
        ctx["lists"]["activity_types"] = cfg.get("activity_types", [])

    elif tab == "readiness":
        ctx["escalation_thresholds"] = cfg.get("escalation_thresholds", {})
        ctx["readiness_thresholds"] = cfg.get("readiness_thresholds", {})
        ctx["readiness_weights"] = cfg.get("readiness_weights", {})
        ctx["readiness_status_scores"] = cfg.get("readiness_status_scores", {})
        ctx["readiness_milestone_weights"] = cfg.get("readiness_milestone_weights", {})
        ctx["readiness_activity_tiers"] = cfg.get("readiness_activity_tiers", [])
        ctx["renewal_statuses"] = cfg.get("renewal_statuses", [])
        ctx["renewal_milestones"] = cfg.get("renewal_milestones", [])
        ctx["fu_workload"] = cfg.get("followup_workload_thresholds", {"warning": 3, "danger": 5})

    elif tab == "carriers":
        ctx["carrier_aliases"] = cfg.get("carrier_aliases", {})

    elif tab == "property-risk":
        pass  # Only needs lists, already loaded above

    elif tab == "issues":
        ctx["issue_severities"] = cfg.get("issue_severities", [])
        ctx["renewal_issue_window_days"] = cfg.get("renewal_issue_window_days", 120)
        ctx["renewal_issue_health_threshold"] = cfg.get("renewal_issue_health_threshold", "at_risk")

    elif tab == "email-contacts":
        ctx["email_subject_policy"] = cfg.get("email_subject_policy", "")
        ctx["email_subject_client"] = cfg.get("email_subject_client", "")
        ctx["email_subject_followup"] = cfg.get("email_subject_followup", "")
        ctx["email_subject_request"] = cfg.get("email_subject_request", "")
        ctx["email_subject_request_all"] = cfg.get("email_subject_request_all", "")
        ctx["email_subject_rfi_notify"] = cfg.get("email_subject_rfi_notify", "")

    elif tab == "data-health":
        ctx["data_health_threshold"] = cfg.get("data_health_threshold", 85)
        ctx["completeness_weight"] = cfg.get("data_health_completeness_weight", 0.7)
        ctx["freshness_weight"] = cfg.get("data_health_freshness_weight", 0.3)
        ctx["data_health_fields"] = cfg.get("data_health_fields", {})

    elif tab == "anomalies":
        ctx["thresholds"] = cfg.get("anomaly_thresholds", {})

    elif tab == "database":
        ctx["logo_exists"] = Path(cfg.get("report_logo_path", "")).exists()
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        wal_path = str(DB_PATH) + "-wal"
        wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
        backup_dir = DB_PATH.parent / "backups"
        backups = (
            sorted(backup_dir.glob("policydb_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
            if backup_dir.exists()
            else []
        )
        migration_backup_dir = backup_dir / "migrations"
        migration_backups = (
            sorted(migration_backup_dir.glob("policydb_*_pre_migration.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
            if migration_backup_dir.exists()
            else []
        )
        legacy_backups = sorted(DB_PATH.parent.glob("policydb.sqlite.backup_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        db_counts: dict = {}
        for tbl in ["clients", "policies", "activity_log", "contacts"]:
            try:
                db_counts[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]  # noqa: S608
            except Exception:
                db_counts[tbl] = 0
        try:
            db_counts["clients_archived"] = conn.execute(
                "SELECT COUNT(*) FROM clients WHERE archived=1"
            ).fetchone()[0]
        except Exception:
            db_counts["clients_archived"] = 0
        try:
            db_counts["policies_archived"] = conn.execute(
                "SELECT COUNT(*) FROM policies WHERE archived=1"
            ).fetchone()[0]
        except Exception:
            db_counts["policies_archived"] = 0
        try:
            max_migration = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        except Exception:
            max_migration = None
        try:
            db_tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
        except Exception:
            db_tables = []

        ctx["db_health"] = _HEALTH_STATUS
        ctx["db_size"] = db_size
        ctx["wal_size"] = wal_size
        ctx["db_counts"] = db_counts
        ctx["backups"] = backups
        ctx["migration_backups"] = migration_backups
        ctx["legacy_backups"] = legacy_backups
        ctx["backup_retention_max"] = cfg.get("backup_retention_count", 30)
        ctx["migration_backup_retention_max"] = cfg.get("migration_backup_retention_count", 10)
        ctx["max_migration"] = max_migration
        ctx["backup_dir"] = backup_dir
        ctx["sql_examples"] = _SQL_EXAMPLES
        ctx["db_tables"] = db_tables
        ctx["google_places_api_key"] = cfg.get("google_places_api_key", "")
        ctx["google_places_daily_limit"] = cfg.get("google_places_daily_limit", 1000)
        ctx["brokerage_name"] = cfg.get("brokerage_name", "")

    return ctx


# ── Tab content endpoint (MUST come before parameterized routes) ─────────────

@router.get("/tab/{tab_name}", response_class=HTMLResponse)
def settings_tab(tab_name: str, request: Request, conn=Depends(get_db)):
    if tab_name not in TAB_LABELS:
        return HTMLResponse("Not found", status_code=404)
    ctx = _build_tab_context(tab_name, conn)
    ctx["request"] = request
    return templates.TemplateResponse(f"settings/_tab_{tab_name.replace('-', '_')}.html", ctx)


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, tab: str = Query("workflow"), conn=Depends(get_db)):
    initial_tab = tab if tab in TAB_LABELS else "workflow"

    # Build context for the initially-active tab
    tab_ctx = _build_tab_context(initial_tab, conn)

    # Base context
    ctx = {
        "request": request,
        "active": "settings",
        "initial_tab": initial_tab,
        "tab_labels": TAB_LABELS,
        "all_list_labels": EDITABLE_LISTS,
        "search_index": SEARCH_INDEX,
    }
    ctx.update(tab_ctx)

    return templates.TemplateResponse("settings.html", ctx)


@router.post("/config/data-health")
def save_data_health_config(
    request: Request,
    threshold: int = Form(85),
    completeness_weight: float = Form(0.7),
    conn=Depends(get_db),
):
    full = dict(cfg.load_config())
    full["data_health_threshold"] = threshold
    full["data_health_completeness_weight"] = completeness_weight
    full["data_health_freshness_weight"] = round(1.0 - completeness_weight, 2)
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=data-health", status_code=303)


@router.post("/anomaly-thresholds", response_class=HTMLResponse)
def save_anomaly_thresholds(
    renewal_not_started_days: int = Form(60),
    stale_followup_count: int = Form(10),
    status_no_activity_days: int = Form(30),
    no_activity_days: int = Form(90),
    no_followup_scheduled: str = Form(""),
    heavy_week_threshold: int = Form(5),
    forecast_window_days: int = Form(30),
    light_week_window_days: int = Form(14),
    bound_missing_effective: str = Form(""),
    expired_no_renewal: str = Form(""),
    review_min_health_score: int = Form(70),
    review_activity_window_days: int = Form(30),
    overdue_review_days: int = Form(90),
):
    """Save anomaly detection thresholds."""
    thresholds = {
        "renewal_not_started_days": renewal_not_started_days,
        "stale_followup_count": stale_followup_count,
        "status_no_activity_days": status_no_activity_days,
        "no_activity_days": no_activity_days,
        "no_followup_scheduled": no_followup_scheduled == "on",
        "heavy_week_threshold": heavy_week_threshold,
        "forecast_window_days": forecast_window_days,
        "light_week_window_days": light_week_window_days,
        "bound_missing_effective": bound_missing_effective == "on",
        "expired_no_renewal": expired_no_renewal == "on",
        "review_min_health_score": review_min_health_score,
        "review_activity_window_days": review_activity_window_days,
        "overdue_review_days": overdue_review_days,
    }
    full = dict(cfg.load_config())
    full["anomaly_thresholds"] = thresholds
    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs font-medium">Saved</span>')


@router.post("/email-subject", response_class=HTMLResponse)
def save_email_subject(key: str = Form(...), value: str = Form(...)):
    _allowed = {"email_subject_policy", "email_subject_client", "email_subject_followup", "email_subject_request", "email_subject_request_all", "email_subject_rfi_notify"}
    if key in _allowed:
        full = dict(cfg.load_config())
        full[key] = value
        cfg.save_config(full)
        cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs">Saved</span>')


def _sync_readiness_on_add(key: str, item: str) -> None:
    """When a status or milestone is added, ensure it has a readiness weight entry."""
    if key == "renewal_statuses":
        full = dict(cfg.load_config())
        scores = full.get("readiness_status_scores", {})
        if item not in scores:
            scores[item] = 25  # conservative default
            full["readiness_status_scores"] = scores
            cfg.save_config(full)
            cfg.reload_config()
    elif key == "renewal_milestones":
        full = dict(cfg.load_config())
        weights = full.get("readiness_milestone_weights", {})
        if item not in weights:
            weights[item] = 1  # default weight
            full["readiness_milestone_weights"] = weights
            cfg.save_config(full)
            cfg.reload_config()


def _sync_readiness_on_remove(key: str, item: str) -> None:
    """When a status or milestone is removed, clean up its readiness weight entry."""
    if key == "renewal_statuses":
        full = dict(cfg.load_config())
        scores = full.get("readiness_status_scores", {})
        if item in scores:
            del scores[item]
            full["readiness_status_scores"] = scores
            cfg.save_config(full)
            cfg.reload_config()
    elif key == "renewal_milestones":
        full = dict(cfg.load_config())
        weights = full.get("readiness_milestone_weights", {})
        if item in weights:
            del weights[item]
            full["readiness_milestone_weights"] = weights
            cfg.save_config(full)
            cfg.reload_config()


@router.post("/list/add", response_class=HTMLResponse)
def list_add(request: Request, key: str = Form(...), item: str = Form(...)):
    item = item.strip()
    if key in EDITABLE_LISTS and item:
        cfg.add_list_item(key, item)
        _sync_readiness_on_add(key, item)
    return _render_list(request, key)


@router.post("/list/remove", response_class=HTMLResponse)
def list_remove(request: Request, key: str = Form(...), item: str = Form(...)):
    if key in EDITABLE_LISTS:
        cfg.remove_list_item(key, item)
        _sync_readiness_on_remove(key, item)
    return _render_list(request, key)


@router.post("/list/reorder", response_class=HTMLResponse)
def list_reorder(request: Request, key: str = Form(...), item: str = Form(...), direction: str = Form(...)):
    if key in EDITABLE_LISTS and direction in ("up", "down"):
        reorder_list_item(key, item, direction)
    return _render_list(request, key)


@router.post("/list/reorder-all")
async def list_reorder_all(request: Request):
    """Accept a full reordered list from drag-to-reorder."""
    body = await request.json()
    key = body.get("key", "")
    items = body.get("items", [])
    if key not in EDITABLE_LISTS or not items:
        return JSONResponse({"ok": False})
    full = dict(cfg.load_config())
    full[key] = items
    cfg.save_config(full)
    cfg.reload_config()
    return JSONResponse({"ok": True})


@router.post("/dispositions/add")
def disposition_add(request: Request, label: str = Form(...), default_days: int = Form(0), category: str = Form("action")):
    """Add a new disposition to follow_up_dispositions."""
    lst = cfg.get("follow_up_dispositions", [])
    if any(d["label"] == label for d in lst):
        return RedirectResponse("/settings?tab=workflow", status_code=303)
    valid_cats = ("waiting", "action", "completed")
    cat = category if category in valid_cats else "action"
    lst.append({"label": label, "default_days": max(0, default_days), "category": cat})
    full = dict(cfg.load_config())
    full["follow_up_dispositions"] = lst
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=workflow", status_code=303)


@router.post("/dispositions/remove")
def disposition_remove(request: Request, label: str = Form(...)):
    """Remove a disposition by label."""
    lst = cfg.get("follow_up_dispositions", [])
    lst = [d for d in lst if d["label"] != label]
    full = dict(cfg.load_config())
    full["follow_up_dispositions"] = lst
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=workflow", status_code=303)


@router.post("/dispositions/reorder")
def disposition_reorder(request: Request, label: str = Form(...), direction: str = Form(...)):
    """Move a disposition up or down."""
    lst = cfg.get("follow_up_dispositions", [])
    idx = next((i for i, d in enumerate(lst) if d["label"] == label), None)
    if idx is None:
        return RedirectResponse("/settings?tab=workflow", status_code=303)
    if direction == "up" and idx > 0:
        lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
    elif direction == "down" and idx < len(lst) - 1:
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
    full = dict(cfg.load_config())
    full["follow_up_dispositions"] = lst
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=workflow", status_code=303)


@router.patch("/dispositions/update")
async def disposition_update(request: Request):
    """Update default_days and/or accountability for a disposition."""
    body = await request.json()
    label = body.get("label", "")
    lst = cfg.get("follow_up_dispositions", [])
    for d in lst:
        if d["label"] == label:
            if "default_days" in body:
                d["default_days"] = max(0, int(body["default_days"]))
            if "accountability" in body:
                d["accountability"] = body["accountability"]
            if "category" in body:
                d["category"] = body["category"]
            break
    full = dict(cfg.load_config())
    full["follow_up_dispositions"] = lst
    cfg.save_config(full)
    cfg.reload_config()
    return JSONResponse({"ok": True})


def _render_list(request: Request, key: str) -> HTMLResponse:
    ctx = {
        "request": request,
        "key": key,
        "label": EDITABLE_LISTS.get(key, key),
        "items": cfg.get(key, []),
    }
    if key == "renewal_statuses":
        ctx["excluded_items"] = cfg.get("renewal_statuses_excluded", [])
    if key == "renewal_milestones":
        ctx["client_facing_items"] = cfg.get("client_facing_milestones", [])
    return templates.TemplateResponse("settings/_list_card.html", ctx)


@router.post("/thresholds", response_class=HTMLResponse)
def save_thresholds(
    request: Request,
    critical_days: int = Form(60),
    critical_stale_days: int = Form(14),
    warning_days: int = Form(90),
    nudge_days: int = Form(120),
    nudge_stale_days: int = Form(30),
    readiness_ready: int = Form(75),
    readiness_on_track: int = Form(50),
    readiness_at_risk: int = Form(25),
    followup_workload_warning: int = Form(3),
    followup_workload_danger: int = Form(5),
):
    full = dict(cfg.load_config())
    full["escalation_thresholds"] = {
        "critical_days": critical_days,
        "critical_stale_days": critical_stale_days,
        "warning_days": warning_days,
        "nudge_days": nudge_days,
        "nudge_stale_days": nudge_stale_days,
    }
    full["readiness_thresholds"] = {
        "ready": readiness_ready,
        "on_track": readiness_on_track,
        "at_risk": readiness_at_risk,
    }
    full["followup_workload_thresholds"] = {
        "warning": followup_workload_warning,
        "danger": followup_workload_danger,
    }
    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse(
        '<span id="thresh-status" class="text-xs text-green-600 font-medium">Saved</span>'
    )


@router.post("/config/stale-threshold")
def update_stale_threshold(request: Request, value: int = Form(...)):
    full = dict(cfg.load_config())
    full["stale_threshold_days"] = max(1, min(value, 90))
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings", status_code=303)


@router.post("/config/expiration-buffer")
def update_expiration_buffer(request: Request, value: int = Form(...)):
    full = dict(cfg.load_config())
    full["followup_expiration_buffer_days"] = max(1, min(value, 30))
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings", status_code=303)


@router.post("/config/renewal-issue-window")
def update_renewal_issue_window(
    request: Request,
    window_days: int = Form(...),
    health_threshold: str = Form("at_risk"),
):
    full = dict(cfg.load_config())
    full["renewal_issue_window_days"] = max(30, min(window_days, 365))
    full["renewal_issue_health_threshold"] = health_threshold
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=issues", status_code=303)


@router.post("/config/google-places")
def update_google_places(request: Request, api_key: str = Form(""), daily_limit: int = Form(1000)):
    full = dict(cfg.load_config())
    full["google_places_api_key"] = api_key.strip()
    full["google_places_daily_limit"] = max(1, min(daily_limit, 100000))
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=database", status_code=303)


@router.post("/config/brokerage")
def update_brokerage(request: Request, brokerage_name: str = Form("")):
    full = dict(cfg.load_config())
    full["brokerage_name"] = brokerage_name.strip()
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=database", status_code=303)


@router.post("/readiness-weights", response_class=HTMLResponse)
async def save_readiness_weights(request: Request):
    """Save all readiness score weight configuration."""
    form = await request.form()
    full = dict(cfg.load_config())

    # Component weights
    weights = {
        "status": int(form.get("w_status", 40)),
        "checklist": int(form.get("w_checklist", 25)),
        "activity": int(form.get("w_activity", 15)),
        "followup": int(form.get("w_followup", 10)),
        "placement": int(form.get("w_placement", 10)),
    }
    weight_sum = sum(weights.values())
    if weight_sum != 100:
        diff = weight_sum - 100
        direction = "over" if diff > 0 else "short"
        return HTMLResponse(
            f'<span id="rw-status" class="text-xs text-red-600 font-medium">'
            f'Weights must total 100 (currently {weight_sum} — {abs(diff)} {direction})</span>'
        )
    full["readiness_weights"] = weights

    # Status score mapping
    status_scores = {}
    for key, val in form.items():
        if key.startswith("ss_") and not key.startswith("ss_name_"):
            idx = key[3:]
            name = form.get(f"ss_name_{idx}", "")
            if name:
                status_scores[name] = int(val)
    full["readiness_status_scores"] = status_scores

    # Milestone weights
    milestone_weights = {}
    for key, val in form.items():
        if key.startswith("mw_") and not key.startswith("mw_name_"):
            idx = key[3:]
            name = form.get(f"mw_name_{idx}", "")
            if name:
                milestone_weights[name] = int(val)
    full["readiness_milestone_weights"] = milestone_weights

    # Activity tiers
    tiers = []
    i = 0
    while f"at_days_{i}" in form:
        tiers.append({
            "days": int(form.get(f"at_days_{i}", 7)),
            "pct": int(form.get(f"at_pct_{i}", 100)),
        })
        i += 1
    if tiers:
        full["readiness_activity_tiers"] = tiers

    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse(
        '<span id="rw-status" class="text-xs text-green-600 font-medium">Saved</span>'
    )


# ── Per-field auto-save PATCH endpoints ──────────────────────────────────────

@router.patch("/threshold-field", response_class=HTMLResponse)
def patch_threshold_field(request: Request, field: str = Form(...), value: str = Form(...)):
    """Save a single threshold field on blur."""
    escalation_fields = {"critical_days", "critical_stale_days", "warning_days", "nudge_days", "nudge_stale_days"}

    full = dict(cfg.load_config())

    if field in escalation_fields:
        thresholds = full.get("escalation_thresholds", {})
        thresholds[field] = int(value)
        full["escalation_thresholds"] = thresholds
    elif field.startswith("readiness_"):
        key = field.replace("readiness_", "")
        thresholds = full.get("readiness_thresholds", {})
        thresholds[key] = int(value)
        full["readiness_thresholds"] = thresholds
    elif field == "followup_workload_warning":
        wl = full.get("followup_workload_thresholds", {})
        wl["warning"] = int(value)
        full["followup_workload_thresholds"] = wl
    elif field == "followup_workload_danger":
        wl = full.get("followup_workload_thresholds", {})
        wl["danger"] = int(value)
        full["followup_workload_thresholds"] = wl
    else:
        return HTMLResponse('<span class="text-red-500 text-xs">Unknown field</span>')

    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs">Saved</span>')


@router.patch("/readiness-weight-field", response_class=HTMLResponse)
def patch_readiness_weight_field(request: Request, field: str = Form(...), value: str = Form(...)):
    """Save a single readiness weight field on blur."""
    full = dict(cfg.load_config())

    component_fields = {"status", "checklist", "activity", "followup", "placement"}

    if field.startswith("w_") and field[2:] in component_fields:
        weights = full.get("readiness_weights", {})
        weights[field[2:]] = int(value)
        full["readiness_weights"] = weights
    elif field.startswith("ss_"):
        # Status score: field is "ss_StatusName", value is the score
        status_name = field[3:]
        scores = full.get("readiness_status_scores", {})
        scores[status_name] = int(value)
        full["readiness_status_scores"] = scores
    elif field.startswith("mw_"):
        # Milestone weight: field is "mw_MilestoneName", value is the weight
        milestone_name = field[3:]
        weights = full.get("readiness_milestone_weights", {})
        weights[milestone_name] = int(value)
        full["readiness_milestone_weights"] = weights
    elif field.startswith("at_"):
        # Activity tier: field is "at_0_days" or "at_0_pct"
        parts = field[3:].split("_", 1)
        if len(parts) == 2:
            idx = int(parts[0])
            subfield = parts[1]  # "days" or "pct"
            tiers = list(full.get("readiness_activity_tiers", []))
            if 0 <= idx < len(tiers):
                tiers[idx][subfield] = int(value)
                full["readiness_activity_tiers"] = tiers
    else:
        return HTMLResponse('<span class="text-red-500 text-xs">Unknown field</span>')

    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs">Saved</span>')


@router.post("/milestone/toggle-client-facing", response_class=HTMLResponse)
def toggle_milestone_client_facing(request: Request, item: str = Form(...)):
    """Toggle whether a renewal milestone is flagged for client request seeding."""
    facing = list(cfg.get("client_facing_milestones", []))
    if item in facing:
        facing = [m for m in facing if m != item]
    else:
        facing.append(item)
    full = dict(cfg.load_config())
    full["client_facing_milestones"] = facing
    cfg.save_config(full)
    cfg.reload_config()
    return _render_list(request, "renewal_milestones")


@router.post("/renewal-status/toggle-exclude", response_class=HTMLResponse)
def toggle_renewal_status_exclude(request: Request, item: str = Form(...)):
    excluded = list(cfg.get("renewal_statuses_excluded", []))
    if item in excluded:
        excluded = [s for s in excluded if s != item]
    else:
        excluded = excluded + [item]
    full = dict(cfg.load_config())
    full["renewal_statuses_excluded"] = excluded
    cfg.save_config(full)
    cfg.reload_config()
    return templates.TemplateResponse("settings/_list_card.html", {
        "request": request,
        "key": "renewal_statuses",
        "label": "Renewal Statuses",
        "items": cfg.get("renewal_statuses", []),
        "excluded_items": excluded,
    })


# ── Mandated Activities ──────────────────────────────────────────────────────

@router.post("/mandated-activities/add", response_class=HTMLResponse)
def mandated_activity_add(
    request: Request,
    name: str = Form(...),
    trigger: str = Form(...),
    days: int = Form(...),
    prep_days: int = Form(0),
    activity_type: str = Form("Meeting"),
    subject: str = Form(""),
):
    full = dict(cfg.load_config())
    rules = list(full.get("mandated_activities", []))
    rules.append({
        "name": name.strip(),
        "trigger": trigger,
        "days": days,
        "prep_days": prep_days,
        "activity_type": activity_type,
        "subject": subject.strip() or f"{name.strip()} — {{{{policy_type}}}}",
    })
    full["mandated_activities"] = rules
    cfg.save_config(full)
    cfg.reload_config()
    return _render_mandated_activities(request)


@router.post("/mandated-activities/remove", response_class=HTMLResponse)
def mandated_activity_remove(request: Request, name: str = Form(...)):
    full = dict(cfg.load_config())
    rules = [r for r in full.get("mandated_activities", []) if r.get("name") != name]
    full["mandated_activities"] = rules
    cfg.save_config(full)
    cfg.reload_config()
    return _render_mandated_activities(request)


def _render_mandated_activities(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("settings/_mandated_activities_rows.html", {
        "request": request,
        "mandated_activities": cfg.get("mandated_activities", []),
        "renewal_milestones": cfg.get("renewal_milestones", []),
        "activity_types": cfg.get("activity_types", []),
    })


# ── Mandated Activities — inline PATCH editor ────────────────────────────────

@router.patch("/mandated-activities/{index}", response_class=HTMLResponse)
async def save_mandated_activity(request: Request, index: int):
    """PATCH a single field on a mandated activity by index."""
    form = await request.form()
    activities = list(cfg.get("mandated_activities", []))
    if 0 <= index < len(activities):
        for key in ("name", "trigger", "days", "prep_days", "activity_type",
                     "checklist_milestone", "prep_notes", "subject"):
            if key in form:
                val = form[key]
                if key in ("days", "prep_days"):
                    val = int(val) if val else 0
                activities[index][key] = val
        full = dict(cfg.load_config())
        full["mandated_activities"] = activities
        cfg.save_config(full)
        cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs">&#10003;</span>')


# ── Milestone Profiles — inline PATCH editor ─────────────────────────────────

@router.patch("/milestone-profiles/{index}", response_class=HTMLResponse)
async def save_milestone_profile(request: Request, index: int):
    """PATCH name, description, or milestones on a milestone profile."""
    form = await request.form()
    profiles = list(cfg.get("milestone_profiles", []))
    if 0 <= index < len(profiles):
        if "name" in form:
            profiles[index]["name"] = form["name"]
        if "description" in form:
            profiles[index]["description"] = form["description"]
        if "milestones" in form:
            profiles[index]["milestones"] = [
                m.strip() for m in form["milestones"].split(",") if m.strip()
            ]
        full = dict(cfg.load_config())
        full["milestone_profiles"] = profiles
        cfg.save_config(full)
        cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs">&#10003;</span>')


# ── Timeline Engine — PATCH editor ───────────────────────────────────────────

@router.patch("/timeline-engine", response_class=HTMLResponse)
async def save_timeline_engine(request: Request):
    """PATCH timeline engine scheduling params and risk alert toggles."""
    form = await request.form()
    full = dict(cfg.load_config())

    te = dict(full.get("timeline_engine", {}))
    for key in ("minimum_gap_days", "drift_threshold_days"):
        if key in form:
            te[key] = int(form[key])
    if "compression_threshold" in form:
        te["compression_threshold"] = float(form["compression_threshold"])
    full["timeline_engine"] = te

    rat = dict(full.get("risk_alert_thresholds", {}))
    for key in ("at_risk_notify", "critical_notify", "critical_auto_draft"):
        if key in form:
            rat[key] = form[key] == "true"
    full["risk_alert_thresholds"] = rat

    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs">&#10003;</span>')


# ── Carrier Aliases ───────────────────────────────────────────────────────────

@router.post("/carrier-aliases/add-group")
def carrier_alias_add_group(request: Request, canonical: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    if canonical and canonical not in aliases:
        aliases[canonical] = []
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings?tab=carriers", status_code=303)


@router.post("/carrier-aliases/add-alias")
def carrier_alias_add(request: Request, canonical: str = Form(...), alias: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    alias = alias.strip()
    if canonical in aliases and alias and alias not in aliases[canonical]:
        aliases[canonical].append(alias)
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings?tab=carriers", status_code=303)


@router.post("/carrier-aliases/remove-alias")
def carrier_alias_remove(request: Request, canonical: str = Form(...), alias: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    if canonical in aliases and alias in aliases[canonical]:
        aliases[canonical].remove(alias)
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings?tab=carriers", status_code=303)


@router.post("/carrier-aliases/rename-group")
def carrier_alias_rename_group(request: Request, old_name: str = Form(...), new_name: str = Form(...)):
    """Rename a carrier group's canonical name."""
    new_name = new_name.strip()
    aliases = cfg.get("carrier_aliases", {})
    if old_name in aliases and new_name and new_name not in aliases:
        aliases[new_name] = aliases.pop(old_name)
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings?tab=carriers", status_code=303)


@router.post("/carrier-aliases/remove-group")
def carrier_alias_remove_group(request: Request, canonical: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    if canonical in aliases:
        del aliases[canonical]
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings?tab=carriers", status_code=303)


# ── DB Health Actions ─────────────────────────────────────────────────────────

@router.post("/db/backup")
def db_backup_now():
    """Create a backup immediately."""
    from policydb.db import _auto_backup
    try:
        _auto_backup(DB_PATH, max_backups=cfg.get("backup_retention_count", 30))
        backup_path = _HEALTH_STATUS.get("last_backup", "")
        verified = _HEALTH_STATUS.get("last_backup_verified", False)
        count = _HEALTH_STATUS.get("backup_count", 0)
        return JSONResponse({
            "ok": True,
            "message": f"Backup created ({count} total). {'Verified.' if verified else 'Verification failed.'}",
            "backup": backup_path,
            "verified": verified,
            "count": count,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/db/cleanup-legacy")
def db_cleanup_legacy():
    """Delete old-format backup files from ~/.policydb/ root."""
    try:
        legacy = sorted(DB_PATH.parent.glob("policydb.sqlite.backup_*"))
        count = 0
        for f in legacy:
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        return JSONResponse({"ok": True, "message": f"Removed {count} legacy backup(s)."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/db/vacuum")
def db_vacuum(conn=Depends(get_db)):
    """Run VACUUM to reclaim space."""
    try:
        before = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        conn.execute("VACUUM")
        after = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        saved = before - after
        return JSONResponse({
            "ok": True,
            "before_bytes": before,
            "after_bytes": after,
            "saved_bytes": saved,
            "message": f"VACUUM complete. Saved {saved // 1024} KB.",
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/db/download")
def db_download(conn=Depends(get_db)):
    """Checkpoint WAL and serve the database file as a download."""
    import datetime
    from starlette.responses import FileResponse
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    if not DB_PATH.exists():
        return JSONResponse({"ok": False, "error": "Database file not found"}, status_code=404)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"policydb_{ts}.sqlite"
    return FileResponse(
        str(DB_PATH),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/db/purge-preview")
def db_purge_preview(conn=Depends(get_db)):
    """Return counts of archived records that would be purged."""
    try:
        n_clients = conn.execute("SELECT COUNT(*) FROM clients WHERE archived=1").fetchone()[0]
        n_policies = conn.execute("SELECT COUNT(*) FROM policies WHERE archived=1").fetchone()[0]
        return JSONResponse({"ok": True, "clients": n_clients, "policies": n_policies})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/db/purge")
def db_purge(conn=Depends(get_db)):
    """Export archived records to XLSX then permanently delete them and VACUUM."""
    import datetime
    try:
        import openpyxl
    except ImportError:
        return JSONResponse({"ok": False, "error": "openpyxl is required for purge export."}, status_code=500)

    try:
        archived_clients = conn.execute("SELECT * FROM clients WHERE archived=1").fetchall()
        archived_policies = conn.execute("SELECT * FROM policies WHERE archived=1").fetchall()

        n_clients = len(archived_clients)
        n_policies = len(archived_policies)

        if n_clients == 0 and n_policies == 0:
            return JSONResponse({"ok": True, "message": "No archived records to purge.", "clients": 0, "policies": 0})

        # Build export XLSX
        exports_dir = DB_PATH.parent / "exports"
        exports_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        export_path = exports_dir / f"purged_archive_{ts}.xlsx"

        wb = openpyxl.Workbook()

        if archived_clients:
            ws_c = wb.active
            ws_c.title = "Clients"
            client_cols = list(archived_clients[0].keys())
            ws_c.append(client_cols)
            for row in archived_clients:
                ws_c.append([row[c] for c in client_cols])

        if archived_policies:
            ws_p = wb.create_sheet("Policies")
            policy_cols = list(archived_policies[0].keys())
            ws_p.append(policy_cols)
            for row in archived_policies:
                ws_p.append([row[c] for c in policy_cols])

        wb.save(str(export_path))

        # Delete archived records — clean up FK dependencies first
        # Get IDs for targeted cleanup
        archived_policy_ids = [r["id"] for r in archived_policies]
        archived_client_ids = [r["id"] for r in archived_clients]
        archived_policy_uids = [r["policy_uid"] for r in archived_policies]

        # Clean up child records referencing archived policies (NO ACTION FKs)
        if archived_policy_ids:
            ph = ",".join("?" * len(archived_policy_ids))
            conn.execute(f"UPDATE activity_log SET policy_id = NULL WHERE policy_id IN ({ph})", archived_policy_ids)
            conn.execute(f"UPDATE policies SET program_id = NULL WHERE program_id IN ({ph})", archived_policy_ids)

        # Clean up child records referencing archived clients (NO ACTION FKs)
        if archived_client_ids:
            ch = ",".join("?" * len(archived_client_ids))
            conn.execute(f"DELETE FROM activity_log WHERE client_id IN ({ch})", archived_client_ids)
            conn.execute(f"DELETE FROM premium_history WHERE client_id IN ({ch})", archived_client_ids)
            conn.execute(f"DELETE FROM project_notes WHERE client_id IN ({ch})", archived_client_ids)
            conn.execute(f"DELETE FROM client_risks WHERE client_id IN ({ch})", archived_client_ids)
            conn.execute(f"DELETE FROM client_request_items WHERE bundle_id IN (SELECT id FROM client_request_bundles WHERE client_id IN ({ch}))", archived_client_ids)
            conn.execute(f"DELETE FROM client_request_bundles WHERE client_id IN ({ch})", archived_client_ids)
            conn.execute(f"UPDATE inbox SET client_id = NULL WHERE client_id IN ({ch})", archived_client_ids)

        # Now delete the archived records (CASCADE FKs handle the rest)
        conn.execute("DELETE FROM policies WHERE archived=1")
        conn.execute("DELETE FROM clients WHERE archived=1")
        conn.commit()

        # VACUUM
        conn.execute("VACUUM")

        return JSONResponse({
            "ok": True,
            "message": f"Purged {n_clients} clients and {n_policies} policies. Export saved to {export_path.name}.",
            "clients": n_clients,
            "policies": n_policies,
            "export": str(export_path),
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── SQL Console ───────────────────────────────────────────────────────────────

_SQL_EXAMPLES = [
    {"label": "Policies expiring in 30 days", "sql": "SELECT policy_uid, c.name, policy_type, carrier, expiration_date FROM policies p JOIN clients c ON p.client_id = c.id WHERE p.archived = 0 AND p.expiration_date BETWEEN date('now') AND date('now', '+30 days') ORDER BY expiration_date"},
    {"label": "Clients with no activity in 90 days", "sql": "SELECT c.name, MAX(a.activity_date) AS last_activity FROM clients c LEFT JOIN activity_log a ON a.client_id = c.id WHERE c.archived = 0 GROUP BY c.id HAVING last_activity < date('now', '-90 days') OR last_activity IS NULL"},
    {"label": "Duplicate contacts by email", "sql": "SELECT email, GROUP_CONCAT(name, ', ') AS names, COUNT(*) AS cnt FROM contacts WHERE email IS NOT NULL AND email != '' GROUP BY LOWER(email) HAVING cnt > 1"},
    {"label": "Orphaned records (FK violations)", "sql": "PRAGMA foreign_key_check"},
    {"label": "Premium by carrier", "sql": "SELECT carrier, COUNT(*) AS policies, SUM(premium) AS total_premium FROM policies WHERE archived = 0 AND carrier IS NOT NULL GROUP BY carrier ORDER BY total_premium DESC"},
    {"label": "Activity hours by client (30 days)", "sql": "SELECT c.name, COALESCE(SUM(a.duration_hours), 0) AS hours, COUNT(*) AS activities FROM activity_log a JOIN clients c ON a.client_id = c.id WHERE a.activity_date >= date('now', '-30 days') GROUP BY c.id ORDER BY hours DESC"},
    {"label": "All archived records", "sql": "SELECT 'Policy' AS type, policy_uid AS id, policy_type AS name FROM policies WHERE archived = 1 UNION ALL SELECT 'Client', CAST(id AS TEXT), name FROM clients WHERE archived = 1"},
    {"label": "Coverage types in use", "sql": "SELECT policy_type, COUNT(*) AS cnt FROM policies WHERE archived = 0 GROUP BY policy_type ORDER BY cnt DESC"},
    {"label": "Thread summary", "sql": "SELECT thread_id, COUNT(*) AS attempts, MIN(subject) AS subject, GROUP_CONCAT(disposition, ' -> ') AS dispositions FROM activity_log WHERE thread_id IS NOT NULL GROUP BY thread_id ORDER BY MAX(activity_date) DESC LIMIT 20"},
]


@router.post("/db/query")
async def db_query(request: Request, conn=Depends(get_db)):
    """Execute a SQL query and return results as JSON."""
    import time
    body = await request.json()
    sql = body.get("sql", "").strip()
    write_mode = body.get("write_mode", False)

    if not sql:
        return JSONResponse({"ok": False, "error": "No query provided"})

    if not write_mode:
        first_word = sql.split()[0].upper() if sql.split() else ""
        if first_word not in ("SELECT", "PRAGMA", "EXPLAIN", "WITH"):
            return JSONResponse({"ok": False, "error": "Read-only mode. Enable write mode for INSERT/UPDATE/DELETE."})

    try:
        start = time.time()
        cursor = conn.execute(sql)
        if cursor.description:
            columns = [d[0] for d in cursor.description]
            rows = [list(r) for r in cursor.fetchmany(1000)]
        else:
            columns = []
            rows = []
            conn.commit()
        duration = round((time.time() - start) * 1000, 1)
        return JSONResponse({"ok": True, "columns": columns, "rows": rows, "row_count": len(rows), "duration_ms": duration})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.get("/db/query/export")
def db_query_export(sql: str = "", conn=Depends(get_db)):
    """Execute a read-only query and return results as a CSV download."""
    import csv
    import io
    from starlette.responses import Response

    if not sql.strip():
        return HTMLResponse("No query provided", status_code=400)

    first_word = sql.strip().split()[0].upper()
    if first_word not in ("SELECT", "PRAGMA", "EXPLAIN", "WITH"):
        return HTMLResponse("Read-only queries only for export", status_code=400)

    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for r in rows:
            writer.writerow(list(r))
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="query_results.csv"'},
        )
    except Exception as e:
        return HTMLResponse(f"Query error: {e}", status_code=400)


# ── Schema Reference ──────────────────────────────────────────────────────────


@router.get("/db/schema")
def db_schema(table: str = "", conn=Depends(get_db)):
    """Return table list or column/index info for a specific table."""
    if not table:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        return JSONResponse({"tables": tables})
    try:
        columns = [
            {"name": r[1], "type": r[2], "nullable": not r[3], "default": r[4], "pk": bool(r[5])}
            for r in conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
        ]
        indexes = [
            {"name": r[1], "unique": bool(r[2])}
            for r in conn.execute(f"PRAGMA index_list({table})").fetchall()  # noqa: S608
        ]
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        return JSONResponse({"table": table, "columns": columns, "indexes": indexes, "row_count": row_count})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Logo Upload ──────────────────────────────────────────────────────────────


@router.post("/logo", response_class=HTMLResponse)
async def upload_logo(request: Request, file: UploadFile = File(...)):
    """Upload a logo image for PDF compliance reports."""
    import shutil

    logo_path = Path(cfg.get("report_logo_path"))
    logo_path.parent.mkdir(parents=True, exist_ok=True)
    with open(logo_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        from PIL import Image

        img = Image.open(logo_path)
        img.thumbnail((300, 80))
        img.save(logo_path)
    except ImportError:
        pass
    # Return updated preview
    import time

    return HTMLResponse(
        f'<img src="/settings/logo/preview?t={int(time.time())}" class="max-h-12 mb-3" alt="Logo">'
        f'<button hx-delete="/settings/logo" hx-target="#logo-preview" hx-swap="innerHTML"'
        f' class="text-xs text-red-500 hover:underline block mt-1">Remove Logo</button>'
        f'<form hx-post="/settings/logo" hx-target="#logo-preview" hx-swap="innerHTML"'
        f' hx-encoding="multipart/form-data" class="mt-3">'
        f'<input type="file" name="file" accept="image/*" class="text-xs">'
        f'<button type="submit" class="ml-2 text-xs text-marsh hover:underline">Replace</button>'
        f'</form>'
    )


@router.delete("/logo", response_class=HTMLResponse)
def remove_logo():
    """Remove the uploaded logo."""
    logo_path = Path(cfg.get("report_logo_path"))
    if logo_path.exists():
        logo_path.unlink()
    return HTMLResponse(
        '<p class="text-xs text-gray-400 mb-3">No logo uploaded</p>'
        '<form hx-post="/settings/logo" hx-target="#logo-preview" hx-swap="innerHTML"'
        ' hx-encoding="multipart/form-data" class="mt-2">'
        '<input type="file" name="file" accept="image/*" class="text-xs">'
        '<button type="submit" class="ml-2 text-xs text-marsh hover:underline">Upload</button>'
        '</form>'
    )


@router.get("/logo/preview")
def logo_preview():
    """Serve the uploaded logo for preview."""
    from fastapi.responses import FileResponse

    logo_path = Path(cfg.get("report_logo_path"))
    if logo_path.exists():
        return FileResponse(logo_path, media_type="image/png")
    return Response(status_code=404)


# ── Audit Log ────────────────────────────────────────────────────────────────

_AUDIT_TABLES = [
    "clients", "policies", "activity_log", "contacts",
    "inbox", "policy_milestones", "saved_notes",
]

_AUDIT_OPERATIONS = ["INSERT", "UPDATE", "DELETE"]


@router.get("/audit-log")
def audit_log_page(request: Request):
    """Redirect to the unified logs page (audit tab)."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/logs?tab=audit", status_code=302)
