"""分析启动/状态/结果端点"""
from pathlib import Path
import uuid
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.models.schemas import AnalysisRequest, TaskProgress
from app.models.task_store import task_store
from app.config import RESULTS_DIR, UPLOAD_DIR
from app.processing.pipeline import run_pipeline

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analyze")
async def start_analysis(request: AnalysisRequest):
    """启动分析流水线"""
    task_id = uuid.uuid4().hex[:12]
    task_store.create(task_id, request.upload_id)

    params = request.params.model_dump(exclude_none=True) if request.params else {}

    async def _run():
        await run_pipeline(task_id, request.upload_id, params)

    # 后台运行流水线
    import asyncio
    asyncio.create_task(_run())

    return {"task_id": task_id, "status": "queued"}


@router.post("/start")
async def start_service_analysis(
    file: UploadFile = File(None),
    mineral: str = Form(None),
    aoi_name: str = Form(None),
    project: str = Form(None),
    trace_id: str = Form(None),
    deposit_type: str = Form(None),
    family: str = Form(None),
    x_tenant_id: str = Header(None),     # P2 隔离:BFF 注入 X-Tenant-Id
    x_delivery_id: str = Header(None),   # 交付绑定:BFF 注入,优先按此定位交付
):
    """Service-compatible entrypoint used by portal/orchestrator.

    It prepares an upload session from the shared delivery library, then reuses the
    existing async pipeline. Raw KML-only execution is intentionally routed through
    the delivery matcher because geo-7slow still needs DEM/S2/ASTER rasters.
    """
    from app.processing import delivery

    if not delivery.delivery_mounted():
        raise HTTPException(503, f"交付目录未挂载或不可访问: {delivery.delivery_root()}")

    roi_src = None
    tmp_dir = None
    project_dir = None
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in (".ovkml", ".kml", ".geojson", ".json"):
            raise HTTPException(400, "ROI 文件需为 .ovkml / .kml / .geojson 格式")
        tmp_dir = UPLOAD_DIR / "_roi_tmp" / uuid.uuid4().hex[:8]
        tmp_dir.mkdir(parents=True, exist_ok=True)
        roi_src = tmp_dir / file.filename
        roi_src.write_bytes(await file.read())
        # 解析 ROI 几何,配合 delivery_id/几何兜底定位(KML 改名也能命中)
        _roi_geom = None
        try:
            import sys as _sys
            if "/opt/deepexplor-services" not in _sys.path:
                _sys.path.insert(0, "/opt/deepexplor-services")
            from commons.delivery import parse_roi as _parse_roi
            _roi_geom = _parse_roi(roi_src)
        except Exception:
            _roi_geom = None
        project_dir = delivery.resolve_project_dir(
            project or aoi_name or file.filename, roi_geojson=_roi_geom, delivery_id=x_delivery_id or "")
    elif project:
        project_dir = delivery.resolve_project_dir(project, delivery_id=x_delivery_id or "")
        if project_dir:
            for ext in (".ovkml", ".kml"):
                cands = sorted(project_dir.glob(f"*{ext}"))
                if cands:
                    roi_src = cands[0]
                    break
    else:
        raise HTTPException(400, "请上传 ROI 文件或指定 project")

    try:
        if project_dir is None:
            raise HTTPException(404, "在交付目录中未找到匹配项目")
        if roi_src is None or not Path(roi_src).exists():
            raise HTTPException(404, f"项目 {project_dir.name} 缺少可用 ROI 文件")
        prepared = delivery.prepare_session(project_dir, Path(roi_src))
    finally:
        if tmp_dir is not None:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not prepared.get("roi_ok"):
        raise HTTPException(400, "ROI 边界解析失败")

    task_id = uuid.uuid4().hex[:12]
    upload_id = prepared["upload_id"]
    task_store.create(task_id, upload_id)
    params = {
        "mineral": mineral,
        "aoi_name": aoi_name or prepared.get("project_name"),
        "project": prepared.get("project_name"),
        "trace_id": trace_id,
        "tenant_id": x_tenant_id,
        "deposit_type": deposit_type or (prepared.get("geologic_context") or {}).get("deposit_type"),
        "family": family,
        "geologic_context": prepared.get("geologic_context"),
    }
    params = {k: v for k, v in params.items() if v is not None}

    import asyncio
    asyncio.create_task(run_pipeline(task_id, upload_id, params))
    return {
        "task_id": task_id,
        "status": "queued",
        "upload_id": upload_id,
        "bbox": prepared.get("bbox"),
        "project_name": prepared.get("project_name"),
        "trace_id": trace_id,
    }


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/status/{task_id}")
async def get_service_status(task_id: str):
    """Service-compatible status shape used by orchestrator adapters."""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return {"success": True, "task": task}


@router.get("/tasks/{task_id}/results")
async def get_task_results(task_id: str):
    """获取分析结果"""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] != "completed":
        raise HTTPException(400, f"任务状态为 {task['status']}，尚未完成")
    return task["results"]


@router.get("/result/{task_id}/{filename}")
async def get_result_file(task_id: str, filename: str):
    """Download a standard result artifact from a completed run."""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, "非法文件名")
    task = task_store.get(task_id)
    if not task:
        # Results may exist after process restart even if in-memory task state is gone.
        if not (RESULTS_DIR / task_id).exists():
            raise HTTPException(404, "任务不存在")
    path = RESULTS_DIR / task_id / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    media = "application/json" if filename.endswith((".json", ".geojson")) else "image/tiff"
    return FileResponse(str(path), media_type=media, filename=filename)


@router.get("/deposit-presets")
async def get_deposit_presets():
    """矿床类型权重预设清单(成因族 + 矿床类型→族映射),供前端下拉。"""
    from app.processing.deposit_presets import list_presets
    return list_presets()


@router.get("/sensitivity/{task_id}")
async def get_sensitivity(task_id: str, perturbation: float = 0.2):
    """权重敏感性分析:扰动各权重±,量化靶区面积稳健性与变量重要性。"""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] != "completed":
        raise HTTPException(400, f"任务状态为 {task['status']}，尚未完成")
    from app.processing.sensitivity import analyze_sensitivity
    try:
        return analyze_sensitivity(task_id, perturbation)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
