"""Discovery helpers for geo-7slow evidence products.

This local broker shape mirrors the intended commons slowvars_broker contract:
discover completed geo-7slow runs by bbox and expose manifest-backed evidence
layers without guessing files from the result directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.config import RESULTS_DIR


def _intersects(a: Iterable[float], b: Iterable[float]) -> bool:
    ax0, ay0, ax1, ay1 = [float(v) for v in a]
    bx0, by0, bx1, by1 = [float(v) for v in b]
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


def _read_manifest(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def discover_slowvars(
    bbox: Optional[List[float]] = None,
    result_root: Path = RESULTS_DIR,
) -> List[Dict[str, Any]]:
    """Return completed geo-7slow evidence runs intersecting bbox.

    bbox uses EPSG:4326 order [min_lon, min_lat, max_lon, max_lat]. When omitted,
    all manifest-backed runs are returned newest first.
    """
    records: List[Dict[str, Any]] = []
    if not result_root.exists():
        return records

    for manifest_path in result_root.glob("*/manifest.json"):
        manifest = _read_manifest(manifest_path)
        if not manifest:
            continue
        aoi_bbox = manifest.get("aoi_bbox")
        if bbox and (not aoi_bbox or not _intersects(aoi_bbox, bbox)):
            continue
        run_dir = manifest_path.parent
        layers = []
        for layer in manifest.get("layers", []):
            rel = layer.get("path")
            layers.append({
                **layer,
                "path": str(run_dir / rel) if rel else None,
            })
        records.append({
            "source": "geo-7slow",
            "run_id": manifest.get("run_id") or run_dir.name,
            "trace_id": manifest.get("trace_id"),
            "aoi_name": manifest.get("aoi_name"),
            "aoi_bbox": aoi_bbox,
            "created_at": manifest.get("created_at"),
            "metadata_path": str(run_dir / "metadata.json"),
            "manifest_path": str(manifest_path),
            "geologic_context": manifest.get("geologic_context"),
            "model_stats": manifest.get("model_stats"),
            "evidence_catalog": manifest.get("evidence_catalog", {}),
            "layers": layers,
        })

    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return records
