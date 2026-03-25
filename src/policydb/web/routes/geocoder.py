"""Proxy endpoints for Google Places / Geocoding API."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from policydb import geocoder

router = APIRouter(prefix="/api/address", tags=["geocoder"])


@router.get("/autocomplete")
async def address_autocomplete(q: str = Query("", min_length=3)):
    predictions = await geocoder.autocomplete(q)
    return {"predictions": predictions}


@router.get("/details/{place_id}")
async def address_details(place_id: str):
    result = await geocoder.place_details(place_id)
    if result is None:
        return JSONResponse({"error": "Could not retrieve address details"}, status_code=502)
    return result


@router.get("/geocode")
async def address_geocode(address: str = Query("", min_length=3)):
    result = await geocoder.geocode(address)
    if result is None:
        return JSONResponse({"error": "Could not geocode address"}, status_code=502)
    return result


@router.get("/usage")
async def address_usage():
    return geocoder.get_usage()
