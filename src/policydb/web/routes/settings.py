"""Settings routes — manage configurable dropdown lists and email subjects."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
}


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, conn=Depends(get_db)):
    lists = {key: cfg.get(key, []) for key in EDITABLE_LISTS}

    # DB health data
    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    wal_path = str(DB_PATH) + "-wal"
    wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    backup_dir = DB_PATH.parent / "backups"
    backups = (
        sorted(backup_dir.glob("policydb_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if backup_dir.exists()
        else []
    )
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

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "active": "settings",
        "lists": lists,
        "list_labels": EDITABLE_LISTS,
        "excluded_statuses": cfg.get("renewal_statuses_excluded", []),
        "email_subject_policy": cfg.get("email_subject_policy", ""),
        "email_subject_client": cfg.get("email_subject_client", ""),
        "email_subject_followup": cfg.get("email_subject_followup", ""),
        "email_subject_request": cfg.get("email_subject_request", ""),
        "email_subject_request_all": cfg.get("email_subject_request_all", ""),
        "escalation_thresholds": cfg.get("escalation_thresholds", {}),
        "readiness_thresholds": cfg.get("readiness_thresholds", {}),
        "readiness_weights": cfg.get("readiness_weights", {}),
        "readiness_status_scores": cfg.get("readiness_status_scores", {}),
        "readiness_milestone_weights": cfg.get("readiness_milestone_weights", {}),
        "readiness_activity_tiers": cfg.get("readiness_activity_tiers", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "renewal_milestones": cfg.get("renewal_milestones", []),
        "fu_workload": cfg.get("followup_workload_thresholds", {"warning": 3, "danger": 5}),
        "auto_review_enabled": cfg.get("auto_review_enabled", True),
        "auto_review_field_threshold": cfg.get("auto_review_field_threshold", 3),
        "auto_review_activity_threshold": cfg.get("auto_review_activity_threshold", 3),
        "mandated_activities": cfg.get("mandated_activities", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
        # DB health
        "db_health": _HEALTH_STATUS,
        "db_size": db_size,
        "wal_size": wal_size,
        "db_counts": db_counts,
        "backups": backups,
        "max_migration": max_migration,
        "backup_dir": backup_dir,
    })


@router.post("/email-subject", response_class=HTMLResponse)
def save_email_subject(key: str = Form(...), value: str = Form(...)):
    _allowed = {"email_subject_policy", "email_subject_client", "email_subject_followup", "email_subject_request", "email_subject_request_all"}
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


@router.post("/dispositions/add")
def disposition_add(request: Request, label: str = Form(...), default_days: int = Form(0)):
    """Add a new disposition to follow_up_dispositions."""
    lst = cfg.get("follow_up_dispositions", [])
    if any(d["label"] == label for d in lst):
        return RedirectResponse("/settings", status_code=303)
    lst.append({"label": label, "default_days": max(0, default_days)})
    full = dict(cfg.load_config())
    full["follow_up_dispositions"] = lst
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings", status_code=303)


@router.post("/dispositions/remove")
def disposition_remove(request: Request, label: str = Form(...)):
    """Remove a disposition by label."""
    lst = cfg.get("follow_up_dispositions", [])
    lst = [d for d in lst if d["label"] != label]
    full = dict(cfg.load_config())
    full["follow_up_dispositions"] = lst
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings", status_code=303)


@router.post("/dispositions/reorder")
def disposition_reorder(request: Request, label: str = Form(...), direction: str = Form(...)):
    """Move a disposition up or down."""
    lst = cfg.get("follow_up_dispositions", [])
    idx = next((i for i, d in enumerate(lst) if d["label"] == label), None)
    if idx is None:
        return RedirectResponse("/settings", status_code=303)
    if direction == "up" and idx > 0:
        lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
    elif direction == "down" and idx < len(lst) - 1:
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
    full = dict(cfg.load_config())
    full["follow_up_dispositions"] = lst
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings", status_code=303)


@router.patch("/dispositions/update")
async def disposition_update(request: Request):
    """Update default_days for a disposition."""
    body = await request.json()
    label = body.get("label", "")
    default_days = int(body.get("default_days", 0))
    lst = cfg.get("follow_up_dispositions", [])
    for d in lst:
        if d["label"] == label:
            d["default_days"] = max(0, default_days)
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


@router.post("/auto-review", response_class=HTMLResponse)
def save_auto_review(
    auto_review_enabled: str = Form(""),
    auto_review_field_threshold: int = Form(3),
    auto_review_activity_threshold: int = Form(3),
):
    full = dict(cfg.load_config())
    full["auto_review_enabled"] = bool(auto_review_enabled)
    full["auto_review_field_threshold"] = max(2, min(10, auto_review_field_threshold))
    full["auto_review_activity_threshold"] = max(1, min(10, auto_review_activity_threshold))
    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse(
        '<span id="ar-status" class="text-xs text-green-600 font-medium">Saved</span>'
    )


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
    activity_type: str = Form("Meeting"),
    subject: str = Form(""),
):
    full = dict(cfg.load_config())
    rules = list(full.get("mandated_activities", []))
    rules.append({
        "name": name.strip(),
        "trigger": trigger,
        "days": days,
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
    rules = cfg.get("mandated_activities", [])
    activity_types = cfg.get("activity_types", [])
    html_parts = []
    for r in rules:
        trigger_label = {"days_before_expiry": "before expiry", "days_after_effective": "after effective", "days_after_binding": "after binding"}.get(r.get("trigger"), r.get("trigger", ""))
        html_parts.append(
            f'<div class="flex items-center justify-between py-2 border-b border-gray-50">'
            f'<div class="text-sm text-gray-800">'
            f'<span class="font-medium">{r["name"]}</span>'
            f' &mdash; <span class="text-gray-500">{r["days"]}d {trigger_label}</span>'
            f' &middot; <span class="text-gray-400">{r.get("activity_type", "Meeting")}</span>'
            f'</div>'
            f'<form hx-post="/settings/mandated-activities/remove" hx-target="#mandated-activities-list" hx-swap="innerHTML">'
            f'<input type="hidden" name="name" value="{r["name"]}">'
            f'<button type="submit" class="text-xs text-red-400 hover:text-red-600">&times;</button>'
            f'</form></div>'
        )
    if not html_parts:
        html_parts.append('<p class="text-xs text-gray-400 py-2">No mandated activities configured.</p>')
    return HTMLResponse("".join(html_parts))


# ── DB Health Actions ─────────────────────────────────────────────────────────

@router.post("/db/backup")
def db_backup_now():
    """Force a backup immediately (skip recency check)."""
    from policydb.db import _auto_backup
    try:
        _auto_backup(DB_PATH, max_backups=cfg.get("backup_retention_count", 30), force=True)
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

        # Delete archived records in a transaction
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
