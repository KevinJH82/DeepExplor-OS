"""交付自检 + 分级修复(postprocess/delivery_check)单测。

合成 fixture 为主(快、不依赖真实交付),外加一个对真实交付的路径守卫(存在才跑)。
覆盖:路径解析、正常、packaging_gap→SAFE、截断→SAFE、not_expected、pending_async、
risky 只通知、防循环限次、幂等,以及 EnMAP METADATA.XML 打包(真实 raw 在才跑)。
"""
import json
import time
from pathlib import Path

import pytest

from postprocess.delivery_check import (
    ArtifactStatus, RepairTier, check_delivery, execute_repairs, load_rules,
)

RULES = load_rules()
MIN = RULES["min_bytes"]
SUMMER = RULES["seasons"]["summer"]["label"]
WINTER = RULES["seasons"]["winter"]["label"]
BIG = MIN + 10_000
SMALL = 1_000


def _w(p: Path, n: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)


def _build_delivery(root: Path, area: str, *, enmap=None, prisma=None,
                    seasons=(("summer", SUMMER), ("winter", WINTER)),
                    kml=True, png=True, dem=True):
    """构造交付区域目录。enmap/prisma: dict(file->size) 或 None(不建文件夹)。
    dem=True 时在每个季节根写一个合法 DEM.tif(DEM 为必选项,默认满足)。"""
    area_dir = root / area
    area_dir.mkdir(parents=True, exist_ok=True)
    if kml:
        _w(area_dir / f"{area}.ovkml", 2000)
    if png:
        _w(area_dir / "satellite_overview.png", BIG)
    for _key, label in seasons:
        sdir = area_dir / label
        sdir.mkdir(parents=True, exist_ok=True)
        if dem:
            _w(sdir / "DEM.tif", BIG)
        if enmap is not None:
            for fn, sz in enmap.items():
                _w(sdir / "EnMAP L2A" / fn, sz)
        if prisma is not None:
            for fn, sz in prisma.items():
                _w(sdir / "PRISMA L2D" / fn, sz)
    return area_dir


def _enmap_full():
    return {"SPECTRAL_IMAGE_VNIR.tif": BIG, "SPECTRAL_IMAGE_SWIR.tif": BIG,
            "METADATA.XML": 5000}


def _sensor_checks(report, sensor_label):
    return [c for c in report.checks if c.scope == "sensor" and c.name == sensor_label]


# ── A 正常 ────────────────────────────────────────────────────────────────────
def test_happy_all_ok(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full())
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    assert rep.overall == "ok"
    assert all(c.status == ArtifactStatus.OK.value for c in _sensor_checks(rep, "EnMAP L2A"))


# ── B 缺产物 → PACKAGING_GAP → SAFE 修复 ──────────────────────────────────────
def test_packaging_gap_safe_repair(tmp_path):
    # 缺 METADATA.XML,raw 有数据
    area = _build_delivery(tmp_path / "deliv", "blk",
                           enmap={"SPECTRAL_IMAGE_VNIR.tif": BIG, "SPECTRAL_IMAGE_SWIR.tif": BIG})
    raw = tmp_path / "raw" / "blk"; _w(raw / "enmap" / "cube.tar.gz", BIG)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    gaps = _sensor_checks(rep, "EnMAP L2A")
    assert all(c.status == ArtifactStatus.PACKAGING_GAP.value for c in gaps)
    assert all(c.repair_tier == RepairTier.SAFE.value for c in gaps)
    assert rep.overall == "repairing"

    # SAFE 修复:stub cb 把缺的 METADATA.XML 补上
    def _fix():
        for label in (SUMMER, WINTER):
            _w(area / label / "EnMAP L2A" / "METADATA.XML", 5000)
        return True
    attempts = {}
    execute_repairs(rep, safe_package_cb=_fix,
                    attempts_get=lambda k: attempts.get(k, 0),
                    attempts_inc=lambda k: attempts.__setitem__(k, attempts.get(k, 0) + 1))
    assert attempts.get("package") == 1
    rep2 = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    assert rep2.overall == "ok"


# ── C 截断 → SAFE(删坏档)────────────────────────────────────────────────────
def test_truncated_safe_deletes_bad(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk",
                           enmap={"SPECTRAL_IMAGE_VNIR.tif": SMALL,  # 截断
                                  "SPECTRAL_IMAGE_SWIR.tif": BIG, "METADATA.XML": 5000})
    raw = tmp_path / "raw" / "blk"; _w(raw / "enmap" / "cube.tar.gz", BIG)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    chk = _sensor_checks(rep, "EnMAP L2A")[0]
    assert chk.status == ArtifactStatus.TRUNCATED.value
    assert "SPECTRAL_IMAGE_VNIR.tif" in chk.truncated

    called = {"n": 0}
    execute_repairs(rep, safe_package_cb=lambda: called.__setitem__("n", called["n"] + 1) or True,
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    # 坏档被删 + 触发补包
    assert not (area / SUMMER / "EnMAP L2A" / "SPECTRAL_IMAGE_VNIR.tif").exists()
    assert called["n"] == 1


# ── D not_expected(规则6)─────────────────────────────────────────────────────
def test_not_expected_zero_scenes(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full(), prisma={})
    raw = tmp_path / "raw" / "blk"; (raw / "prisma").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap", "prisma"],
                         summary={"enmap": 1, "prisma": 0}, rules=RULES)
    pr = _sensor_checks(rep, "PRISMA L2D")
    assert all(c.status == ArtifactStatus.NOT_EXPECTED.value for c in pr)
    assert rep.overall == "ok"   # prisma 无数据不算故障


# ── E pending_async ──────────────────────────────────────────────────────────
def test_pending_async_waits(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=None)   # 文件夹缺
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    (raw / "enmap" / ".enmap_pending_order.json").write_text(json.dumps({
        "order_id": "x", "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    assert all(c.status == ArtifactStatus.PENDING_ASYNC.value for c in _sensor_checks(rep, "EnMAP L2A"))
    assert rep.overall == "waiting_async"


# ── F risky:确缺却无 raw 无订单 → 只通知,不自动 ─────────────────────────────
def test_missing_is_risky_not_auto(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=None)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)   # raw 空
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 2},
                         rules=RULES, task_id="t1")
    assert all(c.status == ArtifactStatus.MISSING.value for c in _sensor_checks(rep, "EnMAP L2A"))
    assert all(c.repair_tier == RepairTier.RISKY.value for c in _sensor_checks(rep, "EnMAP L2A"))
    ran = []
    execute_repairs(rep, safe_package_cb=lambda: ran.append(1) or True,
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    assert ran == []                       # 危险动作绝不自动执行
    assert rep.overall == "needs_attention"
    assert rep.risky_repairs_offered
    assert rep.risky_repairs_offered[0]["endpoint"].endswith("/t1/restart")


# ── F2 band_range:交付目录在但缺必备波段,且原始源也缺 → MISSING(走重下),不空转补包 ──
_S2 = "Sentinel 2 L2"


def _build_s2_partial(tmp_path, *, raw_b08=False, raw_b11_b12=False, decoy_part=True):
    """构造 Sentinel-2 交付:两季节都只打出 B01-B07,缺必备 B08/B11/B12。
    raw_*: 控制原始 mosaic 源是否存在;decoy_part: 放一个大 .part 残包(触发旧逻辑误判)。
    """
    area = tmp_path / "deliv" / "blk"
    for label in (SUMMER, WINTER):
        d = area / label / _S2
        for i in range(1, 8):
            _w(d / f"B0{i}.tiff", BIG)
    raw = tmp_path / "raw" / "blk" / "sentinel2"
    raw.mkdir(parents=True, exist_ok=True)
    for i in range(1, 8):
        _w(raw / f"mosaic_B0{i}_20m.tif", BIG)        # B01-B07 真实源
    _w(raw / "mosaic_B08.tif", SMALL)                 # B08 仅空占位(< MIN)
    if raw_b08:
        _w(raw / "mosaic_B08_10m.tif", BIG)
    if raw_b11_b12:
        _w(raw / "mosaic_B11_20m.tif", BIG)
        _w(raw / "mosaic_B12_20m.tif", BIG)
    if decoy_part:
        _w(raw / "S2B_MSIL2A_x.SAFE.zip.part", BIG)   # 残包:_raw_has_data 会返回 True
    return area, tmp_path / "raw" / "blk"


def test_s2_missing_band_no_raw_source_is_risky_redownload(tmp_path):
    # 缺 B08/B11/B12,原始源全无(只有空占位 + 残包)→ 应判 MISSING + RISKY,不空转补包
    area, raw = _build_s2_partial(tmp_path)
    rep = check_delivery(area, "blk", raw, ["sentinel2"],
                         summary={"sentinel2": 3}, rules=RULES, task_id="t9")
    s2 = _sensor_checks(rep, _S2)
    assert s2 and all(c.status == ArtifactStatus.MISSING.value for c in s2)
    assert all(c.repair_tier == RepairTier.RISKY.value for c in s2)
    assert all("B08.tiff" in c.missing for c in s2)
    ran = []
    execute_repairs(rep, safe_package_cb=lambda: ran.append(1) or True,
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    assert ran == []                       # 不再空转补包
    assert rep.overall == "needs_attention"
    assert rep.risky_repairs_offered[0]["endpoint"].endswith("/t9/restart")


def test_s2_missing_band_with_raw_source_still_packaging_gap(tmp_path):
    # 回归:缺的 B08/B11/B12 原始源都在 → 仍走 SAFE 补包(不误判为重下)
    area, raw = _build_s2_partial(tmp_path, raw_b08=True, raw_b11_b12=True)
    rep = check_delivery(area, "blk", raw, ["sentinel2"],
                         summary={"sentinel2": 3}, rules=RULES)
    s2 = _sensor_checks(rep, _S2)
    assert s2 and all(c.status == ArtifactStatus.PACKAGING_GAP.value for c in s2)
    assert all(c.repair_tier == RepairTier.SAFE.value for c in s2)
    assert rep.overall == "repairing"


# ── F3 必选 DEM:缺失/截断 → SAFE dem_fetch 自动补全;到顶 needs_attention ──
def _dem_checks(report):
    return [c for c in report.checks if c.scope == "required_file" and c.name == "DEM.tif"]


def test_dem_present_ok(tmp_path):
    # 默认 fixture 写了合法 DEM.tif → 必选项满足
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full())
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    dem = _dem_checks(rep)
    assert len(dem) == 2 and all(c.status == ArtifactStatus.OK.value for c in dem)
    assert all(not c.informational for c in dem)   # 必选,非 informational


def test_dem_missing_triggers_dem_fetch(tmp_path):
    # 缺 DEM.tif → MISSING + SAFE;execute_repairs 调 dem_fetch_cb 补全
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full(), dem=False)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    dem = _dem_checks(rep)
    assert dem and all(c.status == ArtifactStatus.MISSING.value for c in dem)
    assert all(c.repair_tier == RepairTier.SAFE.value for c in dem)

    fetched = {"n": 0}
    def _fetch():
        fetched["n"] += 1
        for label in (SUMMER, WINTER):      # 模拟下载+补包
            _w(area / label / "DEM.tif", BIG)
        return True
    execute_repairs(rep, dem_fetch_cb=_fetch,
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    assert fetched["n"] == 1                 # 全区域只补一次,非按季节重复
    assert any(a.get("kind") == "dem_fetch" for a in rep.safe_repairs_run)
    rep2 = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    assert all(c.status == ArtifactStatus.OK.value for c in _dem_checks(rep2))


def test_dem_truncated_deleted_then_fetched(tmp_path):
    # DEM.tif 存在但 < 2048B(截断)→ 先删坏档再 dem_fetch
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full(), dem=False)
    for label in (SUMMER, WINTER):
        _w(area / label / "DEM.tif", 500)    # 截断
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    assert all(c.status == ArtifactStatus.TRUNCATED.value for c in _dem_checks(rep))
    seen = {}
    def _fetch():
        # dem_fetch 调用时坏档应已被删除
        seen["summer_exists"] = (area / SUMMER / "DEM.tif").exists()
        return True
    execute_repairs(rep, dem_fetch_cb=_fetch,
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    assert seen.get("summer_exists") is False


def test_dem_fetch_capped_needs_attention(tmp_path):
    # dem_fetch 已达上限 → needs_attention,不再调用 cb
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full(), dem=False)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    called = []
    execute_repairs(rep, dem_fetch_cb=lambda: called.append(1) or True,
                    attempts_get=lambda k: 3, attempts_inc=lambda k: None)
    assert called == []
    assert rep.overall == "needs_attention"


# ── F4 投影底图(satellite_overview.png):缺失 → SAFE overview_fetch 自动补全 ──
def _png_check(report):
    return [c for c in report.checks if c.name == "projection_png"]


def test_projection_png_present_informational(tmp_path):
    # 默认 fixture 写了 satellite_overview.png → OK 且仅记录(informational)
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full())
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    png = _png_check(rep)
    assert png and all(c.status == ArtifactStatus.OK.value for c in png)
    assert all(c.informational for c in png)


def test_projection_png_missing_triggers_overview_fetch(tmp_path):
    # 缺投影底图 → MISSING + SAFE(非 informational);execute_repairs 调 overview_fetch_cb
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full(), png=False)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    png = _png_check(rep)
    assert png and png[0].status == ArtifactStatus.MISSING.value
    assert png[0].repair_tier == RepairTier.SAFE.value and not png[0].informational
    assert rep.overall == "repairing"

    fetched = {"n": 0}
    def _fetch():
        fetched["n"] += 1
        _w(area / "satellite_overview.png", BIG)   # 模拟重下底图
        return True
    execute_repairs(rep, overview_fetch_cb=_fetch,
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    assert fetched["n"] == 1
    assert any(a.get("kind") == "overview_fetch" for a in rep.safe_repairs_run)
    rep2 = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    assert rep2.overall == "ok"
    assert all(c.status == ArtifactStatus.OK.value for c in _png_check(rep2))


def test_projection_png_missing_does_not_trigger_package(tmp_path):
    # 仅缺投影底图时,不应触发传感器增量补包(safe_package_cb),只走 overview_fetch
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full(), png=False)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    pkg = {"n": 0}
    execute_repairs(rep,
                    safe_package_cb=lambda: pkg.__setitem__("n", pkg["n"] + 1) or True,
                    overview_fetch_cb=lambda: (_w(area / "satellite_overview.png", BIG), True)[1],
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    assert pkg["n"] == 0


def test_projection_png_fetch_capped_needs_attention(tmp_path):
    # overview_fetch 到上限 → needs_attention,不再调 cb
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full(), png=False)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    called = []
    execute_repairs(rep, overview_fetch_cb=lambda: called.append(1) or True,
                    attempts_get=lambda k: 3, attempts_inc=lambda k: None)
    assert called == []
    assert rep.overall == "needs_attention"


# ── G 防循环:SAFE 永修不好 → 限 3 次后 needs_attention ───────────────────────
def test_loop_safety_caps_attempts(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk",
                           enmap={"SPECTRAL_IMAGE_VNIR.tif": BIG, "SPECTRAL_IMAGE_SWIR.tif": BIG})
    raw = tmp_path / "raw" / "blk"; _w(raw / "enmap" / "cube.tar.gz", BIG)
    attempts = {}
    calls = {"n": 0}

    def _never_fix():
        calls["n"] += 1
        return False
    last = None
    for _ in range(4):
        rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
        last = execute_repairs(rep, safe_package_cb=_never_fix,
                               attempts_get=lambda k: attempts.get(k, 0),
                               attempts_inc=lambda k: attempts.__setitem__(k, attempts.get(k, 0) + 1))
    assert calls["n"] == 3                 # 第 4 次不再调
    assert attempts["package"] == 3
    assert last.overall == "needs_attention"


# ── H 幂等:正常交付跑修复不动任何东西 ───────────────────────────────────────
def test_idempotent_no_repair_when_ok(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=_enmap_full())
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    rep = check_delivery(area, "blk", raw, ["enmap"], summary={"enmap": 1}, rules=RULES)
    ran = []
    execute_repairs(rep, safe_package_cb=lambda: ran.append(1) or True,
                    attempts_get=lambda k: 0, attempts_inc=lambda k: None)
    assert ran == []
    assert rep.overall == "ok"


# ── 回归:web 侧无 summary 时,0 景不得误报 risky;有异步 marker 须 pending ───────
def test_no_summary_zero_scene_not_risky(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=None)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)   # 空 raw,无 marker
    rep = check_delivery(area, "blk", raw, ["enmap"], summary=None, progress={}, rules=RULES)
    assert all(c.status == ArtifactStatus.NOT_EXPECTED.value for c in _sensor_checks(rep, "EnMAP L2A"))
    assert rep.overall == "ok"


def test_no_summary_async_marker_pending(tmp_path):
    area = _build_delivery(tmp_path / "deliv", "blk", enmap=None)
    raw = tmp_path / "raw" / "blk"; (raw / "enmap").mkdir(parents=True)
    (raw / "enmap" / ".enmap_pending_order.json").write_text(json.dumps({
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    rep = check_delivery(area, "blk", raw, ["enmap"], summary=None, progress={}, rules=RULES)
    assert all(c.status == ArtifactStatus.PENDING_ASYNC.value for c in _sensor_checks(rep, "EnMAP L2A"))
    assert rep.overall == "waiting_async"


# ── Step0 路径守卫 + I 打包:对真实交付/raw(存在才跑)──────────────────────────
_REAL_DEL = Path("/Volumes/大硬盘可劲用/DeepExplor/数据留存/交付数据/辽矿两测试区块_1779870023/辽矿两测试区块_1780279565")
_REAL_RAW = Path("/Volumes/大硬盘可劲用/DeepExplor/数据留存/原始数据/辽矿两测试区块_1779870023/辽矿两测试区块_1780279565")


@pytest.mark.skipif(not _REAL_DEL.exists(), reason="真实交付目录不在")
def test_real_path_resolution_finds_enmap(tmp_path):
    rep = check_delivery(_REAL_DEL, "辽矿两测试区块_1780279565", _REAL_RAW,
                         ["enmap", "prisma"], summary={"enmap": 1, "prisma": 0}, rules=RULES)
    enmap = _sensor_checks(rep, "EnMAP L2A")
    # VNIR/SWIR 真实在盘 → 至少不应判 MISSING(允许缺 METADATA.XML 的 packaging_gap)
    assert all(c.status in (ArtifactStatus.OK.value, ArtifactStatus.PACKAGING_GAP.value)
               for c in enmap)
    assert "SPECTRAL_IMAGE_VNIR.tif" not in sum((c.missing for c in enmap), [])


@pytest.mark.skipif(not _REAL_RAW.exists(), reason="真实 EnMAP raw 不在")
def test_real_enmap_metadata_copy(tmp_path):
    from postprocess.package import _copy_enmap_metadata
    vnir = list(_REAL_RAW.rglob("*SPECTRAL_IMAGE_VNIR.TIF"))
    if not vnir:
        pytest.skip("未找到 EnMAP 立方体")
    out = tmp_path / "EnMAP L2A"; out.mkdir()
    _copy_enmap_metadata(vnir[0].parent, out)
    meta = out / "METADATA.XML"
    assert meta.exists() and meta.stat().st_size > 0
