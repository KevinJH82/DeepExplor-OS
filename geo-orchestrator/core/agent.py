"""多轮对话式编排 Agent（P4 · 4.1）。

用户可追问"为什么没推荐 ASTER"、要求"加上 ASTER / 去掉 InSAR / 把 top_n 调小"。
Agent 结合系统知识 + 当前编排单回答；若涉及修改，返回一份**新版编排单提案**
（proposal），由用户确认后才落为新版本（见架构决策：可改但需确认）。

LLM（DeepSeek，复用 planner 的 OpenAI 兼容配置）优先；无 key / 调用失败时降级为
规则化意图识别，覆盖最常见的解释/加减传感器/跳过服务/改参数。
"""

from __future__ import annotations

import copy
import json
import os
import re
from typing import List, Optional

from core.system_knowledge import system_knowledge_for_prompt

# 传感器别名 → 规范名
_SENSOR_ALIASES = {
    "aster": "aster", "sentinel2": "sentinel2", "哨兵2": "sentinel2", "s2": "sentinel2",
    "landsat": "landsat", "陆地卫星": "landsat",
    "sentinel1": "sentinel1", "哨兵1": "sentinel1", "s1": "sentinel1", "雷达": "sentinel1",
    "dem": "dem",
}
# 服务别名（中文/关键词 → 服务名）
_SERVICE_ALIASES = {
    "insar": "geo-insar", "形变": "geo-insar",
    "物探": "geo-geophys", "磁": "geo-geophys", "重力": "geo-geophys",
    "化探": "geo-geochem", "钻探": "geo-drill", "布孔": "geo-drill",
    "构造": "geo-stru", "蚀变": "geo-analyser", "深部": "geo-exploration",
    "报告": "geo-reporter", "三维": "geo-model3d", "建模": "geo-model3d",
}

CHAT_SYSTEM_PROMPT = """你是 DeepExplor 找矿系统的智能编排 Agent，正在与地质专家多轮对话。
你掌握以下系统知识：
{system_knowledge}

当前这个 ROI 的编排单（JSON）：
{current_plan}

【你的任务】
- 回答用户对编排单的追问（为什么选/不选某传感器、某服务的作用、决策理由等）。
- 若用户要求修改（加/减传感器、跳过/启用某服务、调整参数），你需要给出**修改后的完整编排单**，
  但不要自行执行——交由用户确认。

【严格输出 JSON（不要加 ```）】
{{
  "reply": "给用户的自然语言回答",
  "action": "explain" | "modify",
  "proposed_plan": 修改后的完整编排单对象（action=modify 时必填，结构与当前编排单一致；explain 时为 null）
}}
"""


class OrchestratorAgent:
    """对话式编排 Agent。"""

    def chat(self, plan: dict, history: List[dict], message: str) -> dict:
        """返回 {reply, proposal}（proposal 为新版编排单或 None）。"""
        # 1. LLM 优先
        try:
            res = self._chat_via_llm(plan, history, message)
            if res is not None:
                return res
        except Exception:
            pass
        # 2. 规则化降级
        return self._chat_rule_based(plan, message)

    # ── LLM ───────────────────────────────────────────────────
    def _chat_via_llm(self, plan, history, message) -> Optional[dict]:
        try:
            from openai import OpenAI
            from config.config import Config
        except ImportError:
            return None
        api_key = os.environ.get("DEEPSEEK_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        if not api_key:
            return None

        client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("ORCHESTRATOR_LLM_BASE_URL", Config.LLM_BASE_URL),
        )
        system = CHAT_SYSTEM_PROMPT.format(
            system_knowledge=system_knowledge_for_prompt(),
            current_plan=json.dumps(plan, ensure_ascii=False),
        )
        messages = [{"role": "system", "content": system}]
        for h in (history or [])[-8:]:
            role = "assistant" if h.get("role") == "assistant" else "user"
            messages.append({"role": role, "content": h.get("content", "")})
        messages.append({"role": "user", "content": message})

        resp = client.chat.completions.create(
            model=os.environ.get("ORCHESTRATOR_LLM_MODEL", Config.LLM_MODEL),
            messages=messages, temperature=0.2, max_tokens=Config.LLM_MAX_TOKENS,
            timeout=Config.LLM_TIMEOUT,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        proposal = data.get("proposed_plan") if data.get("action") == "modify" else None
        if proposal is not None:
            proposal.setdefault("meta", {})["chat_mode"] = "llm"
        return {"reply": data.get("reply", ""), "proposal": proposal}

    # ── 规则化降级 ────────────────────────────────────────────
    def _chat_rule_based(self, plan: dict, message: str) -> dict:
        msg = message.lower()
        sensor = self._match(msg, _SENSOR_ALIASES)
        service = self._match(msg, _SERVICE_ALIASES)

        # 意图：为什么没/为什么不
        if any(k in message for k in ("为什么", "为啥", "怎么没", "why")):
            return {"reply": self._explain(plan, sensor, service), "proposal": None}

        # 意图：增加传感器
        if any(k in message for k in ("加上", "添加", "增加", "补充", "加")) and sensor:
            return self._propose_add_sensor(plan, sensor)

        # 意图：去掉/跳过 服务或传感器
        if any(k in message for k in ("去掉", "移除", "删除", "不要", "跳过")):
            if service:
                return self._propose_skip_service(plan, service)
            if sensor:
                return self._propose_remove_sensor(plan, sensor)

        # 意图：调参 top_n（允许中间夹"调到/设为"等中文）
        m = re.search(r"top[_ ]?n[^0-9]{0,8}(\d+)", msg)
        if m:
            return self._propose_set_topn(plan, int(m.group(1)))

        # 兜底解释
        return {"reply": self._explain(plan, sensor, service), "proposal": None}

    # ── 解释 ──────────────────────────────────────────────────
    def _explain(self, plan, sensor, service) -> str:
        dr = plan.get("decision_rationale") or {}
        if sensor:
            present = sensor in self._plan_sensors(plan)
            base = f"传感器 {sensor} {'已在' if present else '未列入'}当前编排单。"
            extra = dr.get("sensor_priority") or ""
            if not present and sensor == "aster":
                extra += " ASTER 为光学高光谱增强项，受云覆盖影响；有 Sentinel-2 时通常可降级。如需可让我加上。"
            if not present and sensor == "sentinel1":
                extra += f" InSAR 决策：{dr.get('insar_decision', '视活动构造发育情况而定')}。"
            return base + ("决策依据：" + extra if extra else "")
        if service:
            for ph in (plan.get("execution_plan") or {}).get("phases", []):
                for g in ph.get("parallel_groups", []):
                    if g.get("service") == service:
                        s = "（已标记跳过）" if g.get("skip") else ""
                        return f"{service}{s}：{g.get('reason', '')}"
            return f"{service} 不在当前编排单中。"
        # 总览
        return ("当前编排单决策概要：" +
                "；".join(f"{k}：{v}" for k, v in dr.items()
                          if isinstance(v, str))[:600] or "请就具体传感器/服务提问。")

    # ── 修改提案 ──────────────────────────────────────────────
    def _propose_add_sensor(self, plan, sensor) -> dict:
        if sensor in self._plan_sensors(plan):
            return {"reply": f"{sensor} 已在编排单中，无需添加。", "proposal": None}
        new = copy.deepcopy(plan)
        for ph in (new.get("execution_plan") or {}).get("phases", []):
            for g in ph.get("parallel_groups", []):
                if g.get("service") == "geo-downloader":
                    g.setdefault("tasks", []).append({
                        "sensor": sensor, "seasons": ["summer"], "required": False,
                        "reason": "用户在对话中要求补充该传感器",
                    })
                    g["skip"] = False
        self._stamp(new, f"对话新增传感器 {sensor}")
        return {"reply": f"已生成新版编排单：在 geo-downloader 中补充 {sensor}（夏季）。请确认后生成新版本。",
                "proposal": new}

    def _propose_remove_sensor(self, plan, sensor) -> dict:
        new = copy.deepcopy(plan)
        removed = False
        for ph in (new.get("execution_plan") or {}).get("phases", []):
            for g in ph.get("parallel_groups", []):
                if g.get("service") == "geo-downloader":
                    before = len(g.get("tasks", []))
                    g["tasks"] = [t for t in g.get("tasks", []) if t.get("sensor") != sensor]
                    removed = removed or len(g["tasks"]) < before
        if not removed:
            return {"reply": f"{sensor} 不在下载任务中，无需移除。", "proposal": None}
        self._stamp(new, f"对话移除传感器 {sensor}")
        return {"reply": f"已生成新版编排单：移除传感器 {sensor}。请确认后生成新版本。", "proposal": new}

    def _propose_skip_service(self, plan, service) -> dict:
        new = copy.deepcopy(plan)
        found = False
        for ph in (new.get("execution_plan") or {}).get("phases", []):
            for g in ph.get("parallel_groups", []):
                if g.get("service") == service:
                    g["skip"] = True
                    g["skip_reason"] = "用户在对话中要求跳过"
                    found = True
        if not found:
            return {"reply": f"{service} 不在编排单中。", "proposal": None}
        self._stamp(new, f"对话跳过服务 {service}")
        return {"reply": f"已生成新版编排单：跳过 {service}。请确认后生成新版本。", "proposal": new}

    def _propose_set_topn(self, plan, top_n) -> dict:
        new = copy.deepcopy(plan)
        changed = False
        for ph in (new.get("execution_plan") or {}).get("phases", []):
            for g in ph.get("parallel_groups", []):
                if g.get("service") in ("geo-drill", "geo-model3d"):
                    for t in g.get("tasks", []):
                        if "top_n" in t:
                            t["top_n"] = top_n
                            changed = True
        if not changed:
            return {"reply": "未找到可调整 top_n 的服务。", "proposal": None}
        self._stamp(new, f"对话调整 top_n={top_n}")
        return {"reply": f"已生成新版编排单：top_n={top_n}。请确认后生成新版本。", "proposal": new}

    # ── helpers ───────────────────────────────────────────────
    @staticmethod
    def _match(msg, alias_map):
        for k, v in alias_map.items():
            if k in msg:
                return v
        return None

    @staticmethod
    def _plan_sensors(plan):
        out = []
        for ph in (plan.get("execution_plan") or {}).get("phases", []):
            for g in ph.get("parallel_groups", []):
                if g.get("service") == "geo-downloader":
                    out += [t.get("sensor") for t in g.get("tasks", []) if t.get("sensor")]
        return out

    @staticmethod
    def _stamp(plan, note):
        meta = plan.setdefault("meta", {})
        meta["chat_mode"] = meta.get("chat_mode", "rule")
        meta.setdefault("chat_edits", []).append(note)
