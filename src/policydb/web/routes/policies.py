"""Policy routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from policydb import config as cfg
from policydb.queries import get_all_policies, get_client_by_id, get_policy_by_uid
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/policies")

def _renewal_statuses() -> list[str]:
    return cfg.get("renewal_statuses", ["Not Started", "In Progress", "Pending Bind", "Bound"])


@router.get("/{policy_uid}/edit", response_class=HTMLResponse)
def policy_edit_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    return templates.TemplateResponse("policies/edit.html", {
        "request": request,
        "active": "",
        "policy": dict(policy),
        "policy_types": cfg.get("policy_types"),
        "coverage_forms": cfg.get("coverage_forms"),
        "renewal_statuses": _renewal_statuses(),
    })


@router.post("/{policy_uid}/edit")
def policy_edit_post(
    request: Request,
    policy_uid: str,
    action: str = Form("save"),
    policy_type: str = Form(...),
    carrier: str = Form(...),
    policy_number: str = Form(""),
    effective_date: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(...),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    description: str = Form(""),
    coverage_form: str = Form(""),
    layer_position: str = Form("Primary"),
    tower_group: str = Form(""),
    is_standalone: str = Form("0"),
    placement_colleague: str = Form(""),
    underwriter_name: str = Form(""),
    underwriter_contact: str = Form(""),
    renewal_status: str = Form("Not Started"),
    commission_rate: str = Form(""),
    prior_premium: str = Form(""),
    notes: str = Form(""),
    project_name: str = Form(""),
    conn=Depends(get_db),
):
    def _float(v: str):
        try:
            return float(v) if v.strip() else None
        except ValueError:
            return None

    uid = policy_uid.upper()
    conn.execute(
        """UPDATE policies SET
           policy_type=?, carrier=?, policy_number=?,
           effective_date=?, expiration_date=?, premium=?,
           limit_amount=?, deductible=?, description=?,
           coverage_form=?, layer_position=?, tower_group=?,
           is_standalone=?, placement_colleague=?,
           underwriter_name=?, underwriter_contact=?,
           renewal_status=?, commission_rate=?, prior_premium=?, notes=?,
           project_name=?
           WHERE policy_uid=?""",
        (
            policy_type, carrier, policy_number or None,
            effective_date, expiration_date, premium,
            _float(limit_amount), _float(deductible), description or None,
            coverage_form or None, layer_position or "Primary", tower_group or None,
            1 if is_standalone == "1" else 0,
            placement_colleague or None, underwriter_name or None,
            underwriter_contact or None, renewal_status,
            _float(commission_rate), _float(prior_premium), notes or None,
            project_name or None,
            uid,
        ),
    )
    conn.commit()

    if action == "save_continue":
        return RedirectResponse(f"/policies/{uid}/edit", status_code=303)

    # Redirect back to client detail
    policy = get_policy_by_uid(conn, uid)
    client_id = policy["client_id"] if policy else 0
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@router.post("/{policy_uid}/status", response_class=HTMLResponse)
def policy_update_status(
    request: Request,
    policy_uid: str,
    status: str = Form(...),
    conn=Depends(get_db),
):
    """HTMX endpoint: update renewal status, return updated badge partial."""
    uid = policy_uid.upper()
    if status not in _renewal_statuses():
        status = _renewal_statuses()[0]
    conn.execute(
        "UPDATE policies SET renewal_status=? WHERE policy_uid=?",
        (status, uid),
    )
    conn.commit()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    p = dict(policy)
    return templates.TemplateResponse("policies/_status_badge.html", {
        "request": request,
        "p": p,
        "renewal_statuses": _renewal_statuses(),
    })


@router.get("/new", response_class=HTMLResponse)
def policy_new_form(request: Request, client: int = 0, conn=Depends(get_db)):
    client_row = get_client_by_id(conn, client) if client else None
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    return templates.TemplateResponse("policies/new.html", {
        "request": request,
        "active": "",
        "client": dict(client_row) if client_row else None,
        "all_clients": [dict(c) for c in all_clients],
        "policy_types": cfg.get("policy_types"),
        "renewal_statuses": _renewal_statuses(),
    })


@router.post("/new")
def policy_new_post(
    request: Request,
    client_id: int = Form(...),
    policy_type: str = Form(...),
    carrier: str = Form(...),
    policy_number: str = Form(""),
    effective_date: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(...),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    description: str = Form(""),
    renewal_status: str = Form("Not Started"),
    placement_colleague: str = Form(""),
    project_name: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.db import next_policy_uid

    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    uid = next_policy_uid(conn)
    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, renewal_status, placement_colleague, account_exec, project_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, client_id, policy_type, carrier, policy_number or None,
         effective_date, expiration_date, premium,
         _float(limit_amount), _float(deductible),
         description or None, renewal_status,
         placement_colleague or None, account_exec, project_name or None),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
