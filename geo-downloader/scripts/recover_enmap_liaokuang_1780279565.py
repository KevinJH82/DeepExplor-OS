#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性抢救:把 task 4265531a(辽矿两测试区块_1780279565)那块卡在 76% 的
EnMAP 文件从 DLR FTPS 拉全,校验大小,再增量补包进交付目录,最后清 pending。

背景(2026-06-02 诊断):
  - 订单 kevin_jh-cat1distributor_... 已 PROCESSED,daemon 也确实进了下载,
    但 FTPS 数据连接半死,文件停在 303,947,776 B / 397,467,782 B(76%)。
  - daemon._download_ftps 非 SOCKS5 路径没设读超时,单线程 _async_pending_loop
    被这条死 socket 钉住,既不超时也不重试,顺带冻住其它异步任务。
  - check_pending 的"文件存在且 size>0 即视为下载完成"还会把这个截断档当成品。

本脚本不依赖 daemon,自带:
  - 可断点续传的 FTPS 下载(REST + 每次读 socket 超时 + 重试退避)
  - 以远端 SIZE 为准校验完整性,不达标不算成功
  - 下到临时文件,校验通过后 os.replace 原子落位(不动 daemon 仍持有的旧 fd)
  - 增量补包,参数与 daemon 到货补包完全一致
  - 校验交付目录出现 SPECTRAL_IMAGE 后才清 .enmap_pending_order.json
幂等:重复运行安全;dest 已是完整文件则跳过下载。

注意:daemon(PID 17738)那个钉死的线程不在本脚本职责内,跑完这个再单独处理。
"""
import os
import sys
import ftplib
import socket
import ssl
import time
import shutil
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from downloader.enmap import EnMAPDownloader            # noqa: E402
from postprocess.package import package_delivery        # noqa: E402

# ── 本次抢救的固定坐标(来自 task 4265531a 持久化) ──────────────────────────
OUTPUT_DIR   = Path("/Volumes/大硬盘可劲用/DeepExplor/数据留存/原始数据/辽矿两测试区块_1779870023")
DELIVERY_DIR = Path("/Volumes/大硬盘可劲用/DeepExplor/数据留存/交付数据/辽矿两测试区块_1779870023")
KML_PATH     = ROOT / "uploads" / "kml" / "辽矿两测试区块_1780279565.ovkml"
AREA_LABEL   = "辽矿两测试区块_1780279565"
FILENAME     = "dims_op_oc_oc-en_703595361_1.tar.gz"

AREA_ROOT  = OUTPUT_DIR / AREA_LABEL              # = sensor_dir.parent(补包入口)
ENMAP_DIR  = AREA_ROOT / "enmap"
DEST       = ENMAP_DIR / FILENAME
TMP        = ENMAP_DIR / (FILENAME + ".recover.partial")
PENDING    = ENMAP_DIR / ".enmap_pending_order.json"

# ── FTPS 下载参数 ─────────────────────────────────────────────────────────
_READ_TIMEOUT   = 120     # 数据连接读超时:120s 无数据即判死,重连续传
_MAX_ATTEMPTS   = 8       # 单文件最多重试次数(每次从断点续)
_BLOCKSIZE      = 1 << 16


def _remote_size(host, port, user, pwd, path) -> int:
    ftp = ftplib.FTP_TLS()
    ftp.connect(host, port, timeout=60)
    try:
        ftp.login(user, pwd)
        ftp.prot_p()
        ftp.voidcmd("TYPE I")
        return int(ftp.size(path))
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def _download_resumable(url: str, tmp: Path, expect: int) -> bool:
    """带 REST 续传 + 读超时 + 重试退避的 FTPS 下载,以远端 SIZE 校验完整。"""
    p = urlparse(url)
    host = p.hostname
    port = p.port or 21
    path = p.path
    user = p.username or DL._username
    pwd  = p.password or DL._password

    tmp.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        if have > expect:                         # 脏档,重头来
            print(f"  临时档({have})已超过远端({expect}),清掉重下")
            tmp.unlink()
            have = 0
        if have == expect:
            print(f"  临时档大小已吻合({have}),跳过传输")
            return True

        print(f"  [第 {attempt}/{_MAX_ATTEMPTS} 次] 从 {have/1048576:.1f}MB 续传 -> {expect/1048576:.1f}MB")
        ftp = ftplib.FTP_TLS()
        try:
            ftp.connect(host, port, timeout=60)   # 控制+数据连接共用此 timeout
            ftp.timeout = _READ_TIMEOUT           # 数据 socket 读超时,治 daemon 那个死挂
            ftp.login(user, pwd)
            ftp.prot_p()
            ftp.voidcmd("TYPE I")
            mode = "ab" if have else "wb"
            with open(tmp, mode) as f:
                def _cb(chunk):
                    f.write(chunk)
                ftp.retrbinary(f"RETR {path}", _cb, blocksize=_BLOCKSIZE,
                               rest=have if have else None)
            try:
                ftp.quit()
            except Exception:
                ftp.close()
        except (ftplib.all_errors, socket.timeout, ssl.SSLError, OSError) as e:
            try:
                ftp.close()
            except Exception:
                pass
            got = tmp.stat().st_size if tmp.exists() else 0
            wait = min(60, 10 * attempt)
            print(f"  [警告] 传输中断@{got/1048576:.1f}MB: {e}")
            if attempt < _MAX_ATTEMPTS:
                print(f"  {wait}s 后续传...")
                time.sleep(wait)
            continue

        got = tmp.stat().st_size if tmp.exists() else 0
        if got == expect:
            print(f"  [完成] {tmp.name} ({got} B)")
            return True
        print(f"  [警告] 落地大小 {got} != 期望 {expect},续传重试")
        time.sleep(min(60, 10 * attempt))

    return False


def main():
    global DL
    creds = yaml.safe_load(open(ROOT / "config" / "credentials.yaml")).get("dlr_eoweb")
    DL = EnMAPDownloader(credentials=creds, output_dir=str(OUTPUT_DIR))

    print(f"{'='*70}\n[抢救] {AREA_LABEL}\n  文件: {FILENAME}\n  落位: {DEST}")
    if not KML_PATH.exists():
        print(f"  [错误] KML 不存在: {KML_PATH}")
        return 2

    url = DL._build_ftps_urls([FILENAME])[0]
    print(f"  URL: {url}")
    expect = _remote_size("download.dsda.dlr.de", 21, DL._username, DL._password,
                          urlparse(url).path)
    print(f"  远端大小: {expect} B ({expect/1048576:.1f} MB)")

    # 0) 已是完整文件?直接跳到补包
    if DEST.exists() and DEST.stat().st_size == expect:
        print("  dest 已是完整文件,跳过下载")
    else:
        cur = DEST.stat().st_size if DEST.exists() else 0
        print(f"  dest 现状: {cur} B ({cur/1048576:.1f} MB) — 残档")
        # 用 daemon 那个 76% 残档播种临时文件,省去重下(TCP 已按序落盘,前缀可续)
        if not TMP.exists() and 0 < cur < expect:
            print(f"  以现有残档播种临时文件(copy {cur/1048576:.1f}MB)...")
            shutil.copyfile(DEST, TMP)
        # 1) 断点续传到完整
        if not _download_resumable(url, TMP, expect):
            print(f"  [错误] {FILENAME} 共 {_MAX_ATTEMPTS} 次仍未拉全,放弃")
            return 1
        # 2) 原子落位(daemon 仍持有旧 inode 的 fd,不受影响)
        os.replace(TMP, DEST)
        print(f"  已原子落位: {DEST} ({DEST.stat().st_size} B)")

    # 3) 增量补包(参数与 daemon._async_check_one 完全一致)
    print(f"  增量补包 -> {DELIVERY_DIR / AREA_LABEL}")
    try:
        package_delivery(
            raw_area_dir=AREA_ROOT,
            kml_path=KML_PATH,
            delivery_root=DELIVERY_DIR,
            area_label=AREA_LABEL,
            incremental=True,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  [错误] 补包失败: {e}")
        return 1

    # 4) 校验交付目录里 EnMAP 立方体就位
    spec = list((DELIVERY_DIR / AREA_LABEL).rglob("SPECTRAL_IMAGE*"))
    if not spec:
        print("  [警告] 补包后交付目录仍无 SPECTRAL_IMAGE,保留 pending 不清,请人工核查")
        return 1
    print(f"  [交付就位] SPECTRAL_IMAGE x{len(spec)}")

    # 5) 清 pending 订单缓存,让 daemon 下个周期把 enmap 从 pending_async 摘掉
    if PENDING.exists():
        PENDING.unlink()
        print("  已清除 .enmap_pending_order.json")

    print(f"\n[完成] {AREA_LABEL} 抢救成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
