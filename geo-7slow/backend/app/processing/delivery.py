"""
delivery.py — 输入 ROI 后从交付数据库(冬季子目录)自动抓取七慢系统所需遥感数据

延用其他 geo-* 系统(geo-analyser / geo-stru)的"交付库 + 冬季子目录"约定:
用户上传一个 ROI 文件(文件名 == 交付项目目录名),或直接选择项目,系统据此
在交付目录里定位 DEM 与 Sentinel-2 / ASTER 波段,按七慢系统的 slot 命名软链到
一个普通的 upload 会话目录,随后沿用既有 /api/analyze 流水线,零改动跑分析。

零 sys.path 污染:用 importlib 从绝对路径加载 geo-analyser 的 delivery_project
(与 geo-stru/core/delivery.py 同一思路),复用其项目定位 / ROI 解析 / 子目录扫描。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import UPLOAD_DIR

# geo-analyser 的交付层(项目定位 / ROI 解析 / 传感器子目录扫描)
_DP_PATH = Path(os.environ.get(
    "DELIVERY_PROJECT_PY",
    "/opt/deepexplor-services/geo-analyser/delivery_project.py",
))
_GEOSTRU_RESULTS = Path(os.environ.get(
    "GEOSTRU_RESULTS",
    "/opt/deepexplor-services/geo-stru/results",
))
_dp = None


def _load_dp():
    """从绝对路径加载并缓存 geo-analyser 的 delivery_project 模块。"""
    global _dp
    if _dp is not None:
        return _dp
    if not _DP_PATH.exists():
        raise ImportError(
            f"找不到 {_DP_PATH}(geo-analyser 缺失,无法复用交付库定位逻辑)"
        )
    spec = importlib.util.spec_from_file_location(
        "geo_analyser_delivery_project", str(_DP_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _dp = module
    return module


# 七慢 slot -> (传感器 key, 归一化波段名)。DEM 单独处理,InSAR 交付库无、保持可选缺省。
SLOT_BAND_MAP: Dict[str, Tuple[str, str]] = {
    "s2_b03": ("Sentinel2", "B3"),   # 绿
    "s2_b04": ("Sentinel2", "B4"),   # 红
    "s2_b08": ("Sentinel2", "B8"),   # 近红外
    "aster_b05": ("ASTER", "B5"),
    "aster_b06": ("ASTER", "B6"),
    "aster_b07": ("ASTER", "B7"),
    "aster_b08": ("ASTER", "B8"),
    "aster_b10": ("ASTER", "B10"),
    "aster_b11": ("ASTER", "B11"),
    "aster_b12": ("ASTER", "B12"),
    "aster_b13": ("ASTER", "B13"),
    "aster_b14": ("ASTER", "B14"),
    # P2 蚀变图谱扩展波段
    "s2_b02": ("Sentinel2", "B2"),     # 蓝(铁氧化参考)
    "s2_b11": ("Sentinel2", "B11"),    # SWIR1(Al-OH)
    "s2_b12": ("Sentinel2", "B12"),    # SWIR2(Al-OH)
    "aster_b01": ("ASTER", "B1"),      # 铁参考
    "aster_b03n": ("ASTER", "B3N"),    # Fe³⁺
    "aster_b09": ("ASTER", "B9"),      # 碳酸盐(SWIR)
}

# P4 季节差分:夏季同名 LST/NDVI 波段(可选,缺夏季包则跳过)
SUMMER_BAND_MAP: Dict[str, Tuple[str, str]] = {
    "aster_b13_summer": ("ASTER", "B13"),
    "aster_b14_summer": ("ASTER", "B14"),
    "s2_b04_summer": ("Sentinel2", "B4"),
    "s2_b08_summer": ("Sentinel2", "B8"),
}


def delivery_root() -> Optional[str]:
    try:
        return str(_load_dp().DELIVERY_ROOT)
    except Exception:
        return None


def delivery_mounted() -> bool:
    try:
        return _load_dp().DELIVERY_ROOT.is_dir()
    except Exception:
        return False


def list_projects() -> List[Dict[str, str]]:
    """列出交付根目录下的所有项目(供前端下拉)。失败/未挂载返回空。"""
    try:
        return _load_dp().list_projects()
    except Exception:
        return []


def resolve_project_dir(name_or_filename: str, roi_geojson=None, delivery_id: str = "") -> Optional[Path]:
    """
    定位交付项目目录(与 geo-stru/analyser 一致,统一委托 commons.delivery):
      解析链:delivery_id(门户绑定) → 精确名 → 归一名 → 几何覆盖。
      roi_geojson 给定则名字不符也能按几何命中(KML 改名不再断)。
    """
    if not name_or_filename and not delivery_id and not roi_geojson:
        return None
    try:
        dp = _load_dp()
        if not delivery_id and name_or_filename:
            cand = dp.DELIVERY_ROOT / name_or_filename
            if cand.is_dir():
                return cand
        return dp.resolve_project_dir(name_or_filename or "", roi_geojson, delivery_id)
    except Exception:
        return None


def _band_index(sub_dir: Path) -> Dict[str, Path]:
    """扫描传感器子目录,返回 {归一化波段名(如 'B3'/'B8A'): 文件路径}。"""
    dp = _load_dp()
    out: Dict[str, Path] = {}
    try:
        for f in sub_dir.iterdir():
            if dp._BAND_RE.match(f.name):
                bn = dp._normalize_bn(f.name)
                if bn and bn not in out:
                    out[bn] = f
    except OSError:
        pass
    return out


def locate_delivery_files(project_dir: Path) -> Dict[str, Optional[str]]:
    """
    在项目冬季子目录定位七慢系统所需的全部 slot 文件。

    Returns: {slot -> 绝对路径 或 None}。键覆盖 dem + SLOT_BAND_MAP 的所有 slot。
    """
    dp = _load_dp()
    out: Dict[str, Optional[str]] = {"dem": None}
    for slot in SLOT_BAND_MAP:
        out[slot] = None

    winter = dp._winter_dir(project_dir)
    if not winter:
        return out

    # DEM(冬季子目录根下)
    dem = winter / "DEM.tif"
    if dem.is_file():
        out["dem"] = str(dem)

    # 按传感器分组扫描波段,避免重复 iterdir
    sensor_index: Dict[str, Dict[str, Path]] = {}
    for slot, (sensor_key, bn) in SLOT_BAND_MAP.items():
        if sensor_key not in sensor_index:
            sub = dp._find_sensor_subdir(winter, sensor_key)
            sensor_index[sensor_key] = _band_index(sub) if sub else {}
        path = sensor_index[sensor_key].get(bn)
        if path is not None:
            out[slot] = str(path)

    # P4 季节差分:夏季同名波段(可选)。仅在找到时加入,避免污染"缺失"列表。
    summer = _summer_dir(project_dir)
    if summer:
        s_index: Dict[str, Dict[str, Path]] = {}
        for slot, (sensor_key, bn) in SUMMER_BAND_MAP.items():
            if sensor_key not in s_index:
                sub = dp._find_sensor_subdir(summer, sensor_key)
                s_index[sensor_key] = _band_index(sub) if sub else {}
            path = s_index[sensor_key].get(bn)
            if path is not None:
                out[slot] = str(path)
    return out


def _summer_dir(project_dir: Path) -> Optional[Path]:
    """定位项目的夏季子目录(名称含"夏"/summer);不存在返回 None。"""
    try:
        for d in sorted(project_dir.iterdir()):
            if d.is_dir() and ("夏" in d.name or "summer" in d.name.lower()):
                return d
    except OSError:
        pass
    return None


# ─────────────────────────────────────────────
# ROI -> kml.kml
# ─────────────────────────────────────────────

def _geojson_to_kml(geom: Dict[str, Any]) -> str:
    """把 GeoJSON Polygon/MultiPolygon 写成最简 KML(供 Fiona 解析)。"""
    def ring_kml(ring: List[List[float]]) -> str:
        coords = " ".join(f"{p[0]},{p[1]}" for p in ring if len(p) >= 2)
        return (
            "<Polygon><outerBoundaryIs><LinearRing>"
            f"<coordinates>{coords}</coordinates>"
            "</LinearRing></outerBoundaryIs></Polygon>"
        )

    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    placemarks = []
    if gtype == "Polygon":
        # coords[0] 为外环
        if coords:
            placemarks.append(f"<Placemark>{ring_kml(coords[0])}</Placemark>")
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly:
                placemarks.append(f"<Placemark>{ring_kml(poly[0])}</Placemark>")
    body = "".join(placemarks)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"{body}</Document></kml>"
    )


def _validate_kml_file(path: Path) -> bool:
    """用 Fiona 试读,确认 geo-7slow 流水线能解析出几何。"""
    try:
        import fiona
        from shapely.geometry import shape
        fiona.drvsupport.supported_drivers["KML"] = "rw"
        with fiona.open(str(path), driver="KML") as src:
            return any(shape(feat["geometry"]) for feat in src)
    except Exception:
        return False


def write_roi_kml(roi_src: Path, dst_kml: Path) -> bool:
    """
    把 ROI 源文件落成 geo-7slow 能解析的 kml.kml。

    策略:.kml/.ovkml 先原样拷贝并用 Fiona 校验(沿用既有 ovkml->kml.kml 路径);
    校验失败或为 .geojson 时,用 delivery_project 解析成 GeoJSON 再合成干净 KML。
    成功返回 True。
    """
    suffix = roi_src.suffix.lower()
    # 1) kml/ovkml 直接拷贝(已被既有系统证明 Fiona 可读)
    if suffix in (".kml", ".ovkml"):
        try:
            shutil.copyfile(str(roi_src), str(dst_kml))
            if _validate_kml_file(dst_kml):
                return True
        except Exception:
            pass
    # 2) 回退:解析为 GeoJSON 再合成 KML(覆盖 geojson 及异常 ovkml)
    try:
        dp = _load_dp()
        geom = dp.parse_roi_file(roi_src)
        if not geom:
            return False
        dst_kml.write_text(_geojson_to_kml(geom), encoding="utf-8")
        return _validate_kml_file(dst_kml)
    except Exception:
        return False


# ─────────────────────────────────────────────
# 高层入口:ROI -> 准备好的 upload 会话
# ─────────────────────────────────────────────

def _link_or_copy(src: str, dst: Path) -> None:
    """优先软链(交付栅格可能很大,免拷贝);失败回退硬拷贝。"""
    try:
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        os.symlink(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


# GDAL 旁车文件:部分交付栅格(如纳兰区块的 Sentinel-2)把 CRS/仿射变换只存在
# PAM 旁车 (<file>.aux.xml) 或世界文件里,而非内嵌 GeoTIFF。软链栅格时若不一并
# 软链旁车(且按目标 basename 重命名),GDAL 解析不到 -> 必需波段"栅格缺少CRS信息"。
_SIDECAR_FULL_SUFFIXES = (".aux.xml", ".wld", ".ovr", ".msk")  # 追加在完整文件名之后
_SIDECAR_EXT_SUFFIXES = (".tfw", ".wld", ".prj", ".j2w")        # 替换原扩展名


def _link_sidecars(src: str, dst: Path) -> None:
    """把源栅格的 GDAL 旁车文件按目标命名一并软链(存在才链),保住 CRS/georef。"""
    src_p = Path(src)
    seen: set = set()
    # 1) 完整文件名 + 后缀:  B03.tiff.aux.xml -> s2_b03.tif.aux.xml
    for suf in _SIDECAR_FULL_SUFFIXES:
        s = Path(str(src_p) + suf)
        d = Path(str(dst) + suf)
        if s.is_file() and d not in seen:
            _link_or_copy(str(s), d)
            seen.add(d)
    # 2) 替换扩展名:  B03.tfw / B03.prj -> s2_b03.tfw / s2_b03.prj
    for suf in _SIDECAR_EXT_SUFFIXES:
        s = src_p.with_suffix(suf)
        d = dst.with_suffix(suf)
        if s.is_file() and d not in seen:
            _link_or_copy(str(s), d)
            seen.add(d)


def _safe_aoi_name(name: str) -> str:
    """Match geo-stru's AOI directory sanitizing convention."""
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or name


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_deposit_inference(md: Dict[str, Any], md_path: Path) -> Optional[Dict[str, Any]]:
    """Convert geo-stru metadata.deposit_inference into geo-7slow geologic_context."""
    di = md.get("deposit_inference")
    if not isinstance(di, dict):
        return None
    primary = di.get("primary_model")
    if not primary or primary == "未确定":
        return None
    return {
        "source": "geo-stru",
        "metadata_path": str(md_path),
        "deposit_type": primary,
        "deposit_type_confidence": di.get("primary_confidence"),
        "deposit_candidates": di.get("candidates") or [],
        "mineral_hint": di.get("mineral_hint"),
        "structural_control_summary": di.get("structural_control_summary"),
    }


def find_geologic_context(project_dir: Path) -> Optional[Dict[str, Any]]:
    """Find the latest geo-stru deposit inference for this delivery project."""
    project_name = project_dir.name
    safe_name = _safe_aoi_name(project_name)
    candidates: List[Path] = []

    # Current geo-stru layout: results/{safe_aoi}/{structural|insar_fusion}/{run}/metadata.json
    for aoi_name in {project_name, safe_name}:
        for category in ("structural", "insar_fusion"):
            candidates.extend((_GEOSTRU_RESULTS / aoi_name / category).glob("*/metadata.json"))

    # Legacy or ad-hoc layouts occasionally place metadata one level below results.
    candidates.extend((_GEOSTRU_RESULTS / safe_name).glob("metadata.json"))
    candidates.extend((_GEOSTRU_RESULTS / project_name).glob("metadata.json"))

    # Fallback: scan metadata entries and match their recorded aoi_name.
    if _GEOSTRU_RESULTS.is_dir():
        for md_path in _GEOSTRU_RESULTS.glob("*/metadata.json"):
            candidates.append(md_path)
        for md_path in _GEOSTRU_RESULTS.glob("*/*/*/metadata.json"):
            candidates.append(md_path)

    seen = set()
    unique = []
    for p in candidates:
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        unique.append(p)

    unique.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for md_path in unique:
        md = _read_json(md_path)
        if not md:
            continue
        aoi = md.get("aoi_name") or md_path.parents[2].name if len(md_path.parents) > 2 else ""
        if aoi not in {project_name, safe_name} and md_path.parents[0].name not in {project_name, safe_name}:
            continue
        ctx = _normalize_deposit_inference(md, md_path)
        if ctx:
            return ctx
    return None


def prepare_session(
    project_dir: Path,
    roi_src: Path,
) -> Dict[str, Any]:
    """
    据交付项目目录 + ROI 文件,创建一个 upload 会话目录并填充 slot 文件,
    返回 {upload_id, project_name, project_dir, fetched, missing, roi_ok}。
    """
    dp = _load_dp()
    upload_id = uuid.uuid4().hex[:12]
    upload_dir = UPLOAD_DIR / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 1) ROI -> kml.kml
    roi_ok = write_roi_kml(roi_src, upload_dir / "kml.kml")

    # 2) 栅格 slot -> 软链
    located = locate_delivery_files(project_dir)
    fetched: List[str] = []
    missing: List[str] = []
    for slot, path in located.items():
        if path:
            dst = upload_dir / f"{slot}.tif"
            _link_or_copy(path, dst)
            _link_sidecars(path, dst)  # 一并软链 PAM/世界文件,保住交付栅格的 CRS
            fetched.append(slot)
        else:
            missing.append(slot)

    bbox = None
    try:
        geom = dp.parse_roi_file(roi_src)
        if geom:
            bbox = dp.bbox_from_geojson(geom)
    except Exception:
        pass

    geologic_context = find_geologic_context(project_dir)
    if geologic_context:
        (upload_dir / "geologic_context.json").write_text(
            json.dumps(geologic_context, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "upload_id": upload_id,
        "project_name": project_dir.name,
        "project_dir": str(project_dir),
        "roi_ok": roi_ok,
        "fetched": fetched,
        "missing": missing,
        "bbox": list(bbox) if bbox else None,
        "geologic_context": geologic_context,
    }
