"""权重敏感性分析:在已完成任务上,扰动权重重算靶区面积,量化稳健性与变量重要性。

(P4:监督式"对已知矿点标定"需正负样本标签,交付包暂无 → 改做无需 ground-truth 的
敏感性分析:扰动各权重 ± 看靶区面积变化,以及留一法变量重要性。)
"""
import numpy as np
import rasterio

from app.config import RESULTS_DIR, DEFAULT_WEIGHTS
from app.processing.fusion import zscore_normalize, compute_fusion, extract_target_zones

# compute_fusion 入参顺序对应的 7 个变量 COG 文件名
_FUSION_VARS = [
    "stress_gradient", "redox_gradient", "fluid_overpressure", "fault_activity",
    "cap_rock_pressure", "temp_gradient", "chem_potential",
]

# 变量 → 它在 a/b 中占用的权重键(⑦温度同时进 temp_drive 与 temp_resist)
_VAR_WEIGHTS = {
    "stress_gradient": ["stress"],
    "redox_gradient": ["redox"],
    "fluid_overpressure": ["fluid"],
    "fault_activity": ["fault"],
    "cap_rock_pressure": ["cap_rock"],
    "temp_gradient": ["temp_drive", "temp_resist"],
    "chem_potential": ["chem"],
}


def _load_zvars(task_id: str):
    rd = RESULTS_DIR / task_id
    z = {}
    pixel_area = None
    for v in _FUSION_VARS:
        p = rd / f"{v}.tif"
        if not p.exists():
            raise FileNotFoundError(f"缺少变量图层 {p}")
        with rasterio.open(p) as s:
            arr = s.read(1).astype(np.float64)
            if pixel_area is None:
                pixel_area = abs(s.transform.a * s.transform.e)  # m²
        z[v] = zscore_normalize(arr)
    return z, pixel_area


def _target_area_km2(z: dict, weights: dict, pixel_area: float) -> float:
    a, b, delta = compute_fusion(
        z["stress_gradient"], z["redox_gradient"], z["fluid_overpressure"],
        z["fault_activity"], z["cap_rock_pressure"], z["temp_gradient"],
        z["chem_potential"], weights,
    )
    targets = extract_target_zones(delta)   # 自适应阈值(与流水线一致)
    return float(np.sum(targets == 1) * pixel_area / 1e6)


def analyze_sensitivity(task_id: str, perturbation: float = 0.2) -> dict:
    """
    扰动幅度 perturbation(默认 ±20%)。返回:
      baseline_area_km2、weight_sensitivity(按相对敏感度排序)、variable_importance(留一法)
    """
    z, pixel_area = _load_zvars(task_id)
    base_w = dict(DEFAULT_WEIGHTS)
    base_area = _target_area_km2(z, base_w, pixel_area)

    # 逐权重 ±perturbation
    weight_sens = []
    for k, v in base_w.items():
        wp = dict(base_w); wp[k] = v * (1 + perturbation)
        wm = dict(base_w); wm[k] = v * (1 - perturbation)
        ap = _target_area_km2(z, wp, pixel_area)
        am = _target_area_km2(z, wm, pixel_area)
        d_area = (ap - am) / 2.0
        rel = (d_area / base_area) if base_area > 0 else 0.0
        weight_sens.append({
            "weight": k, "value": round(v, 4),
            "area_plus_km2": round(ap, 4), "area_minus_km2": round(am, 4),
            "d_area_km2": round(d_area, 4), "rel_sensitivity": round(rel, 4),
        })
    weight_sens.sort(key=lambda x: abs(x["rel_sensitivity"]), reverse=True)

    # 留一法变量重要性:把某变量权重清零,看靶区面积变化
    var_imp = []
    for v, wkeys in _VAR_WEIGHTS.items():
        wp = dict(base_w)
        for wk in wkeys:
            wp[wk] = 0.0
        a0 = _target_area_km2(z, wp, pixel_area)
        var_imp.append({
            "variable": v,
            "area_without_km2": round(a0, 4),
            "area_delta_km2": round(a0 - base_area, 4),
            "importance": round(abs(a0 - base_area), 4),
        })
    var_imp.sort(key=lambda x: x["importance"], reverse=True)

    return {
        "task_id": task_id,
        "baseline_area_km2": round(base_area, 4),
        "perturbation": perturbation,
        "weight_sensitivity": weight_sens,
        "variable_importance": var_imp,
    }
