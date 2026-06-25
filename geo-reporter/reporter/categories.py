"""
Search Categories Module
定义 8 类地学数据搜索的类别、子主题和数据结构。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DataPoint:
    """单个数据点"""
    item: str  # 项目名称
    value: str  # 数值或描述
    source: str  # 数据来源


@dataclass
class Figure:
    """报告插图（来自子系统标准输出的图件）"""
    path: str  # 图片绝对路径
    caption: str  # 图注
    source: str  # 来源子系统（如 'geo-analyser'）


@dataclass
class SearchResult:
    """搜索结果"""
    category_id: str  # 类别 ID（如 'climate'）
    category_name: str  # 类别中文名称
    summary: str  # 100-200 字的概述
    data_points: List[DataPoint] = field(default_factory=list)
    key_findings: List[str] = field(default_factory=list)  # 3-5 个关键发现
    data_sources: List[str] = field(default_factory=list)  # 数据来源列表
    error: Optional[str] = None  # 如果搜索失败，记录错误信息
    mineral_type: str = ""  # 目标矿种
    exploration_impact: str = ""  # 本类数据对目标矿种勘探作业的影响
    figures: List["Figure"] = field(default_factory=list)  # 子系统图件（确定性收集，不经 LLM）
    evidence_level: str = ""  # 证据来源层级（确定性标注：子系统本地实证/直连API/网络检索，可组合）


@dataclass
class SearchCategory:
    """搜索类别定义"""
    id: str  # 类别 ID（英文标识）
    name: str  # 中文名称
    chapter_title: str  # 报告中的章节标题
    sub_topics: List[str]  # 子主题列表（用于 prompt 中）


# 8 类地学数据搜索类别定义
CATEGORIES = [
    SearchCategory(
        id="climate",
        name="气候资料",
        chapter_title="气候资料",
        sub_topics=[
            "年均气温（°C）、最冷月/最热月均温及出现月份，提供具体数值和统计年份",
            "年均降雨量（mm）、各季节降雨量占比（%），提供近10年均值及数据来源",
            "年均蒸发量（mm）、年均相对湿度（%），提供具体数值",
            "历史极端气温（最高/最低值及发生年份）、历史最大单日降雨量（mm）",
            "主要气象灾害类型及历史发生频率（次/10年），影响范围",
            "植被覆盖度（%）估算值、主要植被类型及覆盖面积（km²）"
        ]
    ),
    SearchCategory(
        id="geography",
        name="地理与地形地貌资料",
        chapter_title="地理与地形地貌资料",
        sub_topics=[
            "主要地形地貌类型及各类型占区域面积比例（%）",
            "海拔高程范围（m，最低点/最高点）、最高峰名称与高程（m）及坐标",
            "地面平均坡度（°）及各坡度分级面积（km²）占比",
            "地貌分区名称、地势走向描述（如东北高西南低）及高差（m）",
            "主要河流条数、总长度（km）、流域面积（km²），提供各河流具体数值",
            "人工地形改造面积（km²）及类型（采矿、梯田、水库等）"
        ]
    ),
    SearchCategory(
        id="infrastructure",
        name="交通、基础设施及经济条件",
        chapter_title="交通、基础设施及经济条件",
        sub_topics=[
            "公路总里程（km）、公路密度（km/km²），主干道编号及距研究区距离（km）",
            "最近铁路线名称、距研究区距离（km）、最近车站名称",
            "距最近10万人口以上城市名称及公路里程（km）",
            "最近变电站容量（MVA）及距离（km），区内电力线路电压等级（kV）",
            "区内4G/5G基站覆盖率（%）、光缆到达最近节点距离（km）",
            "所在县/市近3年GDP（亿元）及增速（%）、主导产业及产值占比（%）"
        ]
    ),
    SearchCategory(
        id="hydrology",
        name="水系与水文资料",
        chapter_title="水系与水文资料",
        sub_topics=[
            "主要河流名称、流向、流域面积（km²）、河长（km），提供每条河流具体数值",
            "多年平均年径流量（亿m³）、丰水期/枯水期流量比值，统计年份范围",
            "水系密度（km/km²）、一/二/三级支流条数",
            "历史最大洪水位（m）及发生年份、防洪设施类型及设计标准（年一遇）",
            "地下水位埋深（m）、含水层厚度（m）、年补给量（亿m³）估算",
            "区内湖泊/水库数量、总面积（km²）及库容（亿m³），沼泽面积（km²）"
        ]
    ),
    SearchCategory(
        id="geology",
        name="地质与矿产资料",
        chapter_title="地质与矿产资料",
        sub_topics=[
            "出露地层时代（Ma/亿年）、主要岩性名称及出露面积（km²）占比",
            "主要断层名称、走向（°）、长度（km）、断距（m）及活动期次",
            "已知矿床名称、矿种、探明储量（吨/万吨）及品位（g/t 或 %）",
            "矿化蚀变类型、蚀变强度（弱/中/强）、蚀变带宽度（m）及延伸长度（km）",
            "成矿时代（Ma）、成矿类型（斑岩型/热液型等）、类比矿床名称",
            "区域成矿带名称、已探明矿床数量及总资源量（万吨），近期找矿突破信息"
        ]
    ),
    SearchCategory(
        id="geophysics",
        name="地球物理资料",
        chapter_title="地球物理资料",
        sub_topics=[
            "区域布格重力异常范围（mGal）、磁力异常（nT）峰值及异常面积（km²）",
            "磁铁矿分布面积（km²）、磁异常梯度（nT/km）最大值及位置",
            "重力梯度带走向与位置、重磁异常与已知矿（化）体的空间对应关系",
            "近50年内发生M≥3.0地震次数、最大震级（M）及震源深度（km）",
            "大地热流值（mW/m²）、居里面深度（km），如有资料提供具体数值",
            "可用地球物理探测方法（重、磁、电、震）及推断的深部地质结构"
        ]
    ),
    SearchCategory(
        id="geochemistry",
        name="地球化学资料",
        chapter_title="地球化学资料",
        sub_topics=[
            "主要成矿元素地球化学背景值（ppm）：Cu、Mo、Au、Ag、Pb、Zn 等",
            "区域背景值与全国背景值对比、异常下限（弱/中/强异常）分级标准",
            "化探异常元素种类、异常面积（km²）、衬度值及浓集中心位置，提供具体数值",
            "元素组合特征及分带规律（前缘/近矿/尾晕元素组合）",
            "水系沉积物/土壤/岩石地球化学测量成果及采样密度（点/km²）",
            "化探异常与已知矿（化）点、构造的空间套合关系"
        ]
    ),
    SearchCategory(
        id="insar_deformation",
        name="InSAR 形变监测资料",
        chapter_title="InSAR 形变监测资料",
        sub_topics=[
            "本研究区已有 InSAR 干涉对数量、时间跨度（YYYY-MM 至 YYYY-MM）及主要数据源（Sentinel-1 / ALOS-2 / LiCSAR 等）",
            "LOS 形变速率范围（mm/year）、最大沉降/抬升量级及具体位置（经纬度）",
            "整体相干性均值（0-1）及覆盖率（%），植被/水体掩膜后有效像素占比",
            "时空基线统计：平均时间基线（天）、最大垂直基线（m）、配对策略",
            "主要形变模式描述（线性沉降/季节性振荡/同震突变等）及其与已知矿山开采/构造活动的空间关联",
            "已发表的本区 InSAR 形变研究论文/报告（如有）及结论摘要",
            "数据获取与处理来源：HyP3 / SNAP / GMTSAR / LiCSAR,以及标准化输出契约版本"
        ]
    ),
    SearchCategory(
        id="remote_sensing",
        name="遥感影像分析",
        chapter_title="遥感影像分析",
        sub_topics=[
            "可获取遥感影像数据源（Landsat/Sentinel/高分等）、空间分辨率（m）及时间跨度",
            "可用DEM数据产品名称（SRTM/ASTER/AW3D30等）、水平分辨率（m）及垂直精度（m）",
            "地表侵蚀模数（t/km²·年）、侵蚀等级分区面积（km²）及占比（%）",
            "年均NDVI值及季节变化范围（最大值/最小值）、土壤含水量（%）估算",
            "各土地利用/覆盖类型面积（km²）及占比（%），数据年份",
            "热红外亮温异常值（K）高于背景均值的面积（km²）及位置描述"
        ]
    ),
    SearchCategory(
        id="slow_variables",
        name="慢变量综合证据",
        chapter_title="慢变量综合证据（七因子成矿驱动评估）",
        sub_topics=[
            "构造应力梯度与断裂活动性（成矿驱动力）分布特征",
            "氧化还原梯度、流体超压与化学势（流体-化学驱动）特征",
            "盖层封闭性与温度（驱动/阻力）对成矿系统的约束",
            "尖点突变判别式（Δ=b²+4/27·a³）双稳成矿有利区识别",
            "慢变量综合靶区面积、数量及主控驱动因子",
            "矿床类型权重预设来源（geo-stru 推理/用户选择）与置信度",
        ]
    ),
    SearchCategory(
        id="mining_rights",
        name="矿业权与法律政策资料",
        chapter_title="矿业权与法律政策资料",
        sub_topics=[
            "区内及周边5km范围已登记探矿权数量、面积（km²）、矿种及权利人",
            "已有采矿证数量、证载矿种、年批准开采量（万吨）及有效期",
            "环评审批要求等级（报告书/报告表）、'三废'排放标准具体限值",
            "区内自然保护区/生态红线面积（km²）及占区块面积比例（%）",
            "适用税率：资源税（元/吨或%）、企业所得税优惠税率（%）、增值税率（%）",
            "禁采区面积（km²）及限采区面积（km²），政策依据文件名称及发布年份"
        ]
    )
]


# 报告内容编排顺序（章节、PPT、前端进度均按此顺序；改这里即可统一调整全局顺序）
CATEGORY_ORDER = [
    "geology",            # 1. 地质与矿产
    "geography",          # 2. 地理与地形地貌
    "geophysics",         # 3. 地球物理
    "geochemistry",       # 4. 地球化学
    "remote_sensing",     # 5. 遥感影像分析
    "insar_deformation",  # 6. InSAR 形变监测
    "slow_variables",     # 7. 慢变量综合证据（七因子成矿驱动）
    "hydrology",          # 8. 水系与水文
    "climate",            # 9. 气候资料
    "infrastructure",     # 10. 交通与基础设施
    "mining_rights",      # 11. 矿业权与法律政策
]
CATEGORIES.sort(key=lambda c: CATEGORY_ORDER.index(c.id) if c.id in CATEGORY_ORDER else 999)


def get_category_by_id(cat_id: str) -> Optional[SearchCategory]:
    """根据 ID 获取类别定义"""
    for cat in CATEGORIES:
        if cat.id == cat_id:
            return cat
    return None


def get_all_categories() -> List[SearchCategory]:
    """获取所有类别"""
    return CATEGORIES
