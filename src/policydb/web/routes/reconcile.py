"""Reconciliation routes — compare an uploaded CSV/XLSX against PolicyDB policies."""

from __future__ import annotations

import io
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from policydb.queries import get_client_by_name
from policydb.utils import normalize_carrier, normalize_coverage_type, normalize_policy_number
from policydb.reconciler import (
    _compare_fields,
    _find_likely_pairs,
    build_reconcile_xlsx,
    find_candidates,
    parse_uploaded_file,
    program_reconcile_summary,
    reconcile,
    summarize,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/reconcile")

# In-memory cache for last reconciliation (single-user local app)
import time as _time
import uuid as _uuid
_RESULT_CACHE: dict[str, tuple[bytes, float]] = {}  # token → (xlsx_bytes, timestamp)
_MISSING_CACHE: dict[str, tuple[list[dict], float]] = {}  # token → (missing_ext_rows, timestamp)
_LAST_MISSING_TOKEN: str = ""  # most recent token for batch-create fallback

def _cache_cleanup():
    """Remove cache entries older than 1 hour."""
    cutoff = _time.time() - 3600
    for k in list(_RESULT_CACHE):
        if _RESULT_CACHE[k][1] < cutoff:
            del _RESULT_CACHE[k]
    for k in list(_MISSING_CACHE):
        if _MISSING_CACHE[k][1] < cutoff:
            del _MISSING_CACHE[k]


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
        f"""SELECT p.id, p.policy_uid, c.name AS client_name, p.policy_type, p.carrier,
                   p.policy_number, p.effective_date, p.expiration_date,
                   p.premium, p.limit_amount, p.deductible, p.client_id,
                   p.first_named_insured,
                   p.is_program, p.program_carriers, p.program_carrier_count
            FROM policies p
            JOIN clients c ON p.client_id = c.id
            WHERE {where}
            ORDER BY c.name, p.expiration_date""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/preview-columns")
async def reconcile_preview_columns(file: UploadFile = File(...)):
    """Return column headers and sample data from an uploaded file for column mapping UI."""
    from fastapi.responses import JSONResponse
    content = await file.read()
    headers = []
    sample_rows: list[list[str]] = []  # first 3 data rows for preview
    filename = file.filename or ""
    if filename.lower().endswith(('.xlsx', '.xls')) or content[:2] == b'PK':
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            first_row = next(rows_iter, None)
            if first_row:
                headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(first_row)]
                for _, data_row in zip(range(3), rows_iter):
                    sample_rows.append([str(v).strip() if v is not None else "" for v in data_row])
            wb.close()
        except Exception:
            pass
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        import csv as _csv
        reader = _csv.reader(io.StringIO(text))
        first_row = next(reader, None)
        if first_row:
            headers = [h.strip() for h in first_row]
            for _, data_row in zip(range(3), reader):
                sample_rows.append([v.strip() for v in data_row])
    return JSONResponse({"headers": headers, "sample_rows": sample_rows})


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
    date_priority: str = Form(""),
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

    ext_rows, warnings = parse_uploaded_file(content, column_mapping=col_map or None, filename=file.filename or "")
    ctx["warnings"] = warnings
    ctx["column_mapping_json"] = column_mapping_json

    if not ext_rows:
        ctx["errors"] = ["No usable rows found in the uploaded file. Ensure it has client_name (or insured) and policy columns."]
        return templates.TemplateResponse("reconcile/index.html", ctx)

    db_rows = _load_db_policies(conn, client_id, scope)

    # Attach program carrier rows for structured matching
    program_ids = [r["id"] for r in db_rows if r.get("is_program")]
    _carrier_map = {}
    if program_ids:
        _pc_rows = conn.execute(
            f"SELECT * FROM program_carriers WHERE program_id IN ({','.join('?' * len(program_ids))})",
            program_ids,
        ).fetchall()
        for _pcr in _pc_rows:
            _carrier_map.setdefault(_pcr["program_id"], []).append(dict(_pcr))
    for r in db_rows:
        if r.get("is_program"):
            r["_program_carrier_rows"] = _carrier_map.get(r["id"], [])

    results = reconcile(ext_rows, db_rows, date_priority=bool(date_priority), single_client=bool(client_id))

    missing_rows = [r for r in results if r.status == "MISSING"]
    extra_rows = [r for r in results if r.status == "EXTRA"]

    # Generate token first — used for both MISSING cache and XLSX cache
    download_token = str(_uuid.uuid4())

    # Cache MISSING rows for batch create (keyed by download token)
    global _LAST_MISSING_TOKEN
    _MISSING_CACHE[download_token] = ([r.ext for r in missing_rows if r.ext], _time.time())
    _LAST_MISSING_TOKEN = download_token

    # Cache XLSX for download-without-reupload
    _cache_cleanup()
    xlsx_bytes = build_reconcile_xlsx(results, run_date=date.today().isoformat(), filename=file.filename or "")
    _RESULT_CACHE[download_token] = (xlsx_bytes, _time.time())

    ctx["results"] = results
    ctx["summary"] = summarize(results)
    ctx["pairs"] = _find_likely_pairs(missing_rows, extra_rows)
    ctx["download_token"] = download_token
    ctx["program_summary"] = program_reconcile_summary(results, carrier_map=_carrier_map)
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
    candidates = find_candidates(ext_row, db_rows, limit=8, single_client=bool(client_id))
    # Fallback: if no scored matches and we have a client filter, show ALL client policies
    all_client_policies = []
    if not candidates and client_id:
        all_client_policies = [(db, 0.0) for db in db_rows]
        # Sort by effective date proximity to the ext row's date
        if effective_date:
            from policydb.reconciler import _date_delta_days
            all_client_policies.sort(key=lambda x: _date_delta_days(effective_date, x[0].get("effective_date", "")) or 9999)
    return templates.TemplateResponse("reconcile/_suggest_panel.html", {
        "request": request,
        "ext": ext_row,
        "candidates": candidates,
        "all_client_policies": all_client_policies,
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
        return HTMLResponse(f'<tr id="row-{row_uid}"><td colspan="10" class="px-4 py-3 text-xs text-red-500">Policy {policy_uid} not found.</td></tr>')

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

    diff_fields, _, fillable, score = _compare_fields(ext, db)
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
        f'<td class="px-4 py-3"><span class="text-xs font-semibold px-2 py-0.5 rounded {badge_class}">{status}</span>'
        f' <span class="text-xs text-gray-400 ml-1">manually matched</span></td>'
        f'<td class="px-4 py-3 font-medium text-gray-800 text-sm">{db.get("client_name","")}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{db.get("policy_type","")}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{db.get("carrier","")}</td>'
        f'<td class="px-4 py-3 text-xs font-mono text-gray-500">{db.get("policy_number","") or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{db.get("effective_date","") or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{db.get("expiration_date","")}</td>'
        f'<td class="px-4 py-3 text-right tabular-nums text-gray-700">{premium_fmt}</td>'
        f'<td class="px-4 py-3">{diff_badges} '
        f'<a href="/policies/{db["policy_uid"]}/edit" class="text-xs text-marsh hover:underline ml-1">{db["policy_uid"]} →</a></td>'
        f'</tr>'
    )


@router.post("/fill/{policy_uid}", response_class=HTMLResponse)
async def reconcile_fill(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """HTMX: fill empty fields on a matched policy from imported values."""
    from policydb.importer import _parse_currency
    form = await request.form()

    _CURRENCY = {"premium", "limit_amount", "deductible"}
    _TEXT = {"carrier", "policy_number"}
    _DATE = {"effective_date", "expiration_date"}
    _ALLOWED = _CURRENCY | _TEXT | _DATE

    updates = []
    params = []
    filled_names = []
    for field in _ALLOWED:
        val = form.get(field, "").strip()
        if not val:
            continue
        if field in _CURRENCY:
            parsed = _parse_currency(val)
            if parsed and parsed > 0:
                updates.append(f"{field} = ?")
                params.append(parsed)
                filled_names.append(field.replace("_", " ").title())
        elif field in _DATE:
            updates.append(f"{field} = ?")
            params.append(val)
            filled_names.append(field.replace("_", " ").title())
        elif field in _TEXT:
            updates.append(f"{field} = ?")
            params.append(val)
            filled_names.append(field.replace("_", " ").title())

    if updates:
        params.append(policy_uid.upper())
        conn.execute(
            f"UPDATE policies SET {', '.join(updates)} WHERE policy_uid = ?",
            params,
        )
        conn.commit()

    label = ", ".join(filled_names) if filled_names else "No fields"
    return HTMLResponse(
        f'<span class="text-xs text-green-600 font-medium">Updated {label}</span>'
    )


@router.post("/archive/{policy_uid}", response_class=HTMLResponse)
def reconcile_archive(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX: archive an EXTRA policy and return a confirmation row."""
    conn.execute("UPDATE policies SET archived=1 WHERE policy_uid=?", (policy_uid.upper(),))
    conn.commit()
    return HTMLResponse(
        f'<tr class="bg-gray-50"><td colspan="10" class="px-4 py-3 text-xs text-gray-400 italic">'
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
    is_program: str = Form("0"),
    conn=Depends(get_db),
):
    """HTMX: create a new policy from a MISSING reconcile row, return confirmation."""
    from policydb.db import next_policy_uid
    from policydb import config as cfg

    uid = next_policy_uid(conn)
    account_exec = cfg.get("default_account_exec", "Grant")
    pgm = 1 if is_program == "1" else 0
    policy_type = normalize_coverage_type(policy_type)
    carrier = normalize_carrier(carrier) if carrier else ""
    policy_number = normalize_policy_number(policy_number) if policy_number else ""

    def _f(v):
        try: return float(v) if v else 0.0
        except (ValueError, TypeError): return 0.0

    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, project_name, underwriter_name,
            commission_rate, account_exec,
            is_program)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uid, client_id, policy_type, carrier, policy_number or None,
            effective_date, expiration_date, premium,
            _f(limit_amount), _f(deductible),
            description or None, project_name or None,
            underwriter_name or None,
            _f(commission_rate), account_exec,
            pgm,
        ),
    )
    conn.commit()

    # If this is a program, insert the carrier as the first program_carriers row
    if pgm:
        _pgm_row = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (uid,)).fetchone()
        if _pgm_row and carrier.strip():
            conn.execute(
                """INSERT INTO program_carriers (program_id, carrier, policy_number, premium, limit_amount, sort_order)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (_pgm_row["id"], carrier.strip(), policy_number or None, premium, _f(limit_amount)),
            )
            conn.commit()

    # Create structured contact records for placement colleague and underwriter
    _policy_row = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (uid,)).fetchone()
    if _policy_row:
        _pid = _policy_row["id"]
        _pc_name = (placement_colleague or "").strip()
        if _pc_name:
            from policydb.queries import get_or_create_contact, assign_contact_to_policy
            _pc_cid = get_or_create_contact(conn, _pc_name)
            assign_contact_to_policy(conn, _pc_cid, _pid, is_placement_colleague=1)
        _uw_name = (underwriter_name or "").strip()
        if _uw_name:
            from policydb.queries import get_or_create_contact, assign_contact_to_policy
            _uw_cid = get_or_create_contact(conn, _uw_name)
            assign_contact_to_policy(conn, _uw_cid, _pid, role="Underwriter")
        conn.commit()

    client_name = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()["name"]
    return HTMLResponse(
        f'<tr id="row-{row_uid}" class="bg-green-50">'
        f'<td class="px-3 py-2 text-xs text-gray-300">✓</td>'
        f'<td class="px-4 py-2"><span class="text-xs font-semibold text-green-700 px-2 py-0.5 rounded bg-green-100">CREATED</span></td>'
        f'<td class="px-4 py-2 text-sm font-medium text-gray-800">{client_name}</td>'
        f'<td class="px-4 py-2 text-xs text-gray-600">{policy_type}</td>'
        f'<td class="px-4 py-2 text-xs text-gray-600">{carrier}</td>'
        f'<td class="px-4 py-2 text-xs font-mono text-gray-500">{policy_number or "—"}</td>'
        f'<td class="px-4 py-2 text-xs whitespace-nowrap text-gray-600">{effective_date}</td>'
        f'<td class="px-4 py-2 text-xs whitespace-nowrap text-gray-600">{expiration_date}</td>'
        f'<td class="px-4 py-2 text-right tabular-nums text-gray-700">${premium:,.0f}</td>'
        f'<td class="px-4 py-2"><a href="/policies/{uid}/edit" class="text-xs text-marsh hover:underline">{uid} →</a></td>'
        f'</tr>'
    )


@router.post("/batch-create-program", response_class=HTMLResponse)
async def batch_create_program(
    request: Request,
    conn=Depends(get_db),
):
    """Create a single program record from multiple selected MISSING rows."""
    from policydb.db import next_policy_uid
    from policydb import config as _cfg
    import json as _json

    form = await request.form()
    selected_json = form.get("selected_rows", "[]")
    try:
        selected_indices = sorted(set(_json.loads(selected_json)))
    except Exception:
        return HTMLResponse('<p class="text-xs text-red-500">Invalid selection.</p>')

    if not selected_indices:
        return HTMLResponse('<p class="text-xs text-amber-600">No rows selected.</p>')

    missing_entry = _MISSING_CACHE.get(_LAST_MISSING_TOKEN)
    missing_rows_list = missing_entry[0] if missing_entry else []

    # Gather data from selected rows
    carriers = []
    total_premium = 0.0
    total_limit = 0.0
    eff_date = None
    exp_date = None
    policy_type = None
    client_id_str = form.get("program_client_id", "")

    for idx in selected_indices:
        if idx < 0 or idx >= len(missing_rows_list):
            continue
        ext = missing_rows_list[idx]
        c = normalize_carrier((ext.get("carrier") or "").strip())
        if c and c not in carriers:
            carriers.append(c)
        try:
            total_premium += float(ext.get("premium") or 0)
        except (TypeError, ValueError):
            pass
        try:
            total_limit += float(ext.get("limit_amount") or 0)
        except (TypeError, ValueError):
            pass
        if not eff_date:
            eff_date = ext.get("effective_date")
        if not exp_date:
            exp_date = ext.get("expiration_date")
        if not policy_type:
            policy_type = ext.get("policy_type", "Program")

    if not client_id_str:
        return HTMLResponse('<p class="text-xs text-red-500">No client selected.</p>')

    # Use form override for policy_type if provided
    policy_type = normalize_coverage_type(form.get("program_policy_type", policy_type) or "Program")

    uid = next_policy_uid(conn)
    account_exec = _cfg.get("default_account_exec", "Grant")
    carrier_list = ", ".join(carriers)

    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date,
            premium, limit_amount, account_exec, is_program)
           VALUES (?,?,?,?,?,?,?,?,?,1)""",
        (uid, int(client_id_str), policy_type,
         carriers[0] if carriers else None,
         eff_date or None, exp_date or None,
         total_premium, total_limit if total_limit else None,
         account_exec),
    )
    policy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert carrier rows from selected import data
    for sort_idx, idx in enumerate(selected_indices):
        if idx < 0 or idx >= len(missing_rows_list):
            continue
        ext = missing_rows_list[idx]
        c = normalize_carrier((ext.get("carrier") or "").strip())
        pn = (ext.get("policy_number") or "").strip()
        try:
            prem = float(ext.get("premium") or 0)
        except (TypeError, ValueError):
            prem = 0
        try:
            lim = float(ext.get("limit_amount") or 0)
        except (TypeError, ValueError):
            lim = 0
        conn.execute(
            """INSERT INTO program_carriers (program_id, carrier, policy_number, premium, limit_amount, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (policy_id, c, pn, prem, lim, sort_idx),
        )

    conn.commit()

    return HTMLResponse(
        f'<div class="bg-green-50 border border-green-200 rounded-lg p-4">'
        f'<p class="text-sm font-medium text-green-700">Program created: {uid}</p>'
        f'<p class="text-xs text-green-600 mt-1">{policy_type} · {len(carriers)} carriers · ${total_premium:,.0f} premium</p>'
        f'<p class="text-xs text-gray-500 mt-1">Carriers: {carrier_list}</p>'
        f'<a href="/policies/{uid}/edit" class="text-xs text-marsh hover:underline mt-1 block">Edit program →</a>'
        f'</div>'
    )


@router.get("/batch-create-review", response_class=HTMLResponse)
def batch_create_review(
    request: Request,
    client_id: int = 0,
    scope: str = "active",
    conn=Depends(get_db),
):
    """HTMX: show review table of all MISSING rows before batch creation."""
    # Resolve missing rows from token-keyed cache
    _cache_cleanup()
    missing_entry = _MISSING_CACHE.get(_LAST_MISSING_TOKEN)
    if not missing_entry or not missing_entry[0]:
        return HTMLResponse('<p class="text-xs text-gray-400 italic">No MISSING rows to create. Re-run reconciliation first.</p>')
    missing_rows_list = missing_entry[0]

    from policydb import config as cfg
    from policydb.reconciler import _normalize_coverage

    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    client_lookup = {c["name"].lower(): dict(c) for c in all_clients}

    rows = []
    for i, ext in enumerate(missing_rows_list):
        # Try to auto-match client
        client_name = ext.get("client_name", "")
        matched = client_lookup.get(client_name.lower())
        if not matched and client_name:
            # Fuzzy fallback — try to find best matching client
            from rapidfuzz import fuzz as _fuzz
            best_score = 0
            for cname, cdict in client_lookup.items():
                score = _fuzz.WRatio(client_name.lower(), cname)
                if score >= 80 and score > best_score:
                    best_score = score
                    matched = cdict
        if not matched:
            # Final fallback — check if client_id was filtered
            if client_id > 0:
                c = conn.execute("SELECT id, name FROM clients WHERE id=?", (client_id,)).fetchone()
                if c:
                    matched = dict(c)

        # Normalize coverage type
        raw_type = ext.get("policy_type", "")
        normalized_type = _normalize_coverage(raw_type) if raw_type else ""

        has_dates = bool(ext.get("effective_date") and ext.get("expiration_date"))
        rows.append({
            "idx": i,
            "ext": ext,
            "matched_client": matched,
            "normalized_type": normalized_type,
            "has_dates": has_dates,
            "can_create": bool(matched and has_dates),
        })

    can_create_count = sum(1 for r in rows if r["can_create"])
    return templates.TemplateResponse("reconcile/_batch_create_review.html", {
        "request": request,
        "rows": rows,
        "can_create_count": can_create_count,
        "total_count": len(rows),
        "policy_types": cfg.get("policy_types", []),
        "client_id": client_id,
    })


@router.post("/batch-create", response_class=HTMLResponse)
async def batch_create(
    request: Request,
    conn=Depends(get_db),
):
    """HTMX: create multiple policies from MISSING rows in a single transaction."""
    from policydb.db import next_policy_uid
    from policydb import config as cfg
    from policydb.reconciler import _normalize_coverage
    import json as _json

    form = await request.form()
    selected_json = form.get("selected_rows", "[]")
    try:
        selected_indices = set(_json.loads(selected_json))
    except Exception:
        selected_indices = set()

    if not selected_indices:
        return HTMLResponse('<p class="text-xs text-amber-600">No rows selected.</p>')

    # Resolve missing rows from cache
    missing_entry = _MISSING_CACHE.get(_LAST_MISSING_TOKEN)
    missing_rows_list = missing_entry[0] if missing_entry else []

    account_exec = cfg.get("default_account_exec", "Grant")
    policy_types = set(cfg.get("policy_types", []))

    created = []
    skipped = []

    for idx in sorted(selected_indices):
        if idx < 0 or idx >= len(missing_rows_list):
            continue
        ext = missing_rows_list[idx]

        # Resolve client
        client_id_str = form.get(f"client_{idx}", "")
        if not client_id_str:
            skipped.append(f"Row {idx+1}: no client matched")
            continue
        try:
            cid = int(client_id_str)
        except ValueError:
            skipped.append(f"Row {idx+1}: invalid client")
            continue

        eff = ext.get("effective_date", "")
        exp = ext.get("expiration_date", "")
        if not eff or not exp:
            skipped.append(f"Row {idx+1}: missing dates")
            continue

        raw_type = ext.get("policy_type", "")
        ptype = _normalize_coverage(raw_type) if raw_type else ""
        if ptype not in policy_types:
            ptype = raw_type  # keep original if not in configured types

        def _f(v):
            try: return float(v) if v else 0.0
            except (ValueError, TypeError): return 0.0

        uid = next_policy_uid(conn)
        _raw_pol_num = ext.get("policy_number", "") or ""
        _pol_num = normalize_policy_number(_raw_pol_num) if _raw_pol_num else None
        _carrier = normalize_carrier(ext.get("carrier", "") or "")
        conn.execute(
            """INSERT INTO policies
               (policy_uid, client_id, policy_type, carrier, policy_number,
                effective_date, expiration_date, premium, limit_amount, deductible,
                account_exec)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uid, cid, ptype, _carrier or None,
                _pol_num,
                eff, exp, _f(ext.get("premium")),
                _f(ext.get("limit_amount")), _f(ext.get("deductible")),
                account_exec,
            ),
        )
        created.append({"uid": uid, "type": ptype, "carrier": _carrier})

    conn.commit()

    # Build summary
    created_html = "".join(
        f'<li class="text-xs text-green-700">'
        f'<a href="/policies/{c["uid"]}/edit" class="text-marsh hover:underline">{c["uid"]}</a> '
        f'— {c["type"]}{(" · " + c["carrier"]) if c["carrier"] else ""}'
        f'</li>'
        for c in created
    )
    skipped_html = "".join(
        f'<li class="text-xs text-amber-600">{s}</li>' for s in skipped
    )

    return HTMLResponse(
        f'<div class="border border-green-200 bg-green-50 rounded-lg p-4 mb-4">'
        f'<p class="text-sm font-semibold text-green-700 mb-2">{len(created)} policies created</p>'
        f'<ul class="space-y-1 mb-2">{created_html}</ul>'
        + (f'<p class="text-xs font-medium text-amber-600 mt-2">Skipped ({len(skipped)}):</p>'
           f'<ul class="space-y-0.5">{skipped_html}</ul>' if skipped else '')
        + f'<p class="text-xs text-gray-400 mt-2">Re-run reconciliation to see updated results.</p>'
        f'</div>'
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
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{effective_date}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{expiration_date}</td>'
        f'<td class="px-4 py-3 text-right tabular-nums text-gray-700">{premium_fmt}</td>'
        f'<td class="px-4 py-3"><a href="/policies/{policy_uid.upper()}/edit" class="text-xs text-marsh hover:underline">{policy_uid.upper()} →</a></td>'
        f'</tr>'
    )


@router.post("/apply-selected/{policy_uid}", response_class=HTMLResponse)
async def reconcile_apply_selected(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """HTMX: apply only user-checked fields from a DIFF row to PolicyDB."""
    form = await request.form()
    row_uid = form.get("row_uid", "")

    def _f(v):
        try: return float(v) if v else None
        except (ValueError, TypeError): return None

    _FIELD_MAP = {
        "policy_type": str, "carrier": str, "policy_number": str,
        "effective_date": str, "expiration_date": str,
        "premium": _f, "limit_amount": _f, "deductible": _f,
    }

    updates: dict = {}
    for field_name, converter in _FIELD_MAP.items():
        if form.get(f"field_{field_name}"):  # checkbox was checked
            val = form.get(f"val_{field_name}", "")
            if val:
                updates[field_name] = converter(val) if converter != str else val

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE policies SET {set_clause} WHERE policy_uid=?",
            list(updates.values()) + [policy_uid.upper()],
        )
        conn.commit()

    client_name = conn.execute(
        "SELECT c.name FROM policies p JOIN clients c ON p.client_id=c.id WHERE p.policy_uid=?",
        (policy_uid.upper(),),
    ).fetchone()["name"]

    # Fetch updated values for display
    db = conn.execute(
        "SELECT policy_type, carrier, policy_number, effective_date, expiration_date, premium FROM policies WHERE policy_uid=?",
        (policy_uid.upper(),),
    ).fetchone()

    premium_fmt = f"${float(db['premium'] or 0):,.0f}" if db["premium"] else "—"
    field_list = ", ".join(updates.keys()) if updates else "none"

    return HTMLResponse(
        f'<tr id="row-{row_uid}" class="bg-green-50">'
        f'<td class="px-3 py-3 text-gray-300 text-xs">✓</td>'
        f'<td class="px-4 py-3"><span class="text-xs font-semibold px-2 py-0.5 rounded bg-green-100 text-green-700">UPDATED</span>'
        f' <span class="text-xs text-gray-400 ml-1">{field_list}</span></td>'
        f'<td class="px-4 py-3 font-medium text-gray-800 text-sm">{client_name}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{db["policy_type"] or "—"}</td>'
        f'<td class="px-4 py-3 text-xs text-gray-600">{db["carrier"] or "—"}</td>'
        f'<td class="px-4 py-3 text-xs font-mono text-gray-500">{db["policy_number"] or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{db["effective_date"] or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{db["expiration_date"] or "—"}</td>'
        f'<td class="px-4 py-3 text-right tabular-nums text-gray-700">{premium_fmt}</td>'
        f'<td class="px-4 py-3"><a href="/policies/{policy_uid.upper()}/edit" class="text-xs text-marsh hover:underline">{policy_uid.upper()} →</a></td>'
        f'</tr>'
    )


@router.post("/apply-carrier-field/{policy_uid}/{carrier_id}")
async def apply_carrier_field(
    request: Request,
    policy_uid: str,
    carrier_id: int,
    conn=Depends(get_db),
):
    """Apply an imported value to a specific program carrier row."""
    from fastapi.responses import JSONResponse
    form = await request.form()
    field = form.get("field", "")
    value = form.get("value", "")

    allowed = {"carrier", "policy_number", "premium", "limit_amount"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)

    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    if field in ("premium", "limit_amount"):
        try:
            value = float(str(value).replace("$", "").replace(",", "").strip() or "0")
        except ValueError:
            return JSONResponse({"ok": False, "error": "Invalid number"}, status_code=400)

    conn.execute(f"UPDATE program_carriers SET {field} = ? WHERE id = ? AND program_id = ?",
                 (value, carrier_id, program["id"]))

    # Update parent totals
    totals = conn.execute(
        "SELECT COALESCE(SUM(premium), 0) AS tp, COALESCE(SUM(limit_amount), 0) AS tl FROM program_carriers WHERE program_id = ?",
        (program["id"],),
    ).fetchone()
    conn.execute("UPDATE policies SET premium = ?, limit_amount = ? WHERE id = ?",
                 (totals["tp"], totals["tl"], program["id"]))
    conn.commit()
    return JSONResponse({"ok": True})


@router.post("/add-program-carrier/{policy_uid}")
async def add_program_carrier_from_reconcile(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """Add a new carrier row to a program from reconcile diff."""
    from fastapi.responses import JSONResponse
    form = await request.form()
    carrier = form.get("carrier", "").strip()
    policy_number = form.get("policy_number", "").strip()
    try:
        premium = float(form.get("premium", "0").replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        premium = 0
    try:
        limit_amount = float(form.get("limit_amount", "0").replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        limit_amount = 0

    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM program_carriers WHERE program_id = ?",
        (program["id"],),
    ).fetchone()[0]

    conn.execute(
        """INSERT INTO program_carriers (program_id, carrier, policy_number, premium, limit_amount, sort_order)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (program["id"], carrier, policy_number or None, premium, limit_amount, max_order + 1),
    )

    # Update parent totals
    totals = conn.execute(
        "SELECT COALESCE(SUM(premium), 0) AS tp, COALESCE(SUM(limit_amount), 0) AS tl FROM program_carriers WHERE program_id = ?",
        (program["id"],),
    ).fetchone()
    conn.execute("UPDATE policies SET premium = ?, limit_amount = ? WHERE id = ?",
                 (totals["tp"], totals["tl"], program["id"]))
    conn.commit()
    return JSONResponse({"ok": True, "carrier": carrier})


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
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{effective_date or "—"}</td>'
        f'<td class="px-4 py-3 text-xs whitespace-nowrap text-gray-600">{expiration_date or "—"}</td>'
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

    diff_fields, _, fillable, score = _compare_fields(ext, db)
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


@router.patch("/apply-field/{policy_uid}", response_class=HTMLResponse)
async def reconcile_apply_field(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """HTMX: apply a single field update from uploaded value to a DIFF policy."""
    form = await request.form()
    field_name = form.get("field_name", "")
    value = form.get("value", "")

    _ALLOWED_FIELDS = {
        "policy_type", "carrier", "policy_number",
        "effective_date", "expiration_date",
        "premium", "limit_amount", "deductible",
    }

    if field_name not in _ALLOWED_FIELDS:
        return HTMLResponse(
            f'<td colspan="4" class="text-xs text-red-500">Invalid field: {field_name}</td>',
            status_code=400,
        )

    def _f(v):
        try:
            return float(v) if v else None
        except (ValueError, TypeError):
            return None

    _CURRENCY_FIELDS = {"premium", "limit_amount", "deductible"}
    db_value = _f(value) if field_name in _CURRENCY_FIELDS else value

    conn.execute(
        f"UPDATE policies SET {field_name}=? WHERE policy_uid=?",
        (db_value, policy_uid.upper()),
    )
    conn.commit()

    return HTMLResponse(
        f'<tr id="field-{field_name}" class="bg-green-50 transition-colors">'
        f'<td class="py-1.5 pr-2"><span class="text-green-500 text-xs">✓</span></td>'
        f'<td class="py-1.5 pr-3 font-medium text-green-600 text-xs">{field_name}</td>'
        f'<td class="py-1.5 pr-3 text-green-700 text-xs font-semibold">'
        f'{"${:,.0f}".format(float(value)) if field_name in _CURRENCY_FIELDS and value else value or "—"}'
        f' <span class="text-green-500 ml-1">applied ✓</span></td>'
        f'<td class="py-1.5 text-xs text-gray-400 line-through">(updated)</td>'
        f'</tr>'
    )


@router.get("/download/{token}")
def reconcile_download_cached(token: str):
    """Download cached XLSX report without re-uploading the file."""
    _cache_cleanup()
    entry = _RESULT_CACHE.get(token)
    if not entry:
        return HTMLResponse("Report expired. Please re-run reconciliation.", status_code=404)
    xlsx_bytes, _ = entry
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reconcile_{date.today()}.xlsx"'},
    )


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
    ext_rows, _ = parse_uploaded_file(content, column_mapping=col_map or None, filename=file.filename or "")
    db_rows = _load_db_policies(conn, client_id, scope)
    results = reconcile(ext_rows, db_rows)
    xlsx = build_reconcile_xlsx(results, run_date=date.today().isoformat(), filename=file.filename or "")
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reconcile_{date.today()}.xlsx"'},
    )
