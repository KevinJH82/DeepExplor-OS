"""输出与契约 —— 计划孔 / 钻孔库 / 钻孔反馈 GeoJSON + metadata。

drill_feedback.geojson 的属性 outcome 用 ore/barren —— 严格对齐 geo-model3d
core/labels.py::load_drill_feedback 的识别（ore→正样本, barren→真负样本）。
"""

from __future__ import annotations

import os
import csv
import json
from typing import Dict, List


# 钻孔信息表的列定义（CSV 与 PNG 表格图共用）：(字段名, 中文列名, 取值格式化函数)
HOLE_TABLE_COLUMNS = [
    ("rank", "#", lambda v: "" if v is None else str(int(v))),
    ("hole_id", "孔号", lambda v: "" if v is None else str(v)),
    ("lon", "经度", lambda v: "" if v is None else f"{float(v):.6f}"),
    ("lat", "纬度", lambda v: "" if v is None else f"{float(v):.6f}"),
    ("target_depth_m", "目标深度(m)", lambda v: "" if v is None else str(int(round(float(v))))),
    ("azimuth_deg", "方位角(°)", lambda v: "" if v is None else str(int(round(float(v))))),
    ("dip_deg", "倾角(°)", lambda v: "" if v is None else str(int(round(float(v))))),
    ("score", "预测得分", lambda v: "" if v is None else f"{float(v):.4f}"),
    ("uncertainty", "不确定性", lambda v: "" if v is None else f"{float(v):.4f}"),
    ("info_gain", "信息增益", lambda v: "—" if v is None else f"{float(v):.4f}"),
    ("value", "综合价值", lambda v: "" if v is None else f"{float(v):.4f}"),
    ("slowvars_dominant_driver_cn", "靶区主控", lambda v: "—" if not v else str(v)),
    ("priority", "优先级", lambda v: "" if v is None else str(v)),
]


def write_planned_holes(path: str, holes: List[Dict]) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [h["lon"], h["lat"]]},
              "properties": {**{k: h[k] for k in
                             ("rank", "hole_id", "target_depth_m", "azimuth_deg", "dip_deg",
                              "score", "uncertainty", "info_gain", "value", "priority",
                              "slowvars_in_target", "slowvars_dominant_driver",
                              "slowvars_dominant_driver_cn", "slowvars_mean_delta",
                              "slowvars_zone_rank", "slowvars_confidence", "value_slowvars")
                              if k in h},
                             "trajectory": h.get("trajectory", [])}}
             for h in holes]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, ensure_ascii=False, indent=1)
    return path


def write_holes_table_csv(path: str, holes: List[Dict]) -> str:
    """钻孔信息表 CSV（utf-8-sig 便于 Excel 识别中文）：每行一个计划孔，列同 PNG 表格图。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([zh for (_k, zh, _fmt) in HOLE_TABLE_COLUMNS])
        for h in holes:
            row = []
            for key, _zh, fmt in HOLE_TABLE_COLUMNS:
                cell = fmt(h.get(key))
                row.append("" if cell == "—" else cell)  # CSV 缺失留空（机器友好）
            w.writerow(row)
    return path


def write_drill_feedback(path: str, judged: List[Dict]) -> int:
    """只写确认见矿/无矿的孔（unknown 不进反馈，不臆断）。返回写入条数。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    feats = []
    for r in judged:
        if r.get("outcome") not in ("ore", "barren"):
            continue
        if r.get("lon") is None or r.get("lat") is None:
            continue
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                      "properties": {"hole_id": r.get("hole_id"), "outcome": r["outcome"],
                                     "element": r.get("element"), "max_grade": r.get("max_grade"),
                                     "cutoff": r.get("cutoff")}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, ensure_ascii=False, indent=1)
    return len(feats)


def write_holes_db(path: str, holes_db: Dict) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(holes_db, f, ensure_ascii=False, indent=2)
    return path


def write_metadata(out_dir: str, aoi_name: str, bbox: List[float], crs: str,
                   products: Dict, model_stats: Dict, created_at: str,
                   source_version: str = "1.0",
                   trace_id: str = None, tenant_id: str = None, upstream_metadatas: List[Dict] = None) -> str:
    """trace_id / upstream_metadatas（可选）：注入决策轨迹血缘三键。

    drill 继承上游 model3d 的 trace_id，使闭环金标签与原预测同源（见架构蓝图 §1/§5）。
    """
    meta = {
        "source": "geo-drill",
        "source_version": source_version,
        "aoi_name": aoi_name,
        "aoi_bbox": [float(v) for v in bbox],
        "crs": crs,
        "created_at": created_at,
        "products": products,
        "model_stats": model_stats,
    }
    try:
        from commons.trace import stamp_metadata
        stamp_metadata(meta, explicit_trace_id=trace_id,
                       upstream_metadatas=upstream_metadatas, tenant_id=tenant_id)
    except Exception:
        pass
    path = os.path.join(out_dir, "metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return path
