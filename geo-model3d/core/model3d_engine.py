"""Model3DEngine —— 编排 T2~T7 全流程，产出 results 目录与平台契约。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

import numpy as np

from config.config import Config
from core.grid import VoxelGrid
from core.ingest import gather_evidence
from core.evidence import build_surface_layers, depth_consistency_profile, depth_preference_profile
from core import knowledge
from core.scorers import (knowledge_weighted_fusion, fuse_surface_2d, gate_to_3d,
                          fuzzy_gamma_score, bayesian_fusion_score)
from core.uncertainty import uncertainty_volume
from core import geophys as geophys_mod
from core.validate import surface_consistency, weight_sensitivity
from outputs import writers, render
from utils.logger import get_logger

logger = get_logger(__name__)


def _noop(msg, level="INFO"):
    pass


def _safe_stat(arr, fn, nd: int = 4):
    """对可能含 NaN 的数组做 nan 安全统计：仅取有限值；全无效返回 None（绝不产出 NaN）。"""
    a = np.asarray(arr)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return round(float(fn(a)), nd)


def _json_safe(obj):
    """递归把 NaN/Infinity 等非有限浮点替换为 None——保证产出**合法 JSON**。

    Python json/Flask 默认会把 NaN 写成字面量 `NaN`，浏览器 JSON.parse 会直接报错
    （表现为前端进度卡住、图表不渲染）。在写 metadata.json 与返回前统一清洗。
    """
    import math
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, np.floating):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _write_known_deposits_geojson(path: str, points: List[Dict]) -> str:
    """已知矿点 → GeoJSON（供溯源/复用；不含任何预测靶点）。"""
    import json
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
              "properties": {"commodity": p.get("commodity", ""), "deposit_type": p.get("deposit_type", ""),
                             "name": p.get("name", ""), "source": p.get("source", "")}}
             for p in points]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, ensure_ascii=False)
    return path


def _data_driven_2d(surface_layers, known_rc, grid, log, barren_rc=None):
    """数据驱动 2D 有利度，按 PU→RF→WofE 顺序（PU 最诚实：未标注≠负样本）。
    barren_rc=钻孔确认无矿(真负样本，可选，闭环)。
    返回 (F_xy, method, feature_importance, pu_uncertainty2d|None)；全失败则抛异常→上层回退知识融合。"""
    from core import ml_scorers
    from core.scorers import woe_score
    nb = len(barren_rc) if barren_rc else 0
    if ml_scorers.has_sklearn():
        try:
            F, unc2d, st = ml_scorers.pu_bagging_score(surface_layers, known_rc, grid, barren_rowcol=barren_rc)
            fi = {"method": "pu_bagging", "importances": st["importances"],
                  "n_positive": st["n_positive"], "n_bags": st["n_bags"],
                  "n_barren": st.get("n_barren", 0), "mean_uncertainty": st["mean_uncertainty"]}
            log(f"数据驱动 PU-learning：{st['n_positive']}正/{st['n_bags']}袋/真负{st.get('n_barren',0)}，"
                f"平均不确定性={st['mean_uncertainty']}，重要性top={list(st['importances'].items())[:3]}")
            return F, "pu", fi, unc2d
        except Exception as e:
            log(f"PU 失败，尝试随机森林：{e}", "WARNING")
        try:
            F, st = ml_scorers.rf_score(surface_layers, known_rc, grid, barren_rowcol=barren_rc)
            fi = {"method": "random_forest", "importances": st["importances"], "oob_score": st["oob_score"],
                  "n_positive": st["n_positive"], "n_negative": st["n_negative"], "n_barren": st.get("n_barren", 0)}
            log(f"数据驱动 随机森林：OOB={st['oob_score']}，真负{st.get('n_barren',0)}，重要性top={list(st['importances'].items())[:3]}")
            return F, "rf", fi, None
        except Exception as e:
            log(f"随机森林失败，尝试 WofE：{e}", "WARNING")
    F, wt = woe_score(surface_layers, known_rc, grid, label_source="known_deposits")
    log("数据驱动 WofE（sklearn 不可用或 RF/PU 失败）")
    return F, "woe", {"method": "woe", "weights": wt}, None


def _write_deposits_layer(output_dir, aoi_name, bbox, grid, points, label_status, created_at):
    """把本次用到的已知矿点写入 deposits broker 布局：<AOI>/deposits/<run_id>/。"""
    import json
    run_id = os.path.basename(output_dir.rstrip("/"))
    aoi_root = os.path.dirname(os.path.dirname(output_dir.rstrip("/")))   # .../<AOI>
    dep_dir = os.path.join(aoi_root, "deposits", run_id)
    os.makedirs(dep_dir, exist_ok=True)
    _write_known_deposits_geojson(os.path.join(dep_dir, "known_deposits.geojson"), points)
    meta = {"source": "geo-deposits", "source_version": "1.0", "aoi_name": aoi_name,
            "aoi_bbox": [float(v) for v in bbox], "crs": grid.crs.to_string(),
            "created_at": created_at, "products": {"known_deposits": "known_deposits.geojson"},
            "label_status": label_status}
    with open(os.path.join(dep_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return dep_dir


class Model3DEngine:
    @staticmethod
    def run(aoi_name: str, mineral_type: str, bbox: List[float], output_dir: str,
            params: Optional[Dict] = None, log_callback: Optional[Callable] = None,
            roots: Optional[Dict[str, str]] = None) -> Dict:
        """主流程。返回结果摘要 dict（含 result_dir / products / model_stats / targets）。"""
        params = params or {}
        log = log_callback or _noop
        roots = roots or Config.upstream_roots()
        os.makedirs(output_dir, exist_ok=True)
        created_at = datetime.now().isoformat(timespec="seconds")

        # P2 特性B：2D 证据融合方法（knowledge|fuzzy|bayesian）；仅作用于知识回退路径
        fusion_method = (params.get("fusion_method") or getattr(Config, "FUSION_METHOD", "knowledge")).lower()
        if fusion_method not in ("knowledge", "fuzzy", "bayesian"):
            fusion_method = "knowledge"

        def _knowledge_fuse(layers, w):
            """按 fusion_method 分派 2D 融合。返回 (F_xy, used_weights, var2d|None)。"""
            if fusion_method == "fuzzy":
                F, used = fuzzy_gamma_score(layers, w, gamma=params.get("fuzzy_gamma", Config.FUZZY_GAMMA))
                return F, used, None
            if fusion_method == "bayesian":
                F, var2d = bayesian_fusion_score(layers, w)
                used = {k: w.get(k, 0.0) for k in layers if w.get(k, 0) > 0}  # 报告先验权重
                return F, used, var2d
            F, used = fuse_surface_2d(layers, w)
            return F, used, None

        # 1) 网格
        grid = VoxelGrid(bbox,
                         res_m=params.get("res_m", Config.GRID_RES_M),
                         z_max_m=params.get("z_max_m", Config.GRID_ZMAX_M),
                         dz_m=params.get("dz_m", Config.GRID_DZ_M),
                         max_cells=Config.GRID_MAX_CELLS)
        log(f"体元网格 {grid.shape} @ {grid.crs.to_string()} res={grid.res_m:.0f}m")

        # 2) 取证（含方向四已知矿点标签）
        es = gather_evidence(bbox, mineral_type, grid, roots,
                             known_deposits_upload=params.get("known_deposits_path"),
                             deposits_cache=getattr(Config, "DEPOSITS_CACHE_DIR", None),
                             drill_feedback_path=params.get("drill_feedback_path"))
        log(f"证据层: {es.available_layers()}; 矿床类型: {es.deposit_type or '未知'}")

        # 3) 解析成因族 + 适用度警告
        #    deposit_type 仅在与用户矿种一致时用于定族（否则尊重用户矿种）
        dt_for_family = es.deposit_type if es.deposit_type_trusted else None
        fam = knowledge.resolve_family(mineral_type, dt_for_family, es.tectonic_setting)
        spec = knowledge.family_spec(fam)
        applicability = spec.get("applicability")
        warnings: List[str] = []
        if applicability in ("low", "na"):
            warnings.append(
                f"矿床族 {fam} 适用度={applicability}：{spec.get('note')}。三维建模增益有限，建议以遥感/化探/物探为主。")
            log("⚠ " + warnings[-1], "WARNING")
        log(f"成因族={fam} 深度带={spec['depth_km']}km 适用度={applicability}")

        if not es.available_layers():
            raise RuntimeError("该 AOI 无任何可用上游证据（蚀变/构造/形变均缺），无法建模。")

        # 4) 取 2D 地表证据层
        surface_layers, coverage = build_surface_layers(es)
        band = spec["depth_km"]
        weights = dict(spec["w"])

        # 化探接入：geo-geochem 多元素组合异常作 2D 地表证据，按成因族权重并入融合
        if "geochem" in surface_layers:
            weights["geochem"] = knowledge.geochem_weight(fam)
        if "slowvars" in surface_layers:
            weights["slowvars"] = float(params.get("slowvars_weight", 0.15))
            log(f"七慢变量接入: Δ判别式作为机制综合证据层，权重={weights['slowvars']:.2f}")
        # 曲率接入：geo-stru 地形曲率(褶皱铰链/张性扩张带)作 2D 地表证据，并入融合
        if "curvature" in surface_layers:
            weights["curvature"] = float(params.get("curvature_weight", 0.08))
            log(f"曲率接入: 褶皱铰链/扩张带控矿，权重={weights['curvature']:.2f}")
        # 活动断裂接入：geo-stru insar_fusion 实测形变梯度(活动构造)作 2D 地表证据
        if "active_fault" in surface_layers:
            weights["active_fault"] = float(params.get("active_fault_weight", 0.10))
            log(f"活动断裂接入: InSAR 实测形变梯度(活动构造)，权重={weights['active_fault']:.2f}")

        # 4b) 物探接入（方向二）：磁源深度→实测深度门控；磁AS→2D证据；速度→3D证据
        geo = geophys_mod.load_geophys(bbox, grid, roots.get("geophys", ""), mineral_type) if roots.get("geophys") else None
        geo_used = bool(geo and geo.get("status") == "ok")
        geo_stats: Dict = {"status": (geo or {}).get("status", "not_queried")}
        if geo_used:
            if geo.get("magnetic") is not None:
                surface_layers["magnetic"] = geo["magnetic"]
                coverage["magnetic"] = np.isfinite(geo["magnetic"])
                weights["magnetic"] = knowledge.magnetic_weight(fam)
            euler_pts = geo.get("euler_points") or []
            measured_gate, geo_cov2d = geophys_mod.measured_depth_gate(grid, euler_pts)
            vs_fav = geo.get("vs_favorability")
            geo_stats.update(n_euler=len(euler_pts),
                             magnetic=("yes" if geo.get("magnetic") is not None else "no"),
                             velocity=("yes" if vs_fav is not None else "no"),
                             run_id=geo.get("run_id"), scale_note=geo.get("scale_note"))
            log(f"物探接入: 磁源点 {len(euler_pts)}, 磁AS {geo_stats['magnetic']}, 速度体 {geo_stats['velocity']}")
        else:
            measured_gate, geo_cov2d, vs_fav = None, None, None
            if geo and geo.get("status") == "missing":
                log("物探: 未发现本AOI产物，按纯知识深度建模")

        # 5) 定预测方法（方向四）：数据驱动(PU→RF→WofE) → [P4]域自适应迁移 → 知识加权融合（诚实回退）
        from core.labels import rasterize_points
        from core import transfer
        label_min = int(getattr(Config, "LABEL_MIN", 8))
        known_rc = rasterize_points(es.known_deposits, grid)
        barren_rc = rasterize_points(es.known_barren, grid) if es.known_barren else None
        label_status = {"n_positive": len(es.known_deposits), "n_in_grid": len(known_rc),
                        "n_barren": len(es.known_barren), "label_min": label_min,
                        "sources": (es.provenance.get("labels", {}) or {}).get("sources", []),
                        "drill_feedback": es.provenance.get("drill_feedback", {})}
        reg_dir = getattr(Config, "MODEL_REGISTRY_DIR", None)
        prediction_method = "knowledge"
        feature_importance = None
        pu_unc2d = None
        bayes_var2d = None          # 贝叶斯后验方差(仅 fusion_method=bayesian 的知识回退路径产生)
        transfer_stats = None
        if len(known_rc) >= label_min and surface_layers:
            try:
                F_xy, prediction_method, feature_importance, pu_unc2d = _data_driven_2d(
                    surface_layers, known_rc, grid, log, barren_rc=barren_rc)
                used_w = {k: None for k in surface_layers}   # 数据驱动自带证据权(见 feature_importance)
                label_status["sufficient"] = True
                # P4：标签充足→训练并持久化"源模型"，供数据稀缺新区跨区迁移
                if reg_dir and transfer.has_deps():
                    try:
                        bundle = transfer.train_transferable(surface_layers, known_rc, grid)
                        if bundle:
                            transfer.save_source_model(reg_dir, fam, mineral_type, bundle, aoi_name)
                            log(f"已持久化源模型（族={fam}）供跨区迁移")
                    except Exception as e:
                        log(f"源模型持久化跳过：{e}", "WARNING")
            except Exception as e:
                F_xy, used_w, bayes_var2d = _knowledge_fuse(surface_layers, weights)
                label_status["sufficient"] = False
                label_status["ml_error"] = str(e)
                log(f"数据驱动全失败，回退知识融合：{e}", "WARNING")
        else:
            label_status["sufficient"] = False
            # P4：标签不足→若有同成因族源模型，做域自适应跨区迁移（介于数据驱动与知识回退之间）
            src = transfer.find_source_model(reg_dir, fam, mineral_type) if (reg_dir and transfer.has_deps()) else None
            if src is not None and surface_layers:
                try:
                    F_xy, pu_unc2d, transfer_stats = transfer.domain_adapt_score(surface_layers, src, grid)
                    used_w = {k: None for k in surface_layers}
                    prediction_method = "domain_adapt"
                    tc = transfer_stats.get("transfer_confidence")
                    log(f"域自适应迁移：源={transfer_stats.get('source_aoi')} 族={fam} "
                        f"迁移置信度={tc} 特征漂移={transfer_stats.get('feature_shift')}")
                    if tc is not None and tc < 0.4:
                        warnings.append(f"跨区迁移置信度低({tc})：源/目标特征分布差异大，结果仅供参考。")
                        log("⚠ " + warnings[-1], "WARNING")
                except Exception as e:
                    F_xy, used_w, bayes_var2d = _knowledge_fuse(surface_layers, weights)
                    log(f"域自适应失败，回退知识融合：{e}", "WARNING")
            else:
                F_xy, used_w, bayes_var2d = _knowledge_fuse(surface_layers, weights)
                if len(known_rc) > 0:
                    log(f"已知矿点 {len(known_rc)} < LABEL_MIN({label_min})，回退知识融合")
        # 打分用"带中心峰"深度偏好（靶点落在成矿带内预期深度，而非被表层项打平到顶层）
        dc_profile = depth_preference_profile(grid, band)
        dc3d = geophys_mod.blend_depth_gate(dc_profile, grid, measured_gate, geo_cov2d)
        score = (F_xy[None, :, :] * dc3d).astype("float32")
        # P2 特性A：断裂三维倾向投影骨架（替代纯垂直向深尾部；深部沿倾向横移）
        struct_dip_used = None
        if "structure" in surface_layers and weights.get("structure", 0) > 0:
            from core.evidence import structure_skeleton_volume
            struct_dip_used = knowledge.structure_dip(fam)
            skel = structure_skeleton_volume(grid, surface_layers["structure"], es.strikes,
                                             band, struct_dip_used)
            wsum = sum(v for k, v in weights.items() if k in surface_layers and v > 0) or 1.0
            alpha = 0.5 * (weights.get("structure", 0.0) / wsum)
            score = (1 - alpha) * score + alpha * skel
        # 速度反演体作真三维证据：仅在**有覆盖处**混合（NaN=无数据，保留原分数，避免误压低）
        vs_cov = None
        if vs_fav is not None:
            vs_cov = np.isfinite(vs_fav)
            cov_frac = float(vs_cov.mean())
            if cov_frac > 0:
                beta = getattr(Config, "VELOCITY_BETA", 0.35)
                vfill = np.where(vs_cov, np.clip(vs_fav, 0.0, 1.0), 0.0).astype("float32")
                blended = (1 - beta) * score + beta * vfill
                score = np.where(vs_cov, blended, score).astype("float32")
                geo_stats["velocity_coverage"] = round(cov_frac, 4)
                log(f"速度体三维融合: 覆盖 {cov_frac*100:.1f}% 体元, β={beta}")
            else:
                vs_fav = None     # 速度体无有效覆盖→视同无速度证据
                geo_stats["velocity"] = "no_coverage"
        score = np.clip(score, 0.0, 1.0).astype("float32")
        mode = "gate+geophys" if geo_used else "gate"
        log(f"融合权重(归一化): { {k: (round(v,3) if isinstance(v,(int,float)) else 'woe') for k,v in used_w.items()} }")

        # 6) 不确定性（物探实测约束区下降）
        weak = es.provenance.get("structure", {}).get("status") == "weak"
        unc = uncertainty_volume(surface_layers, coverage, grid, band, weak_structure=weak)
        if geo_used:
            constraint = np.zeros(grid.shape, dtype="float32")
            if geo_cov2d is not None:
                constraint = np.maximum(constraint, geo_cov2d[None, :, :])
            if vs_fav is not None and vs_cov is not None:
                constraint = np.maximum(constraint, vs_cov.astype("float32") * 0.6)
            unc = np.clip(unc * (1.0 - 0.4 * constraint), 0.0, 1.0).astype("float32")
        # 数据驱动预测不确定性（PU 集成离散度）并入不确定性体：模型分歧大处不确定性升高
        if pu_unc2d is not None:
            pu3d = np.repeat(np.where(np.isfinite(pu_unc2d), pu_unc2d, 0.0).astype("float32")[None, :, :],
                             grid.shape[0], axis=0)
            unc = np.clip(0.5 * unc + 0.5 * pu3d, 0.0, 1.0).astype("float32")
        # 贝叶斯后验方差并入不确定性体（仅 fusion_method=bayesian 知识回退时）
        if bayes_var2d is not None:
            bv3d = np.repeat(np.where(np.isfinite(bayes_var2d), bayes_var2d, 0.0).astype("float32")[None, :, :],
                             grid.shape[0], axis=0)
            unc = np.clip(0.5 * unc + 0.5 * bv3d, 0.0, 1.0).astype("float32")

        # 6.5) 水体排除：河流/湖泊等水域不可能是岩金成矿有利区，将 score 置零
        from core import water_mask as wm
        water_exclusion: Dict = {"status": "not_attempted"}
        analyser_run_dir = (es.provenance.get("alteration") or {}).get("run_dir")
        insar_dir = (es.provenance.get("deformation") or {}).get("insar_dir")
        water = wm.build_water_mask(
            grid, analyser_run_dir,
            mineral_type=mineral_type,
            water_mask_path=params.get("water_mask_path"),
            insar_dir=insar_dir)
        if water is not None:
            water_3d = np.broadcast_to(water[None, :, :], score.shape)
            n_water = int(water.sum())
            n_total = water.size
            score = np.where(water_3d, 0.0, score).astype("float32")
            # 不确定性也标记为最高（水体处无有效预测）
            unc = np.where(water_3d, 1.0, unc).astype("float32")
            water_exclusion = {
                "status": "ok",
                "source": "multi_strategy",
                "water_pixels": n_water,
                "total_pixels": n_total,
                "water_ratio": round(n_water / n_total, 4) if n_total > 0 else 0,
            }
            log(f"水体排除: {n_water}/{n_total} 像元 ({n_water/n_total*100:.1f}%)")
        else:
            water_exclusion = {"status": "no_mask", "note": "无法构建水体掩码，未排除"}

        # 7) 输出
        vol_dir = os.path.join(output_dir, "volume")
        slice_dir = os.path.join(output_dir, "depth_slices")
        fig_dir = os.path.join(output_dir, "figures")
        nc_path = writers.write_volume_netcdf(os.path.join(vol_dir, "prospectivity_volume.nc"),
                                              score, unc, grid)
        slice_tifs = writers.write_depth_slices(slice_dir, score, grid)
        targets = writers.write_targets_3d(os.path.join(output_dir, "targets_3d.json"),
                                           score, unc, grid,
                                           top_n=params.get("top_n", 20))
        writers.write_targets_kml(os.path.join(output_dir, "targets_3d.kml"),
                                  targets, aoi_name=aoi_name)
        slice_pngs = render.render_depth_slices(fig_dir, score, grid, targets=targets)
        profile_png = render.render_depth_profile(fig_dir, score, unc, grid)
        # P2 特性C：三维 Web 查看器（非关键产物，失败不影响整次 run）
        viewer_rel = None
        try:
            from outputs import viewer
            viewer_path = viewer.write_web_viewer(
                os.path.join(output_dir, "viewer_3d.html"), score, unc, grid, targets,
                max_points=getattr(Config, "VIEWER_MAX_POINTS", 60000),
                vexag=getattr(Config, "VIEWER_VEXAG", 3.0), aoi_name=aoi_name, family=fam)
            viewer_rel = os.path.relpath(viewer_path, output_dir)
        except Exception as e:
            log(f"三维查看器生成跳过：{e}", "WARNING")
        log(f"输出: NetCDF 体 + {len(slice_tifs)} 深度切片 + {len(targets)} 靶点")

        # 8) 相对验证（无真值）
        consistency = surface_consistency(score, grid, es)
        # 权重敏感性仅对"知识加权融合"有意义（它扰动知识权重重算靶点）。
        # 数据驱动(pu/rf/woe)/迁移/模糊/贝叶斯路径的靶点由别的打分器决定，扰动知识权重得到的
        # 靶点与之不可比（会得到误导性的 0 保留率），故标注为不适用。
        if prediction_method == "knowledge" and fusion_method == "knowledge":
            sensitivity = weight_sensitivity(surface_layers, weights, targets, grid)
        else:
            sensitivity = {"status": "n/a", "reason": f"预测方法={prediction_method}/融合={fusion_method}，"
                           "非知识加权融合，权重敏感性不适用"}
        # 知识驱动校验（方向四 P2）：所定成矿族是否与大地构造背景自洽
        from core import metallogenic_kb
        kb_consistency = metallogenic_kb.knowledge_consistency(fam, es.tectonic_setting, mineral_type)
        if not kb_consistency.get("plausible", True):
            warnings.append(kb_consistency.get("note", ""))
            log("⚠ " + kb_consistency.get("note", ""), "WARNING")

        # 8b) 已知矿点：写产物 + 留一交叉验证（仅 WofE 模式有意义）
        known_geojson_rel = None
        loo = {"status": "no_labels"}
        if es.known_deposits:
            _write_known_deposits_geojson(os.path.join(output_dir, "known_deposits.geojson"),
                                          es.known_deposits)
            known_geojson_rel = "known_deposits.geojson"
            try:
                _write_deposits_layer(output_dir, aoi_name, bbox, grid, es.known_deposits,
                                      label_status, created_at)
            except Exception as e:
                log(f"deposits 层写入跳过：{e}", "WARNING")
            if prediction_method in ("woe", "rf", "pu") and len(known_rc) >= 5:
                from core.validate import loo_hit_rate
                loo = loo_hit_rate(F_xy, known_rc, grid)   # 在 2D 有利度上评估(非深度门控后)

        # 9) 元数据契约
        def _rel(p):
            return os.path.relpath(p, output_dir)
        products = {
            "prospectivity_volume_nc": _rel(nc_path),
            "targets_3d": "targets_3d.json",
            "targets_3d_kml": "targets_3d.kml",
            "depth_slices_dir": _rel(slice_dir),
            "depth_profile_png": _rel(profile_png),
        }
        for p in slice_pngs:
            products.setdefault("slice_pngs", [])
        products["slice_pngs"] = [_rel(p) for p in slice_pngs]
        if known_geojson_rel:
            products["known_deposits"] = known_geojson_rel
        if viewer_rel:
            products["web_viewer_html"] = viewer_rel

        provenance = dict(es.provenance)
        provenance["geophys"] = geo_stats
        # modeling_method 反映 2D 融合方式（仅知识回退路径用 fuzzy/bayesian；数据驱动/迁移不变）
        _fusion_label = {"knowledge": "knowledge_weighted_fusion",
                         "fuzzy": "fuzzy_gamma_fusion",
                         "bayesian": "bayesian_posterior_fusion"}[fusion_method]
        _base = _fusion_label if prediction_method == "knowledge" else f"{prediction_method}(2D)"
        model_stats = {
            "modeling_method": _base + ("+geophys" if geo_used else "(P1)"),
            "prediction_method": prediction_method,
            "fusion_method": fusion_method,
            "fusion_mode": mode,
            "family": fam,
            "family_applicability": applicability,
            "depth_km_band": spec["depth_km"],
            "structure_geometry": {
                "method": "dip_projected_skeleton" if struct_dip_used is not None else "none",
                "dip_deg": struct_dip_used,
                "strikes_deg": [round(float(s), 1) for s in (es.strikes or [])[:3]],
            },
            "deposit_type": es.deposit_type,
            "tectonic_setting": es.tectonic_setting,
            "used_weights": {k: (round(v, 4) if isinstance(v, (int, float)) else None)
                             for k, v in used_w.items()},
            "label_status": label_status,
            "feature_importance": feature_importance,
            "transfer": transfer_stats,
            "geophysics": geo_stats,
            "data_sources": provenance,
            "available_layers": es.available_layers(),
            "score_stats": {"min": _safe_stat(score, np.min), "max": _safe_stat(score, np.max),
                            "mean": _safe_stat(score, np.mean)},
            "uncertainty_stats": {"surface_mean": _safe_stat(unc[0], np.mean),
                                  "deepest_mean": _safe_stat(unc[-1], np.mean),
                                  "mean": _safe_stat(unc, np.mean)},
            "validation": {"surface_consistency": consistency, "weight_sensitivity": sensitivity,
                           "loo_hit_rate": loo, "knowledge_consistency": kb_consistency},
            "n_targets": len(targets),
            "water_exclusion": water_exclusion,
            "warnings": warnings,
        }
        # 关键：清洗 NaN/Infinity → None，保证 metadata.json 与 API 返回是合法 JSON
        # （否则浏览器 JSON.parse 报错，表现为进度卡住、图表不渲染）
        model_stats = _json_safe(model_stats)
        targets = _json_safe(targets)
        meta_path = writers.write_metadata(output_dir, aoi_name, bbox, grid, products,
                                           model_stats, created_at, tenant_id=params.get("tenant_id"))

        log("完成。")
        return {
            "result_dir": output_dir,
            "metadata_path": meta_path,
            "products": products,
            "model_stats": model_stats,
            "targets": targets,
        }
