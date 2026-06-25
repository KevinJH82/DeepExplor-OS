"""Results endpoints."""
import json
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...models.substance import Job, Anomaly, AnalysisResult
from ...schemas.schemas import AnomalyOut, ResultSummary, SpectrumResult, Model3DResult
from ...db.session import get_db

router = APIRouter()


@router.get("/{job_id}/summary")
async def get_result_summary(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    anomalies = db.query(Anomaly).filter(Anomaly.job_id == job_id).all()

    # If no anomalies from segmentation, derive substances from heatmap data
    substances_found = list(set(a.substance_id for a in anomalies))
    if not substances_found:
        heatmap = db.query(AnalysisResult).filter(
            AnalysisResult.job_id == job_id, AnalysisResult.result_type == "heatmap"
        ).first()
        if heatmap and heatmap.data_json:
            data = json.loads(heatmap.data_json)
            subs = set()
            for d in data:
                if d.get("confidence", 0) > 0.4:
                    subs.add(d.get("substance_id", "unknown"))
            substances_found = list(subs)

    # Build anomaly-like results from heatmap if DB anomalies are empty
    result_anomalies = []
    for a in anomalies:
        result_anomalies.append({
            "id": a.id, "substance_id": a.substance_id,
            "center_lat": a.center_lat, "center_lon": a.center_lon,
            "depth_min": a.depth_min, "depth_max": a.depth_max,
            "depth_mean": a.depth_mean, "confidence": a.confidence,
            "area_m2": a.area_m2, "volume_m3": a.volume_m3,
            "geometry_json": a.geometry_json,
        })

    if not result_anomalies:
        heatmap = db.query(AnalysisResult).filter(
            AnalysisResult.job_id == job_id, AnalysisResult.result_type == "heatmap"
        ).first()
        if heatmap and heatmap.data_json:
            data = json.loads(heatmap.data_json)
            # Group by substance, find clusters
            from collections import defaultdict
            groups = defaultdict(list)
            for d in data:
                if d.get("confidence", 0) > 0.4:
                    groups[d["substance_id"]].append(d)
            for sid, points in groups.items():
                if not points:
                    continue
                avg_lat = sum(p["lat"] for p in points) / len(points)
                avg_lon = sum(p["lon"] for p in points) / len(points)
                max_conf = max(p["confidence"] for p in points)
                result_anomalies.append({
                    "id": sid, "substance_id": sid,
                    "center_lat": avg_lat, "center_lon": avg_lon,
                    "depth_min": 0, "depth_max": 0, "depth_mean": 0,
                    "confidence": max_conf,
                    "area_m2": len(points) * 100, "volume_m3": None,
                    "geometry_json": "[]",
                })

    return {
        "job_id": job_id,
        "anomalies": result_anomalies,
        "substances_found": substances_found,
    }


@router.get("/{job_id}/heatmap")
async def get_heatmap(job_id: str, substance: str | None = None, db: Session = Depends(get_db)):
    result = db.query(AnalysisResult).filter(
        AnalysisResult.job_id == job_id,
        AnalysisResult.result_type == "heatmap",
    ).first()
    if not result or not result.data_json:
        raise HTTPException(404, "Heatmap not found")

    data = json.loads(result.data_json)
    if substance:
        data = [d for d in data if d.get("substance_id") == substance or d.get("scores", {}).get(substance, 0) > 0.3]
    return {"type": "FeatureCollection", "features": _to_geojson_features(data)}


def _to_geojson_features(data: list) -> list:
    features = []
    for d in data:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
            "properties": {
                "substance_id": d.get("substance_id"),
                "confidence": d.get("confidence", 0),
                "scores": d.get("scores", {}),
            },
        })
    return features


@router.get("/{job_id}/spectrum", response_model=SpectrumResult)
async def get_spectrum(job_id: str, location_id: int = 0, db: Session = Depends(get_db)):
    result = db.query(AnalysisResult).filter(
        AnalysisResult.job_id == job_id,
        AnalysisResult.result_type == "spectrum",
    ).first()
    if not result or not result.data_json:
        raise HTTPException(404, "Spectrum not found")

    data = json.loads(result.data_json)
    return SpectrumResult(
        frequencies=data.get("frequencies", []),
        amplitudes=data.get("magnitudes", []) or data.get("amplitudes", []),
        peaks=[],
        best_match=None,
    )


@router.get("/{job_id}/model3d")
async def get_model3d(job_id: str, substance: str | None = None, db: Session = Depends(get_db)):
    query = db.query(AnalysisResult).filter(
        AnalysisResult.job_id == job_id,
        AnalysisResult.result_type == "model3d",
    )
    if substance:
        query = query.filter(AnalysisResult.substance_id == substance)

    results = query.all()
    if not results:
        raise HTTPException(404, "3D model not found")

    models = []
    for r in results:
        mesh = json.loads(r.data_json) if r.data_json else {}
        summary = json.loads(r.summary_json) if r.summary_json else {}
        models.append({
            "substance_id": r.substance_id,
            "vertices": mesh.get("vertices", []),
            "faces": mesh.get("faces", []),
            "normals": mesh.get("normals", []),
            "bounds": summary.get("bounds", {}),
            "volume_m3": summary.get("volume_m3"),
        })
    return {"models": models}


@router.get("/{job_id}/substances")
async def get_job_substances(job_id: str, db: Session = Depends(get_db)):
    results = db.query(AnalysisResult).filter(
        AnalysisResult.job_id == job_id,
        AnalysisResult.result_type == "heatmap",
    ).first()
    if not results or not results.data_json:
        return {"substances": []}

    data = json.loads(results.data_json)
    substances = set()
    for d in data:
        substances.add(d.get("substance_id", "unknown"))
    return {"substances": list(substances)}
