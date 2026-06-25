"""FastAPI应用入口"""
import os
import uuid
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import RESULTS_DIR, UPLOAD_DIR
from app.models.task_store import task_store

app = FastAPI(title="七个慢变量分析系统", version="1.0.0")

# CORS - 允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from app.api.upload import router as upload_router
from app.api.analysis import router as analysis_router
from app.api.export import router as export_router
from app.api.delivery import router as delivery_router
from app.api.slowvars import router as slowvars_router

app.include_router(upload_router)
app.include_router(analysis_router)
app.include_router(export_router)
app.include_router(delivery_router)
app.include_router(slowvars_router)


# ─── WebSocket（直接挂载，不带 /api 前缀）──────────────────
@app.websocket("/ws/tasks/{task_id}")
async def ws_task_progress(websocket: WebSocket, task_id: str):
    """WebSocket实时进度推送"""
    await websocket.accept()
    task = task_store.get(task_id)
    if not task:
        await websocket.close(code=4004)
        return

    await task_store.add_ws(task_id, websocket)
    import json
    await websocket.send_text(json.dumps(task, ensure_ascii=False))

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await task_store.remove_ws(task_id, websocket)


# ─── 自定义底图 ──────────────────────────────────────────
BASEMAP_DIR = RESULTS_DIR / "_basemap"
BASEMAP_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/basemap")
async def upload_basemap(file: UploadFile = File(...)):
    """上传自定义底图（支持 GeoTIFF / JPG / PNG）"""
    import io
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.transform import from_bounds as rio_from_bounds
    from PIL import Image as PILImage
    import numpy as np

    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in (".tif", ".tiff", ".jpg", ".jpeg", ".png"):
        raise HTTPException(400, "不支持的文件格式，请上传 GeoTIFF、JPG 或 PNG")

    basemap_id = uuid.uuid4().hex[:12]
    bmp_dir = RESULTS_DIR / basemap_id
    bmp_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()

    if ext in (".tif", ".tiff"):
        # GeoTIFF：直接保存，有坐标信息
        save_path = bmp_dir / "basemap.tif"
        save_path.write_bytes(content)
        try:
            with rasterio.open(str(save_path)) as src:
                if src.crs is None:
                    raise ValueError("GeoTIFF 缺少坐标参考系统(CRS)信息")
                bounds_4326 = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        except Exception as e:
            raise HTTPException(400, f"GeoTIFF 文件无效: {e}")

    elif ext in (".jpg", ".jpeg", ".png"):
        # JPG/PNG：需要转换为 GeoTIFF，用当前上传会话的 KML 边界作为坐标参考
        img = PILImage.open(io.BytesIO(content))
        if img.mode == "RGBA":
            img_arr = np.array(img)
            rgb = img_arr[:, :, :3]
            alpha = img_arr[:, :, 3]
            nodata_mask = alpha < 128
            rgb[nodata_mask] = 0
        else:
            rgb = np.array(img.convert("RGB"))

        # 查找最近的 KML 获取边界
        kml_bounds = _find_latest_kml_bounds()
        if kml_bounds is None:
            # 没有KML，用图片中心坐标（需要用户通过查询参数提供）
            raise HTTPException(400, "JPG/PNG 图片需要先上传 KML 研究区边界作为坐标参考")

        xmin, ymin, xmax, ymax = kml_bounds  # EPSG:4326
        h, w = rgb.shape[:2]

        transform = rio_from_bounds(xmin, ymin, xmax, ymax, w, h)
        save_path = bmp_dir / "basemap.tif"
        with rasterio.open(
            str(save_path), "w", driver="GTiff",
            height=h, width=w, count=3, dtype="uint8",
            crs="EPSG:4326", transform=transform,
            compress="deflate", tiled=True, blockxsize=256, blockysize=256,
        ) as dst:
            for i in range(3):
                dst.write(rgb[:, :, i], i + 1)

        bounds_4326 = [xmin, ymin, xmax, ymax]
    else:
        raise HTTPException(400, "不支持的格式")

    lat_min, lat_max = bounds_4326[1], bounds_4326[3]
    lon_min, lon_max = bounds_4326[0], bounds_4326[2]

    return {
        "id": basemap_id,
        "filename": file.filename,
        "bounds": [[lat_min, lon_min], [lat_max, lon_max]],
    }


def _find_latest_kml_bounds():
    """在所有上传目录中查找最近的 KML 文件，返回 EPSG:4326 bounds"""
    import fiona
    from shapely.geometry import shape
    from shapely.ops import unary_union

    upload_dir = Path(str(RESULTS_DIR)).parent / "uploads"
    if not upload_dir.exists():
        return None

    kml_files = sorted(upload_dir.rglob("*.kml"), key=lambda f: f.stat().st_mtime, reverse=True)
    for kf in kml_files[:5]:
        try:
            fiona.drvsupport.supported_drivers["KML"] = "rw"
            with fiona.open(str(kf), driver="KML") as src:
                geoms = [shape(f["geometry"]) for f in src]
                if geoms:
                    roi = unary_union(geoms) if len(geoms) > 1 else geoms[0]
                    return roi.bounds  # (xmin, ymin, xmax, ymax) in EPSG:4326
        except Exception:
            continue
    return None


# ─── 瓦片服务 ──────────────────────────────────────────────

# 全局百分位缓存：{(task_id, layer_name): (vmin, vmax)}
_global_stats_cache: dict = {}

def _blank_tile():
    from PIL import Image
    import io
    blank = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    buf = io.BytesIO()
    blank.save(buf, format="PNG")
    return buf.getvalue()


def _get_global_percentiles(task_id: str, layer_name: str, cog_path):
    """获取全局 p2/p98 百分位范围（带缓存）"""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling as RS

    cache_key = (task_id, layer_name)
    mtime = cog_path.stat().st_mtime
    cached = _global_stats_cache.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]

    with rasterio.open(str(cog_path)) as src:
        nodata = src.nodata if src.nodata else -9999
        h = min(src.height, 500)
        w = min(src.width, 500)
        data = src.read(1, out_shape=(h, w), resampling=RS.average)

    valid = np.isfinite(data) & (data != nodata)
    if np.any(valid):
        vals = data[valid]
        pmin, pmax = float(np.percentile(vals, 2)), float(np.percentile(vals, 98))
        if pmax <= pmin:
            pmax = pmin + 1
    else:
        pmin, pmax = 0.0, 1.0

    _global_stats_cache[cache_key] = (mtime, pmin, pmax)
    return pmin, pmax


@app.get("/tiles/{task_id}/{layer_name}/{z}/{x}/{y}.png")
async def get_tile(
    task_id: str, layer_name: str, z: int, x: int, y: int,
    vmin: float = Query(None), vmax: float = Query(None),
):
    """从COG文件渲染XYZ瓦片（重投影到 Web Mercator EPSG:3857）"""
    import numpy as np
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_bounds as rio_from_bounds

    cog_path = RESULTS_DIR / task_id / f"{layer_name}.tif"
    if not cog_path.exists():
        return HTMLResponse(content=_blank_tile(), media_type="image/png")

    # 如果前端没传 vmin/vmax，用全局百分位
    if vmin is None or vmax is None:
        vmin, vmax = _get_global_percentiles(task_id, layer_name, cog_path)

    # 计算 Web Mercator (EPSG:3857) 瓦片边界
    tile_size = 256
    n = 2 ** z
    # 经纬度边界
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * y / n))))
    lat_min = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * (y + 1) / n))))

    # 转 EPSG:3857 米制坐标
    origin_shift = 20037508.342789244
    x_min = lon_min / 180.0 * origin_shift
    x_max = lon_max / 180.0 * origin_shift
    y_max = np.log(np.tan((90.0 + lat_max) * np.pi / 360.0)) / (np.pi / 180.0) / 180.0 * origin_shift
    y_min = np.log(np.tan((90.0 + lat_min) * np.pi / 360.0)) / (np.pi / 180.0) / 180.0 * origin_shift

    dst_transform = rio_from_bounds(x_min, y_min, x_max, y_max, tile_size, tile_size)

    try:
        with rasterio.open(str(cog_path)) as src:
            # 快速检查：瓦片范围是否与数据范围重叠
            from rasterio.warp import transform_bounds
            data_bounds_3857 = transform_bounds(src.crs, "EPSG:3857", *src.bounds)
            if (x_max < data_bounds_3857[0] or x_min > data_bounds_3857[2] or
                y_max < data_bounds_3857[1] or y_min > data_bounds_3857[3]):
                return HTMLResponse(content=_blank_tile(), media_type="image/png")

            if src.count >= 3 and src.dtypes[0] == "uint8":
                # RGB 图片底图：3 波段重投影
                rgb = np.zeros((3, tile_size, tile_size), dtype=np.uint8)
                for i in range(3):
                    reproject(
                        source=rasterio.band(src, i + 1),
                        destination=rgb[i],
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=dst_transform,
                        dst_crs="EPSG:3857",
                        resampling=Resampling.bilinear,
                    )
                img_data = _render_rgb_tile(rgb)
                return HTMLResponse(content=img_data, media_type="image/png")

            # 单波段分析结果
            nodata = src.nodata if src.nodata else -9999.0
            data = np.full((tile_size, tile_size), nodata, dtype=np.float64)
            reproject(
                source=rasterio.band(src, 1),
                destination=data,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs="EPSG:3857",
                resampling=Resampling.bilinear,
                dst_nodata=nodata,
            )
    except Exception:
        import traceback; traceback.print_exc()
        return HTMLResponse(content=_blank_tile(), media_type="image/png")

    valid = (data != nodata) & np.isfinite(data)
    img_data = _render_tile(data, valid, layer_name, vmin=vmin, vmax=vmax)
    return HTMLResponse(content=img_data, media_type="image/png")


def _render_rgb_tile(rgb: "np.ndarray") -> bytes:
    """将 3 波段 RGB uint8 数组渲染为 PNG 瓦片"""
    import numpy as np
    from PIL import Image
    import io

    h, w = rgb.shape[1], rgb.shape[2]
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 0] = rgb[0]
    rgba[:, :, 1] = rgb[1]
    rgba[:, :, 2] = rgb[2]
    # 全黑像素视为透明
    non_black = np.any(rgb != 0, axis=0)
    rgba[:, :, 3] = np.where(non_black, 220, 0)

    img = Image.fromarray(rgba, "RGBA")
    img = img.resize((256, 256), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_tile(data: "np.ndarray", valid: "np.ndarray", layer_name: str,
                 vmin: float = None, vmax: float = None) -> bytes:
    """将栅格数据渲染为PNG瓦片，异常区域色彩加强"""
    import numpy as np
    from PIL import Image
    import io

    h, w = data.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    if not np.any(valid):
        img = Image.fromarray(rgba, "RGBA")
        img = img.resize((256, 256), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    if vmin is None or vmax is None:
        vals = data[valid]
        vmin, vmax = np.percentile(vals, 2), np.percentile(vals, 98)
    if vmax <= vmin:
        vmax = vmin + 1

    t = np.clip((data - vmin) / (vmax - vmin), 0, 1)  # 0=低, 1=高

    # ── Δ判别式：蓝(有利 Δ<0) → 白(中性) → 红(不利 Δ>0) ──
    if "delta" in layer_name:
        # t≈0 → 强蓝(有利区), t≈0.5 → 白, t≈1 → 强红(不利区)
        # 加强两端对比度
        r = np.where(t > 0.5, 255, (t * 2 * 255)).astype(np.uint8)
        g = (255 * (1 - 2 * np.abs(t - 0.5))).astype(np.uint8)
        b = np.where(t < 0.5, 255, ((1 - t) * 2 * 255)).astype(np.uint8)
        # 异常区（两端）提高不透明度
        alpha = np.where(valid, np.clip((np.abs(t - 0.5) * 2) * 255, 120, 255), 0).astype(np.uint8)

    # ── 靶区：亮黄+红边框，0/1 二值 ──
    elif "target" in layer_name:
        mask = (t > 0.5) & valid
        r = np.where(mask, 255, 0).astype(np.uint8)
        g = np.where(mask, 200, 0).astype(np.uint8)
        b = np.where(mask, 0, 0).astype(np.uint8)
        alpha = np.where(mask, 240, 0).astype(np.uint8)

    # ── 七慢变量：低值冷色→高值暖色，异常区色彩加强 ──
    else:
        # 5段色彩：深蓝 → 青 → 黄绿 → 橙 → 亮红
        # 用分段线性插值，高值区(异常)用更饱和的暖色
        r_w = np.array([10, 30, 180, 255, 255], dtype=np.float64)
        g_w = np.array([20, 140, 230, 170, 25], dtype=np.float64)
        b_w = np.array([120, 220, 50, 0, 0], dtype=np.float64)
        stops = np.array([0.0, 0.25, 0.5, 0.75, 1.0])

        r = np.interp(t, stops, r_w).astype(np.uint8)
        g = np.interp(t, stops, g_w).astype(np.uint8)
        b = np.interp(t, stops, b_w).astype(np.uint8)
        # 异常区（高值）不透明度更高，背景区半透明
        alpha = np.where(valid, np.clip(80 + t * 200, 80, 255), 0).astype(np.uint8)

    rgba[:, :, 0] = r
    rgba[:, :, 1] = g
    rgba[:, :, 2] = b
    rgba[:, :, 3] = alpha

    img = Image.fromarray(rgba, "RGBA")
    img = img.resize((256, 256), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ─── 前端静态文件 ──────────────────────────────────────────
# 生产模式下服务前端构建产物
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_ASSETS)), name="frontend-assets")


@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>七个慢变量分析系统</h1><p>前端未构建，请先运行前端开发服务器</p>")


@app.get("/api/slots")
async def get_upload_slots():
    """获取上传槽位定义"""
    from app.config import UPLOAD_SLOTS
    return {"slots": UPLOAD_SLOTS}


@app.get("/{full_path:path}")
async def serve_frontend_route(full_path: str):
    """Serve built frontend assets and SPA routes when running only the backend."""
    index = FRONTEND_DIST / "index.html"
    static_file = FRONTEND_DIST / full_path
    if static_file.exists() and static_file.is_file():
        return FileResponse(str(static_file))
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>七个慢变量分析系统</h1><p>前端未构建，请先运行前端开发服务器</p>", status_code=404)
