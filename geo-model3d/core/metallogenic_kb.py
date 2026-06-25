"""成矿模型知识库（方向四 P2，知识驱动）—— 用大地构造背景设定矿床族先验，校验数据驱动结果。

沉淀《方法论汇总》第六/七章："构造-岩浆-成矿耦合"——哪些矿床族在某构造环境下可能存在。
用途：
1. permissible_families(tectonic) → 该构造背景下合理的成矿族集合；
2. knowledge_consistency(family, tectonic, commodity) → 校验所定族是否与构造背景自洽（先验合理性）；
3. 与 geo-analyser/alteration_deposit_db.json 的 tectonic_setting 文本对接（关键词匹配，容错）。

诚实约束：无构造背景信息时**中性不约束**；不自洽时只**告警/降权提示**，不静默抹掉数据驱动结果。
"""

from __future__ import annotations

from typing import Dict, List, Optional

# 构造背景关键词 → 该环境下合理的成矿族（family 命名同 knowledge.FAMILY_WEIGHTS）
TECTONIC_FAVORABLE: Dict[str, List[str]] = {
    "岛弧":         ["porphyry", "skarn", "epithermal", "vms"],
    "陆缘弧":       ["porphyry", "skarn", "epithermal"],
    "汇聚":         ["porphyry", "skarn", "epithermal", "vms", "orogenic_gold"],
    "俯冲":         ["porphyry", "skarn", "epithermal", "vms"],
    "活动陆缘":     ["porphyry", "skarn", "epithermal"],
    "造山":         ["orogenic_gold", "struct_vein", "greisen_pegmatite", "skarn"],
    "碰撞":         ["orogenic_gold", "greisen_pegmatite", "struct_vein"],
    "克拉通":       ["iocg", "magmatic_sulfide", "kimberlite", "carbonatite_ree", "sedimentary"],
    "地盾":         ["iocg", "magmatic_sulfide", "kimberlite", "sedimentary"],
    "地台":         ["mvt", "sedimentary", "sedex"],
    "裂谷":         ["sedex", "carbonatite_ree", "magmatic_sulfide", "iocg"],
    "裂陷":         ["sedex", "carbonatite_ree", "magmatic_sulfide"],
    "陆内":         ["carbonatite_ree", "magmatic_sulfide", "struct_vein"],
    "被动":         ["mvt", "sedimentary", "sedex"],
    "陆缘":         ["mvt", "sedimentary", "porphyry", "skarn"],
    "碳酸盐":       ["mvt", "carlin", "sedimentary"],
    "台地":         ["mvt", "carlin", "sedimentary"],
    "沉积盆地":     ["sandstone_u", "sedimentary", "hydrocarbon", "brine_evaporite", "sedex"],
    "盆地":         ["sandstone_u", "sedimentary", "hydrocarbon", "mvt"],
    "风化":         ["laterite"],
    "表生":         ["laterite", "sandstone_u"],
}


def permissible_families(tectonic_setting: Optional[str]) -> List[str]:
    """返回该构造背景下合理的成矿族（关键词匹配并集）。无背景→空列表（=不约束）。"""
    if not tectonic_setting:
        return []
    fams: List[str] = []
    for kw, fl in TECTONIC_FAVORABLE.items():
        if kw in tectonic_setting:
            for f in fl:
                if f not in fams:
                    fams.append(f)
    return fams


def knowledge_consistency(family: str, tectonic_setting: Optional[str],
                          commodity: Optional[str] = None) -> Dict:
    """校验所定成矿族与构造背景的自洽性（知识驱动先验）。

    返回 {plausible, prior_score∈[0,1], note, permissible_families, tectonic_setting}。
    无构造背景→中性(plausible=True, prior_score=0.5, 不约束)。
    """
    perm = permissible_families(tectonic_setting)
    if not perm:
        return {"plausible": True, "prior_score": 0.5,
                "note": "无大地构造背景信息（或未匹配关键词），知识先验不约束",
                "permissible_families": [], "tectonic_setting": tectonic_setting}
    if family in perm:
        return {"plausible": True, "prior_score": 1.0,
                "note": f"成矿族 {family} 与构造背景自洽（{tectonic_setting}）",
                "permissible_families": perm, "tectonic_setting": tectonic_setting}
    return {"plausible": False, "prior_score": 0.35,
            "note": (f"⚠ 成矿族 {family} 与构造背景（{tectonic_setting}）不典型——"
                     f"该环境更常见 {('、'.join(perm[:4]))}。数据驱动结果请谨慎，建议复核矿床类型。"),
            "permissible_families": perm, "tectonic_setting": tectonic_setting}
