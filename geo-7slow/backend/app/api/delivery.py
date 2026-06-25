"""交付库自动获取接口:输入 ROI -> 在交付目录定位项目 -> 抓取所需遥感数据。"""
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import UPLOAD_DIR
from app.processing import delivery
from app.api.upload import _build_upload_status

router = APIRouter(prefix="/api/delivery", tags=["delivery"])

_ROI_EXTS = (".ovkml", ".kml", ".geojson", ".json")


@router.get("/projects")
async def list_delivery_projects():
    """列出交付库中的可选项目(供前端下拉)。"""
    return {
        "delivery_root": delivery.delivery_root(),
        "mounted": delivery.delivery_mounted(),
        "projects": delivery.list_projects(),
    }


def _project_roi_file(project_dir: Path):
    """在项目目录(非子目录)里找项目自带的 ROI 文件 (.ovkml/.kml)。"""
    for ext in (".ovkml", ".kml"):
        cands = sorted(project_dir.glob(f"*{ext}"))
        if cands:
            return cands[0]
    return None


@router.post("/prepare")
async def prepare_from_delivery(
    file: UploadFile = File(None),
    project: str = Form(None),
):
    """
    两种入参(其一):
      - file: 上传 ROI 文件(.ovkml/.kml/.geojson),按文件名定位交付项目;
      - project: 直接给交付项目目录名,用项目自带的 ROI 文件。

    成功后在 uploads 下建好会话并软链所需栅格,返回与 ZIP 上传一致的匹配状态
    (matched/unmatched/required_missing/all_required_filled),前端可直接调用 /api/analyze。
    """
    if not delivery.delivery_mounted():
        raise HTTPException(
            503,
            f"交付目录未挂载或不可访问: {delivery.delivery_root()}",
        )

    roi_src = None
    tmp_path = None

    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in _ROI_EXTS:
            raise HTTPException(400, "ROI 文件需为 .ovkml / .kml / .geojson 格式")
        # 暂存上传的 ROI 文件:项目目录名 == ROI 文件主名,故按原文件名另存一份用于定位与解析
        tmp_dir = UPLOAD_DIR / "_roi_tmp" / uuid.uuid4().hex[:8]
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / file.filename
        tmp_path.write_bytes(await file.read())
        roi_src = tmp_path
        project_dir = delivery.resolve_project_dir(file.filename)
    elif project:
        project_dir = delivery.resolve_project_dir(project)
        if project_dir:
            roi_src = _project_roi_file(project_dir)
    else:
        raise HTTPException(400, "请上传 ROI 文件或指定项目名")

    try:
        if project_dir is None:
            raise HTTPException(
                404,
                "在交付目录中未找到匹配的项目(项目目录名应与 ROI 文件名一致)",
            )
        if roi_src is None or not Path(roi_src).exists():
            raise HTTPException(404, f"项目 {project_dir.name} 缺少可用的 ROI 文件")

        result = delivery.prepare_session(project_dir, Path(roi_src))
    finally:
        if tmp_path is not None:
            import shutil
            shutil.rmtree(tmp_path.parent, ignore_errors=True)

    if not result["roi_ok"]:
        raise HTTPException(400, "ROI 边界解析失败,无法生成研究区掩膜")

    status = _build_upload_status(result["upload_id"])
    payload = status.model_dump()
    payload.update({
        "source": "delivery",
        "project_name": result["project_name"],
        "project_dir": result["project_dir"],
        "bbox": result["bbox"],
        "fetched": result["fetched"],
        "geologic_context": result.get("geologic_context"),
    })
    return payload
