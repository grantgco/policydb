"""Reconciliation routes — compare an uploaded CSV against PolicyDB policies."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from policydb.queries import get_client_by_name
from policydb.reconciler import (
    _compare_fields,
    _find_likely_pairs,
    build_reconcile_xlsx,
    find_candidates,
    parse_uploaded_csv,
    reconcile,
    summarize,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/reconcile")


def _load_db_policies(conn, client_id: int, scope: str) -> list[dict]:
    """Query PolicyDB policies for the reconcile comparison side."""
    conditions = ["p.archived = 0"]
    params: list = []

    if client_id > 0:
        conditions.append("p.client_id = ?")
        params.append(client_id)

    if scope == "active":
        conditions.append("p.expiration_date >= date('now', '-365 days')")

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"""SELECT p.policy_uid, c.name AS client_name, p.policy_type, p.carrier,
                   p.policy_number, p.effective_date, p.expiration_date,
                   p.premium, p.limit_amount, p.deductible, p.client_id
            FROM policies p
            JOIN clients c ON p.client_id = c.id
            WHERE {where}
            ORDER BY c.name, p.expiration_date""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("", response_class=HTMLResponse)
def reconcile_index(request: Request, conn=Depends(get_db)):
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    return templates.TemplateResponse("reconcile/index.html", {
        "request": request,
        "active": "reconcile",
        "all_clients": [dict(c) for c in all_clients],
        "results": None,
        "summary": None,
        "pairs": [],
        "warnings": [],
        "errors": [],
        "selected_client_id": 0,
        "selected_scope": "active",
        "filename": "",
        "run_date": "",
    })


@router.post("", response_class=HTMLResponse)
async def reconcile_run(
    request: Request,
    file: UploadFile = File(...),
    client_id: int = Form(0),
    scope: str = Form("active"),
    column_mapping_json: str = Form(""),
    conn=Depends(get_db),
):
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    ctx = {
        "request": request,
        "active": "reconcile",
        "all_clients": [dict(c) for c in all_clients],
        "selected_client_id": client_id,
        "selected_scope": scope,
        "filename": file.filename or "",
        "run_date": date.today().isoformat(),
        "results": None,
        "summary": None,
        "warnings": [],
        "errors": [],
    }

    import json as _json
    col_map = {}
    if column_mapping_json:
        try:
            col_map = {k: v for k, v in _json.loads(column_mapping_json).items() if v and v != "ignore"}
        except Exception:
            pass

    content = await file.read()
    if not content:
        ctx["errors"] = ["Uploaded file is empty."]
        return templates.TemplateResponse("reconcile/index.html", ctx)

    ext_rows, warnings = parse_uploaded_csv(content, column_mapping=col_map or None)
    ctx["warnings"] = warnings
    ctx["column_mapping_json"] = column_mapping_json

    if not ext_rows:
        ctx["errors"] = ["No usable rows found in the uploaded file. Ensure it has client_name (or insured) and policy columns."]
        return templates.TemplateResponse("reconcile/index.html", ctx)

    db_rows = _load_db_policies(conn, client_id, scope)
    results = reconcile(ext_rows, db_rows)

    missing_rows = [r for r in results if r.status == "MISSING"]
    extra_rows = [r for r in results if r.status == "EXTRA"]

    ctx["results"] = results
    ctx["summary"] = summarize(results)
    ctx["pairs"] = _find_likely_pairs(missing_rows, extra_rows)
    return templates.TemplateResponse("reconcile/index.html", ctx)


@router.get("/suggest", response_class=HTMLResponse)
def reconcile_suggest(
    request: Request,
    client_name: str = "",
    policy_type: str = "",
    carrier: str = "",
    effective_date: str = "",
    expiration_date: str = "",
    policy_number: str = "",
    premium: str = "",
    limit_amount: str = "",
    deductible: str = "",
    row_uid: str = "",
    scope: str = "active",
    client_id: int = 0,
    conn=Depends(get_db),
):
    """HTMX: return candidate PolicyDB policies for a MISSING ext row."""
    from policydb.importer import _parse_currency
    ext_row = {
        "client_name": client_name,
        "policy_type": policy_type,
        "carrier": carrier,
        "effective_date": effective_date,
        "expiration_date": expiration_date,
        "policy_number": policy_number,
        "premium": _parse_currency(premium),
        "limit_amount": _parse_currency(limit_amount),
        "deductible": _parse_currency(deductible),
    }
    db_rows = _load_db_policies(conn, client_id, scope)
    candidates = find_candidates(ext_row, db_rows, limit=8)
    return templates.TemplateResponse("reconcile/_suggest_panel.html", {
        "request": request,
        "ext": ext_row,
        "candidates": candidates,
        "row_uid": row_uid,
        "scope": scope,
        "client_id": client_id,
    })


@router.get("/suggest-extra", response_class=HTMLResponse)
def reconcile_suggest_extra(
    request: Request,
    policy_uid: str = "",
    client_name: str = "",
    policy_type: str = "",
    carrier: str = "",
    effective_date: str = "",
    expiration_date: str = "",
    scope: str = "active",
    client_id: int = 0,
    conn=Depends(get_db),
):
    """HTMX: for an EXTRA DB policy, show MISSING-like rows from a re-run as candidate matches."""
    # We don't have the original upload here, so we just show policy info for context
    db_row = conn.execute(
        """SELECT p.policy_uid, c.name AS client_name, p.policy_type, p.carrier,
                  p.policy_number, p.effective_date, p.expiration_date, p.premium,
                  p.limit_amount, p.deductible, p.client_id
           FROM policies p JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (policy_uid,),
    ).fetchone()
    return templates.TemplateResponse("reconcile/_extra_panel.html", {
        "request": request,
        "db": dict(db_row) if db_row else {},
        "policy_uid": policy_uid,
    })


@router.post("/confirm-match", response_class=HTMLResponse)
def reconcile_confirm_match(
    request: Request,
    row_uid: str = Form(...),
    policy_uid: str = Form(...),
    # ext fields sent as hidden form values
    ext_client_name: str = Form(""),
    ext_policy_type: str = Form(""),
    ext_carrier: str = Form(""),
    ext_policy_number: str = Form(""),
    ext_effective_date: str = Form(""),
    ext_expiration_date: str = Form(""),
    ext_premium: str = Form(""),
    ext_limit_amount: str = Form(""),
    ext_deductible: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: user manually confirms a MISSING row matches a specific PolicyDB policy.
    Returns a MATCH or DIFF row replacing the original MISSING row."""
    from policydb.importer import _parse_currency

    db_row_raw = conn.execute(
        """SELECT p.policy_uid, c.name AS client_name, p.policy_type, p.carrier,
                  p.policy_number, p.effective_date, p.expiration_date,
                  p.premium, p.limit_amount, p.deductible, p.client_id
           FROM policies p JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (policy_uid.upper(),),
    ).fetchone()

    if not db_row_raw:
        return HTMLResponse(f'<tr id="row-{row_uid}"><td colspan="9" class="px-4 py-3 text-xs text-red-500">Policy {policy_uid} not found.</td></tr>')

    db = dict(db_row_raw)
    ext = {
        "client_name": ext_client_name,
        "policy_type": ext_policy_type,
        "carrier": ext_carrier,
        "policy_number": ext_policy_number,
        "effective_date": ext_effective_date,
        "expiration_date": ext_expiration_date,
        "premium": _parse_currency(ext_premium),
        "limit_amount": _parse_currency(ext_limit_amount),
        "deductible": _parse_currency(ext_deductible),
    }

    diff_fields, score = _compare_fields(ext, db)
    status = "DIFF" if diff_fields else "MATCH"

    status_classes = {
        "MATCH": "bg-green-100 text-green-700",
        "DIFF": "bg-amber-100 text-amber-700",
    }
    badge_class = status_classes[status]
    diff_badges = "".join(
        f'<span class="inline-block text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded mr-1">{f}</span>'
        for f in diff_fields
    ) or '<span class="text-xs text-green-500">✓ manually confirmed</span>'

    premium_val = db.get("premium") or 0
    try:
        premium_fmt = f"${float(premium_val):,.0f}"
    except (TypeError, ValueError):
        premium_fmt = "—"

    return HTMLResponse(
        f'<tr id="row-{row_uid}" data-status="{status}" data-uid="{row_uid}" class="bg-{"green" if status == "MATCH" else "amber"}-50/40">'
        f'<td class="px-3 py-3 text-gray-300 text-xs">✓</td>'
        f'<td class="px-4 py-3" colspan="2"><span class="text-xs font-semibold px-2 py-0.5 rounded {badge_class}">{status}</span>'
        f' <span class="text-xs text-gray-400 ml-1">manually matched</span></td>'
        f'<td class="px-4 py-3 font-medium text-gray-800 text-sm">{db.get("client_name","")}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{db.get("policy_type","")}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{db.get("carrier","")}</td>'
        f'<td class="px-4 py-3 text-xs font-mono text-gray-500">{db.get("policy_number","") or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{db.get("expiration_date","")}</td>'
        f'<td class="px-4 py-3 text-right tabular-nums text-gray-700">{premium_fmt}</td>'
        f'<td class="px-4 py-3">{diff_badges} '
        f'<a href="/policies/{db["policy_uid"]}/edit" class="text-xs text-marsh hover:underline ml-1">{db["policy_uid"]} →</a></td>'
        f'</tr>'
    )


@router.post("/archive/{policy_uid}", response_class=HTMLResponse)
def reconcile_archive(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX: archive an EXTRA policy and return a confirmation row."""
    conn.execute("UPDATE policies SET archived=1 WHERE policy_uid=?", (policy_uid.upper(),))
    conn.commit()
    return HTMLResponse(
        f'<tr class="bg-gray-50"><td colspan="9" class="px-4 py-3 text-xs text-gray-400 italic">'
        f'Policy {policy_uid.upper()} archived.</td></tr>'
    )


@router.get("/create-form", response_class=HTMLResponse)
def reconcile_create_form(
    request: Request,
    client_name: str = "",
    policy_type: str = "",
    carrier: str = "",
    effective_date: str = "",
    expiration_date: str = "",
    premium: str = "",
    limit_amount: str = "",
    deductible: str = "",
    policy_number: str = "",
    description: str = "",
    project_name: str = "",
    placement_colleague: str = "",
    underwriter_name: str = "",
    commission_rate: str = "",
    row_uid: str = "",
    conn=Depends(get_db),
):
    """HTMX: render an inline quick-create form for a MISSING row."""
    client_row = get_client_by_name(conn, client_name) if client_name else None
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    from policydb import config as cfg
    return templates.TemplateResponse("reconcile/_create_form.html", {
        "request": request,
        "ext": {
            "client_name": client_name,
            "policy_type": policy_type,
            "carrier": carrier,
            "effective_date": effective_date,
            "expiration_date": expiration_date,
            "premium": premium,
            "limit_amount": limit_amount,
            "deductible": deductible,
            "policy_number": policy_number,
            "description": description,
            "project_name": project_name,
            "placement_colleague": placement_colleague,
            "underwriter_name": underwriter_name,
            "commission_rate": commission_rate,
        },
        "matched_client": dict(client_row) if client_row else None,
        "all_clients": [dict(c) for c in all_clients],
        "policy_types": cfg.get("policy_types", []),
        "row_uid": row_uid,
    })


@router.post("/create", response_class=HTMLResponse)
def reconcile_create(
    request: Request,
    row_uid: str = Form(""),
    client_id: int = Form(...),
    policy_type: str = Form(...),
    carrier: str = Form(...),
    effective_date: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(0.0),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    policy_number: str = Form(""),
    description: str = Form(""),
    project_name: str = Form(""),
    placement_colleague: str = Form(""),
    underwriter_name: str = Form(""),
    commission_rate: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: create a new policy from a MISSING reconcile row, return confirmation."""
    from policydb.db import next_policy_uid
    from policydb import config as cfg

    uid = next_policy_uid(conn)
    account_exec = cfg.get("default_account_exec", "Grant")

    def _f(v):
        try: return float(v) if v else 0.0
        except (ValueError, TypeError): return 0.0

    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, project_name, placement_colleague, underwriter_name,
            commission_rate, account_exec)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uid, client_id, policy_type, carrier, policy_number or None,
            effective_date, expiration_date, premium,
            _f(limit_amount), _f(deductible),
            description or None, project_name or None,
            placement_colleague or None, underwriter_name or None,
            _f(commission_rate), account_exec,
        ),
    )
    conn.commit()

    client_name = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()["name"]
    return HTMLResponse(
        f'<tr id="row-{row_uid}" class="bg-green-50">'
        f'<td class="px-3 py-2 text-xs text-gray-300">✓</td>'
        f'<td colspan="2" class="px-4 py-2"><span class="text-xs font-semibold text-green-700 px-2 py-0.5 rounded bg-green-100">CREATED</span></td>'
        f'<td class="px-4 py-2 text-sm font-medium text-gray-800">{client_name}</td>'
        f'<td class="px-4 py-2 text-xs text-gray-600">{policy_type}</td>'
        f'<td class="px-4 py-2 text-xs text-gray-600">{carrier}</td>'
        f'<td class="px-4 py-2 text-xs font-mono text-gray-500">{policy_number or "—"}</td>'
        f'<td class="px-4 py-2 text-xs whitespace-nowrap text-gray-600">{expiration_date}</td>'
        f'<td class="px-4 py-2 text-right tabular-nums text-gray-700">${premium:,.0f}</td>'
        f'<td class="px-4 py-2"><a href="/policies/{uid}/edit" class="text-xs text-marsh hover:underline">{uid} →</a></td>'
        f'</tr>'
    )


@router.get("/edit-form/{policy_uid}", response_class=HTMLResponse)
def reconcile_edit_form(
    request: Request,
    policy_uid: str,
    row_uid: str = "",
    ext_carrier: str = "",
    ext_policy_number: str = "",
    ext_effective_date: str = "",
    ext_expiration_date: str = "",
    ext_premium: str = "",
    ext_limit_amount: str = "",
    ext_deductible: str = "",
    ext_policy_type: str = "",
    conn=Depends(get_db),
):
    """HTMX: render inline edit form for a DIFF row, pre-filled with current DB values."""
    db = conn.execute(
        """SELECT p.*, c.name AS client_name FROM policies p
           JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (policy_uid.upper(),),
    ).fetchone()
    if not db:
        return HTMLResponse(f"Policy {policy_uid} not found.", status_code=404)
    from policydb import config as cfg
    return templates.TemplateResponse("reconcile/_edit_form.html", {
        "request": request,
        "db": dict(db),
        "ext": {
            "carrier": ext_carrier,
            "policy_number": ext_policy_number,
            "effective_date": ext_effective_date,
            "expiration_date": ext_expiration_date,
            "premium": ext_premium,
            "limit_amount": ext_limit_amount,
            "deductible": ext_deductible,
            "policy_type": ext_policy_type,
        },
        "row_uid": row_uid,
        "policy_types": cfg.get("policy_types", []),
    })


@router.post("/update/{policy_uid}", response_class=HTMLResponse)
def reconcile_update(
    request: Request,
    policy_uid: str,
    row_uid: str = Form(""),
    policy_type: str = Form(""),
    carrier: str = Form(""),
    policy_number: str = Form(""),
    effective_date: str = Form(""),
    expiration_date: str = Form(""),
    premium: str = Form(""),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    project_name: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save inline edits for a DIFF row and return a confirmation row."""
    def _f(v):
        try: return float(v) if v else None
        except (ValueError, TypeError): return None

    conn.execute(
        """UPDATE policies SET
           policy_type=?, carrier=?, policy_number=?,
           effective_date=?, expiration_date=?,
           premium=?, limit_amount=?, deductible=?,
           project_name=?
           WHERE policy_uid=?""",
        (
            policy_type, carrier, policy_number or None,
            effective_date, expiration_date,
            _f(premium), _f(limit_amount), _f(deductible),
            project_name or None,
            policy_uid.upper(),
        ),
    )
    conn.commit()

    client_name = conn.execute(
        """SELECT c.name FROM policies p JOIN clients c ON p.client_id=c.id WHERE p.policy_uid=?""",
        (policy_uid.upper(),),
    ).fetchone()["name"]

    premium_fmt = f"${float(premium):,.0f}" if premium else "—"
    project_line = f'<p class="text-gray-400 text-xs italic">{project_name}</p>' if project_name else ""

    return HTMLResponse(
        f'<tr id="row-{row_uid}" class="bg-green-50">'
        f'<td class="px-3 py-3 text-gray-300 text-xs">✓</td>'
        f'<td class="px-4 py-3"><span class="text-xs font-semibold px-2 py-0.5 rounded bg-green-100 text-green-700">UPDATED</span></td>'
        f'<td class="px-4 py-3 font-medium text-gray-800 text-sm">{client_name}{project_line}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{policy_type}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{carrier}</td>'
        f'<td class="px-4 py-3 text-xs font-mono text-gray-500">{policy_number or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{expiration_date}</td>'
        f'<td class="px-4 py-3 text-right tabular-nums text-gray-700">{premium_fmt}</td>'
        f'<td class="px-4 py-3"><a href="/policies/{policy_uid.upper()}/edit" class="text-xs text-marsh hover:underline">{policy_uid.upper()} →</a></td>'
        f'</tr>'
    )


@router.post("/apply/{policy_uid}", response_class=HTMLResponse)
def reconcile_apply(
    request: Request,
    policy_uid: str,
    row_uid: str = Form(""),
    carrier: str = Form(""),
    policy_number: str = Form(""),
    effective_date: str = Form(""),
    expiration_date: str = Form(""),
    premium: str = Form(""),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    policy_type: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: apply ext upload values to a DIFF policy (one-click update)."""
    def _f(v):
        try: return float(v) if v else None
        except (ValueError, TypeError): return None

    # Build update for non-empty ext fields only
    updates: dict = {}
    if carrier: updates["carrier"] = carrier
    if policy_number: updates["policy_number"] = policy_number
    if effective_date: updates["effective_date"] = effective_date
    if expiration_date: updates["expiration_date"] = expiration_date
    if premium: updates["premium"] = _f(premium)
    if limit_amount: updates["limit_amount"] = _f(limit_amount)
    if deductible: updates["deductible"] = _f(deductible)
    if policy_type: updates["policy_type"] = policy_type

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE policies SET {set_clause} WHERE policy_uid=?",
            list(updates.values()) + [policy_uid.upper()],
        )
        conn.commit()

    client_name = conn.execute(
        """SELECT c.name FROM policies p JOIN clients c ON p.client_id=c.id WHERE p.policy_uid=?""",
        (policy_uid.upper(),),
    ).fetchone()["name"]

    eff_exp = expiration_date or "—"
    premium_fmt = f"${float(premium):,.0f}" if premium else "—"
    ptype = policy_type or "—"
    carr = carrier or "—"

    return HTMLResponse(
        f'<tr id="row-{row_uid}" class="bg-green-50">'
        f'<td class="px-3 py-3 text-gray-300 text-xs">✓</td>'
        f'<td class="px-4 py-3"><span class="text-xs font-semibold px-2 py-0.5 rounded bg-green-100 text-green-700">UPDATED</span></td>'
        f'<td class="px-4 py-3 font-medium text-gray-800 text-sm">{client_name}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{ptype}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{carr}</td>'
        f'<td class="px-4 py-3 text-xs font-mono text-gray-500">{policy_number or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{eff_exp}</td>'
        f'<td class="px-4 py-3 text-right tabular-nums text-gray-700">{premium_fmt}</td>'
        f'<td class="px-4 py-3"><a href="/policies/{policy_uid.upper()}/edit" class="text-xs text-marsh hover:underline">{policy_uid.upper()} →</a></td>'
        f'</tr>'
    )


@router.post("/confirm-pair", response_class=HTMLResponse)
def reconcile_confirm_pair(
    request: Request,
    pair_id: int = Form(...),
    extra_policy_uid: str = Form(...),
    ext_client_name: str = Form(""),
    ext_policy_type: str = Form(""),
    ext_carrier: str = Form(""),
    ext_policy_number: str = Form(""),
    ext_effective_date: str = Form(""),
    ext_expiration_date: str = Form(""),
    ext_premium: str = Form(""),
    ext_limit_amount: str = Form(""),
    ext_deductible: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: user confirms a MISSING/EXTRA pair represents the same policy.
    Returns a status card replacing the pair card."""
    from policydb.importer import _parse_currency

    db_row_raw = conn.execute(
        """SELECT p.policy_uid, c.name AS client_name, p.policy_type, p.carrier,
                  p.policy_number, p.effective_date, p.expiration_date,
                  p.premium, p.limit_amount, p.deductible, p.client_id
           FROM policies p JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (extra_policy_uid.upper(),),
    ).fetchone()

    if not db_row_raw:
        return HTMLResponse(
            f'<div id="pair-card-{pair_id}" class="p-3 text-xs text-red-500 border border-red-200 rounded-lg">'
            f'Policy {extra_policy_uid} not found.</div>'
        )

    db = dict(db_row_raw)
    ext = {
        "client_name": ext_client_name,
        "policy_type": ext_policy_type,
        "carrier": ext_carrier,
        "policy_number": ext_policy_number,
        "effective_date": ext_effective_date,
        "expiration_date": ext_expiration_date,
        "premium": _parse_currency(ext_premium),
        "limit_amount": _parse_currency(ext_limit_amount),
        "deductible": _parse_currency(ext_deductible),
    }

    diff_fields, score = _compare_fields(ext, db)
    status = "DIFF" if diff_fields else "MATCH"

    badge_class = "bg-green-100 text-green-700" if status == "MATCH" else "bg-amber-100 text-amber-700"
    bg_color = "green" if status == "MATCH" else "amber"
    diff_badges = "".join(
        f'<span class="inline-block text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded mr-1">{f}</span>'
        for f in diff_fields
    ) or '<span class="text-xs text-green-500">✓ confirmed match</span>'

    try:
        premium_fmt = f"${float(db.get('premium') or 0):,.0f}"
    except (TypeError, ValueError):
        premium_fmt = "—"

    return HTMLResponse(
        f'<div id="pair-card-{pair_id}" class="p-3 bg-{bg_color}-50 border border-{bg_color}-200 rounded-lg flex items-center gap-3">'
        f'<span class="text-xs font-semibold px-2 py-0.5 rounded {badge_class}">{status}</span>'
        f'<span class="text-sm font-medium text-gray-800">{db.get("client_name","")}</span>'
        f'<span class="text-xs text-gray-400">·</span>'
        f'<span class="text-xs text-gray-600">{db.get("policy_type","")}</span>'
        f'<span class="text-xs text-gray-400">·</span>'
        f'<span class="text-xs text-gray-600">{db.get("expiration_date","")}</span>'
        f'<span class="text-xs text-gray-400">·</span>'
        f'<span class="tabular-nums text-xs text-gray-600">{premium_fmt}</span>'
        f'<span class="ml-2">{diff_badges}</span>'
        f'<a href="/policies/{db["policy_uid"]}/edit" class="text-xs text-marsh hover:underline ml-auto">{db["policy_uid"]} →</a>'
        f'</div>'
    )


@router.post("/ignore-pair", response_class=HTMLResponse)
def reconcile_ignore_pair(
    request: Request,
    pair_id: int = Form(...),
):
    """HTMX: dismiss a MISSING/EXTRA pair suggestion without action."""
    return HTMLResponse(f'<div id="pair-card-{pair_id}"></div>')


@router.post("/download")
async def reconcile_download(
    file: UploadFile = File(...),
    client_id: int = Form(0),
    scope: str = Form("active"),
    column_mapping_json: str = Form(""),
    conn=Depends(get_db),
):
    import json as _json
    col_map = {}
    if column_mapping_json:
        try:
            col_map = {k: v for k, v in _json.loads(column_mapping_json).items() if v and v != "ignore"}
        except Exception:
            pass
    content = await file.read()
    ext_rows, _ = parse_uploaded_csv(content, column_mapping=col_map or None)
    db_rows = _load_db_policies(conn, client_id, scope)
    results = reconcile(ext_rows, db_rows)
    xlsx = build_reconcile_xlsx(results, run_date=date.today().isoformat(), filename=file.filename or "")
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reconcile_{date.today()}.xlsx"'},
    )
