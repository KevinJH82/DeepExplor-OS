"""Job management endpoints + WebSocket progress."""
import json
import asyncio
import uuid
import numpy as np
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from ...models.substance import Job, Anomaly, AnalysisResult, SpectralReference
from ...schemas.schemas import JobCreate, JobOut, JobProgress
from ...db.session import get_db, SessionLocal
from ...pipeline.orchestrator import run_pipeline, generate_demo_data, load_real_data
from ...core.spectral_library import SpectralLibrary

router = APIRouter()

# In-memory progress store for WebSocket connections
_active_connections: Dict[str, list[WebSocket]] = {}
_job_progress: Dict[str, dict] = {}


def _notify_progress(job_id: str, stage: int, stage_name: str, percent: float, message: str):
    """Push progress to WebSocket clients and in-memory store."""
    progress = {"stage": stage, "stage_name": stage_name, "percent": percent, "message": message}
    _job_progress[job_id] = progress
    for ws in _active_connections.get(job_id, []):
        try:
            asyncio.get_event_loop().create_task(ws.send_json(progress))
        except Exception:
            pass


def _run_analysis(job_id: str, target_substances: list[str], params: dict, demo_mode: bool):
    """Background task: run the full analysis pipeline."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        job.status = "running"
        job.started_at = datetime.now()
        db.commit()

        def progress_cb(stage, name, pct, msg):
            job.current_stage = stage
            job.percent = pct
            db.commit()
            _notify_progress(job_id, stage, name, pct, msg)

        # Load spectral library
        refs = db.query(SpectralReference).all()
        lib = SpectralLibrary()
        lib.load_from_db(refs)

        # Generate or load data
        upload_id = job.upload_id
        if demo_mode:
            data = generate_demo_data(target_substances)
        else:
            from ...core.config import settings
            data = load_real_data(upload_id, settings.UPLOAD_DIR)

        # Run pipeline
        result = run_pipeline(data, params, lib, progress_callback=progress_cb)

        # Save anomalies
        for anom in result.get("anomalies", []):
            anomaly = Anomaly(
                id=str(uuid.uuid4()),
                job_id=job_id,
                substance_id=anom["substance_id"],
                center_lat=anom["center_lat"],
                center_lon=anom["center_lon"],
                depth_min=anom["depth_min"],
                depth_max=anom["depth_max"],
                depth_mean=anom["depth_mean"],
                confidence=anom["confidence"],
                area_m2=anom.get("area_m2"),
                volume_m3=anom.get("volume_m3"),
                geometry_json=json.dumps(anom.get("boundary", [])),
            )
            db.add(anomaly)

        # Save 3D models
        for sid, model_data in result.get("models", {}).items():
            ar = AnalysisResult(
                id=str(uuid.uuid4()),
                job_id=job_id,
                substance_id=sid,
                result_type="model3d",
                data_json=json.dumps(model_data["mesh"]),
                summary_json=json.dumps({"volume_m3": model_data["volume_m3"], "bounds": model_data["bounds"]}),
            )
            db.add(ar)

        # Save spectral data (first point as sample)
        if result.get("fft_results"):
            fft_sample = result["fft_results"][0]
            sr = AnalysisResult(
                id=str(uuid.uuid4()),
                job_id=job_id,
                result_type="spectrum",
                data_json=json.dumps({
                    "frequencies": fft_sample["frequencies"][:200].tolist(),
                    "magnitudes": fft_sample["magnitudes"][:200].tolist(),
                }),
            )
            db.add(sr)

        # Save heatmap data
        heatmap_data = []
        for i, ident in enumerate(result.get("identifications", [])):
            coords = result.get("coordinates", [])
            if i < len(coords):
                entry = {
                    "lat": coords[i]["lat"],
                    "lon": coords[i]["lon"],
                    "substance_id": ident["substance_id"],
                    "confidence": ident["confidence"],
                    "scores": ident.get("scores", {}),
                }
                wc = ident.get("world_class_assessment")
                if wc:
                    entry["world_class"] = wc.get("is_world_class", False)
                    entry["world_class_score"] = wc.get("composite_score", 0.0)
                    entry["deep_channel"] = {
                        "peak_470": wc.get("conditions", {}).get("channel_470", {}).get("value", 0),
                        "peak_996": 0,
                        "ratio": wc.get("conditions", {}).get("ratio_470_996", {}).get("value", 0),
                    }
                heatmap_data.append(entry)

        ar_heatmap = AnalysisResult(
            id=str(uuid.uuid4()),
            job_id=job_id,
            result_type="heatmap",
            data_json=json.dumps(heatmap_data),
        )
        db.add(ar_heatmap)

        job.status = "completed"
        job.completed_at = datetime.now()
        job.percent = 100.0
        db.commit()

        _notify_progress(job_id, 8, "complete", 100.0, "分析完成")

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(e)
            db.commit()
        _notify_progress(job_id, 0, "error", 0.0, str(e))
    finally:
        db.close()


@router.post("/", response_model=JobOut)
async def create_job(job_create: JobCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = Job(
        id=str(uuid.uuid4()),
        upload_id=job_create.upload_id,
        status="queued",
        target_substances=json.dumps(job_create.target_substances),
        parameters_json=json.dumps(job_create.parameters or {}),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(
        _run_analysis,
        job.id,
        job_create.target_substances,
        job_create.parameters or {},
        job_create.demo_mode,
    )
    return job


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/", response_model=list[JobOut])
async def list_jobs(limit: int = 20, db: Session = Depends(get_db)):
    return db.query(Job).order_by(Job.created_at.desc()).limit(limit).all()


@router.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str):
    await websocket.accept()
    if job_id not in _active_connections:
        _active_connections[job_id] = []
    _active_connections[job_id].append(websocket)

    # Send current progress if available
    if job_id in _job_progress:
        await websocket.send_json(_job_progress[job_id])

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _active_connections[job_id].remove(websocket)
