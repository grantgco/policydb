"""The Dock — narrow pinnable PolicyDB view for quickly copying ref tags
into Outlook replies. Search powered by /search/live?mode=dock."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from policydb.web.app import get_db, templates

router = APIRouter()


@router.get("/dock", response_class=HTMLResponse)
@router.get("/d", response_class=HTMLResponse)
def dock(request: Request):
    return templates.TemplateResponse("dock.html", {"request": request})
