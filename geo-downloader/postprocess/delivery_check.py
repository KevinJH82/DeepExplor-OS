"""交付目录自检 + 分级修复(纯逻辑层)。

按 config/delivery_rules.yaml 校验一个区域的交付目录是否完整,诊断不完整的
原因,并按"分级"策略给出修复:
  - SAFE  :用已有原始数据重新增量补包、续传已就绪的异步文件 —— 幂等 + SIZE 校验,
            做不坏,自动执行(经回调注入)。
  - RISKY :整任务重下 / 重启 daemon / 触碰 running 任务 —— 只产出一键描述符,
            绝不在此自动执行。

本模块**不 import 任何 Flask / web 状态**,修复动作全部经回调注入,使其能被
main.py(下载子进程)和 web/app.py(daemon)共用。网络调用(check_pending 等)
不在这里发生 —— check_delivery 只看磁盘信号,保持纯净可测。

判定基准见 config/delivery_rules.yaml(权威来源:数据下载交付规则.md)。
规则第 6 条:搜索阶段无该卫星数据 / 某些波段无数据属正常,不判为缺失。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "delivery_rules.yaml"

# 截断阈值只对栅格生效;XML/HDR 等小文本文件只要非空即可
_RASTER_EXTS = {".tif", ".tiff", ".he5", ".h5", ".hdf", ".nc", ".img"}


class ArtifactStatus(str, Enum):
    OK            = "ok"             # 齐全且大小合理
    MISSING       = "missing"        # 期望产物缺失(确有数据却没下到/没打包)
    TRUNCATED     = "truncated"      # 存在但 <= 阈值,疑似截断
    NOT_EXPECTED  = "not_expected"   # 无该卫星数据 / 本季节无数据(规则6,非故障)
    PENDING_ASYNC = "pending_async"  # 异步订单未到货且未超时,交给 daemon 等
    PACKAGING_GAP = "packaging_gap"  # 原始数据在,但交付产物缺/坏 → 重新补包可修


class RepairTier(str, Enum):
    NONE  = "none"
    SAFE  = "safe"     # 自动内联执行
    RISKY = "risky"    # 只通知 + 一键,绝不自动


@dataclass
class RepairAction:
    kind:       str                       # incremental_package | ftps_resume | restart_task | daemon_restart
    tier:       str                       # RepairTier 值
    sensor_id:  str = ""
    season_key: str = ""
    label:      str = ""                  # UI 按钮文案
    endpoint:   str = ""                  # RISKY 一键的 API 路径
    method:     str = "POST"
    params:     dict = field(default_factory=dict)


@dataclass
class ArtifactCheck:
    scope:        str                     # "sensor" | "season_file" | "top_level"
    name:         str                     # 传感器 label / 文件名 / 顶层项
    season_key:   str = ""                # summer | winter |(顶层为空)
    status:       str = ArtifactStatus.OK.value
    expected:     List[str] = field(default_factory=list)
    present:      List[str] = field(default_factory=list)
    missing:      List[str] = field(default_factory=list)
    truncated:    List[str] = field(default_factory=list)
    cause:        str = ""
    repair_tier:  str = RepairTier.NONE.value
    repair_action: Optional[dict] = None  # RepairAction.asdict 或 None
    informational: bool = False           # True = 仅报告,不驱动 needs_attention/修复


@dataclass
class DeliveryCheckReport:
    task_id:      str
    area_label:   str
    delivery_dir: str
    raw_area_dir: str
    checked_at:   float
    checks:       List[ArtifactCheck] = field(default_factory=list)
    overall:      str = "ok"              # ok | repairing | needs_attention | waiting_async
    safe_repairs_run:      List[dict] = field(default_factory=list)
    risky_repairs_offered: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DeliveryCheckReport":
        checks = [ArtifactCheck(**c) for c in d.get("checks", [])]
        d = dict(d)
        d["checks"] = checks
        return cls(**d)


# ── 规则加载 ──────────────────────────────────────────────────────────────────

def load_rules(path: Path = _RULES_PATH) -> dict:
    """读取交付规则;对缺字段回填默认值,容错(读不到返回最小可用结构)。"""
    try:
        rules = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        rules = {}
    rules.setdefault("top_level", {})
    rules.setdefault("seasons", {})
    rules.setdefault("season_files", [])
    rules.setdefault("required_season_files", [])
    rules.setdefault("min_bytes", 51200)
    for s in rules.get("sensors", []) or []:
        s.setdefault("async", False)
        s.setdefault("match", "all_files")
        s.setdefault("raw_dir", s.get("id"))
        s.setdefault("min_bytes", rules["min_bytes"])
        s.setdefault("async_giveup_seconds", 259200)
    rules.setdefault("sensors", [])
    return rules


# ── 小工具 ────────────────────────────────────────────────────────────────────

def _find_file(folder: Path, name: str) -> Optional[Path]:
    """在 folder 下找名为 name 的文件(大小写不敏感兜底)。"""
    direct = folder / name
    if direct.exists():
        return direct
    if not folder.is_dir():
        return None
    low = name.lower()
    for p in folder.iterdir():
        if p.name.lower() == low:
            return p
    return None


def _is_truncated(p: Path, min_bytes: int) -> bool:
    """栅格 < min_bytes 判截断;非栅格(xml/hdr)只要非空即可。"""
    try:
        size = p.stat().st_size
    except OSError:
        return True
    if p.suffix.lower() in _RASTER_EXTS:
        return size < min_bytes
    return size <= 0


def _raw_has_data(raw_dir: Path, min_bytes: int) -> bool:
    """raw 子目录里有没有像样的输入(任一 > min_bytes 的文件,或归档)。"""
    if not raw_dir.is_dir():
        return False
    for p in raw_dir.rglob("*"):
        if p.is_file():
            try:
                if p.stat().st_size > min_bytes:
                    return True
            except OSError:
                continue
    return False


def _band_source_exists(raw_dir: Path, fname: str, min_bytes: int) -> bool:
    """band_range 传感器:raw 子目录里是否有该波段的像样原始源(mosaic_<BAND>[_res].tif)。

    以"波段分隔"匹配(_B08_ / _B08.),避免 B1 误命中 B10/B11;并要求 > min_bytes,
    从而排除全零空占位(如下载未完成时残留的 mosaic_B08.tif)。
    """
    if not raw_dir.is_dir():
        return False
    bkey = Path(fname).stem.upper()
    needles = (f"_{bkey}_", f"_{bkey}.")
    for p in raw_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name.upper()
        if not any(n in name for n in needles):
            continue
        try:
            if p.stat().st_size > min_bytes:
                return True
        except OSError:
            continue
    return False


def _missing_without_raw_source(
    sensor: dict, raw_dir: Path, missing: List[str], min_bytes: int,
) -> List[str]:
    """缺失产物中"原始源也不存在"的子集 —— 这些只能重下,补包无济于事。

    band_range(Sentinel-2 等):逐波段核查 mosaic_<BAND> 源是否在;
    其他匹配模式:raw 命名无法按波段定位,只在整个 raw 子目录无像样数据时判全部缺源
    (保守:有任何像样输入就仍走补包,避免误判为重下)。
    """
    if sensor.get("match") == "band_range":
        return [f for f in missing if not _band_source_exists(raw_dir, f, min_bytes)]
    if not _raw_has_data(raw_dir, min_bytes):
        return list(missing)
    return []


def _async_marker(raw_dir: Path, sensor_id: str) -> Optional[dict]:
    """读 .{sensor}_pending_order.json;不存在/坏返回 None。"""
    import json
    f = raw_dir / f".{sensor_id}_pending_order.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def _marker_age_seconds(marker: dict) -> Optional[float]:
    from datetime import datetime
    ts = marker.get("submitted_at")
    if not ts:
        return None
    try:
        return max(0.0, time.time() - datetime.fromisoformat(ts).timestamp())
    except Exception:
        return None


# ── 核心:诊断 ────────────────────────────────────────────────────────────────

def _check_sensor_in_season(
    sensor: dict, season_key: str, season_dir: Path, raw_dir: Path,
    has_data: bool, other_season_ok: Optional[bool],
) -> ArtifactCheck:
    label = sensor["label"]
    min_bytes = sensor["min_bytes"]
    required = sensor.get("files") or sensor.get("required") or []
    out_dir = season_dir / label

    chk = ArtifactCheck(scope="sensor", name=label, season_key=season_key,
                        expected=list(required))

    marker = _async_marker(raw_dir, sensor["id"]) if sensor.get("async") else None

    # 1) 规则6:无数据 且 无异步订单 → 不期望(异步订单存在即视为"期望")
    if not has_data and marker is None:
        chk.status = ArtifactStatus.NOT_EXPECTED.value
        chk.cause = "搜索阶段该传感器无数据(规则6)"
        return chk

    folder_exists = out_dir.is_dir()

    if folder_exists:
        present, missing, truncated = [], [], []
        for fname in required:
            p = _find_file(out_dir, fname)
            if p is None:
                missing.append(fname)
            elif _is_truncated(p, min_bytes):
                truncated.append(fname)
            else:
                present.append(p.name)
        # 无固定文件名要求(match=nonempty_dir)时,目录非空即 OK
        if not required:
            any_file = any(x.is_file() for x in out_dir.rglob("*"))
            chk.present = [out_dir.name] if any_file else []
            chk.status = ArtifactStatus.OK.value if any_file else ArtifactStatus.PACKAGING_GAP.value
            if not any_file:
                chk.cause = "交付目录为空,原始数据在 → 重新补包"
                chk.repair_tier = RepairTier.SAFE.value
            return chk
        chk.present, chk.missing, chk.truncated = present, missing, truncated
        if not missing and not truncated:
            chk.status = ArtifactStatus.OK.value
        elif truncated:
            chk.status = ArtifactStatus.TRUNCATED.value
            chk.cause = f"产物截断: {truncated}(删坏档后重新补包)"
            chk.repair_tier = RepairTier.SAFE.value
        else:
            no_source = _missing_without_raw_source(sensor, raw_dir, missing, min_bytes)
            if no_source:
                # 缺的产物连原始源都没有(下载未完成/被中断)→ 补包永远补不出,需重下
                chk.status = ArtifactStatus.MISSING.value
                chk.cause = (f"缺产物 {missing},其中 {no_source} 无原始源"
                             f"(原始下载未完成/被中断)→ 需重下整任务")
                chk.repair_tier = RepairTier.RISKY.value
            else:
                chk.status = ArtifactStatus.PACKAGING_GAP.value
                chk.cause = f"缺产物 {missing},原始源在但未打包 → 重新补包"
                chk.repair_tier = RepairTier.SAFE.value
        return chk

    # 2) 文件夹整体缺失 —— 区分"本季节无数据"vs"异步等待"vs"确缺"
    chk.missing = list(required)
    if marker is not None:
        age = _marker_age_seconds(marker)
        giveup = sensor.get("async_giveup_seconds", 259200)
        if age is None or age < giveup:
            chk.status = ArtifactStatus.PENDING_ASYNC.value
            chk.cause = "异步订单待到货(交给 daemon 续查)"
            return chk
        # 超时 —— 升级为需关注(危险一键重下)
        chk.status = ArtifactStatus.MISSING.value
        chk.cause = f"异步订单已超时({int((age or 0)/3600)}h)仍未到货"
        chk.repair_tier = RepairTier.RISKY.value
        return chk

    # 跨季节启发式:另一季节 OK 而本季节整目录缺 → 本季节大概率无景(规则6)
    if other_season_ok:
        chk.status = ArtifactStatus.NOT_EXPECTED.value
        chk.cause = "另一季节已交付,本季节无对应景(规则6)"
        return chk

    if _raw_has_data(raw_dir, min_bytes):
        chk.status = ArtifactStatus.PACKAGING_GAP.value
        chk.cause = "原始数据在但未打包到交付 → 重新补包"
        chk.repair_tier = RepairTier.SAFE.value
    else:
        chk.status = ArtifactStatus.MISSING.value
        chk.cause = "确有景却无原始数据且无异步订单 → 需整任务重下"
        chk.repair_tier = RepairTier.RISKY.value
    return chk


def check_delivery(
    delivery_dir: Path,
    area_label: str,
    raw_area_dir: Path,
    requested_sensors: List[str],
    summary: Optional[Dict[str, int]] = None,
    progress: Optional[Dict[str, dict]] = None,
    rules: Optional[dict] = None,
    task_id: str = "",
) -> DeliveryCheckReport:
    """纯诊断,不发网络、不改盘。

    delivery_dir : 该区域的交付目录(= delivery_root/<area_label>,调用方已解析)
    raw_area_dir : 该区域的原始数据目录(= output_dir/<area_label>)
    requested_sensors : 本任务请求的传感器(只校验 规则 ∩ 请求)
    summary  : {sensor_id: 下载文件数};0 表示全局无数据(规则6)
    progress : {sensor_id: {phase,target,done}};search/target==0 同样表示无数据
    """
    delivery_dir = Path(delivery_dir)
    raw_area_dir = Path(raw_area_dir)
    rules = rules or load_rules()
    summary = summary or {}
    progress = progress or {}
    report = DeliveryCheckReport(
        task_id=task_id, area_label=area_label,
        delivery_dir=str(delivery_dir), raw_area_dir=str(raw_area_dir),
        checked_at=time.time(),
    )

    requested = set(requested_sensors or [])

    def _has_data(sensor_id: str) -> bool:
        if sensor_id in summary:
            return summary.get(sensor_id, 0) > 0
        prog = progress.get(sensor_id) or {}
        if prog.get("phase") == "search" and prog.get("target", None) == 0:
            return False
        # 无 summary/progress 信息时,以"原始目录有无数据"兜底。
        # 不再默认 True —— 否则 0 景传感器(无 summary)会被误判为"期望"进而误报缺失;
        # 异步订单的"期望"由 _check_sensor_in_season 里的 marker 判断兜住。
        rd = next((s.get("raw_dir", sensor_id) for s in rules["sensors"]
                   if s["id"] == sensor_id), sensor_id)
        return _raw_has_data(raw_area_dir / rd, rules["min_bytes"])

    seasons = rules.get("seasons", {})

    # ── 顶层结构(informational:报告但不驱动 needs_attention/修复)──
    tl = rules.get("top_level", {})
    if tl.get("kml"):
        kml_ok = any((delivery_dir / f"{area_label}{ext}").exists()
                     for ext in (".kml", ".ovkml", ".KML"))
        report.checks.append(ArtifactCheck(
            scope="top_level", name="kml", informational=True,
            status=ArtifactStatus.OK.value if kml_ok else ArtifactStatus.MISSING.value,
            present=[f"{area_label}.kml"] if kml_ok else [],
            missing=[] if kml_ok else [f"{area_label}.kml/.ovkml"],
        ))
    png_names = tl.get("projection_png") or []
    if png_names:
        png_ok = any((delivery_dir / n).exists() for n in png_names)
        # 投影底图缺失可自动补救(重新下载 Google 卫星底图),故非 informational、
        # 走独立 SAFE 修复预算(见 execute_repairs 的 overview_fetch),不混入传感器补包。
        chk = ArtifactCheck(
            scope="top_level", name="projection_png",
            informational=png_ok,   # 已存在则仅记录;缺失则驱动修复
            status=ArtifactStatus.OK.value if png_ok else ArtifactStatus.MISSING.value,
            expected=list(png_names),
            present=[n for n in png_names if (delivery_dir / n).exists()],
            missing=[] if png_ok else list(png_names),
        )
        if not png_ok:
            chk.repair_tier = RepairTier.SAFE.value
            chk.cause = "投影底图缺失 → 重新下载 Google 卫星底图(satellite_overview.png)"
        report.checks.append(chk)

    # ── 逐季节 × 传感器 ──
    sensor_defs = [s for s in rules["sensors"] if s["id"] in requested]
    for sensor in sensor_defs:
        has_data = _has_data(sensor["id"])
        # 先算每季节"文件夹是否齐全OK",供跨季节启发式
        per_season_ok: Dict[str, bool] = {}
        for skey, sinfo in seasons.items():
            out_dir = delivery_dir / sinfo["label"] / sensor["label"]
            required = sensor.get("files") or sensor.get("required") or []
            ok = out_dir.is_dir() and all(
                (_find_file(out_dir, f) is not None
                 and not _is_truncated(_find_file(out_dir, f), sensor["min_bytes"]))
                for f in required
            ) if required else (out_dir.is_dir())
            per_season_ok[skey] = bool(ok)

        for skey, sinfo in seasons.items():
            season_dir = delivery_dir / sinfo["label"]
            raw_dir = raw_area_dir / sensor.get("raw_dir", sensor["id"])
            other_ok = any(v for k, v in per_season_ok.items() if k != skey)
            chk = _check_sensor_in_season(
                sensor, skey, season_dir, raw_dir, has_data, other_ok)
            report.checks.append(chk)

    # ── 季节影像文件(informational:输入相关,v1 只报告不强制)──
    for skey, sinfo in seasons.items():
        season_dir = delivery_dir / sinfo["label"]
        if not season_dir.is_dir():
            continue
        for fname in rules.get("season_files", []):
            p = _find_file(season_dir, fname)
            ok = p is not None and not _is_truncated(p, rules["min_bytes"])
            report.checks.append(ArtifactCheck(
                scope="season_file", name=fname, season_key=skey, informational=True,
                expected=[fname], present=[fname] if ok else [],
                missing=[] if ok else [fname],
                status=ArtifactStatus.OK.value if ok else ArtifactStatus.MISSING.value,
            ))

    # ── 必选季节根文件(DEM 等):缺/截断即故障,SAFE 自动修(下载/补包)──
    for skey, sinfo in seasons.items():
        season_dir = delivery_dir / sinfo["label"]
        if not season_dir.is_dir():
            continue
        for spec in rules.get("required_season_files", []):
            fname = spec["file"]
            fmin = spec.get("min_bytes", rules["min_bytes"])
            p = _find_file(season_dir, fname)
            chk = ArtifactCheck(scope="required_file", name=fname, season_key=skey,
                                expected=[fname])
            if p is None:
                chk.status = ArtifactStatus.MISSING.value
                chk.missing = [fname]
                chk.cause = f"必选文件缺失 → {spec.get('repair', 'fetch')} 自动补全"
                chk.repair_tier = RepairTier.SAFE.value
            elif _is_truncated(p, fmin):
                chk.status = ArtifactStatus.TRUNCATED.value
                chk.truncated = [fname]
                chk.cause = f"必选文件疑似截断(<{fmin}B)→ {spec.get('repair', 'fetch')} 重新补全"
                chk.repair_tier = RepairTier.SAFE.value
            else:
                chk.status = ArtifactStatus.OK.value
                chk.present = [p.name]
            report.checks.append(chk)

    _set_overall(report)
    return report


def _set_overall(report: DeliveryCheckReport) -> None:
    """据非 informational 的传感器检查项决定总体状态。"""
    drivers = [c for c in report.checks if not c.informational]
    statuses = {c.status for c in drivers}
    if any(c.repair_tier == RepairTier.RISKY.value for c in drivers):
        report.overall = "needs_attention"
    elif ArtifactStatus.PENDING_ASYNC.value in statuses:
        report.overall = "waiting_async"
    elif any(c.repair_tier == RepairTier.SAFE.value for c in drivers):
        report.overall = "repairing"
    else:
        report.overall = "ok"


# ── 核心:分级修复执行 ────────────────────────────────────────────────────────

def execute_repairs(
    report: DeliveryCheckReport,
    *,
    safe_package_cb: Optional[Callable[[], bool]] = None,
    ftps_resume_cb: Optional[Callable[[str], bool]] = None,
    dem_fetch_cb: Optional[Callable[[], bool]] = None,
    overview_fetch_cb: Optional[Callable[[], bool]] = None,
    attempts_get: Callable[[str], int] = lambda k: 0,
    attempts_inc: Callable[[str], None] = lambda k: None,
    delete_truncated: bool = True,
    max_attempts: int = 3,
    api_base: str = "/api/tasks",
) -> DeliveryCheckReport:
    """对 report 执行分级修复(原地更新并返回):
      SAFE  : 删截断的交付坏档(原始数据在时) + 调一次增量补包;异步 raw 截断走续传;
              必选文件(DEM)缺失/截断 → 下载/补包补全。
              受 attempts_get/inc 限次(max_attempts),到顶置 needs_attention。
      RISKY : 只生成一键描述符塞进 risky_repairs_offered,绝不在此执行。

    safe_package_cb() : 跑一次 package_delivery(incremental=True),成功返回 True。
    ftps_resume_cb(sensor_id) : 对异步传感器续传已就绪 raw(_fetch_ftps_verified)。
    dem_fetch_cb() : 下载 Copernicus DEM 并补包到交付季节根,成功返回 True。
    overview_fetch_cb() : 重新下载 Google 卫星底图(satellite_overview.png),成功返回 True。
    attempts_get/inc(key) : 限次计数器读写(key 形如 "package" / "ftps:enmap" / "dem_fetch" / "overview_fetch")。
    """
    # required_file(DEM)/ 投影底图各走独立修复预算,不混入传感器补包
    safe_findings = [c for c in report.checks
                     if not c.informational and c.repair_tier == RepairTier.SAFE.value
                     and c.scope != "required_file"
                     and c.name != "projection_png"]
    overview_findings = [c for c in report.checks
                         if c.name == "projection_png"
                         and c.repair_tier == RepairTier.SAFE.value
                         and c.status != ArtifactStatus.OK.value]
    dem_findings = [c for c in report.checks
                    if c.scope == "required_file"
                    and c.repair_tier == RepairTier.SAFE.value
                    and c.status != ArtifactStatus.OK.value]
    risky_findings = [c for c in report.checks
                      if not c.informational and c.repair_tier == RepairTier.RISKY.value]

    # ── RISKY:只产出一键描述符 ──
    for c in risky_findings:
        act = RepairAction(
            kind="restart_task", tier=RepairTier.RISKY.value,
            sensor_id="", season_key=c.season_key,
            label=f"重新下载整任务({c.name} {c.season_key} 缺数据)",
            endpoint=f"{api_base}/{report.task_id}/restart", method="POST",
        )
        c.repair_action = asdict(act)
        report.risky_repairs_offered.append(asdict(act))

    # ── SAFE:必选文件(DEM)自动补全 —— 独立预算,不与传感器补包共用 ──
    dem_capped = False
    if dem_findings and dem_fetch_cb is not None:
        # 截断的 DEM 先删,否则 _package_dem 见已存在会跳过补包
        if delete_truncated:
            for c in dem_findings:
                if c.status == ArtifactStatus.TRUNCATED.value:
                    season_label = _season_label_of(report, c.season_key)
                    bad = _find_file(Path(report.delivery_dir) / season_label, c.name)
                    if bad is not None:
                        try:
                            bad.unlink()
                        except OSError:
                            pass
        dkey = "dem_fetch"
        if attempts_get(dkey) < max_attempts:
            attempts_inc(dkey)
            try:
                ran = dem_fetch_cb()
            except Exception:
                ran = False
            if ran:
                report.safe_repairs_run.append(asdict(RepairAction(
                    kind="dem_fetch", tier=RepairTier.SAFE.value, label="下载/补包 DEM")))
        else:
            dem_capped = True
            for c in dem_findings:
                c.repair_tier = RepairTier.NONE.value
                c.cause += "(已达自动修复上限,需人工)"

    # ── SAFE:投影底图自动补全 —— 独立预算,重下 Google 卫星底图 ──
    overview_capped = False
    if overview_findings and overview_fetch_cb is not None:
        okey = "overview_fetch"
        if attempts_get(okey) < max_attempts:
            attempts_inc(okey)
            try:
                ran = overview_fetch_cb()
            except Exception:
                ran = False
            if ran:
                for c in overview_findings:
                    c.repair_tier = RepairTier.NONE.value
                    c.informational = True
                    c.status = ArtifactStatus.OK.value
                report.safe_repairs_run.append(asdict(RepairAction(
                    kind="overview_fetch", tier=RepairTier.SAFE.value,
                    label="下载投影底图(satellite_overview.png)")))
        else:
            overview_capped = True
            for c in overview_findings:
                c.repair_tier = RepairTier.NONE.value
                c.cause += "(已达自动修复上限,需人工)"

    if not safe_findings:
        _set_overall(report)
        # _set_overall 据剩余非 informational driver 定 ok/repairing;再叠加终态需关注。
        # (overview 修好已置 informational+OK → 不再是 driver;修复失败则仍为 SAFE driver → repairing)
        if risky_findings or dem_capped or overview_capped:
            report.overall = "needs_attention"
        return report

    # ── SAFE:异步 raw 续传(若提供回调)按传感器去重 ──
    if ftps_resume_cb is not None:
        done_sensors = set()
        for c in safe_findings:
            sid = _sensor_id_of(report, c)
            if not sid or sid in done_sensors:
                continue
            if c.status != ArtifactStatus.TRUNCATED.value:
                continue
            key = f"ftps:{sid}"
            if attempts_get(key) >= max_attempts:
                continue
            attempts_inc(key)
            done_sensors.add(sid)
            try:
                if ftps_resume_cb(sid):
                    report.safe_repairs_run.append(asdict(RepairAction(
                        kind="ftps_resume", tier=RepairTier.SAFE.value, sensor_id=sid,
                        label=f"续传 {sid} 异步文件")))
            except Exception:
                pass

    # ── SAFE:删坏档(截断且原始数据在)──
    if delete_truncated:
        for c in safe_findings:
            if c.status != ArtifactStatus.TRUNCATED.value:
                continue
            season_label = _season_label_of(report, c.season_key)
            out_dir = Path(report.delivery_dir) / season_label / c.name
            for bad in list(c.truncated):
                p = _find_file(out_dir, bad)
                if p is not None:
                    try:
                        p.unlink()
                    except OSError:
                        pass

    # ── SAFE:增量补包一次(去重)──
    pkg_key = "package"
    if safe_package_cb is not None and attempts_get(pkg_key) < max_attempts:
        attempts_inc(pkg_key)
        try:
            ran = safe_package_cb()
        except Exception:
            ran = False
        if ran:
            report.safe_repairs_run.append(asdict(RepairAction(
                kind="incremental_package", tier=RepairTier.SAFE.value,
                label="增量重新补包")))
        report.overall = "repairing"
    elif safe_package_cb is not None:
        # 已到重试上限仍未修好 → 终态需关注
        report.overall = "needs_attention"
        for c in safe_findings:
            c.repair_tier = RepairTier.NONE.value
            c.cause += "(已达自动修复上限,需人工)"

    if risky_findings or dem_capped or overview_capped:
        report.overall = "needs_attention"
    return report


# 反查辅助(report 内不带 sensor_id,用 label 反推)
def _sensor_id_of(report: DeliveryCheckReport, chk: ArtifactCheck) -> str:
    rules = load_rules()
    for s in rules["sensors"]:
        if s["label"] == chk.name:
            return s["id"]
    return ""


def _season_label_of(report: DeliveryCheckReport, season_key: str) -> str:
    rules = load_rules()
    return (rules.get("seasons", {}).get(season_key, {}) or {}).get("label", season_key)
