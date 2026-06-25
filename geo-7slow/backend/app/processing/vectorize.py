"""Vector outputs for geo-7slow target zones."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import numpy as np
from rasterio.features import rasterize, shapes
from shapely.geometry import mapping, shape
from shapely.ops import transform as shapely_transform

from app.config import NODATA


DRIVER_CODES: Dict[str, int] = {
    "stress_gradient": 1,
    "redox_gradient": 2,
    "fluid_overpressure": 3,
    "fault_activity": 4,
    "chem_potential": 5,
    "cap_rock_pressure": 6,
    "temp_gradient": 7,
}

DRIVER_NAMES = {v: k for k, v in DRIVER_CODES.items()}


def dominant_driver_layer(layers: Dict[str, np.ndarray]) -> np.ndarray:
    """Return a categorical uint8 layer identifying the strongest slow variable."""
    names = list(DRIVER_CODES)
    ref = next((layers.get(n) for n in names if layers.get(n) is not None), None)
    if ref is None:
        return np.full((1, 1), 255, dtype=np.uint8)

    stack = []
    valid = np.ones(ref.shape, dtype=bool)
    for name in names:
        arr = layers.get(name)
        if arr is None:
            valid &= False
            arr = np.full(ref.shape, NODATA)
        valid &= np.isfinite(arr) & (arr != NODATA)
        stack.append(np.where(np.isfinite(arr) & (arr != NODATA), arr, -np.inf))

    idx = np.argmax(np.stack(stack, axis=0), axis=0)
    out = np.full(ref.shape, 255, dtype=np.uint8)
    out[valid] = (idx[valid] + 1).astype(np.uint8)
    return out


def _to_epsg4326(geom, crs):
    if str(crs).upper() in ("EPSG:4326", "OGC:CRS84"):
        return geom
    import pyproj

    project = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    return shapely_transform(project, geom)


def _safe_stats(arr: np.ndarray, mask: np.ndarray) -> dict:
    valid = mask & np.isfinite(arr) & (arr != NODATA)
    if not np.any(valid):
        return {"mean": None, "min": None, "max": None}
    vals = arr[valid].astype(np.float64)
    return {
        "mean": float(np.mean(vals)),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
    }


def write_target_zones_geojson(
    target_zones: np.ndarray,
    delta: np.ndarray,
    driving_force_b: np.ndarray,
    resistance_a: np.ndarray,
    dominant_driver: np.ndarray,
    transform,
    crs,
    output_path: str | Path,
) -> dict:
    """Polygonize target zones and write a GeoJSON FeatureCollection."""
    output_path = Path(output_path)
    target_mask = target_zones == 1
    features = []

    for geom_dict, value in shapes(
        target_zones.astype(np.uint8),
        mask=target_mask,
        transform=transform,
    ):
        if int(value) != 1:
            continue
        geom = _to_epsg4326(shape(geom_dict), crs)
        if geom.is_empty:
            continue

        mask = rasterize(
            [(geom_dict, 1)],
            out_shape=target_zones.shape,
            transform=transform,
            fill=0,
            dtype="uint8",
        ).astype(bool) & target_mask

        delta_stats = _safe_stats(delta, mask)
        b_stats = _safe_stats(driving_force_b, mask)
        a_stats = _safe_stats(resistance_a, mask)
        drivers = dominant_driver[mask & (dominant_driver != 255)]
        dominant = None
        if drivers.size:
            codes, counts = np.unique(drivers.astype(np.uint8), return_counts=True)
            dominant = DRIVER_NAMES.get(int(codes[np.argmax(counts)]))

        area_km2 = float(np.sum(mask) * abs(transform.a * transform.e) / 1e6)
        features.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "rank": 0,
                "area_km2": round(area_km2, 4),
                "mean_delta": delta_stats["mean"],
                "min_delta": delta_stats["min"],
                "mean_driving_force_b": b_stats["mean"],
                "mean_resistance_a": a_stats["mean"],
                "dominant_driver": dominant,
                "confidence": None if delta_stats["mean"] is None else float(max(0.0, -delta_stats["mean"])),
            },
        })

    features.sort(
        key=lambda f: (
            f["properties"]["mean_delta"]
            if f["properties"]["mean_delta"] is not None
            else float("inf")
        )
    )
    for idx, feat in enumerate(features, start=1):
        feat["properties"]["rank"] = idx

    payload = {
        "type": "FeatureCollection",
        "features": features,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "target_count": len(features),
        "target_area_km2": round(sum(f["properties"]["area_km2"] for f in features), 4),
    }
