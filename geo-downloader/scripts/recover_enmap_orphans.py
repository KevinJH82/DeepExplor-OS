#!/usr/bin/env python3
"""一次性抢救脚本:把已就绪但 daemon 漏下的 EnMAP 订单从 FTPS 拉回并增量补包。

修复历史:
  - 旧 check_pending 缺 FTPS 兜底 → PROCESSED 订单永远下不下来。
  - _download_ftps 无重试 → 单次 FTPS EOFError(DLR 对快速重连节流)直接让
    整脚本崩溃,后续区块全丢(实测第 2 个文件崩了第 3 个就没跑)。
  - _package_enmap 不解内层 ZIP → 即使下载成功也产不出 SPECTRAL_IMAGE
    (DLR tar.gz 内嵌一层 .ZIP,1GB 高光谱立方体在里面)。← 已在 package.py 修。

本脚本(配合 package.py 修复):
  - 每区块独立 try,一个失败不影响其余
  - FTPS 下载带重试 + 退避,并以期望文件大小校验完整性
  - 区块之间留间隔,规避 DLR 快速重连节流
  - 已下载/已补包则跳过,可重复运行(幂等)

映射依据(FTPS 文件名 与 下单先后/pre_existing 快照吻合):
  703582883 -> 云顶4口井  (ea6f9b88)
  703582917 -> 云顶外围    (36c726d5)
  703586712 -> 津巴布韦    (54f57c8b)
"""
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from downloader.enmap import EnMAPDownloader
from postprocess.package import package_delivery

OUTPUT_ROOT   = Path("/Volumes/大硬盘可劲用/DeepExplor/数据留存/原始数据")
DELIVERY_ROOT = Path("/Volumes/大硬盘可劲用/DeepExplor/数据留存/交付数据")
KML_ROOT      = Path("/opt/deepexplor-services/geo-downloader/uploads/kml")

# (区块 kml stem, dims 文件名)
JOBS = [
    ("云顶4口井油气测试区块6.82km2_1779869303", "dims_op_oc_oc-en_703582883_1.tar.gz"),
    ("云顶油气测试区块-外围_1779869391",          "dims_op_oc_oc-en_703582917_1.tar.gz"),
    ("津巴布韦5区块-大区块8.98km2_1779965024",     "dims_op_oc_oc-en_703586712_1.tar.gz"),
]

# FTPS 期望文件大小(校验下载完整性,避免半截文件被当成功)
EXPECTED_SIZE = {
    "dims_op_oc_oc-en_703582883_1.tar.gz": 380475049,
    "dims_op_oc_oc-en_703582917_1.tar.gz": 380474970,
    "dims_op_oc_oc-en_703586712_1.tar.gz": 415096198,
}

_MAX_ATTEMPTS = 4
_INTER_BLOCK_DELAY = 20   # 区块之间的间隔秒数,规避 DLR 快速重连节流


def _download_with_retry(dl, url: str, dest: Path, expect: int = 0) -> bool:
    """带重试 + 退避的 FTPS 下载,以期望大小校验完整性。"""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            if dest.exists() and expect and dest.stat().st_size == expect:
                print(f"  已存在且大小吻合,跳过下载: {dest.name} ({dest.stat().st_size} B)")
                return True
            if dest.exists():                 # 半截或大小未知,删掉重下
                dest.unlink()
            print(f"  FTPS 下载(第 {attempt}/{_MAX_ATTEMPTS} 次): {url}")
            dl._download_ftps(url, dest)
            got = dest.stat().st_size if dest.exists() else 0
            if expect and got != expect:
                raise IOError(f"大小不符: 实际 {got} 期望 {expect}")
            print(f"  [完成] {dest.name} ({got} B)")
            return True
        except Exception as e:
            wait = 15 * attempt               # 15/30/45s
            print(f"  [警告] 下载失败(第 {attempt} 次): {e}")
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            if attempt < _MAX_ATTEMPTS:
                print(f"  {wait}s 后重试...")
                time.sleep(wait)
            else:
                print(f"  [错误] {dest.name} 共 {_MAX_ATTEMPTS} 次均失败,跳过本区块")
                return False
    return False


def _process_block(dl, stem: str, fname: str) -> str:
    area = OUTPUT_ROOT / stem
    enmap_dir = area / "enmap"
    enmap_dir.mkdir(parents=True, exist_ok=True)
    dest = enmap_dir / fname
    expect = EXPECTED_SIZE.get(fname, 0)

    # 1) FTPS 下载(带重试)
    url = dl._build_ftps_urls([fname])[0]
    if not _download_with_retry(dl, url, dest, expect):
        return "DOWNLOAD_FAIL"

    # 2) 增量补包到交付目录(与 daemon 到货后一致)
    kml = KML_ROOT / f"{stem}.ovkml"
    if not kml.exists():
        kml = KML_ROOT / f"{stem}.kml"
    print(f"  增量补包 -> {DELIVERY_ROOT / stem}")
    try:
        package_delivery(
            raw_area_dir=area,
            kml_path=kml,
            delivery_root=DELIVERY_ROOT,
            area_label=stem,
            incremental=True,
        )
    except Exception as e:
        print(f"  [错误] 补包失败: {e}")
        return "PACKAGE_FAIL"

    # 3) 校验交付目录里 EnMAP 是否就位
    spec = list((DELIVERY_ROOT / stem).rglob("SPECTRAL_IMAGE*"))
    if not spec:
        print(f"  [警告] 补包后交付目录仍无 SPECTRAL_IMAGE,请检查")
        return "NO_ENMAP_IN_DELIVERY"

    # 4) 清理过期 pending 订单文件
    pend = enmap_dir / ".enmap_pending_order.json"
    if pend.exists():
        pend.unlink()
        print(f"  已清除 pending 订单缓存")

    print(f"  [区块完成] {stem}  (交付 SPECTRAL_IMAGE x{len(spec)})")
    return "OK"


def main():
    creds = yaml.safe_load(open("config/credentials.yaml")).get("dlr_eoweb")
    dl = EnMAPDownloader(credentials=creds, output_dir=str(OUTPUT_ROOT))

    results = {}
    for idx, (stem, fname) in enumerate(JOBS):
        print(f"\n{'='*70}\n[抢救 {idx+1}/{len(JOBS)}] {stem}\n  文件: {fname}")
        try:
            results[stem] = _process_block(dl, stem, fname)
        except Exception as e:
            print(f"  [致命] 区块异常但继续下一个: {e}")
            results[stem] = f"EXC:{type(e).__name__}"
        if idx < len(JOBS) - 1:
            print(f"  区块间隔 {_INTER_BLOCK_DELAY}s(规避 DLR 重连节流)...")
            time.sleep(_INTER_BLOCK_DELAY)

    print(f"\n{'='*70}\n抢救汇总:")
    for stem, r in results.items():
        print(f"  {r:24s} {stem}")
    ok = sum(1 for r in results.values() if r == "OK")
    print(f"成功 {ok}/{len(JOBS)} 个区块。")


if __name__ == "__main__":
    main()
