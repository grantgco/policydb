"""Client routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from policydb import config as cfg
from policydb.queries import (
    get_activities,
    get_all_clients,
    get_client_by_id,
    get_client_summary,
    get_policies_for_client,
    full_text_search,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/clients")


@router.get("", response_class=HTMLResponse)
def client_list(request: Request, conn=Depends(get_db)):
    clients = [dict(r) for r in get_all_clients(conn)]
    return templates.TemplateResponse("clients/list.html", {
        "request": request,
        "active": "clients",
        "clients": clients,
        "q": "",
    })


@router.get("/search", response_class=HTMLResponse)
def client_search(request: Request, q: str = "", conn=Depends(get_db)):
    """HTMX partial: filtered client table rows."""
    if q.strip():
        raw = full_text_search(conn, q.strip())
        client_ids = {r["id"] for r in raw["clients"]}
        all_clients = [dict(r) for r in get_all_clients(conn)]
        clients = [c for c in all_clients if c["id"] in client_ids or q.lower() in c["name"].lower()]
    else:
        clients = [dict(r) for r in get_all_clients(conn)]
    return templates.TemplateResponse("clients/_table_rows.html", {
        "request": request,
        "clients": clients,
    })


@router.get("/new", response_class=HTMLResponse)
def client_new_form(request: Request):
    return templates.TemplateResponse("clients/edit.html", {
        "request": request,
        "active": "clients",
        "client": None,
        "industry_segments": cfg.get("industry_segments"),
    })


@router.post("/new")
def client_new_post(
    request: Request,
    name: str = Form(...),
    industry_segment: str = Form(...),
    cn_number: str = Form(""),
    primary_contact: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO clients (name, industry_segment, cn_number, primary_contact, contact_email,
           contact_phone, address, notes, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, industry_segment, cn_number or None, primary_contact or None, contact_email or None,
         contact_phone or None, address or None, notes or None, account_exec),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{cursor.lastrowid}", status_code=303)


@router.get("/{client_id}", response_class=HTMLResponse)
def client_detail(request: Request, client_id: int, conn=Depends(get_db)):
    from collections import defaultdict
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    summary = get_client_summary(conn, client_id)
    policies = [dict(p) for p in get_policies_for_client(conn, client_id)]
    activities = [dict(a) for a in get_activities(conn, client_id=client_id, days=90)]
    activity_types = cfg.get("activity_types")

    # Group policies by project_name; blank → "Corporate / Standalone" (sorted last)
    groups: dict[str, list] = defaultdict(list)
    for p in policies:
        groups[p.get("project_name") or ""].append(p)
    policy_groups = sorted(groups.items(), key=lambda x: ("\xff" if not x[0] else x[0].lower()))

    return templates.TemplateResponse("clients/detail.html", {
        "request": request,
        "active": "clients",
        "client": dict(client),
        "summary": dict(summary) if summary else {},
        "policy_groups": policy_groups,
        "activities": activities,
        "activity_types": activity_types,
        "renewal_statuses": cfg.get("renewal_statuses"),
    })


@router.get("/{client_id}/edit", response_class=HTMLResponse)
def client_edit_form(request: Request, client_id: int, conn=Depends(get_db)):
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    return templates.TemplateResponse("clients/edit.html", {
        "request": request,
        "active": "clients",
        "client": dict(client),
        "industry_segments": cfg.get("industry_segments"),
    })


@router.post("/{client_id}/edit")
def client_edit_post(
    request: Request,
    client_id: int,
    name: str = Form(...),
    industry_segment: str = Form(...),
    cn_number: str = Form(""),
    primary_contact: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """UPDATE clients SET name=?, industry_segment=?, cn_number=?, primary_contact=?,
           contact_email=?, contact_phone=?, address=?, notes=?
           WHERE id=?""",
        (name, industry_segment, cn_number or None, primary_contact or None, contact_email or None,
         contact_phone or None, address or None, notes or None, client_id),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@router.get("/{client_id}/export/schedule")
def export_schedule(client_id: int, fmt: str = "md", conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import export_schedule_csv, export_schedule_json, export_schedule_md
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    if fmt == "csv":
        content = export_schedule_csv(conn, client_id)
        media_type = "text/csv"
        ext = "csv"
    elif fmt == "json":
        content = export_schedule_json(conn, client_id, client["name"])
        media_type = "application/json"
        ext = "json"
    else:
        content = export_schedule_md(conn, client_id, client["name"])
        media_type = "text/markdown"
        ext = "md"
    safe = client["name"].lower().replace(" ", "_")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}_schedule.{ext}"'},
    )


@router.get("/{client_id}/export/llm")
def export_llm(client_id: int, conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import export_llm_client_md
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    content = export_llm_client_md(conn, client_id)
    safe = client["name"].lower().replace(" ", "_")
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe}_llm.md"'},
    )
