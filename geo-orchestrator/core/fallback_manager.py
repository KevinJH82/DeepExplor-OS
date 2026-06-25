"""服务失败的智能降级策略（P3）。

P2 的执行器对失败只做"记录 + 隔离不中断"。P3 引入按服务的降级策略：
  - abort     ：核心证据失败，中止后续（下载/蚀变/数据获取）
  - skip      ：非必选项失败，跳过并通知（InSAR/物探/深部探测）
  - continue  ：降级继续（构造弱化 / 化探退为先验）；下游 model3d 会自动按
                broker 实际产物重归一化权重，无需传参

依赖联动：geo-model3d 失败 → geo-drill 自动跳过（drill 消费 model3d 有利度体）。
"""

from __future__ import annotations

from dataclasses import dataclass

ACTION_ABORT = "abort"
ACTION_SKIP = "skip"
ACTION_CONTINUE = "continue"


@dataclass
class FallbackDecision:
    service: str
    action: str           # abort | skip | continue
    impact: str           # 对后续/建模的影响
    notify: str           # 给用户的通知
    cascade_skip: tuple = ()   # 因本次失败需联动跳过的下游服务


class FallbackManager:
    """服务失败 → 降级决策。"""

    STRATEGIES = {
        "geo-downloader": {
            "action": ACTION_ABORT,
            "impact": "无遥感数据，全管线无法继续",
            "notify": "数据下载失败，无法继续。请检查网络或卫星数据凭据配置。",
        },
        "data-colle": {
            "action": ACTION_SKIP,
            "impact": "缺少地质/物探/化探资料与阈值先验；物探/化探将缺少输入",
            "notify": "在线资料查取失败，已跳过。物探/化探与建模将缺少先验约束。",
        },
        "geo-analyser": {
            "action": ACTION_ABORT,
            "impact": "蚀变是核心证据层，缺失则建模不可信",
            "notify": "蚀变分析失败（核心证据），中止后续建模。请检查遥感数据质量。",
        },
        "geo-stru": {
            "action": ACTION_CONTINUE,
            "impact": "构造证据缺失；model3d 自动按可用证据层重归一化、不确定性提高",
            "notify": "构造解译失败，降级继续。三维建模将不含构造证据层。",
        },
        "geo-insar": {
            "action": ACTION_SKIP,
            "impact": "形变证据层缺失；model3d 自动以可用层建模",
            "notify": "InSAR 处理失败/未完成（常因相干性不足），已跳过。建模不含形变证据层。",
        },
        "geo-geophys": {
            "action": ACTION_SKIP,
            "impact": "磁/重异常与磁源深度约束缺失；model3d 深度门控放宽",
            "notify": "物探处理失败，已跳过。三维建模缺少磁源深度约束。",
        },
        "geo-geochem": {
            "action": ACTION_CONTINUE,
            "impact": "化探降级为阈值先验（prior_only）",
            "notify": "化探处理失败，降级为阈值先验继续。",
        },
        "geo-exploration": {
            "action": ACTION_SKIP,
            "impact": "无经验深度先验；model3d 退化为地表证据 + 几何衰减",
            "notify": "深部探测失败，已跳过。建模缺少经验深度先验。",
        },
        "geo-model3d": {
            "action": ACTION_SKIP,
            "impact": "无三维有利度体；钻探布孔无法进行",
            "notify": "三维建模失败，已跳过。将联动跳过钻探布孔，报告仅汇总二维证据。",
            "cascade_skip": ("geo-drill",),
        },
        "geo-drill": {
            "action": ACTION_SKIP,
            "impact": "无 AI 布孔方案",
            "notify": "钻探布孔失败，已跳过（可选项）。",
        },
        "geo-reporter": {
            "action": ACTION_CONTINUE,
            "impact": "综合报告缺失或不完整",
            "notify": "报告生成失败。请检查报告服务与各产物可用性。",
        },
    }

    def handle_failure(self, service: str, error: str = "") -> FallbackDecision:
        strat = self.STRATEGIES.get(service, {
            "action": ACTION_SKIP,
            "impact": "未知服务失败，默认跳过",
            "notify": f"{service} 失败，已跳过。",
        })
        notify = strat["notify"]
        if error:
            notify = f"{notify}（原因：{error}）"
        return FallbackDecision(
            service=service,
            action=strat["action"],
            impact=strat["impact"],
            notify=notify,
            cascade_skip=tuple(strat.get("cascade_skip", ())),
        )
