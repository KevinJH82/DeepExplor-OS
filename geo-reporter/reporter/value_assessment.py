"""
资源潜力与价值评估(Phase C)——参数化定量评估。
平台派生靶区/进尺,经济参数由用户**上传参数表**(CSV/JSON)提供,据此算三情景
(保守/基准/乐观)的资源增量 koz、价值、ROI 与发现成本。缺参数则不出定量(由调用方降级为定性)。

参数表(CSV:两列「参数,值」;或 JSON 同名键)字段:
  resource_kt          现有资源-矿石量(千吨 kt)        [与 resource_koz 二选一]
  resource_grade_gpt   平均品位(g/t)                    [与 resource_kt 搭配]
  resource_koz         现有资源-金属量(koz)             [可直接给,优先]
  metal_price          金属价格(每盎司,按 currency)
  drill_rate_per_m     钻探综合单价(每米,按 currency)
  explore_budget       勘探预算(总额,按 currency;留空则按 进尺×单价 估)
  currency             货币(默认 USD)
  inc_conservative/inc_base/inc_optimistic   三情景资源增量比例(默认 0.15/0.20/0.25)
"""
import csv
import json
from pathlib import Path
from typing import Optional

_OZ_PER_GRAM = 1.0 / 31.1035
_DEFAULT_INC = {"保守": 0.15, "基准": 0.20, "乐观": 0.25}


def _num(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(",", "").replace("，", "").strip())
    except (TypeError, ValueError):
        return None


def parse_econ_params(path: str) -> Optional[dict]:
    """解析上传的经济参数表(CSV 两列 或 JSON)。失败/空返回 None。"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except Exception:
        return None
    data = {}
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(raw) or {}
        except Exception:
            return None
    else:  # csv:每行「键,值」
        for row in csv.reader(raw.splitlines()):
            if len(row) >= 2 and str(row[0]).strip():
                data[str(row[0]).strip()] = row[1]
    return data or None


def compute_value_assessment(econ: dict, targets=None, total_drill_m: float = 0.0) -> Optional[dict]:
    """据经济参数 + 靶区/进尺算三情景定量评估。关键参数不足返回 None。"""
    if not econ:
        return None
    currency = str(econ.get("currency") or "USD").strip()
    price = _num(econ.get("metal_price"))
    # 现有资源金属量(koz):优先直接给,否则按 kt×品位 理论换算(未扣回收率)
    existing_koz = _num(econ.get("resource_koz"))
    if existing_koz is None:
        kt, gpt = _num(econ.get("resource_kt")), _num(econ.get("resource_grade_gpt"))
        if kt is not None and gpt is not None:
            existing_koz = (kt * 1000.0 * gpt) * _OZ_PER_GRAM / 1000.0
    if existing_koz is None or price is None:
        return None  # 关键参数缺失 → 不出定量
    # 预算:优先总额;否则 进尺×单价
    budget = _num(econ.get("explore_budget"))
    rate = _num(econ.get("drill_rate_per_m"))
    if budget is None and rate is not None and total_drill_m:
        budget = rate * float(total_drill_m)
    incs = {
        "保守": _num(econ.get("inc_conservative")) or _DEFAULT_INC["保守"],
        "基准": _num(econ.get("inc_base")) or _DEFAULT_INC["基准"],
        "乐观": _num(econ.get("inc_optimistic")) or _DEFAULT_INC["乐观"],
    }
    scenarios = []
    for name, inc in incs.items():
        new_koz = existing_koz * inc
        total_koz = existing_koz + new_koz
        new_value = new_koz * 1000.0 * price          # 新增金属盎司 × 单价
        roi_pct = (new_value / budget * 100.0) if budget else None
        disc_cost = (budget / (new_koz * 1000.0)) if (budget and new_koz) else None
        scenarios.append({
            "name": name, "increment_pct": inc * 100.0,
            "new_koz": new_koz, "total_koz": total_koz,
            "new_value": new_value, "roi_pct": roi_pct, "discovery_cost_per_oz": disc_cost,
        })
    return {
        "currency": currency, "metal_price": price,
        "existing_koz": existing_koz, "budget": budget,
        "total_drill_m": total_drill_m, "n_targets": len(targets or []),
        "scenarios": scenarios,
    }


# 供下载的参数表模板(CSV 文本)
ECON_PARAMS_TEMPLATE_CSV = (
    "参数,值\n"
    "resource_koz,2507\n"
    "resource_kt,\n"
    "resource_grade_gpt,\n"
    "metal_price,2400\n"
    "drill_rate_per_m,210\n"
    "explore_budget,\n"
    "currency,USD\n"
    "inc_conservative,0.15\n"
    "inc_base,0.20\n"
    "inc_optimistic,0.25\n"
)
