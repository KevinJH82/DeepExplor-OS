"""Slow-variable evidence discovery API."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.processing.slowvars_broker import discover_slowvars


router = APIRouter(prefix="/api/slowvars", tags=["slowvars"])


@router.get("")
async def list_slowvars_evidence(
    min_lon: Optional[float] = Query(None),
    min_lat: Optional[float] = Query(None),
    max_lon: Optional[float] = Query(None),
    max_lat: Optional[float] = Query(None),
):
    """Discover geo-7slow evidence runs, optionally filtered by EPSG:4326 bbox."""
    parts = [min_lon, min_lat, max_lon, max_lat]
    if any(v is not None for v in parts) and not all(v is not None for v in parts):
        raise HTTPException(400, "bbox 参数需同时提供 min_lon,min_lat,max_lon,max_lat")
    bbox = [min_lon, min_lat, max_lon, max_lat] if all(v is not None for v in parts) else None
    return {
        "success": True,
        "source": "geo-7slow",
        "bbox": bbox,
        "records": discover_slowvars(bbox=bbox),
    }
