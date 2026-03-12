"""Settings routes — manage configurable dropdown lists and email subjects."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import policydb.config as cfg
from policydb.config import reorder_list_item
from policydb.web.app import templates

router = APIRouter(prefix="/settings")

EDITABLE_LISTS: dict[str, str] = {
    "policy_types": "Lines of Business",
    "coverage_forms": "Coverage Forms",
    "renewal_statuses": "Renewal Statuses",
    "industry_segments": "Industry Segments",
    "activity_types": "Activity Types",
    "renewal_milestones": "Renewal Checklist",
}


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request):
    lists = {key: cfg.get(key, []) for key in EDITABLE_LISTS}
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "active": "settings",
        "lists": lists,
        "list_labels": EDITABLE_LISTS,
        "excluded_statuses": cfg.get("renewal_statuses_excluded", []),
        "email_subject_policy": cfg.get("email_subject_policy", ""),
        "email_subject_client": cfg.get("email_subject_client", ""),
        "email_subject_followup": cfg.get("email_subject_followup", ""),
    })


@router.post("/email-subject", response_class=HTMLResponse)
def save_email_subject(key: str = Form(...), value: str = Form(...)):
    _allowed = {"email_subject_policy", "email_subject_client", "email_subject_followup"}
    if key in _allowed:
        full = dict(cfg.load_config())
        full[key] = value
        cfg.save_config(full)
        cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs">Saved</span>')


@router.post("/list/add", response_class=HTMLResponse)
def list_add(request: Request, key: str = Form(...), item: str = Form(...)):
    item = item.strip()
    if key in EDITABLE_LISTS and item:
        cfg.add_list_item(key, item)
    return _render_list(request, key)


@router.post("/list/remove", response_class=HTMLResponse)
def list_remove(request: Request, key: str = Form(...), item: str = Form(...)):
    if key in EDITABLE_LISTS:
        cfg.remove_list_item(key, item)
    return _render_list(request, key)


@router.post("/list/reorder", response_class=HTMLResponse)
def list_reorder(request: Request, key: str = Form(...), item: str = Form(...), direction: str = Form(...)):
    if key in EDITABLE_LISTS and direction in ("up", "down"):
        reorder_list_item(key, item, direction)
    return _render_list(request, key)


def _render_list(request: Request, key: str) -> HTMLResponse:
    ctx = {
        "request": request,
        "key": key,
        "label": EDITABLE_LISTS.get(key, key),
        "items": cfg.get(key, []),
    }
    if key == "renewal_statuses":
        ctx["excluded_items"] = cfg.get("renewal_statuses_excluded", [])
    return templates.TemplateResponse("settings/_list_card.html", ctx)


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
