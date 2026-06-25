"""11 个微服务的注册表:名称 → host/port/能力。BFF 反向代理据此转发。

端口来自 DeepExplor 现状(可经环境变量覆盖,便于私有部署/容器编排)。
"""
import os

# 服务名 → 默认端口
_DEFAULT_PORTS = {
    "orchestrator": 8090,
    "downloader": 8080,
    "preprocess": 5002,
    "analyser": 5001,
    "stru": 8082,
    "geophys": 8087,
    "geochem": 8088,
    "insar": 8084,
    "slowvars": 8001,
    "exploration": 8083,
    "model3d": 8086,
    "drill": 8089,
    "reporter": 8081,
    "datacolle": 8085,   # data-colle/prospector:地学位场(EMAG2/重力)+ 化探先验
}

# 服务能力/角色,供前端编排状态机与阶段映射使用
SERVICE_META = {
    "orchestrator": {"stage": "plan", "label": "编排单", "kind": "plan"},
    "downloader":   {"stage": "data", "label": "数据下载", "kind": "data"},
    "preprocess":   {"stage": "data", "label": "预处理", "kind": "data"},
    "analyser":     {"stage": "evidence", "label": "蚀变", "kind": "evidence"},
    "stru":         {"stage": "evidence", "label": "构造", "kind": "evidence"},
    "geophys":      {"stage": "evidence", "label": "物探", "kind": "evidence"},
    "geochem":      {"stage": "evidence", "label": "化探", "kind": "evidence"},
    "insar":        {"stage": "evidence", "label": "形变", "kind": "evidence"},
    "slowvars":     {"stage": "evidence", "label": "七慢变量", "kind": "evidence"},
    "exploration":  {"stage": "evidence", "label": "深部探测", "kind": "evidence"},
    "model3d":      {"stage": "model3d", "label": "3D融合建模", "kind": "model3d"},
    "drill":        {"stage": "drill", "label": "AI布孔/闭环", "kind": "drill"},
    "reporter":     {"stage": "report", "label": "综合报告", "kind": "report"},
    "datacolle":    {"stage": "data", "label": "资料获取(物探/化探先验)", "kind": "data"},
}


def _host(name: str) -> str:
    return os.environ.get(f"SVC_{name.upper()}_HOST", "127.0.0.1")


def _port(name: str) -> int:
    return int(os.environ.get(f"SVC_{name.upper()}_PORT", _DEFAULT_PORTS[name]))


def base_url(name: str) -> str:
    """返回某服务的内网基础 URL。未知服务抛 KeyError。"""
    if name not in _DEFAULT_PORTS:
        raise KeyError(name)
    return f"http://{_host(name)}:{_port(name)}"


def all_services() -> dict:
    """前端工具箱/独立模式用:服务清单 + 能力 + base_url。"""
    return {
        name: {**SERVICE_META.get(name, {}), "base_url": base_url(name)}
        for name in _DEFAULT_PORTS
    }


def is_known(name: str) -> bool:
    return name in _DEFAULT_PORTS
