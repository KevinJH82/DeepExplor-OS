"""导出端点"""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import RESULTS_DIR

router = APIRouter(prefix="/api/export", tags=["export"])

LAYER_FILES = {
    "stress_gradient": "stress_gradient.tif",
    "redox_gradient": "redox_gradient.tif",
    "fluid_overpressure": "fluid_overpressure.tif",
    "fault_activity": "fault_activity.tif",
    "cap_rock_pressure": "cap_rock_pressure.tif",
    "temp_gradient": "temp_gradient.tif",
    "chem_potential": "chem_potential.tif",
    "driving_force_b": "driving_force_b.tif",
    "resistance_a": "resistance_a.tif",
    "delta_discriminant": "delta_discriminant.tif",
    "target_zones": "target_zones.tif",
    "dominant_driver": "dominant_driver.tif",
}


@router.get("/{task_id}/{layer_name}")
async def export_layer(task_id: str, layer_name: str):
    """下载指定结果图层的GeoTIFF"""
    filename = LAYER_FILES.get(layer_name)
    if not filename:
        raise HTTPException(400, f"未知图层: {layer_name}")

    filepath = RESULTS_DIR / task_id / filename
    if not filepath.exists():
        raise HTTPException(404, "文件不存在")

    return FileResponse(
        str(filepath),
        media_type="image/tiff",
        filename=f"{layer_name}.tif",
    )
