"""GeophysEngine —— 编排位场处理 + ANT 速度体接入，产出 results 目录与平台契约。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

import numpy as np

from config.config import Config
from core.grid import VoxelGrid
from core.ingest import gather_potential_fields
from core import potential_field as pf
from core.igrf import igrf_inc_dec
from core.velocity import ingest_velocity_model
from outputs import writers, render
from utils.logger import get_logger

logger = get_logger(__name__)


def _noop(msg, level="INFO"):
    pass


class GeophysEngine:
    @staticmethod
    def run(aoi_name: str, mineral_type: str, bbox: List[float], output_dir: str,
            params: Optional[Dict] = None, log_callback: Optional[Callable] = None,
            roots: Optional[Dict[str, str]] = None) -> Dict:
        params = params or {}
        log = log_callback or _noop
        roots = roots or Config.upstream_roots()
        os.makedirs(output_dir, exist_ok=True)
        created_at = datetime.now().isoformat(timespec="seconds")

        igrf_date = params.get("igrf_date", Config.IGRF_DATE)
        si = float(params.get("euler_si", Config.EULER_SI))
        cen_lon, cen_lat = 0.5*(bbox[0]+bbox[2]), 0.5*(bbox[1]+bbox[3])

        products: Dict = {}
        figures: List[str] = []
        model_stats: Dict = {"scale_note": "输入为全球区域网格(EMAG2~4km/ICGEM~14km)，产物为区域尺度，非矿体尺度"}
        warnings: List[str] = []

        # 1) 取磁/重网格
        fields, prov = gather_potential_fields(bbox, roots)
        model_stats["data_sources"] = prov
        log(f"物探网格: 磁={prov.get('magnetic',{}).get('status')} 重力={prov.get('gravity',{}).get('status')}")

        grid_dir = os.path.join(output_dir, "grids")
        fig_dir = os.path.join(output_dir, "figures")

        # 2) 位场处理（磁为主）
        euler_points: List[Dict] = []
        mag_epsg = None
        if "magnetic" in fields:
            fg = fields["magnetic"]
            mag_epsg = fg.epsg
            inc, dec, isrc = igrf_inc_dec(cen_lon, cen_lat, igrf_date)
            log(f"IGRF({isrc}) 倾角 {inc:.1f}° 偏角 {dec:.1f}°（date={igrf_date}）")
            grids = {
                "magnetic_rtp": pf.reduction_to_pole(fg.field, fg.dy, fg.dx, inc, dec),
                "magnetic_analytic_signal": pf.analytic_signal(fg.field, fg.dy, fg.dx),
                "magnetic_tilt": pf.tilt_angle(fg.field, fg.dy, fg.dx),
                "magnetic_vertical_deriv": pf.vertical_derivative(fg.field, fg.dy, fg.dx, 1),
                "magnetic_thd": pf.total_horizontal_derivative(fg.field, fg.dy, fg.dx),
                "magnetic_upward_2km": pf.upward_continuation(fg.field, fg.dy, fg.dx, 2000),
            }
            for k, arr in grids.items():
                products[k] = os.path.relpath(writers.write_grid_geotiff(
                    os.path.join(grid_dir, f"{k}.tif"), arr, fg), output_dir)

            # 欧拉反褶积磁源深度
            win = max(4, min(int(params.get("euler_window", Config.EULER_WINDOW)),
                             min(fg.field.shape)))
            euler_points = pf.euler_deconvolution(
                fg.field, fg.dy, fg.dx, fg.x_origin, fg.y_origin, si=si, window=win,
                depth_min_m=float(params.get("depth_min_m", 200.0)),
                depth_max_m=float(params.get("depth_max_m", 15000.0)))
            # 聚类成磁源（并给散点回写 cluster_id），再写产物
            # 水平尺度取欧拉窗 footprint：同一磁源的共位解合并
            euler_clusters = pf.cluster_euler_sources(
                euler_points, fg.dx, fg.dy, horiz_scale=win * 0.5 * (fg.dx + fg.dy))
            writers.write_euler_geojson(os.path.join(output_dir, "euler_sources.geojson"),
                                        euler_points, fg.epsg)
            products["euler_sources"] = "euler_sources.geojson"
            if euler_clusters:
                writers.write_euler_clusters_geojson(
                    os.path.join(output_dir, "euler_clusters.geojson"), euler_clusters, fg.epsg)
                products["euler_clusters"] = "euler_clusters.geojson"
            dep_arr = np.array([p['depth_m'] for p in euler_points], float) if euler_points else np.array([])
            log(f"欧拉磁源点: {len(euler_points)} 个 → {len(euler_clusters)} 簇" +
                (f"，深度中位 {np.median(dep_arr)/1000:.1f} km" if euler_points else ""))
            model_stats["igrf"] = {"inclination_deg": round(inc, 2), "declination_deg": round(dec, 2),
                                   "source": isrc, "date": igrf_date}
            model_stats["euler"] = {
                "n_points": len(euler_points), "si": si, "window": win,
                "depth_median_m": (round(float(np.median(dep_arr))) if euler_points else None),
                "depth_p25_m": (round(float(np.percentile(dep_arr, 25))) if euler_points else None),
                "depth_p75_m": (round(float(np.percentile(dep_arr, 75))) if euler_points else None),
                "n_clusters": len(euler_clusters),
                "mean_confidence": (round(float(np.mean([p.get('confidence', 0.0) for p in euler_points])), 3)
                                    if euler_points else None),
            }
            # 图件
            figures.append(render.render_field_map(fig_dir, "map_magnetic_rtp", grids["magnetic_rtp"], fg,
                                                   "磁异常化极(RTP)·区域", cmap="turbo"))
            figures.append(render.render_field_map(fig_dir, "map_analytic_signal",
                                                   grids["magnetic_analytic_signal"], fg,
                                                   "解析信号(磁源边界)+欧拉磁源点", cmap="magma",
                                                   euler=euler_points, epsg=fg.epsg))
            figures.append(render.render_field_map(fig_dir, "map_tilt", grids["magnetic_tilt"], fg,
                                                   "倾斜角(Tilt)·构造边界", cmap="coolwarm"))
            figures.append(render.render_euler_depth_hist(fig_dir, euler_points))
        else:
            warnings.append("无磁网格：prospector 未存 EMAG2，跳过位场处理（请先在 data-colle 运行该 AOI 或启用磁下载）")
            log("⚠ " + warnings[-1], "WARNING")

        # 3) 重力（粗，仅区域趋势：导数/Tilt）
        if "gravity" in fields:
            gg = fields["gravity"]
            gt = pf.tilt_angle(gg.field, gg.dy, gg.dx)
            products["gravity_tilt"] = os.path.relpath(writers.write_grid_geotiff(
                os.path.join(grid_dir, "gravity_tilt.tif"), gt, gg), output_dir)

        # 4) ANT/地震 3D Vs 体接入（可选）
        vel_path = params.get("velocity_path")
        model_stats["velocity_model"] = {"status": "absent"}
        if vel_path:
            grid = VoxelGrid(bbox, res_m=Config.GRID_RES_M, z_max_m=Config.GRID_ZMAX_M,
                             dz_m=Config.GRID_DZ_M, max_cells=Config.GRID_MAX_CELLS)
            vel = ingest_velocity_model(vel_path, grid)
            if vel:
                ncp = writers.write_velocity_nc(os.path.join(output_dir, "volume", "velocity_volume.nc"),
                                                vel["vs"], vel["favorability"], grid)
                products["velocity_volume_nc"] = os.path.relpath(ncp, output_dir)
                model_stats["velocity_model"] = {"status": "ok", "source": vel["source"],
                                                 "coverage": round(vel["coverage"], 3),
                                                 "grid": grid.summary()}
                log(f"ANT 速度体接入 OK（{vel['source']}，覆盖 {vel['coverage']:.2f}）")
            else:
                model_stats["velocity_model"] = {"status": "ingest_failed", "path": vel_path}
                warnings.append(f"速度体接入失败/格式不支持：{vel_path}")
                log("⚠ " + warnings[-1], "WARNING")

        if not products:
            raise RuntimeError("该 AOI 无任何可处理的物探数据（磁/重/速度体均缺）。")

        # 图件相对路径
        products["figures"] = [os.path.relpath(p, output_dir) for p in figures if p]
        model_stats["mineral_type"] = mineral_type
        model_stats["warnings"] = warnings

        # 大白话解读（给非物探专家）
        plain: List[str] = []
        eu = model_stats.get("euler", {})
        if eu.get("n_points"):
            dm = eu.get("depth_median_m")
            p25, p75 = eu.get("depth_p25_m"), eu.get("depth_p75_m")
            nc = eu.get("n_clusters")
            mc = eu.get("mean_confidence")
            conf_label = ("高" if (mc or 0) >= 0.66 else "中" if (mc or 0) >= 0.4 else "低")
            band = (f"（多数落在 {p25/1000:.1f}–{p75/1000:.1f} 公里）"
                    if p25 is not None and p75 is not None else "")
            plain.append(
                f"从区域磁场聚出 {nc} 个磁源（共 {eu['n_points']} 个欧拉解），主深度中位 "
                f"{dm/1000:.1f} 公里{band}，整体置信度{conf_label}——说明这一带磁性矿/岩体大概在这个深度，"
                f"越集中、置信越高的磁源越可靠（区域级估计，非精确）。")
        if "magnetic_analytic_signal" in products:
            plain.append("『解析信号』图上的亮区＝磁性体的边界/中心，常对应含矿岩体或控矿构造；"
                         "『化极(RTP)』图把磁异常归位到磁体正上方，方便定位。")
        if model_stats.get("velocity_model", {}).get("status") == "ok":
            plain.append("已接入被动源地震(ANT)速度体：低速度区＝破碎/蚀变/可能矿化带，已作三维证据。")
        plain.append("⚠ 这些是『区域尺度』结果（输入是全球~4km磁/重网格），用于判断大致深度与构造背景，"
                     "不是矿体级精度。")
        plain.append("👉 具体『哪里、多深、概率多大』的钻探靶区，请看 geo-model3d 三维模型——"
                     "本服务的磁源深度与速度体已自动喂给它，让靶点更准。")
        model_stats["plain_summary"] = plain

        crs_str = f"EPSG:{mag_epsg}" if mag_epsg else "EPSG:4326"
        meta_path = writers.write_metadata(output_dir, aoi_name, bbox, crs_str,
                                           products, model_stats, created_at, tenant_id=params.get("tenant_id"))
        log("完成。")
        return {"result_dir": output_dir, "metadata_path": meta_path,
                "products": products, "model_stats": model_stats,
                "n_euler": len(euler_points)}
