"""分析流水线协调器"""
import concurrent.futures
import json
import os
import numpy as np
from pathlib import Path
from datetime import datetime

from app.config import (
    UPLOAD_DIR, RESULTS_DIR, NODATA,
    DEFAULT_WEIGHTS, DEFAULT_DELTA_THRESHOLD, DEFAULT_DELTA_PERCENTILE,
    DEFAULT_GAUSSIAN_SIGMA, DEFAULT_TARGET_RESOLUTION,
    DEFAULT_NDVI_VEG_THRESHOLD,
)
from app.models.task_store import task_store
from app.processing.alignment import prepare_data
from app.processing.slow_variables import (
    compute_stress_gradient,
    _valid_mask,
    compute_redox_gradient,
    compute_fluid_overpressure,
    compute_fault_activity,
    compute_cap_rock_pressure,
    compute_temperature_gradient,
    compute_chemical_potential,
)
from app.processing.fusion import (
    zscore_normalize, compute_fusion,
    extract_target_zones, compute_stats,
)
from app.processing.thermal import compute_lst_proxy, compute_seasonal_diff
from app.processing.utils import write_cog
from app.processing.vectorize import dominant_driver_layer, write_target_zones_geojson


# 流水线步骤列表（用于进度追踪）
PIPELINE_STEPS = [
    ("parsing_kml", "解析KML研究区边界"),
    ("aligning_grids", "对齐多源栅格数据"),
    ("computing_stress", "计算① 地应力异常梯度"),
    ("computing_redox", "计算② 氧逸度突变带强度"),
    ("computing_fluid", "计算③ 流体超压指数"),
    ("computing_fault", "计算④ 断裂活动性指数"),
    ("computing_chem", "计算⑤ 化学势梯度"),
    ("computing_cap_rock", "计算⑥ 盖层封闭性"),
    ("computing_temp", "计算⑦ 温度异常梯度"),
    ("normalizing", "Z-score标准化"),
    ("computing_fusion", "计算Δ判别式"),
    ("extracting_targets", "提取靶区"),
    ("writing_outputs", "写入结果文件"),
    ("generating_stats", "生成统计摘要"),
]

EVIDENCE_CATALOG = {
    "stress_gradient": {
        "category": "atomic_evidence",
        "evidence_role": "structural_stress_or_deformation_concentration",
        "proxy_level": "proxy",
        "recommended_consumers": ["geo-model3d", "geo-drill", "geo-exploration", "geo-reporter"],
    },
    "redox_gradient": {
        "category": "atomic_evidence",
        "evidence_role": "redox_transition_interface",
        "proxy_level": "proxy",
        "recommended_consumers": ["geo-model3d", "geo-exploration", "geo-reporter"],
    },
    "fluid_overpressure": {
        "category": "atomic_evidence",
        "evidence_role": "hydrothermal_or_fluid_activity",
        "proxy_level": "proxy",
        "recommended_consumers": ["geo-model3d", "geo-drill", "geo-exploration", "geo-reporter"],
    },
    "fault_activity": {
        "category": "atomic_evidence",
        "evidence_role": "ore_controlling_fault_or_permeability_pathway",
        "proxy_level": "proxy",
        "recommended_consumers": ["geo-model3d", "geo-drill", "geo-exploration", "geo-reporter"],
    },
    "chem_potential": {
        "category": "atomic_evidence",
        "evidence_role": "alteration_and_element_migration_gradient",
        "proxy_level": "proxy",
        "recommended_consumers": ["geo-model3d", "geo-exploration", "geo-reporter"],
    },
    "cap_rock_pressure": {
        "category": "atomic_evidence",
        "evidence_role": "seal_or_preservation_condition",
        "proxy_level": "proxy",
        "recommended_consumers": ["geo-model3d", "geo-drill", "geo-reporter"],
    },
    "temp_gradient": {
        "category": "atomic_evidence",
        "evidence_role": "thermal_anomaly_boundary",
        "proxy_level": "proxy",
        "recommended_consumers": ["geo-model3d", "geo-exploration", "geo-reporter"],
    },
    "driving_force_b": {
        "category": "composite_evidence",
        "evidence_role": "slow_variable_driving_force",
        "proxy_level": "composite_proxy",
        "recommended_consumers": ["geo-model3d", "geo-drill", "geo-exploration", "geo-reporter"],
    },
    "resistance_a": {
        "category": "composite_evidence",
        "evidence_role": "slow_variable_resistance",
        "proxy_level": "composite_proxy",
        "recommended_consumers": ["geo-model3d", "geo-drill", "geo-reporter"],
    },
    "delta_discriminant": {
        "category": "composite_evidence",
        "evidence_role": "cusp_catastrophe_favorability_discriminant",
        "proxy_level": "derived_index",
        "recommended_consumers": ["geo-model3d", "geo-drill", "geo-exploration", "geo-reporter"],
    },
    "dominant_driver": {
        "category": "explainability",
        "evidence_role": "dominant_slow_variable_class",
        "proxy_level": "derived_index",
        "recommended_consumers": ["geo-drill", "geo-reporter"],
    },
    "target_zones": {
        "category": "target",
        "evidence_role": "candidate_target_mask",
        "proxy_level": "derived_index",
        "recommended_consumers": ["geo-drill", "geo-reporter"],
    },
}


def layer_unit_scale(name: str) -> str:
    if name in {"driving_force_b", "resistance_a", "delta_discriminant"}:
        return "weighted_zscore_composite"
    if name in {"target_zones", "dominant_driver"}:
        return "categorical"
    return "raw_proxy_index"


async def run_pipeline(task_id: str, upload_id: str, params: dict | None = None):
    """执行完整的14步分析流水线"""
    params = params or {}
    upload_dir = str(UPLOAD_DIR / upload_id)
    geologic_context = params.get("geologic_context")
    if not geologic_context:
        ctx_path = Path(upload_dir) / "geologic_context.json"
        if ctx_path.exists():
            try:
                geologic_context = json.loads(ctx_path.read_text(encoding="utf-8"))
            except Exception:
                geologic_context = None

    ctx_deposit_type = (geologic_context or {}).get("deposit_type")
    ctx_mineral = (geologic_context or {}).get("mineral_hint")
    deposit_type = params.get("deposit_type") or ctx_deposit_type
    mineral = params.get("mineral") or params.get("commodity") or ctx_mineral
    family = params.get("family")
    resolved_family = family
    weights_source = "default"

    # 权重解析优先级:显式 weights > 矿床类型/族预设 > 全局默认
    if params.get("weights"):
        weights = params["weights"]
        weights_source = "explicit"
    elif deposit_type or family or mineral:
        from app.processing.deposit_presets import resolve_preset_weights
        weights, resolved_family = resolve_preset_weights(
            deposit_type=deposit_type, family=family, commodity=mineral)
        weights_source = "preset"
    else:
        weights = DEFAULT_WEIGHTS
    delta_threshold = params.get("delta_threshold", DEFAULT_DELTA_THRESHOLD)
    sigma = params.get("gaussian_sigma", DEFAULT_GAUSSIAN_SIGMA)
    trace_id = params.get("trace_id")
    aoi_name = params.get("aoi_name") or params.get("project") or upload_id

    result_dir = RESULTS_DIR / task_id
    result_dir.mkdir(parents=True, exist_ok=True)

    step_idx = 0
    total_steps = len(PIPELINE_STEPS)

    def progress(step_name: str, step_desc: str):
        nonlocal step_idx
        step_idx += 1
        pct = round(step_idx / total_steps * 100, 1)
        task_store.update(task_id, progress=pct, current_step=step_desc)

    try:
        task_store.update(task_id, status="running", current_step="准备数据")

        # ─── Step 1-2: 数据准备 ───────────────────────────────
        progress("parsing_kml", "解析KML研究区边界")
        aligned, meta, roi = prepare_data(upload_dir)
        progress("aligning_grids", "对齐多源栅格数据")

        transform = meta["transform"]
        crs = meta["crs"]
        pixel_size_m = (abs(transform.a), abs(transform.e))  # 目标网格像元尺寸(米)

        # 取出各波段（缺失波段共享NODATA数组，计算函数不修改输入）
        dem = aligned.get("dem", np.full((meta["height"], meta["width"]), NODATA))
        insar = aligned.get("insar")
        insar_coh = aligned.get("insar_coherence")
        nodata_arr = np.full((meta["height"], meta["width"]), NODATA)
        s2_b03 = aligned.get("s2_b03", nodata_arr)
        s2_b04 = aligned.get("s2_b04", nodata_arr)
        s2_b08 = aligned.get("s2_b08", nodata_arr)
        aster_b05 = aligned.get("aster_b05", nodata_arr)
        aster_b06 = aligned.get("aster_b06", nodata_arr)
        aster_b07 = aligned.get("aster_b07", nodata_arr)
        aster_b08 = aligned.get("aster_b08", nodata_arr)
        # P2 蚀变扩展波段(可选,缺失为 None → 相关端元自动跳过)
        s2_b02 = aligned.get("s2_b02")
        s2_b11 = aligned.get("s2_b11")
        s2_b12 = aligned.get("s2_b12")
        aster_b01 = aligned.get("aster_b01")
        aster_b03n = aligned.get("aster_b03n")
        aster_b09 = aligned.get("aster_b09")
        aster_b12 = aligned.get("aster_b12")
        aster_b13 = aligned.get("aster_b13")
        aster_b14 = aligned.get("aster_b14")

        # 植被掩膜(NDVI>0.20 剔除,复用 geo-analyser 阈值),供②⑤⑥蚀变指数
        veg_valid = _valid_mask(s2_b04) & _valid_mask(s2_b08)
        b4f = s2_b04.astype(np.float64)
        b8f = s2_b08.astype(np.float64)
        ndvi_full = np.zeros((meta["height"], meta["width"]), dtype=np.float64)
        ndvi_full[veg_valid] = (b8f[veg_valid] - b4f[veg_valid]) / (b8f[veg_valid] + b4f[veg_valid] + 1e-10)
        veg_thr = params.get("ndvi_veg_threshold", DEFAULT_NDVI_VEG_THRESHOLD)
        veg_mask = veg_valid & (ndvi_full > veg_thr)

        # LST 代理:窗区(B13/B14)亮温(thermal.py),供③流体、⑦温度使用。
        # P1 改:不再用5波段TIR原始均值(会被B10-B12硅酸盐发射率污染),改用定标后的窗区亮温。
        lst = compute_lst_proxy(aligned)
        if lst is None:
            # 无TIR时返回NODATA(让③流体、⑦温度自然失效),不再把高程当温度
            lst = np.full_like(dem, NODATA)

        # P4 季节差分(冬−夏 ΔLST/ΔNDVI;无夏季包则为 None)
        seasonal = compute_seasonal_diff(aligned)
        dlst = seasonal["dlst"]
        dndvi = seasonal["dndvi"]

        # ─── Step 3-9: 七慢变量计算（①②③⑤⑥⑦并行，④依赖①）────────
        # 驱动力①-⑤: stress, redox, fluid, chem + fault(依赖stress)
        # 阻力⑥⑦: cap_rock, temp
        progress("computing_stress", "计算① 地应力异常梯度")
        progress("computing_redox", "计算② 氧逸度突变带强度")
        progress("computing_fluid", "计算③ 流体超压指数")
        progress("computing_chem", "计算⑤ 化学势梯度")
        progress("computing_cap_rock", "计算⑥ 盖层封闭性")
        progress("computing_temp", "计算⑦ 温度异常梯度")

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            f_stress = executor.submit(compute_stress_gradient, dem, insar, insar_coh,
                                       sigma=sigma, pixel_size_m=pixel_size_m)
            f_redox = executor.submit(compute_redox_gradient, aster_b01, aster_b03n, s2_b02, s2_b04,
                                      veg_mask=veg_mask, sigma=sigma)
            f_fluid = executor.submit(compute_fluid_overpressure, lst, s2_b04, s2_b08,
                                      sigma=sigma, seasonal_lst_diff=dlst)
            f_cap = executor.submit(compute_cap_rock_pressure, aster_b08, aster_b09,
                                    aster_b12, aster_b13, aster_b14, veg_mask=veg_mask, sigma=sigma)
            f_temp = executor.submit(compute_temperature_gradient, lst, sigma=sigma)
            f_chem = executor.submit(compute_chemical_potential, aster_b01, aster_b03n, aster_b05,
                                     aster_b06, aster_b08, aster_b12, aster_b14,
                                     s2_b02, s2_b04, s2_b11, s2_b12, veg_mask=veg_mask, sigma=sigma)

            stress = f_stress.result()
            redox = f_redox.result()
            fluid = f_fluid.result()
            cap_rock = f_cap.result()
            temp = f_temp.result()
            chem = f_chem.result()

        progress("computing_fault", "计算④ 断裂活动性指数")
        fault = compute_fault_activity(dem, stress, transform,
                                       pixel_size_m=pixel_size_m, insar_velocity=insar)

        # ─── Step 10: Z-score标准化 ──────────────────────────
        progress("normalizing", "Z-score标准化")
        stress_z = zscore_normalize(stress)
        redox_z = zscore_normalize(redox)
        fluid_z = zscore_normalize(fluid)
        fault_z = zscore_normalize(fault)
        cap_rock_z = zscore_normalize(cap_rock)
        temp_z = zscore_normalize(temp)
        chem_z = zscore_normalize(chem)

        dominant_driver = dominant_driver_layer({
            "stress_gradient": stress_z,
            "redox_gradient": redox_z,
            "fluid_overpressure": fluid_z,
            "fault_activity": fault_z,
            "chem_potential": chem_z,
            "cap_rock_pressure": cap_rock_z,
            "temp_gradient": temp_z,
        })

        # ─── Step 11: 融合与Δ判别式 ───────────────────────────
        progress("computing_fusion", "计算Δ判别式")
        a, b, delta = compute_fusion(
            stress_z, redox_z, fluid_z, fault_z,
            cap_rock_z, temp_z, chem_z, weights,
        )

        # ─── Step 12: 靶区提取 ────────────────────────────────
        progress("extracting_targets", "提取靶区")
        targets = extract_target_zones(delta, delta_threshold)

        # ─── Step 13: 写入COG输出 ─────────────────────────────
        progress("writing_outputs", "写入结果文件")

        # 裁剪到有效数据范围：找到所有慢变量的公共有效区域边界
        all_valid = np.ones((meta["height"], meta["width"]), dtype=bool)
        for var in [stress, redox, fluid, fault, cap_rock, temp, chem]:
            all_valid &= (_valid_mask(var) if var is not None else np.zeros_like(all_valid))

        # 找到包含所有有效数据的最小矩形范围（加一点边距）
        if np.any(all_valid):
            rows, cols = np.where(all_valid)
            r0, r1 = max(0, rows.min() - 2), min(meta["height"], rows.max() + 3)
            c0, c1 = max(0, cols.min() - 2), min(meta["width"], cols.max() + 3)
        else:
            r0, r1 = 0, meta["height"]
            c0, c1 = 0, meta["width"]

        # 裁剪 transform：偏移到裁剪后的左上角
        from rasterio.transform import Affine
        crop_transform = Affine(
            transform.a, transform.b, transform.c + c0 * transform.a,
            transform.d, transform.e, transform.f + r0 * transform.e,
        )

        layers = {
            "stress_gradient": stress,
            "redox_gradient": redox,
            "fluid_overpressure": fluid,
            "fault_activity": fault,
            "cap_rock_pressure": cap_rock,
            "temp_gradient": temp,
            "chem_potential": chem,
            "driving_force_b": b,
            "resistance_a": a,
            "delta_discriminant": delta,
            "target_zones": targets,
            "dominant_driver": dominant_driver,
        }
        # P4 季节差分诊断图层(有夏季数据时)
        if dlst is not None:
            layers["seasonal_lst_diff"] = dlst
        if dndvi is not None:
            layers["seasonal_ndvi_diff"] = dndvi

        for layer_name, data in layers.items():
            dtype = "uint8" if layer_name in ("target_zones", "dominant_driver") else "float32"
            nd = 255 if layer_name in ("target_zones", "dominant_driver") else NODATA
            cropped = data[r0:r1, c0:c1]
            write_cog(cropped, str(result_dir / f"{layer_name}.tif"),
                     crop_transform, crs, nodata=nd, dtype=dtype)

        # ─── Step 14: 统计摘要 ────────────────────────────────
        progress("generating_stats", "生成统计摘要")

        layer_titles = {
            "stress_gradient": "① 地应力异常梯度 (τ)",
            "redox_gradient": "② 氧逸度突变带 (Δlog fO₂)",
            "fluid_overpressure": "③ 流体超压指数 (λ)",
            "fault_activity": "④ 断裂活动性指数 (A)",
            "chem_potential": "⑤ 化学势梯度 (∇μ)",
            "cap_rock_pressure": "⑥ 盖层封闭性 (ΔP)",
            "temp_gradient": "⑦ 温度异常梯度 (∇T)",
            "seasonal_lst_diff": "季节温差 ΔLST(冬−夏)",
            "seasonal_ndvi_diff": "季节植被差 ΔNDVI(冬−夏)",
            "driving_force_b": "驱动力 (b)",
            "resistance_a": "阻力 (a)",
            "delta_discriminant": "Δ判别式",
            "target_zones": "靶区",
            "dominant_driver": "主控慢变量",
        }

        results_list = []
        for layer_name, data in layers.items():
            stats = compute_stats(data)
            results_list.append({
                "name": layer_name,
                "title": layer_titles.get(layer_name, layer_name),
                "stats": stats,
                "tile_url": f"/tiles/{task_id}/{layer_name}/{{z}}/{{x}}/{{y}}.png",
            })

        # 靶区面积
        target_mask = (targets == 1)
        pixel_area = abs(transform.a * transform.e)  # m²
        target_area_km2 = float(np.sum(target_mask) * pixel_area / 1e6)

        from rasterio.transform import array_bounds
        from rasterio.warp import transform_bounds
        cropped_height = max(0, r1 - r0)
        cropped_width = max(0, c1 - c0)
        crop_bounds = array_bounds(cropped_height, cropped_width, crop_transform)
        aoi_bbox = list(transform_bounds(crs, "EPSG:4326", *crop_bounds))
        vector_stats = write_target_zones_geojson(
            targets[r0:r1, c0:c1],
            delta[r0:r1, c0:c1],
            b[r0:r1, c0:c1],
            a[r0:r1, c0:c1],
            dominant_driver[r0:r1, c0:c1],
            crop_transform,
            crs,
            result_dir / "target_zones.geojson",
        )

        products = {
            name: f"{name}.tif"
            for name in layers.keys()
        }
        products["target_zones_geojson"] = "target_zones.geojson"

        model_stats = {
            "target_area_km2": round(target_area_km2, 2),
            "target_count": vector_stats["target_count"],
            "weights": weights,
            "weights_source": weights_source,
            "delta_threshold": delta_threshold,
            "delta_percentile": DEFAULT_DELTA_PERCENTILE,
            "sigma": sigma,
            "mineral": mineral,
            "deposit_type": deposit_type,
            "family": resolved_family,
            "geologic_context_source": (geologic_context or {}).get("source"),
            "geo_struct_confidence": (geologic_context or {}).get("deposit_type_confidence"),
            "status": "ok",
        }
        metadata = {
            "source": "geo-7slow",
            "run_id": task_id,
            "trace_id": trace_id,
            "aoi_name": aoi_name,
            "aoi_bbox": aoi_bbox,
            "crs": str(crs),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "products": products,
            "model_stats": model_stats,
            "geologic_context": geologic_context,
            "evidence_catalog": EVIDENCE_CATALOG,
            "metadata_path": str(result_dir / "metadata.json"),
        }
        try:
            from commons.trace import stamp_metadata
            stamp_metadata(metadata, explicit_trace_id=trace_id, tenant_id=params.get("tenant_id"))
        except Exception:
            pass
        manifest = {
            **metadata,
            "evidence_catalog": EVIDENCE_CATALOG,
            "layers": [
                {
                    "name": name,
                    "title": layer_titles.get(name, name),
                    "path": rel,
                    "stats": compute_stats(layers[name]),
                    "normalized": False,
                    "unit_scale": layer_unit_scale(name),
                    **EVIDENCE_CATALOG.get(name, {}),
                }
                for name, rel in products.items()
                if rel.endswith(".tif")
            ],
        }
        (result_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        (result_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        result_data = {
            "layers": results_list,
            "target_area_km2": round(target_area_km2, 2),
            "target_count": vector_stats["target_count"],
            "metadata_path": str(result_dir / "metadata.json"),
            "manifest_path": str(result_dir / "manifest.json"),
            "params_used": {
                "weights": weights,
                "weights_source": weights_source,
                "delta_threshold": delta_threshold,
                "sigma": sigma,
                "mineral": mineral,
                "deposit_type": deposit_type,
                "family": resolved_family,
                "geologic_context_source": (geologic_context or {}).get("source"),
                "geo_struct_confidence": (geologic_context or {}).get("deposit_type_confidence"),
            },
        }

        task_store.update(task_id, status="completed", progress=100,
                         current_step="分析完成", results=result_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        task_store.update(task_id, status="failed", current_step="分析失败",
                         error=str(e))
