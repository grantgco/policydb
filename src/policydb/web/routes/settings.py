"""Settings routes — manage configurable dropdown lists."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import policydb.config as cfg
from policydb.web.app import templates

router = APIRouter(prefix="/settings")

EDITABLE_LISTS: dict[str, str] = {
    "policy_types": "Lines of Business",
    "coverage_forms": "Coverage Forms",
    "renewal_statuses": "Renewal Statuses",
    "industry_segments": "Industry Segments",
    "activity_types": "Activity Types",
}


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request):
    lists = {key: cfg.get(key, []) for key in EDITABLE_LISTS}
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "active": "settings",
        "lists": lists,
        "list_labels": EDITABLE_LISTS,
    })


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


def _render_list(request: Request, key: str) -> HTMLResponse:
    return templates.TemplateResponse("settings/_list_card.html", {
        "request": request,
        "key": key,
        "label": EDITABLE_LISTS.get(key, key),
        "items": cfg.get(key, []),
    })
