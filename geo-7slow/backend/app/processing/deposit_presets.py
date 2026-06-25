"""矿床类型驱动的权重预设。

复用 geo-model3d 的成因族 taxonomy 与证据权重(importlib 零污染加载其自洽的 core/knowledge.py),
把 model3d 的 4 层证据权重 {alteration, structure, deformation, depth_consistency} 按下述原则
派生为 geo-7slow 的 8 个慢变量权重,并提供:矿床类型/矿种 → 预设权重 的解析,与前端下拉清单。

派生原则:
  结构组 ①stress + ④fault  ← structure(+ deformation 并入 stress 动态项)
  蚀变组 ②redox + ⑤chem + ⑥cap_rock ← alteration(redox/chem 入驱动 b,cap_rock 入阻力 a 封盖项)
  热/流体 ③fluid + ⑦temp ← 各族热液强度(THERMAL_INTENSITY)
  驱动 b 的 6 权重归一到 1(与前端 normalize 一致);阻力 a 的 cap_rock/temp_resist 按族给。

⚠ 预设数值为初版草案,待地质专家复核(沿用 model3d knowledge.py 同口径免责)。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import json
from typing import Dict, List, Optional, Tuple

from app.config import DEFAULT_WEIGHTS

_KNOWLEDGE_PATH = Path(os.environ.get(
    "GEOMODEL3D_KNOWLEDGE", "/opt/deepexplor-services/geo-model3d/core/knowledge.py",
))
# 矿种→矿床类型 分组库(geo-analyser)
_ALTERATION_DB_PATH = Path(os.environ.get(
    "ALTERATION_DB", "/opt/deepexplor-services/geo-analyser/alteration_deposit_db.json",
))
_k = None
_presets: Optional[Dict[str, Dict[str, float]]] = None
_commodities: Optional[List[dict]] = None

# 修正 model3d DEPOSIT_TYPE_TO_FAMILY 未收录、靠矿种兜底不当的类型(尤其招远金矿)。
# 优先级高于 model3d 的映射。
EXTRA_DEPOSIT_TYPE_TO_FAMILY: Dict[str, str] = {
    "破碎带蚀变岩型金矿（焦家式）": "orogenic_gold",
    "热液脉型金矿（石英脉型，玲珑式）": "orogenic_gold",
    "热液脉型铅锌矿": "struct_vein",
    "热液脉型银多金属矿": "struct_vein",
    "石英脉型黑钨矿（钨锡石英脉型，南岭式）": "greisen_pegmatite",
    "石英脉-细脉浸染型钨钼矿": "greisen_pegmatite",
}


def _load():
    """importlib 加载 model3d 的 knowledge.py(自洽,仅 typing 依赖)。"""
    global _k
    if _k is not None:
        return _k
    if not _KNOWLEDGE_PATH.exists():
        raise ImportError(f"找不到 {_KNOWLEDGE_PATH}(geo-model3d 缺失,无法复用矿床族知识)")
    spec = importlib.util.spec_from_file_location("geomodel3d_knowledge", str(_KNOWLEDGE_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _k = mod
    return mod


def available() -> bool:
    try:
        return _KNOWLEDGE_PATH.exists()
    except Exception:
        return False


# 各成因族的热液/热强度(0~0.5,量纲与 model3d 的 A/S 可比),用于 ③流体/⑦温度
THERMAL_INTENSITY: Dict[str, float] = {
    "epithermal": 0.28, "porphyry": 0.25, "skarn": 0.22, "carlin": 0.20,
    "vms": 0.20, "hydrocarbon": 0.20, "iocg": 0.18, "greisen_pegmatite": 0.18,
    "struct_vein": 0.15, "orogenic_gold": 0.12, "sedex": 0.12, "carbonatite_ree": 0.12,
    "mvt": 0.10, "sandstone_u": 0.08, "magmatic_sulfide": 0.08, "kimberlite": 0.05,
    "sedimentary": 0.05, "laterite": 0.03, "brine_evaporite": 0.0,
}

# 阻力 a 中 cap_rock 占比(其余给 temp_resist);封盖/硅化相关族略高
CAP_SEAL: Dict[str, float] = {
    "epithermal": 0.60, "carlin": 0.58, "skarn": 0.55,
    "orogenic_gold": 0.45, "magmatic_sulfide": 0.45, "kimberlite": 0.45,
}
_CAP_SEAL_DEFAULT = 0.50

# 成因族中文标签(下拉显示)
FAMILY_LABEL: Dict[str, str] = {
    "porphyry": "斑岩型", "skarn": "矽卡岩型", "epithermal": "浅成低温热液型",
    "carlin": "卡林型", "vms": "VMS 型", "iocg": "IOCG 型",
    "greisen_pegmatite": "云英岩/伟晶岩型", "carbonatite_ree": "碳酸岩稀土型",
    "orogenic_gold": "造山型金矿", "sedex": "SEDEX/沉积容矿型", "mvt": "MVT 型",
    "struct_vein": "热液脉型", "sandstone_u": "砂岩/不整合铀型",
    "magmatic_sulfide": "岩浆硫化物型", "kimberlite": "金伯利岩型",
    "laterite": "红土/离子吸附型", "sedimentary": "沉积/变质型",
    "hydrocarbon": "油气(微渗漏)", "brine_evaporite": "盐湖/蒸发岩型",
}

_DRIVE_KEYS = ("stress", "redox", "fluid", "fault", "chem", "temp_drive")

SUPPLEMENTAL_COMMODITIES: Dict[str, List[str]] = {
    "石油": ["石油", "oil"],
    "天然气": ["天然气", "gas", "natural_gas"],
    "油气": ["油气", "石油", "天然气", "oil", "gas", "natural_gas"],
    "煤层气": ["煤层气"],
    "天然气水合物": ["天然气水合物"],
    "铅": ["铅"],
    "锌": ["锌"],
    "铜钼": ["铜钼"],
    "钨锡": ["钨锡"],
    "铝": ["铝", "铝土", "bauxite"],
    "磷": ["磷"],
    "铂族": ["铂族", "pge"],
}


def _is_chinese_name(name: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in name)


def _deposit_types_for_family(k, fam: str, presets: Dict[str, Dict[str, float]]) -> List[dict]:
    dtypes = []
    seen = set()
    merged = {**getattr(k, "DEPOSIT_TYPE_TO_FAMILY", {}), **EXTRA_DEPOSIT_TYPE_TO_FAMILY}
    for name, dfam in merged.items():
        if dfam != fam or name in seen:
            continue
        seen.add(name)
        dtypes.append({
            "name": name,
            "family": fam,
            "family_label": FAMILY_LABEL.get(fam, fam),
            "applicability": k.FAMILY_WEIGHTS.get(fam, {}).get("applicability", ""),
            "weights": presets.get(fam, dict(DEFAULT_WEIGHTS)),
        })
    return dtypes


def _append_commodity(
    commodities: List[dict],
    commodity: str,
    dtypes: List[dict],
) -> None:
    if not dtypes:
        return
    by_name = {c.get("commodity"): c for c in commodities}
    existing = by_name.get(commodity)
    if existing is None:
        commodities.append({"commodity": commodity, "deposit_types": dtypes})
        return
    seen = {d.get("name") for d in existing.get("deposit_types", [])}
    for d in dtypes:
        if d.get("name") not in seen:
            existing.setdefault("deposit_types", []).append(d)
            seen.add(d.get("name"))


def _derive(fam_key: str, fw: dict) -> Dict[str, float]:
    """由 model3d 族权重 {alteration,structure,deformation,depth_consistency} 派生 8 个慢变量权重。"""
    w4 = fw.get("w", {})
    A = float(w4.get("alteration", 0.0))
    S = float(w4.get("structure", 0.0))
    D = float(w4.get("deformation", 0.0))
    Th = THERMAL_INTENSITY.get(fam_key, 0.12)

    # 驱动 b(归一前)
    drive = {
        "fault": S,                  # 构造→断裂(直接结构代理)
        "stress": 0.6 * S + D,       # 构造+形变→地应力集中
        "redox": 0.35 * A,           # 蚀变(铁氧化)
        "chem": 0.45 * A,            # 蚀变(矿物组合)
        "fluid": Th,                 # 热液强度
        "temp_drive": 0.05,          # 温度驱动(小)
    }
    tot = sum(drive.values())
    if tot < 1e-6:                   # na/退化族(如盐湖)→ 回退默认
        return dict(DEFAULT_WEIGHTS)
    drive = {k: round(v / tot, 4) for k, v in drive.items()}

    # 阻力 a
    cap = CAP_SEAL.get(fam_key, _CAP_SEAL_DEFAULT)
    res = {"cap_rock": round(cap, 4), "temp_resist": round(1.0 - cap, 4)}
    return {**drive, **res}


def _get_presets() -> Dict[str, Dict[str, float]]:
    global _presets
    if _presets is None:
        k = _load()
        _presets = {fam: _derive(fam, fw) for fam, fw in k.FAMILY_WEIGHTS.items()}
    return _presets


def _resolve_family(commodity: Optional[str], deposit_type: Optional[str]) -> str:
    """族解析优先级:EXTRA 覆盖 > model3d DEPOSIT_TYPE_TO_FAMILY > 矿种兜底。"""
    if deposit_type and deposit_type in EXTRA_DEPOSIT_TYPE_TO_FAMILY:
        return EXTRA_DEPOSIT_TYPE_TO_FAMILY[deposit_type]
    return _load().resolve_family(commodity, deposit_type, None)


def resolve_preset_weights(deposit_type: Optional[str] = None,
                           family: Optional[str] = None,
                           commodity: Optional[str] = None) -> Tuple[Dict[str, float], str]:
    """解析 (权重, 命中的族)。优先 family → (EXTRA 覆盖 > deposit_type > 矿种兜底)。"""
    presets = _get_presets()
    fam = family or _resolve_family(commodity, deposit_type)
    weights = presets.get(fam)
    if weights is None:
        return dict(DEFAULT_WEIGHTS), fam or "default"
    return dict(weights), fam


def _load_commodities() -> List[dict]:
    """读取 geo-analyser 的 矿种→矿床类型 分组(缓存)。失败返回空。"""
    global _commodities
    if _commodities is not None:
        return _commodities
    try:
        db = json.loads(_ALTERATION_DB_PATH.read_text(encoding="utf-8"))
        _commodities = db.get("commodities", []) or []
    except Exception:
        _commodities = []
    return _commodities


def list_presets() -> dict:
    """前端下拉用:按矿种分组(矿种 → 矿床类型,每类型带解析后的族 + 权重)。"""
    try:
        k = _load()
        presets = _get_presets()
    except Exception as e:
        return {"available": False, "error": str(e), "commodities": [], "families": []}

    commodities = []
    for c in _load_commodities():
        comm = c.get("commodity")
        if not comm:
            continue
        dtypes = []
        for t in c.get("deposit_types", []):
            name = t if isinstance(t, str) else (t.get("type_name") or t.get("name"))
            if not name:
                continue
            fam = _resolve_family(comm, name)
            dtypes.append({
                "name": name,
                "family": fam,
                "family_label": FAMILY_LABEL.get(fam, fam),
                "applicability": k.FAMILY_WEIGHTS.get(fam, {}).get("applicability", ""),
                "weights": presets.get(fam, dict(DEFAULT_WEIGHTS)),
            })
        if dtypes:
            commodities.append({"commodity": comm, "deposit_types": dtypes})

    existing_names = {c.get("commodity") for c in commodities}
    commodity_defaults = getattr(k, "COMMODITY_DEFAULT_FAMILY", {})

    # Curated canonical additions, especially energy resources that are absent
    # from geo-analyser's alteration-focused commodity list.
    for canonical, aliases in SUPPLEMENTAL_COMMODITIES.items():
        fam = next((commodity_defaults.get(a) for a in aliases if commodity_defaults.get(a)), None)
        if fam is None and canonical == "天然气水合物":
            fam = "hydrocarbon"
        if fam:
            _append_commodity(commodities, canonical, _deposit_types_for_family(k, fam, presets))
            existing_names.add(canonical)

    # Add other Chinese canonical commodity names known by model3d but missing
    # from geo-analyser, while avoiding English aliases and chemical symbols.
    for comm, fam in commodity_defaults.items():
        if not _is_chinese_name(comm) or comm in existing_names:
            continue
        _append_commodity(commodities, comm, _deposit_types_for_family(k, fam, presets))
        existing_names.add(comm)

    commodities.sort(key=lambda c: c.get("commodity") or "")

    # 成因族摘要(备用/回退:交付库无 alteration_db 时也能用)
    families = [{
        "key": fam, "label": FAMILY_LABEL.get(fam, fam),
        "note": fw.get("note", ""), "applicability": fw.get("applicability", ""),
        "weights": presets[fam],
    } for fam, fw in k.FAMILY_WEIGHTS.items()]

    return {"available": True, "default_family": k.DEFAULT_FAMILY,
            "commodities": commodities, "families": families}
