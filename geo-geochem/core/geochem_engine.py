"""GeochemEngine —— 编排化探异常提取（C-A 分离 + 多元素组合），产出 results 目录与平台契约。

P1 范围：点位插值 / 阈值先验降级 → C-A 异常分离 → 多元素组合 → GeoTIFF/geojson/metadata。
不含：原生晕轴向分带、剥蚀程度、构造叠加晕、进 model3d 深度门控（P2+）。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

import numpy as np

from config.config import Config
from core.grid import VoxelGrid
from core.ingest import gather_geochem
from core.separation import ca_anomaly_separation
from core.combo import multi_element_factor
from outputs import writers, render
from utils.logger import get_logger

logger = get_logger(__name__)


def _noop(msg, level="INFO"):
    pass


class GeochemEngine:
    @staticmethod
    def run(aoi_name: str, mineral_type: str, bbox: List[float], output_dir: str,
            params: Optional[Dict] = None, log_callback: Optional[Callable] = None,
            roots: Optional[Dict[str, str]] = None) -> Dict:
        params = params or {}
        log = log_callback or _noop
        roots = roots or Config.upstream_roots()
        os.makedirs(output_dir, exist_ok=True)
        created_at = datetime.now().isoformat(timespec="seconds")

        # 2D 水平网格（z 取单层，仅用水平面）
        grid = VoxelGrid(bbox, res_m=Config.GRID_RES_M, z_max_m=Config.GRID_RES_M,
                         dz_m=Config.GRID_RES_M, max_cells=Config.GRID_MAX_CELLS)
        crs_str = grid.crs.to_string()

        products: Dict = {}
        figures: List[str] = []
        warnings: List[str] = []
        model_stats: Dict = {
            "scale_note": "化探异常为采样点插值/区域阈值先验结果；无实测点位时仅给背景先验、不臆造异常。",
            "mineral_type": mineral_type,
            "grid": grid.summary(),
        }

        # 1) 汇聚证据（上传点位 / 阈值先验 / 关键元素）
        gs = gather_geochem(bbox, mineral_type, grid, roots,
                            upload_path=params.get("geochem_path"))
        model_stats["data_sources"] = gs.provenance
        model_stats["status"] = gs.status
        model_stats["key_elements"] = gs.key_elements
        log(f"化探数据: status={gs.status}，元素 {gs.available_elements() or '无实测(仅先验)'}")

        grid_dir = os.path.join(output_dir, "grids")
        fig_dir = os.path.join(output_dir, "figures")

        # 2) 有实测点位 → 逐元素 C-A 异常分离 + 多元素组合
        if gs.status == "measured" and gs.elements:
            element_anoms: Dict[str, np.ndarray] = {}
            thr_table: Dict[str, dict] = {}
            for sym, conc in gs.elements.items():
                # 背景先验（若 datacolle 阈值里有该元素的 moderate_anomaly）
                prior = None
                t = gs.thresholds.get(sym) or gs.thresholds.get(sym.upper())
                if isinstance(t, dict):
                    prior = t.get("moderate_anomaly") or t.get("weak_anomaly")
                anom, thr, curve = ca_anomaly_separation(
                    conc, prior_threshold=prior, fallback_pct=Config.CA_FALLBACK_PCT)
                element_anoms[sym] = anom
                thr_table[sym] = {"threshold": round(float(thr), 6), "method": curve.get("method"),
                                  "prior_used": prior}
                # 元素异常 GeoTIFF
                p = writers.write_grid_geotiff(os.path.join(grid_dir, f"element_anomaly_{sym}.tif"), anom, grid)
                products[f"element_anomaly_{sym}"] = os.path.relpath(p, output_dir)
                figures.append(render.render_anomaly_map(
                    fig_dir, f"anomaly_{sym}", anom, grid, f"{sym} 地球化学异常强度", cmap="turbo"))
                cap = render.render_ca_curve(fig_dir, sym, curve)
                if cap:
                    figures.append(cap)

            # 多元素组合异常
            combined, combo_stats = multi_element_factor(element_anoms, gs.key_elements)
            cp = writers.write_grid_geotiff(os.path.join(grid_dir, "multi_element_factor.tif"), combined, grid)
            products["multi_element_factor"] = os.path.relpath(cp, output_dir)
            figures.append(render.render_anomaly_map(
                fig_dir, "multi_element_factor", combined, grid,
                f"多元素组合异常（{mineral_type}：{'+'.join(combo_stats.get('elements', []))}）", cmap="turbo"))

            # 浓集中心 / 异常靶 geojson
            ap = writers.write_anomalies_geojson(os.path.join(output_dir, "anomalies.geojson"), combined, grid)
            products["anomalies"] = "anomalies.geojson"

            model_stats["anomaly_stats"] = {
                "elements_processed": sorted(element_anoms.keys()),
                "thresholds": thr_table,
                "combination": combo_stats,
                "n_points": gs.n_points,
            }
            log(f"异常提取完成：{len(element_anoms)} 元素 + 组合异常（{combo_stats.get('method')}）")
        else:
            # 3) 降级：仅背景先验（不出臆造异常网格）
            warnings.append("无实测化探点位：仅提供 data-colle 背景阈值先验，未生成异常网格。"
                            "请上传 ICP-MS/XRF 点位 CSV（含 lon,lat,元素列）以做真实异常提取。")
            log("⚠ " + warnings[-1], "WARNING")
            model_stats["prior_only"] = {
                "thresholds": gs.thresholds,
                "n_threshold_elements": len(gs.thresholds),
            }

        products["figures"] = [os.path.relpath(p, output_dir) for p in figures if p]
        model_stats["warnings"] = warnings

        # 大白话解读
        plain: List[str] = []
        if gs.status == "measured":
            pub = (gs.provenance.get("public") or {})
            if pub.get("dataset"):
                plain.append(f"⚠ 本次异常基于公开区域地球化学数据（{pub.get('dataset')}"
                             f"{'，' + pub['source'] if pub.get('source') else ''}），"
                             "属区域尺度、非矿体详查，可作区域底图；矿体尺度仍需地面/航空实测点位。")
            ast = model_stats.get("anomaly_stats", {})
            els = ast.get("elements_processed", [])
            plain.append(f"基于 {gs.n_points} 个化探采样点，提取了 {len(els)} 种元素（{'、'.join(els)}）的异常，"
                         "并用主成分把它们综合成『多元素组合异常』——亮区＝多元素同步增强，最可能指示矿(化)体。")
            plain.append("『异常下限』用含量-面积(C-A)分形自动确定，比固定倍数背景更客观；"
                         "各元素的 C-A 曲线图可查看拐点。")
            plain.append("👉 组合异常与浓集中心已按平台契约输出，可被 geo-model3d 取作地表证据层、"
                         "与遥感蚀变做联合异常（P2/P3 接入）。")
        else:
            plain.append("本研究区暂无实测化探点位，只能给出区域背景值与异常下限参考（来自 data-colle）。"
                         "上传采样点 CSV 后即可做真实异常提取与多元素组合——本服务不会凭空臆造异常。")
        model_stats["plain_summary"] = plain

        meta_path = writers.write_metadata(output_dir, aoi_name, bbox, crs_str,
                                           products, model_stats, created_at, tenant_id=params.get("tenant_id"))
        log("完成。")
        return {"result_dir": output_dir, "metadata_path": meta_path,
                "products": products, "model_stats": model_stats,
                "status": gs.status}
