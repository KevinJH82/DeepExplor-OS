"""报告生成器 — ROI 针对性报告，围绕目标矿种成矿模型展开（非通用科普）"""

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from urllib.parse import quote

from .logger import get_logger
from config import MYMEMORY_URL

logger = get_logger("report")


def _translate_en_to_cn(text: str) -> str:
    """将英文文本翻译为中文，失败时返回原文"""
    if not text or not text.strip():
        return text

    # 纯中文直接返回
    if len([c for c in text if '一' <= c <= '鿿']) > len(text) * 0.3:
        return text

    # 只用 mymemory（有超时控制），跳过 Google（无超时，容易卡死）
    try:
        result = _try_translate(text, "mymemory")
        if result and result != text:
            return result
    except Exception:
        pass
    return text


def _try_translate(text: str, backend: str) -> Optional[str]:
    """调用免费翻译 API（直接用 requests，不经过带重试的 http_client）"""
    if backend == "mymemory":
        try:
            import requests
            resp = requests.get(
                MYMEMORY_URL,
                params={"q": text[:500], "langpair": "en|zh-CN"},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("responseData", {}).get("translatedText", "")
                if result and result != text:
                    return result
        except Exception:
            pass

    return None


def _has_en_char(text: str) -> bool:
    """检查文本是否含英文字母"""
    return bool(re.search(r'[a-zA-Z]{4,}', text))


def _format_bbox(roi: Dict[str, Any]) -> str:
    b = roi['bbox']
    return (
        f"西: {b['west']:.4f}°E  "
        f"东: {b['east']:.4f}°E  "
        f"南: {b['south']:.4f}°N  "
        f"北: {b['north']:.4f}°N"
    )


def _format_links(links: list) -> str:
    if not links:
        return "_(暂无)_\n"
    lines = []
    for i, link in enumerate(links, 1):
        lines.append(f"{i}. [{link['label']}]({link['url']}) — {link.get('note', '')}")
    return "\n".join(lines)


# ============================================================
# 矿种主线辅助：成矿模型匹配、元素角色、物探覆盖度
# ============================================================

# 元素符号 → 中文名（用于矿种匹配与展示）
_ELEMENT_CN = {
    'Cu': '铜', 'Au': '金', 'Ag': '银', 'Fe': '铁', 'Pb': '铅', 'Zn': '锌',
    'W': '钨', 'Sn': '锡', 'Mo': '钼', 'Li': '锂', 'Co': '钴', 'Ni': '镍',
    'Sb': '锑', 'Hg': '汞', 'Al': '铝', 'U': '铀', 'Cr': '铬', 'Mn': '锰',
    'REE': '稀土', 'P': '磷', 'Nb': '铌', 'Ta': '钽', 'V': '钒', 'Ti': '钛',
    'Pt': '铂', 'Pd': '钯', 'Rh': '铑', 'Ru': '钌', 'Ir': '铱', 'Os': '锇',
    'F': '氟', 'C': '碳', 'Ba': '钡', 'As': '砷', 'Bi': '铋', 'Be': '铍',
    'Te': '碲', 'Se': '硒', 'Tl': '铊', 'Ga': '镓', 'Ge': '锗', 'Cd': '镉',
    'Re': '铼', 'Rb': '铷', 'Cs': '铯', 'B': '硼', 'Sr': '锶', 'Zr': '锆',
    'Y': '钇', 'La': '镧', 'Ce': '铈', 'Nd': '钕', 'Si': '硅', 'Mg': '镁',
    'Ca': '钙', 'Na': '钠', 'K': '钾', 'S': '硫',
    'Oil': '石油', 'Gas': '天然气', 'Coal': '煤',
}

# 构造背景同义词分组（成矿模型的 tectonic_setting 与本区构造单元 features 用词
# 常不同字面、但指同一类构造环境，故按"构造要素组"匹配，而非逐字匹配）
_SETTING_GROUPS = {
    "汇聚/碰撞造山环境": ["汇聚", "会聚", "碰撞", "造山", "岛弧", "陆缘弧",
                          "俯冲", "缝合带", "增生", "弧"],
    "裂谷/伸展环境": ["裂谷", "拗拉槽", "坳拉槽", "陆内裂陷", "伸展"],
    "沉积盆地": ["盆地", "坳陷", "断陷", "前陆", "拗陷"],
    "被动陆缘": ["被动陆缘", "被动大陆边缘"],
    "克拉通/稳定陆块": ["克拉通", "地台", "古老陆块", "稳定", "太古", "元古", "基底"],
    "中酸性岩浆/花岗岩": ["花岗岩", "伟晶岩", "中酸性", "斑岩", "酸性岩", "S型"],
    "基性-超基性/层状侵入": ["超基性", "基性", "层状侵入", "蛇绿岩", "辉长"],
    "碳酸盐岩/接触带": ["碳酸盐岩", "灰岩", "白云岩", "接触带", "矽卡", "台地"],
    "火山活动": ["火山", "破火山口"],
    "风化壳/表生富集": ["风化壳", "红土", "风化", "淋滤"],
    "变质/剪切带": ["变质", "剪切带", "绿岩带", "片岩", "片麻岩"],
}


def _setting_tokens(text: str) -> set:
    """从一段地质描述中提取所属的'构造要素组'标签集合"""
    text = text or ""
    groups = set()
    for label, kws in _SETTING_GROUPS.items():
        if any(kw in text for kw in kws):
            groups.add(label)
    return groups


def _rank_metallogenic_types(mineral_info: Dict[str, Any], location: Optional[Dict]):
    """按与 ROI 构造背景的吻合度对成矿类型排序。

    Returns:
        (ranked, ctx_tokens)
        ranked: [(mt, score, sorted_matched_keywords), ...]，吻合度高者在前
        ctx_tokens: 本区构造单元提取出的关键词集合
    """
    tu = location.get('center_tectonic') if location else None
    context = ""
    if tu:
        context = f"{tu.get('name', '')} {tu.get('features', '')}"
    ctx_tokens = _setting_tokens(context)

    ranked = []
    for mt in mineral_info.get('metallogenic_types', []):
        mt_tokens = _setting_tokens(mt.get('tectonic_setting', ''))
        matched = ctx_tokens & mt_tokens
        ranked.append((mt, len(matched), sorted(matched)))
    # 稳定排序：吻合度降序，原顺序为次序（Python sort 稳定）
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked, ctx_tokens


def _primary_symbols(mineral: str, key_elements: list) -> list:
    """从指示元素中挑出'直接指示元素'（其中文名是矿种名子串者）。

    例：铜→[Cu]；铅锌→[Pb, Zn]；钨锡→[W, Sn]。
    """
    prim = []
    for e in key_elements:
        cn = _ELEMENT_CN.get(e, '')
        if cn and (cn in mineral or mineral in cn):
            prim.append(e)
    return prim


def _geophysical_coverage(mineral_info: Dict[str, Any], geophysical: Dict[str, Any]) -> list:
    """比对该矿种推荐物探方法与本次实际获取的数据，返回 [(方法, 覆盖状态)]"""
    methods = mineral_info.get('all_geophysical_methods', [])
    has_mag = geophysical.get('magnetic') is not None
    has_wgm = geophysical.get('gravity') is not None
    has_icgem = geophysical.get('icgem') is not None

    # 重力状态如实区分 WGM2012（需下载）与 ICGEM（本地计算）
    if has_wgm:
        grav_status = "✅ WGM2012 已获取（见下方分布图）"
    elif has_icgem:
        grav_status = "✅ ICGEM 已算（见下方分布图）；WGM2012 需手动下载"
    else:
        grav_status = "🔗 链接模式（未自动下载）"

    rows = []
    for m in sorted(methods):
        # 注意：电法/电磁/激电类需先判定，避免"电磁法"被后面的 '磁' 误匹配为航磁
        if any(k in m for k in ['IP', '激电', 'CSAMT', 'MT', '电磁', '电法', '大地电磁']):
            status = "⚠️ 无全国性公开数据集，常规需野外施测；部分地区历史成果可在地质资料馆/文献查取"
        elif '放射性' in m or 'γ' in m or '能谱' in m or '氡' in m:
            status = "⚠️ 无全国性公开数据集，需航空/地面伽马能谱测量；部分图幅成果见地质资料馆/文献"
        elif '地震' in m:
            status = "⚠️ 多为矿权/油田方专有数据，公开渠道少，需合作获取或查文献"
        elif '磁' in m or '航磁' in m:
            status = "✅ 本次已获取（EMAG2 航磁，见下方分布图）" if has_mag else "🔗 链接模式（未自动下载）"
        elif '重力' in m:
            status = grav_status
        elif 'DEM' in m:
            status = "✅ 公开 DEM 可下载（见下文）"
        else:
            status = "🔗 需自行获取"
        rows.append((m, status))
    return rows


def _render_spine(
    mineral: str,
    mineral_info: Dict[str, Any],
    location: Optional[Dict],
    ranked: list,
    is_oil: bool,
    pb: Optional[Dict],
):
    """生成"目标矿种成矿分析"主线章节，返回 (markdown, best_model)"""
    tu = location.get('center_tectonic') if location else None
    md = ""

    # --- 构造适宜性结论：把矿种与本区构造背景直接挂钩 ---
    if tu:
        major_cn = sorted({_ELEMENT_CN.get(m, m) for m in tu.get('major_minerals', [])})
        in_catalog = any((mineral in m or m in mineral) for m in major_cn)
        if in_catalog:
            verdict = f"✅ **构造背景有利** —— {mineral} 属于 {tu['name']} 已知主要矿产"
        else:
            verdict = (f"⚠️ **需进一步评估** —— {mineral} 不在 {tu['name']} 主要矿产目录中，"
                       f"须结合下列成矿模型与本区具体地质条件论证")
        md += (f"本报告围绕目标矿种 **{mineral}** 展开。ROI 位于 **{tu['name']}**"
               f"（{tu.get('features', '')}），该单元已知主要矿产为 "
               f"{', '.join(major_cn)}。\n\n{verdict}。\n\n")
    else:
        md += (f"本报告围绕目标矿种 **{mineral}** 展开。ROI 未落入已识别构造单元"
               f"（可能在海域或境外），以下成矿模型供通用参考，吻合度未做本区校正。\n\n")

    if not ranked:
        md += "_知识库中暂无该矿种的成矿模型记录，下列章节按通用框架组织。_\n\n"
        return md, None

    best_model = ranked[0][0]

    # 本区构造要素组（"吻合度"判断的可追溯依据：来自构造单元 name+features 的关键词分组）
    tu_ctx = f"{tu.get('name', '')} {tu.get('features', '')}" if tu else ""
    ctx_tokens = _setting_tokens(tu_ctx)
    ctx_str = '、'.join(sorted(ctx_tokens)) if ctx_tokens else '（构造单元描述未识别出标准构造要素）'

    md += (f"下列 {len(ranked)} 种 **{mineral}** 成矿模型按与本区构造背景的吻合度排序，"
           f"**第一项为最契合本区的模型**；后续化探、物探、结论各章节均围绕它展开。\n\n")
    md += (f"> **吻合度判定依据**：将本区构造要素组与各模型的构造背景要素组比对，有重叠即判为契合。"
           f"本区构造要素组 = ｛{ctx_str}｝。\n\n")

    for idx, (mt, score, matched) in enumerate(ranked):
        mt_tokens = _setting_tokens(mt.get('tectonic_setting', ''))
        mt_str = '、'.join(sorted(mt_tokens)) if mt_tokens else '（未识别出标准构造要素）'
        if idx == 0 and score > 0:
            tag = "✅ 本区最契合模型"
        elif idx == 0:
            tag = "参考模型（与本区构造要素组无重叠，需结合实际地质背景论证）"
        else:
            tag = "候选模型"
        if matched:
            fit = (f"契合 — 该模型构造要素组 ｛{mt_str}｝ 与本区 ｛{ctx_str}｝ "
                   f"共有：{'、'.join(sorted(matched))}")
        else:
            fit = (f"未自动判定契合 — 该模型构造要素组 ｛{mt_str}｝ 与本区 ｛{ctx_str}｝ "
                   f"无重叠；这是基于关键词分组的初判，需结合实际地质背景论证")

        md += f"""### {idx + 1}. {mt['name']} — {tag}

| 要素 | 内容 |
|------|------|
| **构造背景** | {mt.get('tectonic_setting', '')} |
| **与本区吻合度** | {fit} |
| **赋矿围岩** | {mt.get('host_rocks', '')} |
| **蚀变分带** | {mt.get('alteration', '')} |
| **指示元素组合** | {mt.get('element_association', '')} |
"""
        if not is_oil:
            key_elems = mt.get('key_elements', [])
            prim = _primary_symbols(mineral, key_elems)
            prim_str = ', '.join(prim) if prim else (key_elems[0] if key_elems else '—')
            path = [e for e in key_elems if e not in prim]
            md += (f"| **直接指示元素** | {prim_str} |\n"
                   f"| **前缘/晕(pathfinder)元素** | {', '.join(path) if path else '—'} |\n")
        md += (f"| **物探响应特征** | {mt.get('geophysical_anomalies', '')} |\n"
               f"| **推荐物探方法** | {', '.join(mt.get('geophysical_methods', []))} |\n\n")

    # --- 油气：六大要素 + 盆地匹配（围绕成藏模型展开）---
    if is_oil:
        six = mineral_info.get('six_elements', [])
        if six:
            md += "### 油气成藏关键要素（六要素框架）\n\n"
            for se in six:
                md += f"- **{se['element']}**: {se['description']}（关键参数: {se['key_params']}）\n"
            md += "\n"
        if pb:
            md += f"### {pb['name']} — 该盆地已知成藏特征\n\n"
            md += f"- **面积**: {pb['area_km2']:,} km²\n"
            md += f"- **主要成藏组合**: {', '.join(pb['main_plays'])}\n"
            md += f"- **最深钻井**: {pb['max_well_depth']} m\n"
            md += f"- **建议检索**: 在 CNKI/万方 检索 '{pb['name']} {mineral} 成藏'\n\n"

    return md, best_model


def generate_report(
    roi: Dict[str, Any],
    mineral: str,
    mineral_info: Dict[str, Any],
    location: Dict[str, Any],
    geological: Dict[str, Any],
    geophysical: Dict[str, Any],
    geochemical: Dict[str, Any],
    live_data: Optional[Dict] = None,
    output_dir: Optional[Path] = None,
) -> str:
    """生成 ROI 针对性 Markdown 报告（以目标矿种成矿模型为主线）"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from .logger import get_logger
    _rpt_log = get_logger("report")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tu = location.get('center_tectonic')
    pb = location.get('petroleum_basin')
    is_oil = mineral in ('石油', '天然气', '油气')
    tu_name = tu['name'] if tu else ''
    pb_name = pb['name'] if pb else ''

    # --- 成矿模型主线：与本区构造背景做吻合度匹配，贯穿全文 ---
    ranked, ctx_tokens = _rank_metallogenic_types(mineral_info, location)

    # ============================================================
    report = f"""# 🏔️ 矿产勘查前期资料收集报告

> **生成时间**: {now} | **目标矿种**: {mineral} | **图幅号**: {geological.get('map_sheet', 'N/A')}

---

## 一、ROI 位置与构造归属

| 项目 | 内容 |
|------|------|
| **中心坐标** | {roi['center']['lon']:.4f}°E, {roi['center']['lat']:.4f}°N |
| **面积** | {roi['area_km2']:.2f} km² |
| **范围** | {_format_bbox(roi)} |
"""

    # --- 构造归属 ---
    if tu:
        report += f"""| **构造单元** | {tu['name']} ({tu.get('name_en', '')}) |
| **构造特征** | {tu.get('features', 'N/A')} |
| **区内主要矿产** | {', '.join(tu.get('major_minerals', []))} |
"""
    else:
        report += """| **构造单元** | 未识别（可能在海域或境外） |
"""

    # --- 跨单元 ---
    intersecting = location.get('intersecting_tectonics', [])
    if intersecting:
        report += f"""|
| **跨构造单元** | ROI 跨越以下构造单元: {', '.join(f"{i['name']}({i['overlap_fraction']*100:.0f}%)" for i in intersecting)} |
"""

    # --- 含油气盆地 ---
    if pb:
        report += f"""| **含油气盆地** | {pb['name']}（{pb['area_km2']:,} km²） |
| **主要成藏组合** | {', '.join(pb['main_plays'])} |
| **最大钻井深度** | {pb['max_well_depth']} m |
"""
    elif is_oil and not pb:
        report += """| **含油气盆地** | ⚠️ ROI 不在已知主要含油气盆地范围内 |
"""

    # --- 矿种在该单元的潜力（速览标志，详见第二节）---
    if tu and not is_oil:
        major_cn = {_ELEMENT_CN.get(m, m) for m in tu.get('major_minerals', [])}
        mineral_match = [m for m in major_cn if mineral in m or m in mineral]
        if mineral_match:
            report += f"""|
| **矿种匹配** | ✅ '{mineral}' 在 {tu['name']} 已知矿产目录中 ({', '.join(mineral_match)}) |
"""
        else:
            report += f"""|
| **矿种匹配** | ⚠️ '{mineral}' 不在 {tu['name']} 主要矿产目录中，需结合具体成矿条件评估 |
"""

    # ============================================================
    # 二、目标矿种成矿分析（全报告主线）
    # ============================================================
    spine_md, best_model = _render_spine(mineral, mineral_info, location, ranked, is_oil, pb)
    report += f"\n---\n\n## 二、目标矿种成矿分析（{mineral}）\n\n"
    report += spine_md

    # ============================================================
    # 三、地球化学背景与元素套合（针对矿种）
    # ============================================================
    report += f"\n---\n\n## 三、地球化学背景与元素套合（针对 {mineral}）\n\n"

    bgs = geochemical.get('backgrounds', {})
    thresholds = bgs.get('anomaly_thresholds', {})
    source_unit = bgs.get('source_unit', '全国')
    national_ref = bgs.get('national_reference', {})

    if thresholds:
        report += f"""**背景值来源**: {source_unit}（史长义等, 2016）

> 下表只列出与 **{mineral}** 成矿相关的 **{len(thresholds)} 种指示元素**的水系沉积物背景值及异常分级（已按矿种指示元素筛选，非全套 39 元素）。这些数值是判断"本区某元素化探高值是否构成异常"的定量标尺。

| 元素 | {source_unit}背景值 | 全国背景值 | 弱异常(1.5×) | 中异常(2×) | 强异常(3×) |
|------|:---:|:---:|:---:|:---:|:---:|
"""
        for elem, vals in sorted(thresholds.items()):
            nat = national_ref.get(elem, '—')
            report += (
                f"| {elem} | {vals['background']} | {nat} | "
                f"{vals['weak_anomaly']} | "
                f"{vals['moderate_anomaly']} | "
                f"{vals['strong_anomaly']} |\n"
            )
        report += "\n"

    # --- 异常识别要点：回扣成矿模型的元素套合 ---
    if best_model and not is_oil:
        assoc = best_model.get('element_association', '')
        key_elems = best_model.get('key_elements', [])
        prim = _primary_symbols(mineral, key_elems)
        prim_str = ', '.join(prim) if prim else (key_elems[0] if key_elems else mineral)
        path = [e for e in key_elems if e not in prim]
        report += f"**🎯 针对 {mineral} 的异常识别要点（依据「{best_model['name']}」模型）**\n\n"
        report += (f"- 本区找 {mineral} 的核心，是识别 **{assoc}** 这一元素套合，"
                   f"而非孤立看单元素高值。\n")
        report += f"- **直接指示元素**：{prim_str} —— 其异常直接反映矿(化)体。\n"
        if path:
            report += (f"- **前缘/晕(pathfinder)元素**：{', '.join(path)} —— 常构成矿体的前缘晕/尾晕，"
                       f"用于追踪隐伏矿体、判断剥蚀程度。\n")
        report += (f"- 圈靶时应优先关注上述元素 **同时高于中异常(2×)阈值且空间套合** 的地段，"
                   f"并按上述**指示元素组合**描述的分带规律（前缘晕→矿体→尾晕）判断矿体产出部位。\n")
        missing = [e for e in key_elems if e not in thresholds]
        if missing:
            report += (f"- ⚠️ 关键指标 {', '.join(missing)} 不在水系沉积物 39 元素背景体系内，"
                       f"需通过岩石/土壤地球化学或专项测试补充。\n")
        report += "\n"
    elif is_oil:
        report += ("**🎯 针对油气的地球化学评价**：常规元素背景值不适用于油气，"
                   "应改用有机地球化学指标（TOC、Ro、S1+S2、HI 等，见第二节六要素），"
                   "重点评价烃源岩品质与成熟度。\n\n")

    # ============================================================
    # 四、地球物理数据与矿种响应
    # ============================================================
    report += f"---\n\n## 四、地球物理数据与 {mineral} 响应\n\n"

    # --- 本矿种物探响应总述 + 方法覆盖度 ---
    if best_model and best_model.get('geophysical_anomalies'):
        report += (f"> **本矿种物探响应特征（「{best_model['name']}」模型）**："
                   f"{best_model['geophysical_anomalies']}。下方各类数据应据此解读。\n\n")
    coverage = _geophysical_coverage(mineral_info, geophysical)
    if coverage:
        report += "**物探方法覆盖度**（该矿种推荐方法 vs 本次自动获取情况）\n\n"
        report += "| 推荐物探方法 | 本次覆盖情况 |\n|------|------|\n"
        for m, status in coverage:
            report += f"| {m} | {status} |\n"
        report += "\n"

    # --- 磁法 ---
    mag = geophysical.get('magnetic')
    if mag:
        report += f"""### 磁异常数据 ✅ 已获取

| 项目 | 内容 |
|------|------|
| **来源** | {mag['source']} |
| **分辨率** | {mag.get('resolution', 'N/A')} |
| **文件** | `{mag['file']}` |
| **说明** | 已按 ROI 外扩范围裁剪，可在 QGIS 中与地质图/化探叠合 |

"""
        if best_model:
            report += (f"> **如何用于找 {mineral}**：结合「{best_model['name']}」模型的磁响应特征，"
                       f"圈定与成矿相关的磁性/低磁地质体（如岩体、构造带），再与 {mineral} 指示元素"
                       f"化探套合异常叠合定位靶区。\n\n")
        # --- 嵌入磁异常分布图 ---
        if mag.get('map'):
            try:
                map_rel = Path(mag['map']).relative_to(output_dir).as_posix()
                report += f"![磁异常分布图]({map_rel})\n\n"
                report += (
                    "*图：ROI 范围内磁异常空间分布（nT），"
                    "星标为中心点，黑线为 ROI 边界*\n\n"
                )
            except ValueError:
                pass
    else:
        report += "### 磁异常数据 🔗 链接模式\n\n"
        for link in geophysical.get('links', []):
            if '磁' in link.get('label', ''):
                report += f"- [{link['label']}]({link['url']}) — {link.get('note', '')}\n"
        report += "\n"

    # --- 重力 ---
    grav = geophysical.get('gravity')
    if grav:
        report += f"""### 重力数据 ✅ 已获取

| 项目 | 内容 |
|------|------|
| **来源** | {grav['source']} |
| **分辨率** | {grav.get('resolution', 'N/A')} |
| **文件** | `{grav['file']}` |

"""
        if best_model:
            report += (f"> **如何用于找 {mineral}**：依「{best_model['name']}」模型的密度响应，"
                       f"用重力异常识别隐伏岩体/盆地基底/接触带等控矿要素，与磁法、化探联合解释。\n\n")
        # --- 嵌入布格重力异常分布图 ---
        if grav.get('map'):
            try:
                map_rel = Path(grav['map']).relative_to(output_dir).as_posix()
                report += f"![布格重力异常分布图]({map_rel})\n\n"
                report += (
                    "*图：ROI 范围内 WGM2012 布格重力异常空间分布（mGal），"
                    "星标为中心点，黑线为 ROI 边界*\n\n"
                )
            except ValueError:
                pass
    else:
        report += "### 重力数据 🔗 链接模式\n\n"
        for link in geophysical.get('links', []):
            if '重力' in link.get('label', ''):
                report += f"- [{link['label']}]({link['url']}) — {link.get('note', '')}\n"
        report += "\n"

    # --- ICGEM ---
    icgem = geophysical.get('icgem')
    if icgem:
        report += f"""### ICGEM 重力场模型计算结果 ✅

| 项目 | 内容 |
|------|------|
| **来源** | {icgem['source']} |
| **模型** | {icgem.get('model', 'N/A')} |
| **截断阶次** | N = {icgem.get('max_degree', 'N/A')} |
| **分辨率** | {icgem.get('resolution', 'N/A')} |

"""
        for func_name, func_data in icgem.get("functionals", {}).items():
            display = "重力扰动" if func_name == "gravity_disturbance" else "大地水准面高"
            unit = func_data.get("unit", "")
            report += f"**{display}** ({unit})\n\n"
            report += f"| 统计量 | 值 |\n|--------|----|\n"
            report += f"| 最小值 | {func_data.get('min', 'N/A')} {unit} |\n"
            report += f"| 最大值 | {func_data.get('max', 'N/A')} {unit} |\n"
            report += f"| 平均值 | {func_data.get('mean', 'N/A')} {unit} |\n\n"
            if func_data.get('map'):
                try:
                    map_rel = Path(func_data["map"]).relative_to(output_dir).as_posix()
                    report += f"![{display}分布图]({map_rel})\n\n"
                except (ValueError, TypeError):
                    report += f"- 分布图: `{func_data['map']}`\n\n"
            # 找矿解读：重力扰动给模型导向提示；大地水准面如实说明意义有限
            if func_name == "gravity_disturbance":
                if best_model:
                    report += (f"> **如何用于找 {mineral}**：重力扰动反映地下密度差异，"
                               f"结合「{best_model['name']}」模型可识别隐伏岩体、基底起伏与接触带等控矿要素，"
                               f"与磁法、化探异常联合定位靶区。\n\n")
            else:  # geoid_height 大地水准面高
                report += ("> **说明**：大地水准面高反映区域深部质量分布与大地构造背景，"
                           "**对直接圈定矿体意义有限**，仅作区域构造格架参考，不单独用于找矿。\n\n")
    elif geophysical.get('icgem_link'):
        report += f"### 在线重力场精细计算\n\n[ICGEM 在线计算]({geophysical['icgem_link']}) — 可自定义重力场模型和计算参数\n\n"
    else:
        report += "### ICGEM 重力场\n\nICGEM 在线计算链接已包含在上方链接列表中。\n\n"

    # --- DEM ---
    dem = geophysical.get('dem', {})
    if dem.get('downloaded'):
        report += "### DEM 地形数据 ✅ 已获取\n\n"
        report += f"| 项目 | 内容 |\n|------|------|\n"
        report += f"| **来源** | {dem.get('source', 'SRTM')} |\n"
        report += f"| **分辨率** | {dem.get('resolution', '~30 m')} |\n"
        report += f"| **文件** | `{dem.get('file', '')}` |\n"
        st = dem.get('stats') or {}
        if st:
            report += (f"| **高程范围** | {st.get('min')} ~ {st.get('max')} m"
                       f"（均值 {st.get('mean')} m） |\n")
        report += "\n"
        if dem.get('map'):
            try:
                map_rel = Path(dem['map']).relative_to(output_dir).as_posix()
                report += f"![DEM 地形高程分布图]({map_rel})\n\n"
                report += "*图：ROI 范围 SRTM 地形高程分布（m），星标为中心点，黑线为 ROI 边界*\n\n"
            except (ValueError, TypeError):
                pass
        report += (f"> **如何用于找 {mineral}**：地形/水系受构造与岩性控制，"
                   f"线性谷地、环形构造、陡坎常对应断裂/岩体边界；"
                   f"风化壳/蚀变带在地貌上亦有响应，可与物探、化探异常套合辅助圈靶。\n\n")
    else:
        report += "### DEM 地形数据 🔗 链接模式\n\n"
        report += "_未配置 OpenTopography API key，未自动下载；可经以下渠道获取 SRTM 30m DEM：_\n\n"
        for link in dem.get('links', []):
            report += f"- [{link['label']}]({link['url']}) — {link.get('note', '')}\n"
        report += "\n"

    # --- 实时查询论文 ---
    report += f"\n---\n\n## 五、区域已发表研究论文\n\n"
    papers = live_data.get("papers", []) if live_data else []
    if papers:
        _rpt_log.info("开始生成论文部分: %d 篇论文", len(papers))

        # 论文 LLM 提炼（围绕本 ROI 找矿；未配置 key 则跳过，保留下方列表）
        try:
            from .paper_synthesis import synthesize_papers
            _synth = synthesize_papers(papers, mineral, location, roi)
        except Exception as _e:
            _synth = None
            _rpt_log.warning("论文提炼调用异常: %s", _e)
        if _synth:
            report += f"### 📌 论文要点提炼（围绕本 ROI 找矿）\n\n{_synth}\n\n---\n\n"

        report += "### 论文清单\n\n"
        report += f"> 自动检索 OpenAlex + Semantic Scholar，针对 **{tu_name}** + **{mineral}**。\n"
        report += "> 摘要来自数据库，**全文受版权限制无法内嵌，请点击下方链接到出版方/DOI 查看**。\n\n"
        for i, p in enumerate(papers[:15], 1):
            authors = ", ".join(p.get("authors", [])[:3])
            cited = p.get("citation_count") or p.get("cited_by") or 0

            title = p.get('title', '')
            ab = p.get("abstract", "")

            # 翻译英文标题和摘要
            title_cn = ""
            if _has_en_char(title):
                _rpt_log.debug("翻译论文 %d 标题...", i)
                title_cn = _translate_en_to_cn(title)
            ab_cn = ""
            if _has_en_char(ab):
                _rpt_log.debug("翻译论文 %d 摘要...", i)
                ab_cn = _translate_en_to_cn(ab)
            _rpt_log.debug("论文 %d/%d 完成", i, min(len(papers), 15))

            # 溯源链接：有 DOI/出处则给原文链接；并始终给百度学术按标题检索（中文论文兜底）
            links = []
            src_url = p.get("url", "")
            if src_url:
                links.append(f"[原文/DOI]({src_url})")
            if title:
                links.append(f"[百度学术](https://xueshu.baidu.com/s?wd={quote(title)})")
            link_str = "　（" + " · ".join(links) + "）" if links else ""

            report += f"{i}. **[{p.get('year','?')}] {title}**{link_str}\n"
            if title_cn and title_cn != title:
                report += f"   *{title_cn}*\n"
            report += f"   *{authors}* | 引用 {cited}\n"
            if ab_cn and ab_cn != ab:
                report += f"   > {ab_cn}\n"
            elif ab:
                report += f"   > {ab}\n"
            report += "\n"
    else:
        report += (f"> 本次未自动检索到 {tu_name + ' ' if tu_name else ''}{mineral} 相关论文，"
                   f"可使用第六节「学术文献」中按构造单元/盆地精准检索的 CNKI 链接深挖前人研究。\n\n")

    # --- ROI 中心物探值 ---
    if live_data:
        rv = live_data.get("raster_values", {})
        if rv.get("magnetic_nt") is not None or rv.get("bouguer_mgal") is not None:
            report += "### ROI 中心点地球物理参数\n\n| 参数 | 数值 | 来源 |\n|------|------|------|\n"
            if rv.get("magnetic_nt") is not None:
                report += f"| 磁异常 | **{rv['magnetic_nt']} nT** | EMAG2 v3 (上延 4km) |\n"
            if rv.get("bouguer_mgal") is not None:
                report += f"| 布格重力异常 | **{rv['bouguer_mgal']} mGal** | WGM2012 |\n"
            report += "\n"

    # ============================================================
    # 六、地质资料在线检索
    # ============================================================
    report += "\n---\n\n## 六、地质资料在线检索\n\n"

    # 区域地质图（Macrostrat 在线出图，失败则降级为下方 OneGeology 在线查看链接）
    gm = geological.get('geology_map')
    if gm and gm.get('map'):
        try:
            gm_rel = Path(gm['map']).relative_to(output_dir).as_posix()
            report += "### 区域地质图（在线获取）✅\n\n"
            report += f"![ROI 区域地质图]({gm_rel})\n\n"
            report += f"*图：{gm.get('source', '')}；{gm.get('note', '')}（星标为中心点，红框为 ROI）*\n\n"
        except (ValueError, TypeError):
            pass

    report += f"""以下检索链接已自动带入你的 ROI 坐标和图幅号：

### NGAC 全国地质资料馆

{_format_links(geological.get('ngac_geology', []))}

### 矿产地与钻孔

{_format_links(geological.get('ngac_mineral', []))}

### 化探异常图

{_format_links(geological.get('ngac_geochem', []))}

### 学术文献（按构造单元/盆地精准检索）

{_format_links(geological.get('cnki', []))}

### OneGeology 全球地质图（在线查看）

[在 OneGeology 查看 ROI]({geological.get('onegeology', '')}) — 可在线叠加各国地质图层

> ⚠️ **数据获取边界（如实说明）**：NGAC 的 1:5万 地质图、化探原始点位数据**未开放在线下载、无公开 API**，上方为检索入口与建议检索词，原始数据仍需经资料馆线下渠道；CNKI/万方文献需登录查看全文。上方"区域地质图"已用 Macrostrat 全球地质底图在线出图；OneGeology 的中国 1:100万 图层在 cgs.gov.cn（跨境访问受限），故以在线查看链接形式提供。

"""

    report += f"""---

## 七、数据收集优先级（基于 {mineral} × {tu_name if tu else '通用'} 特征）

"""

    for item in mineral_info.get('recommended_data_priority', []):
        report += f"{item['rank']}. **{item['data']}** → {item['method']}\n"

    # ============================================================
    # 八、综合结论与靶区建议（模型驱动）
    # ============================================================
    report += "\n---\n\n## 八、综合结论与靶区建议\n\n"

    loc_str = f"**{tu_name}**" if tu_name else "本区"
    if best_model and not is_oil:
        report += (f"综合上述资料，在 {loc_str} 寻找 **{mineral}**，最可能的成矿模型为 "
                   f"**{best_model['name']}**。应围绕该模型锁定"
                   f"「**赋矿围岩 + 蚀变分带 + 元素套合 + 物探响应**」四位一体的找矿靶区：\n\n")
        report += f"- **赋矿围岩**：{best_model.get('host_rocks', '')}\n"
        report += f"- **蚀变标志**：{best_model.get('alteration', '')}\n"
        report += f"- **化探标志**：{best_model.get('element_association', '')}\n"
        report += f"- **物探标志**：{best_model.get('geophysical_anomalies', '')}\n\n"
        if tu:
            major_cn = {_ELEMENT_CN.get(m, m) for m in tu.get('major_minerals', [])}
            in_catalog = any((mineral in m or m in mineral) for m in major_cn)
            report += ("> **构造适宜性**：" + (
                f"{mineral} 属本区已知主要矿产，成矿地质背景有利，可优先投入。\n\n" if in_catalog
                else f"{mineral} 非本区典型矿种，需以上述四位一体标志严格验证后再决定投入。\n\n"))
    elif is_oil:
        report += (f"综合上述资料，在 {loc_str} 开展 **{mineral}** 勘探，应回到成藏六要素"
                   f"（烃源岩—储层—盖层—圈闭—运移—保存）的有效配置评价"
                   + (f"，并紧扣 **{pb['name']}** 的已知成藏组合（{', '.join(pb['main_plays'])}）" if pb else "")
                   + "。\n\n")
    else:
        report += f"综合上述资料，在 {loc_str} 寻找 **{mineral}**，建议结合区域地质背景与下列数据综合圈靶。\n\n"

    report += "**下一步工作建议**\n\n"
    if best_model and not is_oil:
        report += (f"1. **三重叠合圈靶**：在 QGIS 中将物探异常、{mineral} 指示元素套合化探异常、"
                   f"有利赋矿围岩/蚀变（{best_model['name']} 模型）三者叠合，圈定优先靶区。\n")
    else:
        report += "1. **叠合分析**：在 QGIS 中将物探、化探与地质图叠合，圈定有利部位。\n"
    report += (f"2. **化探异常验证**：对照第三节 {source_unit}背景值，在 NGAC 化探图中圈出 "
               f"{mineral} 指示元素高于中异常(2×)阈值的套合区。\n")
    report += "3. **物探补充**：对照第四节覆盖度表中标 ⚠️ 的方法（如电法/放射性/地震），按需野外施测或查地质资料馆历史成果。\n"
    report += (f"4. **文献深挖**：精读第五/六节 {tu_name + ' ' if tu_name else ''}{mineral} 相关前人研究，"
               f"关注已报道矿化点与异常查证结论。\n")
    report += ("5. **大比例尺数据**：1:5万 地质图与化探原始点位数据未在线开放（NGAC 无公开 API），"
               "需经全国地质资料馆线下渠道申请获取；线上仅能拿到上述区域地质图、公开物探/DEM 与文献。\n")

    report += f"""
---

> 📌 本报告由 Prospector 自动生成 | 目标矿种: {mineral} | ROI: {roi['center']['lon']:.4f}°E, {roi['center']['lat']:.4f}°N | {tu_name if tu else ''}
"""

    _rpt_log.info("报告内容生成完毕, 开始写入文件...")
    report_path = output_dir / "00_项目摘要.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    _rpt_log.info("报告已保存: %s", report_path)
    return str(report_path)


def save_json_summary(
    roi: Dict[str, Any],
    mineral: str,
    mineral_info: Dict[str, Any],
    geological: Dict[str, Any],
    geophysical: Dict[str, Any],
    geochemical: Dict[str, Any],
    output_dir: Path,
    location: Optional[Dict] = None,
) -> str:
    """保存 JSON 格式摘要"""
    output_dir = Path(output_dir)

    # 成矿模型主线信息（与报告正文一致）
    ranked, _ = _rank_metallogenic_types(mineral_info, location or {})
    best = ranked[0] if ranked else None
    best_model_name = best[0]['name'] if best else None
    best_fit_score = best[1] if best else 0
    pathfinder = []
    if best:
        ke = best[0].get('key_elements', [])
        prim = _primary_symbols(mineral, ke)
        pathfinder = [e for e in ke if e not in prim]

    summary = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "mineral": mineral,
            "roi_file": roi.get('filename', ''),
            "roi_area_km2": roi.get('area_km2'),
            "roi_center": roi['center'],
            "roi_bbox": roi['bbox'],
        },
        "location": {
            "tectonic_unit": location.get('center_tectonic', {}).get('name') if location else None,
            "petroleum_basin": location.get('petroleum_basin', {}).get('name') if location else None,
            "intersecting_units": [i['name'] for i in location.get('intersecting_tectonics', [])] if location else [],
        },
        "metallogenic": {
            "best_model": best_model_name,
            "best_model_fit_score": best_fit_score,
            "model_count": len(ranked),
            "pathfinder_elements": pathfinder,
        },
        "geophysical": {
            "magnetic_downloaded": geophysical.get('magnetic') is not None,
            "gravity_downloaded": geophysical.get('gravity') is not None,
        },
        "geochemical": {
            "background_unit": geochemical.get('backgrounds', {}).get('source_unit', '全国'),
            "element_count": len(geochemical.get('backgrounds', {}).get('anomaly_thresholds', {})),
        },
        "links": {
            "ngac_geology": len(geological.get('ngac_geology', [])),
            "ngac_mineral": len(geological.get('ngac_mineral', [])),
            "ngac_geochem": len(geological.get('ngac_geochem', [])),
            "cnki": len(geological.get('cnki', [])),
        },
    }

    json_path = output_dir / "summary.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return str(json_path)
