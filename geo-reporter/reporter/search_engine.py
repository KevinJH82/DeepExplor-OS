"""
Search Engine Module — 双阶段架构
阶段 1：直连权威 API（P2）+ Tavily Search API 并发采集原始文本
         命中缓存（P1）则跳过搜索
阶段 2：每类别单次 claude -p subprocess 提取结构化结果
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from jinja2 import Environment, FileSystemLoader

from .categories import SearchResult, SearchCategory, DataPoint, Figure, get_all_categories
from .geocoder import LocationContext
from .cache import CacheLayer
from .data_sources import collect_subsystem_figures
from .consumption import consume_chapter, CHAPTER_CONTRACT


class SearchEngineError(Exception):
    pass


def _level_label(levels: set) -> str:
    """把证据来源层级集合规整为展示字符串，按可信度优先级排序。"""
    order = ["子系统本地实证", "直连API", "网络检索"]
    picked = [x for x in order if x in levels]
    return "+".join(picked)


def _parse_llm_json(output: str) -> Optional[dict]:
    """
    从 claude 输出中稳健提取 JSON 对象，解决 LLM 偶发的格式问题：
      1) 优先 ```json``` 代码块；否则取最外层 { ... }（容忍前后说明文字）
      2) 清洗非法控制字符（保留 \\n \\t \\r）
      3) json.loads 直解；失败再用 json_repair 兜底（修复尾逗号/未转义引号/截断等）
    返回 dict；彻底失败返回 None。
    """
    if not output:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", output, re.DOTALL)
    if m:
        candidate = m.group(1)
    else:
        start = output.find("{")
        end = output.rfind("}")
        candidate = output[start:end + 1] if (start != -1 and end > start) else output
    candidate = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", candidate)
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    try:
        from json_repair import repair_json
        repaired = repair_json(candidate, return_objects=True)
        if isinstance(repaired, dict) and repaired:
            print("[Claude subprocess] JSON 自动修复成功")
            return repaired
    except Exception:
        pass
    return None


def _build_queries(category: SearchCategory, location: LocationContext, mineral_type: str = "") -> List[str]:
    """
    为每个类别构建 2 条定向查询：
    - 查询1：综合背景（含国家+省份+类别英文关键词）
    - 查询2：核心数据项（具体数值型子主题）
    """
    loc = location.location_str  # 如 "东戈壁省 赛罕都兰苏木"
    country = location.country   # 如 "Mongolia"

    # 类别 → 英文关键词映射，提高国际搜索命中率
    en_keywords = {
        "climate":       "climate temperature precipitation annual",
        "geography":     "topography elevation terrain DEM",
        "infrastructure":"road railway infrastructure GDP",
        "hydrology":     "hydrology river runoff groundwater",
        "geology":       "geology stratigraphy mineral deposit",
        "geophysics":    "geophysics gravity magnetic anomaly seismic",
        "geochemistry":  "geochemistry stream sediment soil anomaly element background",
        "remote_sensing":"remote sensing alteration mineral mapping NDVI satellite",
        "mining_rights": "mining license exploration permit mineral rights",
    }
    en_kw = en_keywords.get(category.id, category.name)

    # 矿种英文关键词
    mineral_kw = f"{mineral_type} exploration deposit" if mineral_type else ""

    # 查询1：综合背景
    q1 = f"{country} {loc} {en_kw} {mineral_kw} data statistics".strip()
    # 查询2：聚焦具体数值（取子主题中最数据密集的2条）
    data_topics = " ".join(category.sub_topics[2:4])
    q2 = f"{country} {loc} {data_topics} {mineral_type}".strip()

    return [q1, q2]


class SearchEngine:
    """三层数据采集：直连 API → 缓存 → Tavily 搜索"""

    def __init__(self, templates_dir: str, tavily_api_key: str,
                 tavily_max_results: int = 5,
                 tavily_search_depth: str = "advanced",
                 cache_db: str = "./cache/geo_cache.db"):
        self.templates_dir = Path(templates_dir)
        self.tavily_api_key = tavily_api_key
        self.tavily_max_results = tavily_max_results
        self.tavily_search_depth = tavily_search_depth
        self.cache = CacheLayer(cache_db)

        self.jinja_env = Environment(loader=FileSystemLoader(str(self.templates_dir)))

    # ------------------------------------------------------------------
    # 阶段 1：Tavily 并发搜索
    # ------------------------------------------------------------------

    def _fetch_one_category(self, category: SearchCategory, location: LocationContext, mineral_type: str = ""):
        """
        三层采集：
        1. 直连权威 API（climate/geography/geology/hydrology）
        2. SQLite 缓存（命中则跳过 Tavily）
        3. Tavily 搜索（2条定向查询）
        返回 (texts, levels)：texts 为合并后的原始文本；levels 为证据来源层级集合
        （"子系统本地实证"/"直连API"/"网络检索"），供报告标注与可信度研判使用。
        """
        texts: List[str] = []
        levels: set = set()

        # 层1：声明式消费契约（直连 API + 子系统本地实证），每段文本显式携带来源层级
        if category.id in CHAPTER_CONTRACT:
            consumed = consume_chapter(
                category.id,
                location.centroid_lat, location.centroid_lon,
                location.min_lon, location.min_lat,
                location.max_lon, location.max_lat
            )
            direct = consumed["texts"]  # [(text, level), ...]
            if direct:
                print(f"[Consume] {category.id} 获取 {len(direct)} 条契约数据")
                for t, level in direct:
                    texts.append(t)
                    levels.add(level)  # 层级由 provider 显式声明，不再字符串嗅探

        # 层2：缓存（P1）
        cached = self.cache.get(
            location.country_code,
            location.centroid_lat, location.centroid_lon,
            category.id
        )
        if cached is not None:
            print(f"[Cache] {category.id} 命中缓存（{len(cached)} 条）")
            texts.extend(cached)
            if cached:
                levels.add("网络检索")  # 缓存内容来源于历史 Tavily 检索
            return texts, levels  # 有缓存则跳过 Tavily

        # 层3：Tavily 搜索
        try:
            from tavily import TavilyClient
        except ImportError:
            raise SearchEngineError("请先安装 tavily-python：pip install tavily-python")

        client = TavilyClient(api_key=self.tavily_api_key)
        queries = _build_queries(category, location, mineral_type)
        tavily_texts: List[str] = []
        import time
        for q in queries:
            print(f"[Tavily] {category.id} → {q[:90]}...")
            success = False
            for attempt in range(3):
                try:
                    resp = client.search(
                        query=q,
                        max_results=self.tavily_max_results,
                        search_depth=self.tavily_search_depth,
                        max_retries=0  # 由外层重试控制
                    )
                    tavily_texts.extend(
                        r.get("content", "") for r in resp.get("results", []) if r.get("content")
                    )
                    success = True
                    break
                except Exception as e:
                    err_msg = str(e)
                    print(f"[Tavily] {category.id} 尝试 {attempt+1}/3 失败：{err_msg}")
                    if attempt < 2:
                        time.sleep(1 * (attempt + 1))
                    else:
                        print(f"[Tavily] {category.id} 查询最终失败，跳过该查询")
            if not success:
                print(f"[WARNING] {category.id} 所有 Tavily 查询均失败，该类别数据可能不完整")

        # 写入缓存
        if tavily_texts:
            self.cache.set(
                location.country_code,
                location.centroid_lat, location.centroid_lon,
                category.id, tavily_texts
            )
            levels.add("网络检索")
        texts.extend(tavily_texts)
        return texts, levels

    def fetch_all_raw_data(self, location: LocationContext,
                           categories: Optional[List[str]] = None,
                           mineral_type: str = ""):
        """
        阶段 1：并发搜索所有类别。
        返回 (raw_data, raw_levels)：
          raw_data   = {cat_id: [原始文本, ...]}
          raw_levels = {cat_id: "证据来源层级展示字符串"}
        使用 ThreadPoolExecutor 并发，Tavily 有独立限流，不影响 Claude。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_cats = get_all_categories()
        if categories:
            all_cats = [c for c in all_cats if c.id in categories]

        raw_data: Dict[str, List[str]] = {}
        raw_levels: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_cat = {
                executor.submit(self._fetch_one_category, cat, location, mineral_type): cat
                for cat in all_cats
            }
            for future in as_completed(future_to_cat):
                cat = future_to_cat[future]
                try:
                    texts, levels = future.result()
                    raw_data[cat.id] = texts
                    raw_levels[cat.id] = _level_label(levels)
                    print(f"[Tavily] 完成：{cat.id}（{len(texts)} 条结果）")
                except Exception as e:
                    print(f"[Tavily] 失败：{cat.id} — {e}")
                    raw_data[cat.id] = []
                    raw_levels[cat.id] = ""

        return raw_data, raw_levels

    # ------------------------------------------------------------------
    # 阶段 2：Claude API 批量提取
    # ------------------------------------------------------------------

    def _run_extraction_batch(self, batch_raw_data: Dict[str, List[str]],
                               location: LocationContext,
                               category_names: Dict[str, str],
                               mineral_type: str = "") -> Dict:
        """对一批类别（约4个）运行单次 claude subprocess 提取，返回解析后的 dict。"""
        import subprocess

        category_ids = list(batch_raw_data.keys())
        template = self.jinja_env.get_template("extraction_prompt.j2")
        prompt = template.render(
            location_str=location.location_str,
            country=location.country,
            coords_str=location.coords_str,
            area_name=location.area_name,
            kml_description=location.kml_description,
            raw_data=batch_raw_data,
            category_names=category_names,
            category_ids=category_ids,
            mineral_type=mineral_type,
            enumerate=enumerate
        )

        print(f"[Claude subprocess] 提取批次：{category_ids}")
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", prompt],
            capture_output=True, text=True, timeout=300, encoding="utf-8"
        )
        if result.returncode != 0:
            raise SearchEngineError(f"claude subprocess 失败：{(result.stderr or result.stdout)[:200]}")

        output = result.stdout.strip()
        print(f"[Claude subprocess] 完成，输出长度：{len(output)} 字符")

        parsed = _parse_llm_json(output)
        if parsed is not None:
            return parsed
        raise SearchEngineError(
            f"JSON 解析失败（批次 {category_ids}）\n原始输出（前500字）：{output[:500]}"
        )

    def extract_all_categories(self, raw_data: Dict[str, List[str]],
                                location: LocationContext,
                                raw_levels: Optional[Dict[str, str]] = None) -> Dict[str, SearchResult]:
        """
        阶段 2：分两批调用 claude subprocess（各4个类别），合并结果。
        拆批可避免单次输出过长导致 JSON 截断。
        """
        raw_levels = raw_levels or {}
        all_cats = get_all_categories()
        category_names = {c.id: c.name for c in all_cats}

        # 每批 1 个类别：纯提取无 WebSearch，不触发限流，单次输出小不截断
        cat_ids = list(raw_data.keys())
        batch_size = 1
        batches = [cat_ids[i:i+batch_size] for i in range(0, len(cat_ids), batch_size)]

        data_map = {}
        for batch_ids in batches:
            batch_raw = {cid: raw_data[cid] for cid in batch_ids}
            batch_result = self._run_extraction_batch(batch_raw, location, category_names)
            data_map.update(batch_result)

        # 构建 SearchResult 对象
        results: Dict[str, SearchResult] = {}
        for cat in all_cats:
            if cat.id not in raw_data:
                continue
            cat_data = data_map.get(cat.id, {})
            if not cat_data:
                results[cat.id] = SearchResult(
                    category_id=cat.id,
                    category_name=cat.name,
                    summary="",
                    error="Claude API 未返回该类别数据"
                )
                continue
            results[cat.id] = SearchResult(
                category_id=cat.id,
                category_name=cat.name,
                summary=cat_data.get("summary", ""),
                data_points=[
                    DataPoint(
                        item=dp.get("item", ""),
                        value=dp.get("value", ""),
                        source=dp.get("source", "")
                    )
                    for dp in cat_data.get("data_points", [])
                ],
                key_findings=cat_data.get("key_findings", []),
                data_sources=cat_data.get("data_sources", []),
                error=None,
                evidence_level=raw_levels.get(cat.id, "")
            )

        return results

    # ------------------------------------------------------------------
    # 公共接口（与旧版签名兼容）
    # ------------------------------------------------------------------

    def search_all_categories(self, location: LocationContext,
                               categories: Optional[List[str]] = None) -> Dict[str, SearchResult]:
        """
        完整两阶段流水线：Tavily 搜索 → Claude API 提取。
        签名与旧版 SearchEngine.search_all_categories 兼容。
        """
        raw_data, raw_levels = self.fetch_all_raw_data(location, categories)
        return self.extract_all_categories(raw_data, location, raw_levels)

    def search_all_categories_stream(self, location: LocationContext,
                                      categories: Optional[List[str]] = None,
                                      mineral_type: str = ""):
        """
        流式版本：阶段1并发 Tavily 完成后，阶段2逐类别提取并立即 yield。
        在 Tavily 等待期间 yield keepalive，避免 SSE 超时断连。
        Yields:
          ("keepalive", None, None, None)  — SSE 注释心跳
          (idx, total, cat_id, SearchResult) — 每类别提取结果
        """
        import threading

        # 阶段1：在后台线程跑 Tavily 并发，主线程持续发心跳
        result_box: Dict = {}
        error_box: Dict = {}
        done_event = threading.Event()

        def _fetch():
            try:
                result_box["data"] = self.fetch_all_raw_data(location, categories, mineral_type)
            except Exception as e:
                error_box["err"] = e
            finally:
                done_event.set()

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()

        while not done_event.wait(timeout=10):
            yield ("keepalive", None, None, None)

        if "err" in error_box:
            raise error_box["err"]

        raw_data, raw_levels = result_box["data"]

        # 阶段2：逐类别提取，每完成一个立即 yield
        all_cats = get_all_categories()
        category_names = {c.id: c.name for c in all_cats}
        cat_map = {c.id: c for c in all_cats}

        cat_ids = [c.id for c in all_cats if c.id in raw_data]
        total = len(cat_ids)

        for idx, cat_id in enumerate(cat_ids, 1):
            cat = cat_map[cat_id]
            batch_raw = {cat_id: raw_data[cat_id]}
            # 在后台线程跑「图件收集 + claude 提取」（geology 图件需联网、claude -p 可能耗时数十秒），
            # 主线程期间持续发 keepalive 心跳，避免任何联网/计算导致 SSE 静默断连
            _box: Dict = {}
            _done = threading.Event()

            def _extract(_raw=batch_raw, _cid=cat_id):
                # 确定性收集子系统图件（不经 LLM，避免幻觉）；失败不影响提取
                try:
                    _box["figs"] = [
                        Figure(path=f["path"], caption=f["caption"], source=f["source"])
                        for f in collect_subsystem_figures(
                            _cid, location.min_lon, location.min_lat,
                            location.max_lon, location.max_lat)
                    ]
                except Exception as exc:  # noqa: BLE001
                    print(f"[Figures] {_cid} 图件收集失败：{exc}")
                    _box["figs"] = []
                try:
                    _box["data"] = self._run_extraction_batch(_raw, location, category_names, mineral_type)
                except Exception as exc:  # noqa: BLE001
                    _box["err"] = exc
                finally:
                    _done.set()

            threading.Thread(target=_extract, daemon=True).start()
            while not _done.wait(timeout=8):
                yield ("keepalive", None, None, None)

            figs = _box.get("figs", [])

            if "err" in _box:
                yield idx, total, cat_id, SearchResult(
                    category_id=cat_id, category_name=cat.name,
                    summary="", error=str(_box["err"]), figures=figs
                )
                continue
            cat_data = _box.get("data", {}).get(cat_id, {})

            if not cat_data:
                result = SearchResult(
                    category_id=cat_id, category_name=cat.name,
                    summary="", error="Claude API 未返回该类别数据", figures=figs
                )
            else:
                result = SearchResult(
                    category_id=cat_id,
                    category_name=cat.name,
                    summary=cat_data.get("summary", ""),
                    data_points=[
                        DataPoint(
                            item=dp.get("item", ""),
                            value=dp.get("value", ""),
                            source=dp.get("source", "")
                        )
                        for dp in cat_data.get("data_points", [])
                    ],
                    key_findings=cat_data.get("key_findings", []),
                    data_sources=cat_data.get("data_sources", []),
                    error=None,
                    mineral_type=mineral_type,
                    exploration_impact=cat_data.get("exploration_impact", ""),
                    figures=figs,
                    evidence_level=raw_levels.get(cat_id, "")
                )
            yield idx, total, cat_id, result
