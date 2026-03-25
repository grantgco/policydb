"""Google Places API client with daily rate limiting."""

from __future__ import annotations

import logging
from datetime import date

import httpx

import policydb.config as cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory daily rate limiter
# ---------------------------------------------------------------------------
_daily_count: int = 0
_daily_date: str = ""
_client: httpx.AsyncClient | None = None

_GOOGLE_AUTOCOMPLETE_URL = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
_GOOGLE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


def _check_rate_limit() -> tuple[bool, str]:
    """Return (allowed, reason).  Resets counter on date rollover."""
    global _daily_count, _daily_date

    api_key = cfg.get("google_places_api_key", "")
    if not api_key:
        return False, "no_api_key"

    today = date.today().isoformat()
    if _daily_date != today:
        _daily_count = 0
        _daily_date = today

    limit = cfg.get("google_places_daily_limit", 1000)
    if _daily_count >= limit:
        return False, "rate_limited"

    return True, ""


def _increment() -> None:
    global _daily_count
    _daily_count += 1


# ---------------------------------------------------------------------------
# Address component parsing
# ---------------------------------------------------------------------------

def _parse_address_components(components: list[dict]) -> dict:
    """Extract street, city, state, zip from Google address_components."""
    parts: dict[str, str] = {}
    for comp in components:
        types = comp.get("types", [])
        if "street_number" in types:
            parts["house_number"] = comp.get("long_name", "")
        elif "route" in types:
            parts["road"] = comp.get("long_name", "")
        elif "locality" in types:
            parts["city"] = comp.get("long_name", "")
        elif "sublocality_level_1" in types and "city" not in parts:
            parts["city"] = comp.get("long_name", "")
        elif "administrative_area_level_1" in types:
            parts["state"] = comp.get("short_name", "")
        elif "postal_code" in types:
            parts["zip"] = comp.get("long_name", "")

    house = parts.get("house_number", "")
    road = parts.get("road", "")
    street = f"{house} {road}".strip() if house else road

    return {
        "street": street,
        "city": parts.get("city", ""),
        "state": parts.get("state", ""),
        "zip": parts.get("zip", ""),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def autocomplete(query: str) -> list[dict]:
    """Return address predictions: [{description, place_id}]."""
    allowed, reason = _check_rate_limit()
    if not allowed:
        return []

    api_key = cfg.get("google_places_api_key", "")
    client = _get_client()
    try:
        resp = await client.get(
            _GOOGLE_AUTOCOMPLETE_URL,
            params={
                "input": query,
                "key": api_key,
                "types": "address",
                "components": "country:us",
            },
        )
        resp.raise_for_status()
        _increment()
        data = resp.json()
        predictions = data.get("predictions", [])
        return [
            {"description": p.get("description", ""), "place_id": p.get("place_id", "")}
            for p in predictions
            if p.get("place_id")
        ]
    except Exception:
        logger.exception("Google autocomplete error")
        return []


async def place_details(place_id: str) -> dict | None:
    """Return parsed address for a place_id: {street, city, state, zip, lat, lon}."""
    allowed, reason = _check_rate_limit()
    if not allowed:
        return None

    api_key = cfg.get("google_places_api_key", "")
    client = _get_client()
    try:
        resp = await client.get(
            _GOOGLE_DETAILS_URL,
            params={
                "place_id": place_id,
                "key": api_key,
                "fields": "address_components,geometry,formatted_address",
            },
        )
        resp.raise_for_status()
        _increment()
        data = resp.json()
        result = data.get("result", {})

        components = result.get("address_components", [])
        parsed = _parse_address_components(components)

        geo = result.get("geometry", {}).get("location", {})
        parsed["lat"] = geo.get("lat")
        parsed["lon"] = geo.get("lng")
        parsed["formatted_address"] = result.get("formatted_address", "")

        return parsed
    except Exception:
        logger.exception("Google place details error")
        return None


async def geocode(address: str) -> dict | None:
    """Return {lat, lon} for an address string."""
    allowed, reason = _check_rate_limit()
    if not allowed:
        return None

    api_key = cfg.get("google_places_api_key", "")
    client = _get_client()
    try:
        resp = await client.get(
            _GOOGLE_GEOCODE_URL,
            params={
                "address": address,
                "key": api_key,
                "components": "country:US",
            },
        )
        resp.raise_for_status()
        _increment()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        geo = results[0].get("geometry", {}).get("location", {})
        return {
            "lat": geo.get("lat"),
            "lon": geo.get("lng"),
        }
    except Exception:
        logger.exception("Google geocode error")
        return None


def get_usage() -> dict:
    """Return current daily usage stats."""
    today = date.today().isoformat()
    global _daily_count, _daily_date
    if _daily_date != today:
        _daily_count = 0
        _daily_date = today
    return {
        "count": _daily_count,
        "date": _daily_date,
        "limit": cfg.get("google_places_daily_limit", 1000),
        "has_key": bool(cfg.get("google_places_api_key", "")),
    }
