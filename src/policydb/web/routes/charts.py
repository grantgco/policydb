"""Chart Deck Builder routes."""

from __future__ import annotations

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb.db import get_connection
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/charts", tags=["charts"])

CHART_REGISTRY = [
    {"id": "premium_comparison", "title": "Premium Comparison", "category": "core", "type": "d3"},
    {"id": "schedule", "title": "Schedule of Insurance", "category": "core", "type": "html"},
    {"id": "tower", "title": "Tower / Layer Diagram", "category": "core", "type": "d3"},
    {"id": "carrier_breakdown", "title": "Carrier Breakdown", "category": "core", "type": "d3"},
    {"id": "rate_change", "title": "Rate Change Summary", "category": "core", "type": "d3"},
    {"id": "activity_timeline", "title": "Activity Timeline", "category": "core", "type": "d3"},
    {"id": "market_conditions", "title": "Market Conditions", "category": "core", "type": "d3"},
    {"id": "premium_history", "title": "Premium History Trend", "category": "core", "type": "d3"},
    {"id": "coverage_comparison", "title": "Coverage Comparison", "category": "core", "type": "html"},
    {"id": "exposure_trend", "title": "Exposure Trend", "category": "exposure", "type": "d3"},
    {"id": "normalized_premium", "title": "Normalized Premium", "category": "exposure", "type": "d3"},
    {"id": "observations", "title": "Key Observations", "category": "exposure", "type": "html"},
    {"id": "exposure_vs_premium", "title": "Exposure vs Premium", "category": "exposure", "type": "d3"},
    {"id": "exec_summary", "title": "Executive Financial Summary", "category": "exec", "type": "html"},
]

_CHART_TITLE_MAP = {c["id"]: c["title"] for c in CHART_REGISTRY}
_CHART_TYPE_MAP = {c["id"]: c["type"] for c in CHART_REGISTRY}


# ── Manual Chart Library ──────────────────────────────────────────────────────

MANUAL_CHART_REGISTRY = [
    {"id": "rate_premium_baseline", "title": "Rate & Premium vs. Baseline",
     "description": "Dual-axis line chart comparing rate and premium trends against a baseline year.",
     "category": "financial", "icon": "chart-line"},
    {"id": "benchmark_distribution", "title": "Benchmarking Distribution",
     "description": "Percentile bars (10th/25th/50th/75th/90th) with average line overlay.",
     "category": "benchmarking", "icon": "chart-bar"},
    {"id": "loss_history", "title": "Loss History",
     "description": "Incurred losses by year with loss ratio trend line overlay.",
     "category": "loss", "icon": "chart-bar"},
    {"id": "premium_allocation", "title": "Premium Allocation",
     "description": "Donut chart showing premium distribution by coverage line.",
     "category": "financial", "icon": "chart-pie"},
    {"id": "rate_trend_line", "title": "Rate Trend by Line",
     "description": "Multi-line chart tracking rate change % over years by coverage line.",
     "category": "rate", "icon": "chart-line"},
    {"id": "tcor_trend", "title": "Total Cost of Risk (Trend)",
     "description": "Stacked bars: retained losses + premiums with TCOR trend line.",
     "category": "tcor", "icon": "chart-bar"},
    {"id": "tcor_breakdown", "title": "TCOR Breakdown (Single Year)",
     "description": "Waterfall chart showing TCOR components for a single year.",
     "category": "tcor", "icon": "chart-bar"},
    {"id": "freq_severity", "title": "Claims Frequency vs. Severity",
     "description": "Dual-axis: claim count bars + average claim cost line.",
     "category": "loss", "icon": "chart-bar"},
    {"id": "quote_comparison", "title": "Quote Comparison",
     "description": "Bubble chart (rate vs TIV) with ranked quote detail table for carrier comparison.",
     "category": "financial", "icon": "chart-scatter"},
    # ── Visual Builders ──
    {"id": "timeline_builder", "title": "Timeline Builder",
     "description": "Multi-step process timeline with horizontal, vertical, or phase layouts.",
     "category": "builder", "icon": "timeline"},
    {"id": "callout_stat", "title": "Big Stat / KPI",
     "description": "Large metric callout with direction arrow, label, and context.",
     "category": "card", "icon": "stat"},
    {"id": "callout_coverage", "title": "Coverage Card",
     "description": "Coverage line summary with limit, deductible, premium, and key terms.",
     "category": "card", "icon": "card"},
    {"id": "callout_carrier", "title": "Carrier Tile",
     "description": "Carrier summary with rating, participation, premium, and notes.",
     "category": "card", "icon": "card"},
    {"id": "callout_milestone", "title": "Milestone Card",
     "description": "Single milestone with date, status badge, description, and progress bar.",
     "category": "card", "icon": "timeline"},
    {"id": "callout_narrative", "title": "Narrative Card",
     "description": "Market update or analysis narrative with callout quote block.",
     "category": "card", "icon": "card"},
    {"id": "team_chart", "title": "Team Chart",
     "description": "Org chart showing internal team members grouped by assignment with contact details.",
     "category": "builder", "icon": "team"},
]

_MANUAL_TITLE_MAP = {c["id"]: c["title"] for c in MANUAL_CHART_REGISTRY}


# ── Client Search (for snapshot tagging) — MUST come BEFORE parameterized routes ──

@router.get("/api/client-search", response_class=JSONResponse)
async def chart_client_search(q: str = "", conn=Depends(get_db)):
    """Search clients by name for snapshot tagging."""
    if not q or len(q) < 2:
        return []
    rows = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 AND name LIKE ? ORDER BY name LIMIT 10",
        (f"%{q}%",),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


# ── Load Team API (for Team Chart pre-populate) ─────────────────────────────

@router.get("/api/team/{client_id}", response_class=JSONResponse)
async def chart_load_team(client_id: int, conn=Depends(get_db)):
    """Return internal team contacts for a client as JSON for the Team Chart editor."""
    rows = conn.execute(
        """
        SELECT c.name, c.email, c.phone, c.mobile,
               ca.title, ca.role, ca.assignment, ca.notes
        FROM contact_client_assignments ca
        JOIN contacts c ON c.id = ca.contact_id
        WHERE ca.client_id = ? AND ca.contact_type = 'internal'
        ORDER BY ca.assignment, c.name
        """,
        (client_id,),
    ).fetchall()
    return [
        {
            "name": r["name"] or "",
            "title": r["title"] or "",
            "role": r["role"] or "",
            "assignment": r["assignment"] or "",
            "phone": r["phone"] or "",
            "email": r["email"] or "",
            "mobile": r["mobile"] or "",
            "notes": r["notes"] or "",
        }
        for r in rows
    ]


# ── Team Suggestions / Confirm / Dismiss APIs ────────────────────────────────

@router.get("/api/team/{client_id}/suggestions", response_class=JSONResponse)
async def chart_team_suggestions(client_id: int, conn=Depends(get_db)):
    """Return placement colleagues not yet on the internal team and not dismissed."""
    rows = conn.execute(
        """
        SELECT DISTINCT c.id   AS contact_id,
               c.name, c.email, c.phone, c.mobile,
               GROUP_CONCAT(DISTINCT
                 COALESCE(
                   CASE WHEN cpa.is_placement_colleague = 1 THEN 'Placement' ELSE cpa.role END,
                   'Policy Contact'
                 ) || ' - ' || COALESCE(p.policy_type, '?')
               ) AS suggested_role
        FROM contact_policy_assignments cpa
        JOIN contacts c  ON c.id  = cpa.contact_id
        JOIN policies p  ON p.id  = cpa.policy_id
        WHERE p.client_id = ?
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND cpa.contact_id NOT IN (
              SELECT contact_id FROM contact_client_assignments
              WHERE client_id = ? AND contact_type = 'internal'
          )
          AND cpa.contact_id NOT IN (
              SELECT contact_id FROM team_chart_dismissals
              WHERE client_id = ?
          )
        GROUP BY c.id
        ORDER BY c.name
        """,
        (client_id, client_id, client_id),
    ).fetchall()
    return [
        {
            "contact_id": r["contact_id"],
            "name":       r["name"] or "",
            "email":      r["email"] or "",
            "phone":      r["phone"] or "",
            "mobile":     r["mobile"] or "",
            "suggested_role": r["suggested_role"] or "",
        }
        for r in rows
    ]


@router.post("/api/team/{client_id}/suggestions/{contact_id}/confirm", response_class=JSONResponse)
async def chart_team_confirm(client_id: int, contact_id: int, conn=Depends(get_db)):
    """Confirm a suggested placement colleague as an internal team member."""
    from policydb.queries import assign_contact_to_client

    # Build smart role from policy assignments
    role_row = conn.execute(
        """
        SELECT GROUP_CONCAT(DISTINCT
                 COALESCE(
                   CASE WHEN cpa.is_placement_colleague = 1 THEN 'Placement' ELSE cpa.role END,
                   'Policy Contact'
                 ) || ' - ' || COALESCE(p.policy_type, '?')
               ) AS suggested_role
        FROM contact_policy_assignments cpa
        JOIN policies p ON p.id = cpa.policy_id
        WHERE cpa.contact_id = ? AND p.client_id = ?
        """,
        (contact_id, client_id),
    ).fetchone()

    assignment_id = assign_contact_to_client(
        conn, contact_id, client_id,
        contact_type="internal",
        assignment=role_row["suggested_role"] if role_row else "",
    )
    conn.commit()
    return {"ok": True, "assignment_id": assignment_id}


@router.post("/api/team/{client_id}/suggestions/{contact_id}/dismiss", response_class=JSONResponse)
async def chart_team_dismiss(client_id: int, contact_id: int, conn=Depends(get_db)):
    """Permanently dismiss a placement colleague suggestion for this client."""
    conn.execute(
        "INSERT OR IGNORE INTO team_chart_dismissals (contact_id, client_id) VALUES (?, ?)",
        (contact_id, client_id),
    )
    conn.commit()
    return {"ok": True}


# ── Manual Snapshot CRUD — MUST come BEFORE /manual/{chart_type} ──────────

@router.get("/manual/snapshots/{chart_type}", response_class=JSONResponse)
async def manual_list_snapshots(chart_type: str, conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT s.id, s.name, s.updated_at, s.client_id, c.name as client_name "
        "FROM chart_snapshots s LEFT JOIN clients c ON s.client_id = c.id "
        "WHERE s.chart_type = ? ORDER BY s.updated_at DESC",
        (f"manual_{chart_type}",),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/manual/snapshots/{chart_type}/{snapshot_id}", response_class=JSONResponse)
async def manual_load_snapshot(chart_type: str, snapshot_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT s.id, s.name, s.data, s.updated_at, s.client_id, c.name as client_name "
        "FROM chart_snapshots s LEFT JOIN clients c ON s.client_id = c.id "
        "WHERE s.id = ? AND s.chart_type = ?",
        (snapshot_id, f"manual_{chart_type}"),
    ).fetchone()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    result = dict(row)
    result["data"] = json.loads(result["data"])
    return result


@router.post("/manual/snapshots/{chart_type}", response_class=JSONResponse)
async def manual_save_snapshot(request: Request, chart_type: str, conn=Depends(get_db)):
    body = await request.json()
    name = body.get("name", "").strip() or "Untitled"
    data = body.get("data", {})
    snapshot_id = body.get("id")

    client_id = body.get("client_id") or None  # convert empty string/0 to None

    if snapshot_id:
        conn.execute(
            "UPDATE chart_snapshots SET name = ?, data = ?, client_id = ?, updated_at = datetime('now') "
            "WHERE id = ? AND chart_type = ?",
            (name, json.dumps(data), client_id, snapshot_id, f"manual_{chart_type}"),
        )
        conn.commit()
        return {"ok": True, "id": snapshot_id, "name": name}
    else:
        cur = conn.execute(
            "INSERT INTO chart_snapshots (client_id, chart_type, name, data) VALUES (?, ?, ?, ?)",
            (client_id, f"manual_{chart_type}", name, json.dumps(data)),
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid, "name": name}


@router.delete("/manual/snapshots/{chart_type}/{snapshot_id}", response_class=JSONResponse)
async def manual_delete_snapshot(chart_type: str, snapshot_id: int, conn=Depends(get_db)):
    conn.execute(
        "DELETE FROM chart_snapshots WHERE id = ? AND chart_type = ?",
        (snapshot_id, f"manual_{chart_type}"),
    )
    conn.commit()
    return {"ok": True}


# ── Manual Gallery + Editor — AFTER snapshot routes, BEFORE /{client_id} ──

@router.get("/manual", response_class=HTMLResponse)
async def manual_gallery(request: Request, conn=Depends(get_db)):
    """Manual chart library gallery."""
    snapshots = conn.execute(
        "SELECT s.id, s.chart_type, s.name, s.updated_at, s.client_id, c.name as client_name "
        "FROM chart_snapshots s LEFT JOIN clients c ON s.client_id = c.id "
        "WHERE s.chart_type LIKE 'manual_%' "
        "ORDER BY s.updated_at DESC LIMIT 12"
    ).fetchall()
    snapshots = [dict(r) for r in snapshots]
    for s in snapshots:
        bare = s["chart_type"].replace("manual_", "", 1)
        s["title"] = _MANUAL_TITLE_MAP.get(bare, bare)
        s["bare_type"] = bare

    charts_list = [c for c in MANUAL_CHART_REGISTRY if c["category"] not in ("builder", "card")]
    builders_list = [c for c in MANUAL_CHART_REGISTRY if c["category"] in ("builder", "card")]

    return templates.TemplateResponse(
        "charts/manual/gallery.html",
        {"request": request, "charts": charts_list, "builders": builders_list, "snapshots": snapshots},
    )


@router.get("/manual/{chart_type}", response_class=HTMLResponse)
async def manual_editor(request: Request, chart_type: str, snapshot_id: Optional[int] = None, conn=Depends(get_db)):
    """Manual chart editor page."""
    chart_info = next((c for c in MANUAL_CHART_REGISTRY if c["id"] == chart_type), None)
    if not chart_info:
        return HTMLResponse("Chart type not found", status_code=404)

    snapshot_data = None
    snapshot_name = ""
    snapshot_client_id = ""
    snapshot_client_name = ""
    if snapshot_id:
        row = conn.execute(
            "SELECT s.name, s.data, s.client_id, c.name as client_name "
            "FROM chart_snapshots s LEFT JOIN clients c ON s.client_id = c.id "
            "WHERE s.id = ? AND s.chart_type = ?",
            (snapshot_id, f"manual_{chart_type}"),
        ).fetchone()
        if row:
            snapshot_data = json.loads(row["data"])
            snapshot_name = row["name"]
            snapshot_client_id = row["client_id"] or ""
            snapshot_client_name = row["client_name"] or ""

    return templates.TemplateResponse(
        "charts/manual/editor.html",
        {
            "request": request,
            "chart_type": chart_type,
            "chart_info": chart_info,
            "snapshot_data": snapshot_data,
            "snapshot_id": snapshot_id,
            "snapshot_name": snapshot_name,
            "snapshot_client_id": snapshot_client_id,
            "snapshot_client_name": snapshot_client_name,
        },
    )


# ── Route 1: Client Selector ─────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def chart_index(request: Request, conn=Depends(get_db)):
    """Client selector — pick a client to build a chart deck for."""
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()
    clients_list = [dict(r) for r in clients]
    return templates.TemplateResponse(
        "charts/index.html", {"request": request, "clients": clients_list}
    )


# ── Route 2: Deck Configurator ───────────────────────────────────────────────

@router.get("/{client_id}/deck", response_class=HTMLResponse)
async def deck_configurator(
    request: Request,
    client_id: int,
    type: str = "renewal-recap",
    conn=Depends(get_db),
):
    """Deck configurator — select charts and configure options."""
    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)

    # Fetch distinct policy types for this client (used by market conditions config)
    rows = conn.execute(
        "SELECT DISTINCT policy_type FROM policies "
        "WHERE client_id = ? AND policy_type IS NOT NULL AND policy_type != '' "
        "ORDER BY policy_type",
        (client_id,),
    ).fetchall()
    policy_types = [r["policy_type"] for r in rows]

    # Pre-fetch exec summary data for configurator pre-population
    from policydb.charts import get_exec_financial_summary_data
    exec_summary_data = get_exec_financial_summary_data(conn, client_id)

    return templates.TemplateResponse(
        "charts/deck.html",
        {
            "request": request,
            "client": client,
            "charts": CHART_REGISTRY,
            "deck_type": type,
            "policy_types": policy_types,
            "exec_summary_data": exec_summary_data,
        },
    )


# ── Route 3: Chart Viewer ────────────────────────────────────────────────────

@router.post("/{client_id}/deck/view", response_class=HTMLResponse)
async def deck_view(
    request: Request,
    client_id: int,
    conn=Depends(get_db),
):
    """Render the selected charts with data."""
    form = await request.form()

    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)

    selected_charts = form.getlist("selected_charts[]")
    if not selected_charts:
        selected_charts = form.getlist("selected_charts")

    # Import chart data functions
    from policydb.charts import (
        get_premium_comparison_data,
        get_schedule_data,
        get_tower_data,
        get_carrier_breakdown_data,
        get_rate_change_data,
        get_activity_timeline_data,
        get_premium_history_data,
        get_coverage_comparison_data,
        get_exposure_trend_data,
        get_normalized_premium_data,
        get_exposure_observations_data,
        get_exposure_vs_premium_data,
        get_exec_financial_summary_data,
    )

    DATA_FUNCTIONS = {
        "premium_comparison": get_premium_comparison_data,
        "schedule": get_schedule_data,
        "tower": get_tower_data,
        "carrier_breakdown": get_carrier_breakdown_data,
        "rate_change": get_rate_change_data,
        "activity_timeline": get_activity_timeline_data,
        "premium_history": get_premium_history_data,
        "coverage_comparison": get_coverage_comparison_data,
        "exposure_trend": get_exposure_trend_data,
        "normalized_premium": get_normalized_premium_data,
        "observations": get_exposure_observations_data,
        "exposure_vs_premium": get_exposure_vs_premium_data,
    }

    # Handle tower layout preference
    tower_layout = form.get("tower__layout", "combined")

    chart_data = {}
    for chart_id in selected_charts:
        if chart_id == "exec_summary":
            # Check for manual override rows from configurator
            exec_sections = form.getlist("exec__section[]")
            exec_lines = form.getlist("exec__line[]")
            exec_carriers = form.getlist("exec__carrier[]")
            exec_expiring = form.getlist("exec__expiring[]")
            exec_normalized = form.getlist("exec__normalized[]")
            exec_renewal = form.getlist("exec__renewal[]")

            has_manual = any(l.strip() for l in exec_lines)
            if has_manual:
                # Parse manual rows into sections
                section_map: dict[str, list] = {}
                for i, line in enumerate(exec_lines):
                    if not line.strip():
                        continue
                    sec = exec_sections[i] if i < len(exec_sections) else "General"
                    carrier = exec_carriers[i] if i < len(exec_carriers) else ""
                    try:
                        exp = float(exec_expiring[i]) if i < len(exec_expiring) and exec_expiring[i] else 0
                    except ValueError:
                        exp = 0
                    try:
                        norm = float(exec_normalized[i]) if i < len(exec_normalized) and exec_normalized[i] else None
                    except ValueError:
                        norm = None
                    try:
                        ren = float(exec_renewal[i]) if i < len(exec_renewal) and exec_renewal[i] else 0
                    except ValueError:
                        ren = 0
                    delta = ren - exp
                    delta_pct = round((delta / exp) * 100, 1) if exp > 0 else None
                    section_map.setdefault(sec, []).append({
                        "line": line.strip(),
                        "carrier": carrier.strip(),
                        "expiring": exp,
                        "normalized": norm,
                        "renewal": ren,
                        "delta_dollars": delta,
                        "delta_pct": delta_pct,
                    })
                # Build sections with subtotals
                sections = []
                grand_exp = grand_norm = grand_ren = 0
                has_any_norm = False
                for sec_title in dict.fromkeys(
                    exec_sections[i] for i in range(len(exec_lines)) if i < len(exec_sections)
                ):
                    if sec_title not in section_map:
                        continue
                    sec_rows = section_map[sec_title]
                    sub_exp = sum(r["expiring"] for r in sec_rows)
                    sub_ren = sum(r["renewal"] for r in sec_rows)
                    norms = [r["normalized"] for r in sec_rows if r["normalized"] is not None]
                    sub_norm = sum(norms) if norms else None
                    sub_delta = sub_ren - sub_exp
                    if sub_norm is not None:
                        has_any_norm = True
                    sections.append({
                        "title": sec_title,
                        "rows": sec_rows,
                        "subtotal_expiring": sub_exp,
                        "subtotal_normalized": sub_norm,
                        "subtotal_renewal": sub_ren,
                        "subtotal_delta_dollars": sub_delta,
                        "subtotal_delta_pct": round((sub_delta / sub_exp) * 100, 1) if sub_exp > 0 else None,
                    })
                    grand_exp += sub_exp
                    grand_ren += sub_ren
                    if sub_norm is not None:
                        grand_norm += sub_norm

                grand_delta = grand_ren - grand_exp
                chart_data["exec_summary"] = {
                    "sections": sections,
                    "grand_total_expiring": grand_exp,
                    "grand_total_normalized": grand_norm if has_any_norm else None,
                    "grand_total_renewal": grand_ren,
                    "grand_total_delta_dollars": grand_delta,
                    "grand_total_delta_pct": round((grand_delta / grand_exp) * 100, 1) if grand_exp > 0 else None,
                }
            else:
                # Auto-populate from DB
                chart_data["exec_summary"] = get_exec_financial_summary_data(conn, client_id)
            continue
        elif chart_id == "market_conditions":
            lines = form.getlist("market__line[]")
            avg_pcts = form.getlist("market__avg_pct[]")
            notes = form.getlist("market__notes[]")
            market_rows = []
            for i, line in enumerate(lines):
                if not line:
                    continue
                try:
                    pct = float(avg_pcts[i]) if i < len(avg_pcts) and avg_pcts[i] else 0
                except (ValueError, IndexError):
                    pct = 0
                note = notes[i] if i < len(notes) else ""
                market_rows.append({"line": line, "avg_pct": pct, "notes": note})
            actuals = get_rate_change_data(conn, client_id)
            actual_map = {a["policy_type"]: a["pct_change"] for a in actuals}
            combined_lines = []
            for mr in market_rows:
                combined_lines.append({
                    "line": mr["line"],
                    "market_avg_pct": mr["avg_pct"],
                    "actual_pct": actual_map.get(mr["line"]),
                    "notes": mr.get("notes", ""),
                })
            chart_data["market_conditions"] = {"lines": combined_lines}
        elif chart_id in DATA_FUNCTIONS:
            chart_data[chart_id] = DATA_FUNCTIONS[chart_id](conn, client_id)

    # If tower layout is "separate", expand into one chart per tower_group
    if tower_layout == "separate" and "tower" in chart_data and chart_data["tower"]:
        tower_groups = chart_data.pop("tower")
        tower_insert_idx = selected_charts.index("tower")
        selected_charts = [c for c in selected_charts if c != "tower"]
        for i, tg in enumerate(tower_groups):
            tid = f"tower_{i}"
            selected_charts.insert(tower_insert_idx + i, tid)
            chart_data[tid] = [tg]  # single tower_group wrapped in list

    chart_titles = {cid: _CHART_TITLE_MAP.get(cid, cid) for cid in selected_charts}
    chart_types = {cid: _CHART_TYPE_MAP.get(cid, "html") for cid in selected_charts}
    # Override titles/types for expanded tower entries
    for cid in selected_charts:
        if cid.startswith("tower_") and cid not in chart_titles:
            idx = int(cid.split("_")[1])
            tg_name = chart_data[cid][0].get("program_name", chart_data[cid][0].get("tower_group", "")) if chart_data.get(cid) else f"Tower {idx+1}"
            chart_titles[cid] = f"Tower: {tg_name}"
            chart_types[cid] = "d3"

    # Check tower completeness for warning banner
    tower_incomplete = None
    tower_missing = []
    if any(cid.startswith("tower") for cid in selected_charts):
        from policydb.queries import get_schematic_completeness
        completeness = get_schematic_completeness(conn, client_id)
        for c in completeness:
            if c["pct_complete"] < 80:
                tower_incomplete = c["tower_group"]
                tower_missing = c.get("missing_fields", [])[:5]  # show up to 5
                break

    return templates.TemplateResponse(
        "charts/view.html",
        {
            "request": request,
            "client": client,
            "client_id": client_id,
            "selected_charts": selected_charts,
            "chart_data": chart_data,
            "chart_titles": chart_titles,
            "chart_types": chart_types,
            "tower_incomplete": tower_incomplete,
            "tower_missing": tower_missing,
        },
    )


# ── Chart Snapshots CRUD ────────────────────────────────────────────────────

@router.get("/{client_id}/snapshots/{chart_type}", response_class=JSONResponse)
async def list_snapshots(
    client_id: int,
    chart_type: str,
    conn=Depends(get_db),
):
    """List saved snapshots for a client + chart type."""
    rows = conn.execute(
        "SELECT id, name, updated_at FROM chart_snapshots "
        "WHERE client_id = ? AND chart_type = ? ORDER BY updated_at DESC",
        (client_id, chart_type),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{client_id}/snapshots/{chart_type}/{snapshot_id}", response_class=JSONResponse)
async def load_snapshot(
    client_id: int,
    chart_type: str,
    snapshot_id: int,
    conn=Depends(get_db),
):
    """Load a single snapshot's data."""
    row = conn.execute(
        "SELECT id, name, data, updated_at FROM chart_snapshots "
        "WHERE id = ? AND client_id = ? AND chart_type = ?",
        (snapshot_id, client_id, chart_type),
    ).fetchone()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    result = dict(row)
    result["data"] = json.loads(result["data"])
    return result


@router.post("/{client_id}/snapshots/{chart_type}", response_class=JSONResponse)
async def save_snapshot(
    request: Request,
    client_id: int,
    chart_type: str,
    conn=Depends(get_db),
):
    """Save or update a chart snapshot."""
    body = await request.json()
    name = body.get("name", "").strip() or "Untitled"
    data = body.get("data", {})
    snapshot_id = body.get("id")

    if snapshot_id:
        # Update existing
        conn.execute(
            "UPDATE chart_snapshots SET name = ?, data = ?, updated_at = datetime('now') "
            "WHERE id = ? AND client_id = ? AND chart_type = ?",
            (name, json.dumps(data), snapshot_id, client_id, chart_type),
        )
        conn.commit()
        return {"ok": True, "id": snapshot_id, "name": name}
    else:
        # Insert new
        cur = conn.execute(
            "INSERT INTO chart_snapshots (client_id, chart_type, name, data) VALUES (?, ?, ?, ?)",
            (client_id, chart_type, name, json.dumps(data)),
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid, "name": name}


@router.delete("/{client_id}/snapshots/{chart_type}/{snapshot_id}", response_class=JSONResponse)
async def delete_snapshot(
    client_id: int,
    chart_type: str,
    snapshot_id: int,
    conn=Depends(get_db),
):
    """Delete a chart snapshot."""
    conn.execute(
        "DELETE FROM chart_snapshots WHERE id = ? AND client_id = ? AND chart_type = ?",
        (snapshot_id, client_id, chart_type),
    )
    conn.commit()
    return {"ok": True}
