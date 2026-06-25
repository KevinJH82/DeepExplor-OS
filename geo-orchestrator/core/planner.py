"""编排决策引擎 — LLM Agent 生成任务编排单。

组合系统知识 + ROI 上下文 + 矿种推荐，调用 LLM 输出完整编排单 JSON。
"""

from __future__ import annotations

import json
import os
import re
import traceback
from typing import Callable, Optional

from core.roi_analyzer import ROIContext, ROIAnalyzer
from core.mineral_engine import MineralEngine, MineralRecommendation
from core.system_knowledge import system_knowledge_for_prompt, EXECUTION_PHASES


# ── 编排单 JSON Schema ─────────────────────────────────────────

PLAN_SYSTEM_PROMPT = """你是 DeepExplor 找矿系统的智能编排引擎。用户提交了 ROI 和目标矿种，你需要输出一份可执行的任务编排单。

【系统全景知识】
{system_knowledge}

【你的职责】
1. 根据 ROI 特征（位置、面积、地形、气候、植被）和矿种特性，确定完整的执行计划
2. 明确每一步需要调用哪个服务、传什么参数、产出什么、下游谁消费
3. 标注哪些步骤可跳过（已有历史产物）、哪些是可选的
4. 给出每步的决策理由，让地质专家可审查

【输出格式】
严格输出以下 JSON（不要加 ```json``` 包裹）：
{{
  "roi": {{
    "aoi_name": "区域名称",
    "bbox": [min_lon, min_lat, max_lon, max_lat],
    "area_km2": 面积,
    "elevation_range": [min_m, max_m],
    "climate_zone": "气候带",
    "vegetation_cover": "植被覆盖",
    "tectonic_setting": "构造背景"
  }},
  "mineral": "矿种",
  "family": "成因族",
  "execution_plan": {{
    "phases": [
      {{
        "phase": 1,
        "name": "阶段名称",
        "parallel_groups": [
          {{
            "service": "服务名",
            "required": true,
            "skip": false,
            "skip_reason": "",
            "reason": "为什么需要这个服务",
            "tasks": [
              {{
                "参数名": "参数值",
                "reason": "为什么这样设置"
              }}
            ]
          }}
        ]
      }}
    ]
  }},
  "decision_rationale": {{
    "family_determination": "矿种 → 族的判断依据",
    "sensor_priority": "传感器优先级排序及理由",
    "insar_decision": "InSAR 决策及理由",
    "geophys_decision": "物探决策及理由",
    "geochem_decision": "化探决策及理由",
    "skipped_services": ["跳过的服务及原因"],
    "roi_specific_notes": "ROI 特定的注意事项"
  }}
}}

【约束】
- phase 编号从 1 开始，按执行顺序排列
- 每个服务只能在编排单中出现一次
- skip=true 的服务也要列出（附 skip_reason）
- 所有决策必须有 reason
- 传感器名用小写（sentinel2/landsat/aster/sentinel1/dem）
"""


class Planner:
    """LLM Agent：持有系统知识 + ROI + 矿种推荐，输出编排单。"""

    def plan(self, kml_path: str, mineral: str, roi_ctx: ROIContext,
             recommendation: MineralRecommendation,
             log_callback: Optional[Callable] = None) -> dict:
        """生成编排单。返回编排单 dict（符合 PLAN JSON schema）。"""

        # 1. 先尝试 LLM 生成
        try:
            plan = self._plan_via_llm(mineral, roi_ctx, recommendation, log_callback)
            if plan and self._validate_plan(plan):
                if log_callback:
                    log_callback("LLM 编排单生成并校验通过")
                plan.setdefault("meta", {})["planner_mode"] = "llm"
                return plan
            elif log_callback:
                log_callback("LLM 编排单校验失败，使用确定性后备方案")
        except Exception as e:
            if log_callback:
                log_callback(f"LLM 调用异常：{e}，使用确定性后备方案")

        # 2. 确定性后备：不依赖 LLM，直接拼编排单
        result = self._plan_deterministic(mineral, roi_ctx, recommendation, log_callback)
        result.setdefault("meta", {})["planner_mode"] = "deterministic"
        return result

    def _plan_via_llm(self, mineral, roi_ctx, recommendation, log_callback) -> Optional[dict]:
        """通过 LLM 生成编排单。"""
        try:
            from openai import OpenAI
            from config.config import Config

            client = OpenAI(
                api_key=os.environ.get('DEEPSEEK_API_KEY', os.environ.get('OPENAI_API_KEY', '')),
                base_url=os.environ.get('ORCHESTRATOR_LLM_BASE_URL', Config.LLM_BASE_URL),
            )

            system_prompt = PLAN_SYSTEM_PROMPT.format(
                system_knowledge=system_knowledge_for_prompt()
            )

            user_prompt = self._build_user_prompt(mineral, roi_ctx, recommendation)

            if log_callback:
                log_callback("调用 LLM 生成编排单...")

            response = client.chat.completions.create(
                model=os.environ.get('ORCHESTRATOR_LLM_MODEL', Config.LLM_MODEL),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=Config.LLM_MAX_TOKENS,
                timeout=Config.LLM_TIMEOUT,
            )

            raw = response.choices[0].message.content.strip()
            # 清洗 LLM 输出
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

            plan = json.loads(raw)
            return plan

        except ImportError:
            return None
        except Exception as e:
            if log_callback:
                log_callback(f"LLM 调用失败：{e}")
            return None

    def _build_user_prompt(self, mineral, roi_ctx, recommendation) -> str:
        roi_dict = ROIAnalyzer.to_dict(roi_ctx)
        rec_dict = MineralEngine.to_dict(recommendation)

        return f"""请为以下 ROI + 矿种生成任务编排单。

## ROI 上下文
{json.dumps(roi_dict, ensure_ascii=False, indent=2)}

## 矿种推荐
{json.dumps(rec_dict, ensure_ascii=False, indent=2)}

请严格按照 JSON 格式输出编排单。"""

    def _plan_deterministic(self, mineral, roi_ctx, recommendation, log_callback) -> dict:
        """确定性后备方案：不依赖 LLM，直接拼编排单。"""
        if log_callback:
            log_callback("使用确定性编排方案")

        rec_dict = MineralEngine.to_dict(recommendation)
        roi_dict = ROIAnalyzer.to_dict(roi_ctx)

        phases = []

        # ── 阶段 1: 数据获取 ──
        phase1_groups = []

        # geo-downloader
        downloader_tasks = []
        existing_dl = roi_ctx.existing_products.get('geo_downloader', False)
        for s in recommendation.sensors:
            task = {
                "sensor": s.sensor,
                "seasons": s.seasons,
                "required": s.required,
                "reason": s.reason,
            }
            downloader_tasks.append(task)

        phase1_groups.append({
            "service": "geo-downloader",
            "required": True,
            "skip": existing_dl,
            "skip_reason": "已有下载产物" if existing_dl else "",
            "reason": f"下载 {len(recommendation.sensors)} 种传感器数据（{mineral} → {recommendation.family}）",
            "tasks": downloader_tasks,
        })

        # data-colle
        existing_dc = roi_ctx.existing_products.get('data_colle', False)
        phase1_groups.append({
            "service": "data-colle",
            "required": True,
            "skip": existing_dc,
            "skip_reason": "已有资料查取产物" if existing_dc else "",
            "reason": "在线查取地质/物探/化探资料 + EMAG2/WGM2012 + geochem_thresholds",
            "tasks": [{"sections": ["geology", "geophysics", "geochemistry"]}],
        })

        # geo-insar —— 独立数据获取子系统（经 ASF/HyP3 自取 SAR），归入阶段一统一获取原始数据。
        # 提交后不阻塞，建模前由 executor 做有界等待（见 executor）。
        insar_rec = next((s for s in recommendation.services if s.service == "geo-insar"), None)
        if insar_rec is not None:
            existing_is = roi_ctx.existing_products.get('geo_insar', False)
            phase1_groups.append({
                "service": "geo-insar",
                "required": insar_rec.required,
                "skip": existing_is,
                "skip_reason": "已有形变产物" if existing_is else (insar_rec.skip_reason or ""),
                "reason": insar_rec.reason + "（SAR 经 HyP3 自取，提交后异步处理）",
                "tasks": [dict(insar_rec.params)] if insar_rec.params else [],
            })

        phases.append({"phase": 1, "name": "数据获取", "parallel_groups": phase1_groups})

        # ── 阶段 2: 并行处理 ──
        phase2_groups = []

        for svc_rec in recommendation.services:
            if svc_rec.service in ("geo-downloader", "data-colle", "geo-insar",
                                    "geo-model3d", "geo-drill", "geo-reporter"):
                continue  # downloader/data-colle/insar 在阶段1；model3d/drill/reporter 在后续阶段

            existing = roi_ctx.existing_products.get(svc_rec.service.replace("-", "_"), False)
            params = dict(svc_rec.params) if svc_rec.params else {}
            if svc_rec.service == "geo-geochem" and recommendation.key_elements:
                params["elements"] = recommendation.key_elements[:8]

            phase2_groups.append({
                "service": svc_rec.service,
                "required": svc_rec.required,
                "skip": existing,
                "skip_reason": "已有历史产物" if existing else (svc_rec.skip_reason or ""),
                "reason": svc_rec.reason,
                "tasks": [params] if params else [],
            })

        phases.append({"phase": 2, "name": "并行处理", "parallel_groups": phase2_groups})

        # ── 阶段 3: 七慢变量机制综合 ──
        phases.append({
            "phase": 3, "name": "七慢变量机制综合", "parallel_groups": [{
                "service": "geo-7slow",
                "required": False,
                "skip": False,
                "skip_reason": "",
                "reason": "把蚀变/构造/InSAR/物化探证据转译为七慢变量,输出Δ判别式与机制靶区",
                "tasks": [{
                    "mineral": mineral,
                    "family": recommendation.family,
                    "project": roi_ctx.aoi_name,
                }],
            }],
        })

        # ── 阶段 4: 三维建模 ──
        existing_m3d = roi_ctx.existing_products.get('geo_model3d', False)
        phases.append({
            "phase": 4, "name": "三维建模", "parallel_groups": [{
                "service": "geo-model3d",
                "required": True,
                "skip": existing_m3d,
                "skip_reason": "已有三维建模产物" if existing_m3d else "",
                "reason": f"融合所有证据产出三维有利度体（{recommendation.family}，"
                          f"深度带 {recommendation.depth_km_band}km）",
                "tasks": [{"mineral": mineral, "family": recommendation.family}],
            }],
        })

        # ── 阶段 5: 钻探布孔（可选）──
        phases.append({
            "phase": 5, "name": "钻探布孔", "parallel_groups": [{
                "service": "geo-drill",
                "required": False,
                "skip": False,
                "skip_reason": "",
                "reason": "AI 辅助布孔 + 见矿判定 + 闭环回灌（可选）",
                "tasks": [{"mineral": mineral, "top_n": 20, "min_sep_m": 200}],
            }],
        })

        # ── 阶段 6: 综合报告 ──
        phases.append({
            "phase": 6, "name": "综合报告", "parallel_groups": [{
                "service": "geo-reporter",
                "required": True,
                "skip": False,
                "skip_reason": "",
                "reason": "汇总所有子系统产物，生成 GB/T 9704 标准报告",
                "tasks": [{"mineral": mineral}],
            }],
        })

        return {
            "roi": roi_dict,
            "mineral": mineral,
            "family": recommendation.family,
            "execution_plan": {"phases": phases},
            "decision_rationale": recommendation.rationale,
        }

    @staticmethod
    def _validate_plan(plan: dict) -> bool:
        """校验编排单基本完整性。"""
        if not isinstance(plan, dict):
            return False
        if 'execution_plan' not in plan:
            return False
        phases = plan['execution_plan'].get('phases', [])
        if not phases:
            return False
        # 检查至少有阶段 1（数据获取）
        services_seen = set()
        for ph in phases:
            for g in ph.get('parallel_groups', []):
                svc = g.get('service', '')
                if svc in services_seen:
                    continue  # 重复不致命
                services_seen.add(svc)
        # 至少应有 geo-downloader 或 geo-model3d
        return bool(services_seen)
