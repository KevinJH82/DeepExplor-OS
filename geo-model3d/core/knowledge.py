"""矿床模型知识库 —— 知识驱动加权证据融合的权重与成矿深度带。

权重为相对值（使用时按可用证据层归一化）。证据层：
  alteration(蚀变) / structure(断裂邻近) / deformation(形变,常缺) / depth_consistency(深度一致性)
depth_km：该矿床成因族的典型成矿深度带 [min,max]，用于给三维深度维定中心（知识驱动软深度，非反演）。
applicability：geo-model3d 对该族的适用度 high/medium/low/na。

⚠ 初版数值，待地质专家复核校准（见 plan: validated-gliding-sloth.md）。
"""

from __future__ import annotations

from typing import Dict, Optional


# ── 成因族权重表 ──
FAMILY_WEIGHTS: Dict[str, dict] = {
    "porphyry":          {"w": {"alteration": 0.45, "structure": 0.25, "deformation": 0.10, "depth_consistency": 0.20}, "depth_km": [1.0, 4.0],  "applicability": "high",   "note": "斑岩:同心蚀变分带最诊断"},
    "skarn":             {"w": {"alteration": 0.40, "structure": 0.30, "deformation": 0.05, "depth_consistency": 0.25}, "depth_km": [0.5, 3.0],  "applicability": "high",   "note": "矽卡岩:接触交代带"},
    "epithermal":        {"w": {"alteration": 0.40, "structure": 0.35, "deformation": 0.10, "depth_consistency": 0.15}, "depth_km": [0.05, 1.0], "applicability": "high",   "note": "浅成低温:脉/断裂控制,浅成"},
    "carlin":            {"w": {"alteration": 0.35, "structure": 0.35, "deformation": 0.05, "depth_consistency": 0.25}, "depth_km": [0.3, 3.0],  "applicability": "high",   "note": "卡林型:断裂+有利岩性"},
    "vms":               {"w": {"alteration": 0.40, "structure": 0.25, "deformation": 0.05, "depth_consistency": 0.20}, "depth_km": [0.3, 2.0],  "applicability": "high",   "note": "VMS:蚀变筒,同火山断裂"},
    "iocg":              {"w": {"alteration": 0.40, "structure": 0.35, "deformation": 0.05, "depth_consistency": 0.20}, "depth_km": [1.0, 5.0],  "applicability": "high",   "note": "IOCG:钠钙-钾-赤铁矿;强磁性→方向二"},
    "greisen_pegmatite": {"w": {"alteration": 0.35, "structure": 0.30, "deformation": 0.05, "depth_consistency": 0.25}, "depth_km": [1.0, 4.0],  "applicability": "high",   "note": "云英岩/伟晶岩:岩体顶部裂隙;伟晶岩锂另需转石填图+化探"},
    "carbonatite_ree":   {"w": {"alteration": 0.35, "structure": 0.30, "deformation": 0.05, "depth_consistency": 0.25}, "depth_km": [1.0, 5.0],  "applicability": "high",   "note": "碳酸岩稀土:钠长石化/萤石化;磁/能谱→方向二"},
    "orogenic_gold":     {"w": {"alteration": 0.30, "structure": 0.45, "deformation": 0.10, "depth_consistency": 0.15}, "depth_km": [2.0, 10.0], "applicability": "medium", "note": "造山型金:剪切带主导,深成,蚀变弱"},
    "sedex":             {"w": {"alteration": 0.30, "structure": 0.30, "deformation": 0.05, "depth_consistency": 0.20}, "depth_km": [1.0, 3.0],  "applicability": "medium", "note": "SEDEX/沉积容矿:层控,同生断裂"},
    "mvt":               {"w": {"alteration": 0.25, "structure": 0.30, "deformation": 0.05, "depth_consistency": 0.15}, "depth_km": [0.3, 2.0],  "applicability": "medium", "note": "MVT:白云石化弱,岩溶/断裂(无地层层→约束不足)"},
    "struct_vein":       {"w": {"alteration": 0.25, "structure": 0.45, "deformation": 0.05, "depth_consistency": 0.20}, "depth_km": [0.2, 2.5],  "applicability": "medium", "note": "热液脉(萤石/锑/汞/重晶石/钠交代铀):断裂-脉主导"},
    "sandstone_u":       {"w": {"alteration": 0.30, "structure": 0.30, "deformation": 0.05, "depth_consistency": 0.15}, "depth_km": [0.05, 1.0], "applicability": "medium", "note": "砂岩/不整合铀:氧化还原前锋,浅成;能谱→方向二"},
    "magmatic_sulfide":  {"w": {"alteration": 0.20, "structure": 0.35, "deformation": 0.05, "depth_consistency": 0.25}, "depth_km": [1.0, 5.0],  "applicability": "medium", "note": "岩浆硫化物/层状/铬铁/钒钛:蚀变弱,靠构造;强磁→方向二"},
    "kimberlite":        {"w": {"alteration": 0.20, "structure": 0.40, "deformation": 0.05, "depth_consistency": 0.25}, "depth_km": [3.0, 8.0],  "applicability": "medium", "note": "金伯利岩:克拉通深断裂;强磁→方向二"},
    "laterite":          {"w": {"alteration": 0.20, "structure": 0.10, "deformation": 0.05, "depth_consistency": 0.05}, "depth_km": [0.0, 0.1],  "applicability": "low",    "note": "红土:风化壳控,近地表2D;3D增益有限"},
    "sedimentary":       {"w": {"alteration": 0.15, "structure": 0.10, "deformation": 0.05, "depth_consistency": 0.10}, "depth_km": [0.0, 0.5],  "applicability": "low",    "note": "沉积/变质:地层控矿,现有证据弱代理;3D增益有限"},
    "brine_evaporite":   {"w": {"alteration": 0.0,  "structure": 0.0,  "deformation": 0.0,  "depth_consistency": 0.0},  "depth_km": [0.0, 0.2],  "applicability": "na",     "note": "盐湖卤水/蒸发岩:非热液非构造,geo-model3d 不适用"},
    "hydrocarbon":       {"w": {"alteration": 0.45, "structure": 0.30, "deformation": 0.05, "depth_consistency": 0.20}, "depth_km": [1.0, 4.0],  "applicability": "low",    "note": "油气微渗漏:地表红层褪色/粘土/碳酸盐/黄铁矿化为渗漏晕,储层在深部;3D深度为微渗漏推断,真正圈定储层需地震/钻井"},
}

DEFAULT_FAMILY = "porphyry"


# ── geo-analyser DB 矿床类型名 → family ──
DEPOSIT_TYPE_TO_FAMILY: Dict[str, str] = {
    "斑岩型铜矿": "porphyry", "斑岩型钼矿（Climax型）": "porphyry", "斑岩型铜钼矿": "porphyry", "斑岩型铜金矿": "porphyry",
    "矽卡岩型铜矿": "skarn", "矽卡岩型铅锌矿": "skarn", "矽卡岩型铁矿": "skarn", "矽卡岩型钨矿": "skarn",
    "浅成低温热液型金矿（低硫化型）": "epithermal", "浅成低温热液型金矿（高硫化型）": "epithermal", "浅成低温热液型银矿": "epithermal",
    "造山型金矿": "orogenic_gold", "卡林型金矿": "carlin",
    "VMS型铜锌矿": "vms", "IOCG型铁氧化物铜金矿": "iocg",
    "MVT型铅锌矿": "mvt", "SEDEX型铅锌矿": "sedex", "沉积岩容矿铜钴矿": "sedex",
    "云英岩型钨锡矿": "greisen_pegmatite", "云英岩型锡矿": "greisen_pegmatite", "伟晶岩型锂矿（锂辉石型）": "greisen_pegmatite",
    "岩浆硫化物型镍铜矿": "magmatic_sulfide", "层状镁铁质侵入体PGE矿": "magmatic_sulfide",
    "豆荚状铬铁矿（蛇绿岩型）": "magmatic_sulfide", "岩浆型钒钛磁铁矿": "magmatic_sulfide",
    "碳酸岩型稀土矿": "carbonatite_ree",
    "离子吸附型稀土矿": "laterite", "红土型镍矿": "laterite", "红土型钴矿": "laterite", "红土型铝土矿": "laterite",
    "不整合面型铀矿": "sandstone_u", "砂岩型铀矿": "sandstone_u",
    "钠交代型铀矿": "struct_vein", "低温热液型锑矿": "struct_vein", "低温热液型汞矿": "struct_vein",
    "热液脉型萤石矿": "struct_vein", "热液/沉积型重晶石矿": "struct_vein",
    "金伯利岩型金刚石矿": "kimberlite",
    # 油气（geo-analyser 微渗漏蚀变模型，半角括号，与 alteration_db 注入名一致）
    "常规油藏(微渗漏蚀变模式)": "hydrocarbon", "致密油藏(微渗漏蚀变)": "hydrocarbon",
    "常规气藏(微渗漏蚀变模式)": "hydrocarbon", "煤层气(微渗漏蚀变)": "hydrocarbon",
    "天然气水合物(微渗漏)": "hydrocarbon",
    "沉积型锰矿": "sedimentary", "火山沉积型锰矿": "sedimentary", "沉积型磷块岩矿": "sedimentary",
    "沉积型钒矿（黑色页岩型）": "sedimentary", "BIF型铁矿（条带状铁建造）": "sedimentary",
    "沉积型锂矿（粘土型）": "sedimentary", "区域变质型石墨矿": "sedimentary",
    "盐湖卤水型锂矿": "brine_evaporite", "蒸发岩型钾盐矿": "brine_evaporite",
}


# ── 矿种(中文/英文/常见别名) → 默认 family ──
COMMODITY_DEFAULT_FAMILY: Dict[str, str] = {
    "铜": "porphyry", "copper": "porphyry", "cu": "porphyry",
    "钼": "porphyry", "molybdenum": "porphyry", "mo": "porphyry",
    "铜钼": "porphyry",
    "金": "epithermal", "gold": "epithermal", "au": "epithermal",
    "银": "epithermal", "silver": "epithermal", "ag": "epithermal",
    "铅锌": "skarn", "铅": "skarn", "锌": "skarn", "lead": "skarn", "zinc": "skarn", "pb-zn": "skarn",
    "铁": "skarn", "iron": "skarn", "fe": "skarn",
    "钨": "skarn", "tungsten": "skarn", "w": "skarn",
    "锡": "greisen_pegmatite", "tin": "greisen_pegmatite", "sn": "greisen_pegmatite",
    "钨锡": "greisen_pegmatite",
    "锂": "greisen_pegmatite", "lithium": "greisen_pegmatite", "li": "greisen_pegmatite",
    "镍": "magmatic_sulfide", "nickel": "magmatic_sulfide", "ni": "magmatic_sulfide",
    "铂族": "magmatic_sulfide", "pge": "magmatic_sulfide", "铬": "magmatic_sulfide", "钛": "magmatic_sulfide",
    "钴": "sedex", "cobalt": "sedex", "co": "sedex",
    "稀土": "carbonatite_ree", "rare_earth": "carbonatite_ree", "ree": "carbonatite_ree",
    "铀": "sandstone_u", "uranium": "sandstone_u", "u": "sandstone_u",
    "锑": "struct_vein", "汞": "struct_vein", "萤石": "struct_vein", "重晶石": "struct_vein",
    "锰": "sedimentary", "钒": "sedimentary", "磷": "sedimentary", "石墨": "sedimentary",
    "铝土": "laterite", "铝": "laterite", "bauxite": "laterite",
    "金刚石": "kimberlite", "diamond": "kimberlite",
    "钾盐": "brine_evaporite",
    "石油": "hydrocarbon", "oil": "hydrocarbon",
    "天然气": "hydrocarbon", "gas": "hydrocarbon", "natural_gas": "hydrocarbon",
    "油气": "hydrocarbon", "煤层气": "hydrocarbon",
}

# 金/银族构造背景自动切换的关键词（→ orogenic_gold）
_OROGENIC_KEYWORDS = ("造山", "汇聚", "缝合", "碰撞", "地体")


def resolve_family(commodity: Optional[str], deposit_type: Optional[str] = None,
                   tectonic_setting: Optional[str] = None) -> str:
    """按优先级解析成因族：deposit_type 直查 → 矿种默认 → 金/银构造背景自动切换。"""
    # 1) 已知矿床类型名直查
    if deposit_type and deposit_type in DEPOSIT_TYPE_TO_FAMILY:
        fam = DEPOSIT_TYPE_TO_FAMILY[deposit_type]
    else:
        # 2) 矿种默认
        key = (commodity or "").strip().lower()
        fam = COMMODITY_DEFAULT_FAMILY.get(commodity or "") or COMMODITY_DEFAULT_FAMILY.get(key) or DEFAULT_FAMILY

    # 3) 金/银：构造背景含造山带关键词 → orogenic_gold
    is_au_ag = (commodity or "").strip() in ("金", "银") or (commodity or "").strip().lower() in ("gold", "silver", "au", "ag")
    if fam == "epithermal" and is_au_ag and tectonic_setting:
        if any(k in tectonic_setting for k in _OROGENIC_KEYWORDS):
            fam = "orogenic_gold"
    return fam


def family_spec(family: str) -> dict:
    """返回某 family 的权重/深度带/适用度规格（未知则回退默认族）。"""
    return FAMILY_WEIGHTS.get(family, FAMILY_WEIGHTS[DEFAULT_FAMILY])


# ── 磁法证据权重（方向二接入时用）：强磁性矿族更高 ──
# 磁解析信号(AS)作 2D 构造证据时按此权重并入融合；仅当物探产物存在才生效。
MAGNETIC_WEIGHT: Dict[str, float] = {
    "iocg": 0.30, "magmatic_sulfide": 0.30, "kimberlite": 0.30,
    "carbonatite_ree": 0.25, "skarn": 0.25,          # 矽卡岩磁铁矿/铁
    "porphyry": 0.15, "vms": 0.15, "sedimentary": 0.15,  # BIF 磁铁矿
    "orogenic_gold": 0.10, "carlin": 0.10, "mvt": 0.10, "sedex": 0.10,
    "struct_vein": 0.10, "sandstone_u": 0.10, "greisen_pegmatite": 0.10,
    "epithermal": 0.08, "laterite": 0.05, "hydrocarbon": 0.05, "brine_evaporite": 0.0,
}


def magnetic_weight(family: str) -> float:
    return MAGNETIC_WEIGHT.get(family, 0.10)


# ── 化探证据权重（geochem 接入时用）：化探对地表/浅成、亲晕元素诊断性强的族更高 ──
# 多元素组合异常作 2D 地表证据时按此权重并入融合；仅当 geo-geochem 组合异常产物存在才生效。
# ⚠ 初版数值，待地质专家复核校准（同上权重表免责声明）。
GEOCHEM_WEIGHT: Dict[str, float] = {
    "epithermal": 0.30, "carlin": 0.30,              # 浅成低温/卡林:Au-As-Sb-Hg 晕最诊断
    "orogenic_gold": 0.25,                           # 造山金:亲晕元素强
    "porphyry": 0.22, "skarn": 0.22, "vms": 0.22,    # 斑岩/矽卡岩/VMS:多金属组合晕
    "iocg": 0.22, "sedex": 0.22, "mvt": 0.20, "struct_vein": 0.22,
    "greisen_pegmatite": 0.22, "carbonatite_ree": 0.20,  # 云英岩/伟晶岩/碳酸岩:Sn-W-Li / REE 化探
    "laterite": 0.20,                                # 离子吸附稀土/红土:实为化探主导
    "magmatic_sulfide": 0.15, "kimberlite": 0.15, "sandstone_u": 0.15,
    "sedimentary": 0.15, "hydrocarbon": 0.10, "brine_evaporite": 0.0,
}


def geochem_weight(family: str) -> float:
    return GEOCHEM_WEIGHT.get(family, 0.15)


# ── 断裂倾角（P2 特性A：三维构造几何）──
# 深部按 offset=depth/tan(dip) 沿倾向横移 2D 构造有利度，模拟断裂面带的三维投影。
# 多为陡倾热液/剪切控矿断裂；缓倾(逆冲/层控)族取较小值。
# ⚠ 初版数值，待地质专家复核校准（同上权重表免责声明）。
DEFAULT_DIP_DEG = 80.0
STRUCTURE_DIP_DEG: Dict[str, float] = {
    "orogenic_gold": 70.0, "carlin": 70.0,           # 造山金/卡林:剪切带,中等-陡倾
    "struct_vein": 75.0, "epithermal": 75.0,         # 热液脉/浅成低温:陡倾脉系
    "vms": 78.0, "porphyry": 80.0, "skarn": 80.0,
    "iocg": 80.0, "greisen_pegmatite": 82.0, "carbonatite_ree": 82.0,
    "kimberlite": 88.0, "magmatic_sulfide": 82.0,    # 金伯利岩近垂直筒/深断裂
    "sandstone_u": 30.0, "mvt": 35.0, "sedex": 40.0,  # 砂岩铀/MVT/SEDEX:缓倾层控
    "laterite": 15.0, "sedimentary": 30.0,           # 红土/沉积:近水平
    "hydrocarbon": 60.0, "brine_evaporite": 90.0,
}


def structure_dip(family: str) -> float:
    """返回某 family 的断裂倾角(度)，未知则回退默认陡倾值。"""
    return STRUCTURE_DIP_DEG.get(family, DEFAULT_DIP_DEG)
