"""
consumption.py — 声明式「章节 ↔ 子系统」消费契约与融合层

本模块把「哪个报告章节消费哪个子系统的文本/图件、按什么优先级、标注什么来源层级」
集中声明在 CHAPTER_CONTRACT 一张表里，取代原先散落在 data_sources.fetch_direct /
collect_subsystem_figures 中的 if/elif 分支。

融合规则（统一在此）：
- 文本（text）：按契约顺序聚合**全部** primary provider 的输出，每段文本显式携带来源层级
  （LEVEL_LOCAL / LEVEL_API），供 search_engine 直接据此标注 evidence_level——不再靠字符串嗅探。
  仅当 primary 全空时，才启用 text_fallback（如 geophysics 主源 geo-geophys 缺失才用 data-colle）。
- 图件（figures）：先取 primary figure provider；为空才启用 figures_fallback
  （如 geology 无 geo-stru/geo-model3d 图才渲染 Macrostrat 公开地质图）。

每个 provider 是对 data_sources 中既有函数的薄封装，统一签名 fn(ctx) -> List[...]，
任意 provider 取不到数据返回 []，章节静默降级，不伪造证据。
"""

from dataclasses import dataclass, field
from typing import Callable, List, Tuple, Dict

from . import data_sources as ds

# 来源层级（显式声明，取代 search_engine 的 _SUBSYSTEM_MARKERS 字符串嗅探）
LEVEL_LOCAL = "子系统本地实证"
LEVEL_API = "直连API"
LEVEL_WEB = "网络检索"


@dataclass
class Ctx:
    """一次章节消费的地理上下文。"""
    lat: float
    lon: float
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


@dataclass
class TextSource:
    """文本 provider：label 仅用于调试，level 决定来源层级标注，fn(ctx)->List[str]。"""
    label: str
    level: str
    fn: Callable[[Ctx], List[str]]


@dataclass
class FigureSource:
    """图件 provider：fn(ctx)->List[dict{path,caption,source}]。"""
    label: str
    fn: Callable[[Ctx], List[dict]]


@dataclass
class ChapterSpec:
    text: List[TextSource] = field(default_factory=list)            # 主文本源（全部聚合）
    text_fallback: List[TextSource] = field(default_factory=list)   # 主源全空时才用
    figures: List[FigureSource] = field(default_factory=list)       # 主图件源
    figures_fallback: List[FigureSource] = field(default_factory=list)  # 主源无图才用


# ---------------------------------------------------------------------------
# Provider 适配器（薄封装 data_sources 既有函数，吸收各自不同的入参签名）
# ---------------------------------------------------------------------------

def _climate(c):            return ds.fetch_climate(c.lat, c.lon)
def _geography(c):          return ds.fetch_geography(c.lat, c.lon, c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _geology(c):            return ds.fetch_geology(c.lat, c.lon)
def _hydrology(c):          return ds.fetch_hydrology(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _structural(c):        return ds.fetch_structural_local(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _model3d_summary(c):    return ds.geo_model3d_modeling_summary(c.bbox)
def _deposits(c):           return ds.fetch_known_deposits_text(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _geophys_text(c):       return ds.fetch_geophys_text(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _geochem_text(c):       return ds.fetch_geochem_summary_text(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _geochem_public(c):     return ds.fetch_geochem_public_text(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _alteration(c):         return ds.fetch_alteration_local(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _insar(c):              return ds.fetch_insar_local(c.lat, c.lon, c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _slowvars(c):           return ds.fetch_slowvars_text(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
def _datacolle(section):    return lambda c: ds.fetch_datacolle_section(section, c.min_lon, c.min_lat, c.max_lon, c.max_lat)

def _stru_figs(c):          return ds._geo_stru_figures(c.bbox)
def _model3d_figs(c):       return ds._geo_model3d_figures(c.bbox)
def _geophys_figs(c):       return ds._geo_geophys_figures(c.bbox)
def _geochem_figs(c):       return ds._geo_geochem_figures(c.bbox)
def _insar_figs(c):         return ds._geo_insar_figures(c.bbox)
def _slowvars_figs(c):      return ds._geo_slowvars_figures(c.bbox)


def _datacolle_figs(c):
    """data-colle 物探图件（geophysics 章节图件兜底）。"""
    ds._import_commons()
    try:
        from commons.datacolle_broker import find_datacolle_for_bbox
        m = find_datacolle_for_bbox(c.bbox, ds.DATACOLLE_OUTPUTS, tenant_id=ds.current_tenant())
        return m[0].get("figures", []) if m else []
    except Exception:
        return []


def _alteration_figs(c):
    ds._import_commons()
    try:
        from commons.analyser_broker import find_alteration_for_bbox
        figs = []
        for e in find_alteration_for_bbox(c.bbox, ds.GEO_ANALYSER_OUTPUTS, tenant_id=ds.current_tenant()):
            figs.extend(e.get("figures", []))
        return figs
    except Exception:
        return []


def _macrostrat_map(c):
    """公开 Macrostrat 地质图（geology 章节图件兜底）。"""
    try:
        from .geology_map import render_geology_map
        fig = render_geology_map(c.min_lon, c.min_lat, c.max_lon, c.max_lat)
        return [fig] if fig else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 声明式消费契约表：一处定义所有「章节 → 子系统」消费与优先级
# ---------------------------------------------------------------------------

CHAPTER_CONTRACT: Dict[str, ChapterSpec] = {
    "climate": ChapterSpec(
        text=[TextSource("open-meteo", LEVEL_API, _climate)],
    ),
    "geography": ChapterSpec(
        text=[
            TextSource("srtm", LEVEL_API, _geography),
            TextSource("geo-stru", LEVEL_LOCAL, _structural),
            TextSource("data-colle:geography", LEVEL_LOCAL, _datacolle("geography")),
        ],
    ),
    "geology": ChapterSpec(
        text=[
            TextSource("macrostrat", LEVEL_API, _geology),
            TextSource("geo-stru", LEVEL_LOCAL, _structural),
            TextSource("geo-model3d:modeling", LEVEL_LOCAL, _model3d_summary),
            TextSource("geo-deposits", LEVEL_LOCAL, _deposits),
            TextSource("data-colle:geology", LEVEL_LOCAL, _datacolle("geology")),
        ],
        figures=[FigureSource("geo-stru", _stru_figs),
                 FigureSource("geo-model3d", _model3d_figs)],
        figures_fallback=[FigureSource("macrostrat-map", _macrostrat_map)],
    ),
    "geophysics": ChapterSpec(
        text=[TextSource("geo-geophys", LEVEL_LOCAL, _geophys_text)],
        text_fallback=[TextSource("data-colle:geophysics", LEVEL_LOCAL, _datacolle("geophysics"))],
        figures=[FigureSource("geo-geophys", _geophys_figs)],
        figures_fallback=[FigureSource("data-colle", _datacolle_figs)],
    ),
    "geochemistry": ChapterSpec(
        text=[
            TextSource("geo-geochem", LEVEL_LOCAL, _geochem_text),
            TextSource("geochem-public", LEVEL_LOCAL, _geochem_public),
        ],
        text_fallback=[TextSource("data-colle:geochemistry", LEVEL_LOCAL, _datacolle("geochemistry"))],
        figures=[FigureSource("geo-geochem", _geochem_figs)],
    ),
    "remote_sensing": ChapterSpec(
        text=[TextSource("geo-analyser", LEVEL_LOCAL, _alteration)],
        figures=[FigureSource("geo-analyser", _alteration_figs)],
    ),
    "insar_deformation": ChapterSpec(
        text=[TextSource("geo-insar", LEVEL_LOCAL, _insar)],
        figures=[FigureSource("geo-insar", _insar_figs)],
    ),
    "slow_variables": ChapterSpec(
        text=[TextSource("geo-7slow", LEVEL_LOCAL, _slowvars)],
        figures=[FigureSource("geo-7slow", _slowvars_figs)],
    ),
    "hydrology": ChapterSpec(
        text=[TextSource("osm-overpass", LEVEL_API, _hydrology)],
    ),
    # infrastructure / mining_rights：暂无子系统/直连源，纯网络检索，不在契约表
}


# ---------------------------------------------------------------------------
# 融合入口
# ---------------------------------------------------------------------------

def _collect_text(sources: List[TextSource], ctx: Ctx) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for s in sources:
        try:
            for t in (s.fn(ctx) or []):
                if t:
                    out.append((t, s.level))
        except Exception as e:
            print(f"[Consume] 文本源 {s.label} 失败：{e}")
    return out


def _collect_figs(sources: List[FigureSource], ctx: Ctx) -> List[dict]:
    out: List[dict] = []
    for s in sources:
        try:
            out.extend(s.fn(ctx) or [])
        except Exception as e:
            print(f"[Consume] 图件源 {s.label} 失败：{e}")
    return out


def consume_chapter(category_id: str, lat: float, lon: float,
                    min_lon: float, min_lat: float,
                    max_lon: float, max_lat: float) -> dict:
    """
    按契约表消费某章节的全部子系统数据。
    返回 {"texts": [(text, level), ...], "figures": [{path,caption,source}, ...]}。
    """
    spec = CHAPTER_CONTRACT.get(category_id)
    if spec is None:
        return {"texts": [], "figures": []}
    ctx = Ctx(lat, lon, min_lon, min_lat, max_lon, max_lat)

    texts = _collect_text(spec.text, ctx)
    if not texts and spec.text_fallback:
        texts = _collect_text(spec.text_fallback, ctx)

    figures = _collect_figs(spec.figures, ctx)
    if not figures and spec.figures_fallback:
        figures = _collect_figs(spec.figures_fallback, ctx)

    return {"texts": texts, "figures": figures}
