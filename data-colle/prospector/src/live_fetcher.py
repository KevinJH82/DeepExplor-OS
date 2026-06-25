"""实时数据获取器 — 真正去对方网站查数据，不扔链接"""

import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote

import requests
import numpy as np

from .logger import get_logger
from .http_client import get as http_get
from config import OPENALEX_URL, S2_URL, MYMEMORY_URL

logger = get_logger("live")

# ============================================================
# 1. OpenAlex — 免费学术论文查询 (无需 API key)
# ============================================================


def search_papers_openalex(
    query: str,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    查询 OpenAlex 学术论文数据库

    Returns:
        [{title, year, authors, doi, cited_by, abstract, url}]
    """
    papers = []
    try:
        params = {
            "search": query,
            "per_page": min(max_results, 25),
            "sort": "cited_by_count:desc",
            "filter": "type:article",
        }
        resp = http_get(OPENALEX_URL, params=params, timeout=15)
        if resp.status_code != 200:
            return papers

        data = resp.json()
        total = data.get("meta", {}).get("count", 0)

        for r in data.get("results", []):
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in r.get("authorships", [])
                if a.get("author", {}).get("display_name")
            ]
            # OpenAlex 的 abstract 是 inverted_index dict，需转换
            abstract = _decode_openalex_abstract(r.get("abstract_inverted_index"))
            # OpenAlex 的 doi 字段通常已是完整 URL（https://doi.org/...），避免重复拼前缀
            doi = r.get("doi") or ""
            if doi.startswith("http"):
                doi_url = doi
            elif doi:
                doi_url = f"https://doi.org/{doi}"
            else:
                doi_url = ""
            papers.append({
                "title": r.get("title", ""),
                "year": r.get("publication_year"),
                "authors": authors[:5],
                "doi": doi,
                "cited_by": r.get("cited_by_count", 0),
                "abstract": _truncate(abstract, 500),
                "url": doi_url,
            })

        logger.info("OpenAlex: %d papers found for '%s...', returning top %d", total, query[:60], len(papers))
        return papers

    except Exception as e:
        logger.warning("OpenAlex: %s", e)
        return papers


# ============================================================
# 2. Semantic Scholar — 免费论文 API
# ============================================================



def search_papers_semantic_scholar(
    query: str,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    查询 Semantic Scholar 论文数据库

    Returns:
        [{title, year, authors, citation_count, abstract, url}]
    """
    papers = []
    try:
        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": "title,year,authors,abstract,citationCount,externalIds",
        }
        resp = http_get(S2_URL, params=params, timeout=15)
        if resp.status_code == 429:
            logger.warning("Semantic Scholar 限流，跳过")
            return papers
        if resp.status_code != 200:
            return papers

        data = resp.json()
        total = data.get("total", 0)

        for r in data.get("data", []):
            authors = [a.get("name", "") for a in r.get("authors", []) if a.get("name")]
            paper_id = r.get("paperId", "")
            papers.append({
                "title": r.get("title", ""),
                "year": r.get("year"),
                "authors": authors[:5],
                "citation_count": r.get("citationCount", 0),
                "abstract": _truncate(r.get("abstract"), 500),
                "url": f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else "",
            })

        logger.info("Semantic Scholar: %d papers, returning top %d", total, len(papers))
        return papers

    except Exception as e:
        logger.warning("Semantic Scholar: %s", e)
        return papers


# ============================================================
# 3. 合并论文结果（去重 + 排序）
# ============================================================

# 矿种中→英翻译
MINERAL_EN = {
    "铜": "copper", "金": "gold", "锂": "lithium", "铅锌": "lead zinc",
    "钨锡": "tungsten tin", "稀土": "rare earth", "铁": "iron",
    "石油": "petroleum", "天然气": "natural gas", "油气": "oil gas",
}

# 各矿种专用检索词
_QUERY_STRATEGIES = {
    "石油": {
        "cn_terms": ["石油地质", "油气勘探", "盆地演化", "成藏条件"],
        "en_terms": ["petroleum geology", "hydrocarbon exploration",
                     "basin analysis", "source rock"],
    },
    "天然气": {
        "cn_terms": ["天然气地质", "油气勘探", "盆地演化", "成藏条件"],
        "en_terms": ["natural gas geology", "hydrocarbon exploration",
                     "basin analysis", "source rock"],
    },
    "油气": {
        "cn_terms": ["油气勘探", "盆地演化", "成藏条件"],
        "en_terms": ["petroleum exploration", "basin evolution", "hydrocarbon"],
    },
}


def _build_queries(
    tectonic_unit: Optional[Dict],
    mineral: str,
    mineral_en: str,
) -> list:
    """
    构建多组检索词（OpenAlex 中文搜索效果差，多用英文 + 多轮尝试）
    返回 [(query_str, is_english)]
    """
    queries = []
    tu_name = tectonic_unit.get("name", "") if tectonic_unit else ""
    tu_name_en = tectonic_unit.get("name_en", "") if tectonic_unit else ""
    strategy = _QUERY_STRATEGIES.get(mineral)

    if strategy:
        cn_terms = strategy["cn_terms"]
        en_terms = strategy["en_terms"]
    else:
        cn_terms = [f"{mineral}矿床", f"{mineral}成矿"]
        en_terms = [f"{mineral_en} deposit", f"{mineral_en} mineralization"]

    # 英文优先（OpenAlex + Semantic Scholar 对英文支持好得多）
    # 第1组：构造单元 + 主术语
    if tu_name_en:
        queries.append((f"{tu_name_en} {en_terms[0]}", True))
    else:
        queries.append((f"{en_terms[0]} China", True))

    # 第2组：构造单元中文 + 主术语（OpenAlex 备选）
    if tu_name:
        queries.append((f"{tu_name} {cn_terms[0]}", False))

    # 第3组：备用英文术语
    if len(en_terms) > 1:
        prefix = tu_name_en + " " if tu_name_en else ""
        queries.append((f"{prefix}{en_terms[1]}", True))

    return queries


def _is_relevant(paper: Dict[str, Any], mineral: str, mineral_en: str) -> bool:
    """过滤明显不相关的论文"""
    title = paper.get("title", "") or ""
    abstract = paper.get("abstract", "") or ""
    text = (title + " " + abstract).lower()

    # ── 硬噪音：这些领域的论文绝对排除 ──
    hard_noise = [
        "香根草", "俚曲", "modal particle", "espnet", "pretrained model",
        "语音识别", "声学模型", "asr", "speech recognition",
        "水土保持", "生态修复", "光伏", "新能源",
        "汉语大字典", "义项", "描写", "规范",
    ]
    for kw in hard_noise:
        if kw.lower() in text:
            return False

    # ── 跨矿种过滤：不要返回别的矿种论文 ──
    other_minerals_cn = ["锂矿", "稀土", "金矿", "银矿", "铜矿", "铁矿", "钨矿", "锡矿",
                         "铅锌矿", "钼矿", "镍矿", "钴矿", "铬矿", "锰矿", "铝土矿"]
    other_minerals_en = ["lithium", "rare earth", "gold", "silver", "tungsten",
                         "tin", "molybdenum", "nickel", "cobalt", "chromium",
                         "manganese", "bauxite", "lead zinc", "iron ore"]
    # 保留当前矿种的词
    keep = [mineral, mineral_en]
    if mineral in ("石油", "天然气", "油气"):
        keep.extend(["石油", "油气", "天然气", "petroleum", "oil", "gas", "hydrocarbon"])
    for om in other_minerals_cn:
        if om not in keep and om in title:
            return False
    for om in other_minerals_en:
        if om not in keep and om.lower() in text:
            return False

    # ── 领域过滤：标题或摘要必须包含地质/矿产相关词 ──
    geo_keywords = [
        "地质", "成矿", "矿床", "盆地", "构造", "断裂", "岩浆",
        "地球化学", "地球物理", "地层", "矿化", "蚀变", "勘探",
        "geology", "deposit", "mineral", "basin", "tectonic", "fault",
        "ore", "magmatic", "hydrothermal", "geochemistry", "geophysics",
        "metallogen", "exploration",
    ]

    # 矿种特定词
    if mineral in ("石油", "天然气", "油气"):
        geo_keywords.extend([
            "石油", "油气", "烃源岩", "储层", "成藏", "生油",
            "petroleum", "hydrocarbon", "reservoir", "source rock",
            "oil", "gas",
        ])
    else:
        geo_keywords.extend([
            mineral.lower(), mineral_en.lower(), "矿", "ore",
        ])

    has_geo = any(kw.lower() in text for kw in geo_keywords)
    return has_geo


def search_papers(
    tectonic_unit: Optional[Dict],
    mineral: str,
    max_results: int = 15,
) -> List[Dict[str, Any]]:
    """
    综合查询该区域 + 矿种的学术论文

    策略：英文查询优先（检索质量好），中文查询作补充，
    结果经相关性过滤 + 去重 + 按引用数排序
    """
    mineral_en = MINERAL_EN.get(mineral, mineral)
    queries = _build_queries(tectonic_unit, mineral, mineral_en)

    all_results = []
    for q, is_en in queries:
        logger.info("检索: \"%s\"", q)
        if is_en:
            # 英文用 Semantic Scholar（质量更好）
            papers = search_papers_semantic_scholar(q, max_results)
            if not papers:
                # S2 限流则回退到 OpenAlex
                papers = search_papers_openalex(q, max_results)
        else:
            papers = search_papers_openalex(q, max_results)
        all_results.extend(papers)

        # 已有足够候选则停
        if len(all_results) >= max_results * 2:
            break

    # 合并去重 + 相关性过滤
    seen_titles = set()
    relevant = []
    for p in all_results:
        title_key = p["title"].lower()[:80]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        if _is_relevant(p, mineral, mineral_en):
            relevant.append(p)

    # 按引用数排序
    relevant.sort(key=lambda p: p.get("citation_count", 0) or 0, reverse=True)

    logger.info("过滤后: %d 篇相关论文 (%d 原始, %d 噪音)", len(relevant), len(all_results), len(all_results) - len(relevant))

    return relevant[:max_results]


# ============================================================
# 4. 读取已下载的 EMAG2 / WGM2012 实际值
# ============================================================

def read_raster_values(
    roi: Dict[str, Any],
    magnetic_file: Optional[str] = None,
    gravity_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    读取 ROI 中心点的磁异常和重力异常实际数值

    Returns:
        {
            "center_lon": float,
            "center_lat": float,
            "magnetic_nt": float or None,   # 磁异常 (nT)
            "bouguer_mgal": float or None,  # 布格重力异常 (mGal)
        }
    """
    result = {
        "center_lon": roi["center"]["lon"],
        "center_lat": roi["center"]["lat"],
        "magnetic_nt": None,
        "bouguer_mgal": None,
    }

    try:
        import rasterio

        if magnetic_file and Path(magnetic_file).exists():
            with rasterio.open(magnetic_file) as src:
                row, col = src.index(result["center_lon"], result["center_lat"])
                window = ((row, row + 1), (col, col + 1))
                val = src.read(1, window=window)[0, 0]
                if val != src.nodata:
                    result["magnetic_nt"] = round(float(val), 1)
                else:
                    result["magnetic_nt"] = None

        if gravity_file and Path(gravity_file).exists():
            with rasterio.open(gravity_file) as src:
                row, col = src.index(result["center_lon"], result["center_lat"])
                window = ((row, row + 1), (col, col + 1))
                val = src.read(1, window=window)[0, 0]
                if val != src.nodata:
                    result["bouguer_mgal"] = round(float(val), 1)
                else:
                    result["bouguer_mgal"] = None

    except ImportError:
        pass
    except Exception as e:
        logger.warning("读取栅格数据失败: %s", e)

    return result


# ============================================================
# 5. 主入口
# ============================================================

def fetch_all_live_data(
    roi: Dict[str, Any],
    mineral: str,
    location: Dict[str, Any],
    mineral_info: Dict[str, Any],
    magnetic_file: Optional[str] = None,
    gravity_file: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    实时获取所有可获取的数据

    Returns:
        {
            "papers": [...],          # 学术论文（真实结果）
            "raster_values": {...},   # 磁法/重力中心值
        }
    """
    logger.info("实时查询学术论文...")

    tu = location.get("center_tectonic")
    papers = search_papers(tu, mineral)

    logger.info("读取物探数据...")
    raster_vals = read_raster_values(roi, magnetic_file, gravity_file)

    if raster_vals.get("magnetic_nt") is not None:
        logger.info("ROI 中心磁异常: %s nT", raster_vals['magnetic_nt'])
    if raster_vals.get("bouguer_mgal") is not None:
        logger.info("ROI 中心布格重力: %s mGal", raster_vals['bouguer_mgal'])

    return {
        "papers": papers,
        "raster_values": raster_vals,
    }


def _truncate(text: Optional[str], max_len: int) -> str:
    """截断文本"""
    if not text:
        return ""
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _decode_openalex_abstract(inverted_index) -> str:
    """OpenAlex abstract_inverted_index → 纯文本"""
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    try:
        word_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort()
        return " ".join(w for _, w in word_positions)
    except Exception:
        return ""


if __name__ == "__main__":
    # 测试
    from tectonic_units import find_tectonic_unit
    tu = find_tectonic_unit(117.975, 30.95)
    papers = search_papers(tu, "铜")
    for i, p in enumerate(papers[:5], 1):
        authors = ", ".join(p["authors"][:3])
        print(f"\n{i}. [{p['year']}] {p['title']}")
        print(f"   {authors} | cited: {p['citation_count']}")
        if p.get("abstract"):
            print(f"   Abstract: {p['abstract'][:200]}")
