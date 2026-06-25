"""论文 LLM 提炼 — 用 DeepSeek（OpenAI 兼容接口）对检索到的论文做围绕 ROI 的找矿导向核心提炼。

约束：只依据给定论文的标题与摘要，不臆造摘要之外的事实；不确定处标注。
未配置 DEEPSEEK_API_KEY 时优雅降级（返回 None），报告保留论文清单。
经 http_client 直连，无需额外 SDK。
"""

from typing import Dict, Any, List, Optional

from .logger import get_logger
from .http_client import post as http_post
from config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL, PAPER_SYNTHESIS_MODEL

logger = get_logger("synth")


def synthesize_papers(
    papers: List[Dict[str, Any]],
    mineral: str,
    location: Optional[Dict] = None,
    roi: Optional[Dict] = None,
) -> Optional[str]:
    """围绕 ROI + 构造单元 + 矿种，对论文做核心内容提炼，返回 Markdown；失败/未配置返回 None。"""
    if not papers:
        return None
    if not DEEPSEEK_API_KEY:
        logger.info("未配置 DEEPSEEK_API_KEY，跳过论文 LLM 提炼（保留论文列表）")
        return None

    tu = (location or {}).get("center_tectonic") or {}
    tu_name = tu.get("name", "")
    center = (roi or {}).get("center", {}) or {}
    region = tu_name or (
        f"{center.get('lon')}°E, {center.get('lat')}°N" if center.get("lon") is not None else "目标区域"
    )

    items = []
    for i, p in enumerate(papers[:15], 1):
        ab = (p.get("abstract") or "").strip()
        items.append(
            f"[{i}] ({p.get('year', '?')}) {p.get('title', '')}\n"
            f"摘要: {ab[:700] if ab else '（无摘要）'}"
        )
    papers_block = "\n\n".join(items)

    system = (
        "你是矿产勘查文献分析专家。**只依据用户给出的论文标题与摘要**做提炼，"
        "不得编造摘要之外的事实、数据或矿床名；凡摘要未明确支撑或需推断之处，"
        "用“（待核实）”标注。输出简体中文 Markdown，紧扣目标区域与目标矿种的找矿。"
    )
    user = (
        f"目标：围绕 ROI 区域（**{region}**）的 **{mineral}** 找矿，"
        f"对下列 {len(items)} 篇论文做核心内容提炼。\n\n"
        "请按以下小节输出，每节 2–5 条要点，引用对应论文编号 [n]：\n"
        "1. 成矿时代与构造背景\n"
        "2. 控矿要素（赋矿围岩、构造、蚀变、成矿流体）\n"
        "3. 指示元素组合与异常查证标志\n"
        "4. 已报道的典型矿床 / 矿化点\n"
        "5. 对本 ROI 找矿的针对性建议\n\n"
        "要求：紧扣本区域与该矿种；与本区关联弱的论文可略；"
        "摘要缺失或不确定的内容标注“（待核实）”。\n\n"
        f"论文清单：\n{papers_block}\n"
    )

    try:
        resp = http_post(
            DEEPSEEK_API_URL,
            json={
                "model": PAPER_SYNTHESIS_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 2500,
                "temperature": 0.3,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=120,
        )
        if resp.status_code != 200:
            logger.warning("DeepSeek API %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if text:
            logger.info("论文 LLM 提炼完成（%d 篇 → %d 字，模型 %s）",
                        len(items), len(text), PAPER_SYNTHESIS_MODEL)
            return text
        return None
    except Exception as e:
        logger.warning("论文 LLM 提炼失败（保留论文列表）: %s", e)
        return None
