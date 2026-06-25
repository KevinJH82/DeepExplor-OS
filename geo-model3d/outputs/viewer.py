"""三维 Web 查看器（P2 特性C）—— 把有利度体导出为单文件自包含 HTML。

阈值化高有利度体元下采样为点云（按 score 着色），叠加靶点标记；three.js 经 CDN
加载，体数据内嵌为 JSON blob。零 pip 依赖（纯 json+numpy），单文件可离线分发
（但渲染需联网取 three.js CDN）。经现有 /api/result 路由直接在浏览器渲染。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 三维成矿有利度</title>
<style>
  html,body{margin:0;height:100%;background:#0b1020;color:#dfe7f5;font-family:system-ui,"PingFang SC",sans-serif;overflow:hidden}
  #c{position:fixed;inset:0;display:block}
  #hud{position:fixed;left:14px;top:12px;font-size:13px;line-height:1.6;background:rgba(12,18,38,.72);
       border:1px solid #28324f;border-radius:8px;padding:10px 14px;max-width:300px}
  #hud b{color:#ffd35c}
  #legend{position:fixed;right:14px;top:12px;background:rgba(12,18,38,.72);border:1px solid #28324f;
          border-radius:8px;padding:10px 12px;font-size:12px}
  #legend .bar{height:10px;width:160px;border-radius:5px;margin:6px 0;
       background:linear-gradient(90deg,#30123b,#28709b,#1fa187,#a2da37,#fde725)}
  #err{position:fixed;inset:0;display:none;align-items:center;justify-content:center;
       background:rgba(8,12,26,.95);text-align:center;padding:24px;font-size:15px;line-height:1.8}
  .row{display:flex;justify-content:space-between;gap:14px}
  label{font-size:12px;color:#9fb0d0}
  input[type=range]{width:150px;vertical-align:middle}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="hud">
  <div><b>__TITLE__</b></div>
  <div class="row"><span>成因族</span><span>__FAMILY__</span></div>
  <div class="row"><span>有利度阈值</span><span>≥ __THR__</span></div>
  <div class="row"><span>显示体元</span><span id="nshow">__NPTS__</span></div>
  <div class="row"><span>靶点</span><span>__NTGT__</span></div>
  <div style="margin-top:8px"><label>有利度下限 <span id="cv">__THR__</span></label><br>
    <input id="cut" type="range" min="0" max="1" step="0.01" value="__THR__"></div>
  <div><label>点大小</label><br><input id="psz" type="range" min="__PSMIN__" max="__PSMAX__" step="__PSTEP__" value="__PSIZE__"></div>
  <div style="margin-top:6px;color:#7e8db0;font-size:11px">拖拽旋转 · 滚轮缩放 · 右键平移</div>
</div>
<div id="legend">有利度<div class="bar"></div><div class="row"><span>低</span><span>高</span></div>
  <div style="margin-top:6px;color:#ffd35c">● 金色 = 三维靶点</div>
  <div style="color:#7e8db0;font-size:11px">垂向夸大 ×__VEXAG__</div></div>
<div id="err">⚠ 三维查看器需联网加载 three.js (CDN)。<br>当前环境无法访问 CDN，无法渲染。<br>（点云数据仍内嵌于本文件，联网后可正常查看）</div>
<script type="application/json" id="vol-data">__DATA__</script>
<script>
  // CDN 加载失败兜底：4 秒内 module 未初始化则提示
  window.__viewerOK = false;
  setTimeout(function(){ if(!window.__viewerOK){ document.getElementById('err').style.display='flex'; } }, 4000);
</script>
<script type="importmap">
{ "imports": {
  "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
  "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const D = JSON.parse(document.getElementById('vol-data').textContent);
const M = D.meta;
const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1020);
const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.1, 1e8);

const W = M.width_m, H = M.height_m, Dz = M.depth_span_m || 1;
const diag = Math.sqrt(W*W + H*H + Dz*Dz);
camera.position.set(W*0.8, Dz*1.1 + H*0.4, H*1.0);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.target.set(0, M.depth_min_y*0.5, 0);

scene.add(new THREE.AmbientLight(0xffffff, 0.95));
const dl = new THREE.DirectionalLight(0xffffff, 0.5); dl.position.set(1,1,1); scene.add(dl);

function turbo(t){
  t = Math.max(0, Math.min(1, t));
  const r = Math.max(0, Math.min(1, 34.61 + t*(1172.33 + t*(-10793.56 + t*(33300.12 + t*(-38394.49 + t*14825.05))))))/255;
  const g = Math.max(0, Math.min(1, 23.31 + t*(557.33 + t*(1225.33 + t*(-3574.96 + t*(3520.0 + t*-1234.65))))))/255;
  const b = Math.max(0, Math.min(1, 27.2 + t*(3211.1 + t*(-15327.97 + t*(27814.0 + t*(-22569.18 + t*6838.66))))))/255;
  return [r, g, b];
}

const n = D.x.length, sArr = D.s;
const geo = new THREE.BufferGeometry();
const mat = new THREE.PointsMaterial({size:__PSIZE__, vertexColors:true, transparent:true, opacity:0.85, sizeAttenuation:true});
scene.add(new THREE.Points(geo, mat));

function applyCut(cut){
  let m = 0; for (let i=0;i<n;i++) if (sArr[i]/255 >= cut) m++;
  const p = new Float32Array(m*3), c = new Float32Array(m*3);
  let j = 0;
  for (let i=0;i<n;i++){
    if (sArr[i]/255 < cut) continue;
    p[j*3]=D.x[i]; p[j*3+1]=D.y[i]; p[j*3+2]=D.z[i];
    const cc = turbo(sArr[i]/255); c[j*3]=cc[0]; c[j*3+1]=cc[1]; c[j*3+2]=cc[2];
    j++;
  }
  geo.setAttribute('position', new THREE.BufferAttribute(p,3));
  geo.setAttribute('color', new THREE.BufferAttribute(c,3));
  document.getElementById('nshow').textContent = m;
}
applyCut(parseFloat(M.thr));

// 靶点金色球
const tg = new THREE.Group();
const tgMat = new THREE.MeshBasicMaterial({color:0xffd35c});
const tgGeo = new THREE.SphereGeometry(diag*0.01, 14, 14);
for (const t of D.targets){
  const s = new THREE.Mesh(tgGeo, tgMat);
  s.position.set(t.x, t.y, t.z); tg.add(s);
}
scene.add(tg);

// AOI 范围线框盒（地表 y=0 顶面，深部向下到 depth_min_y）
const box = new THREE.Box3(new THREE.Vector3(-W/2, M.depth_min_y, -H/2),
                           new THREE.Vector3( W/2, 0,  H/2));
scene.add(new THREE.Box3Helper(box, 0x3a4d7a));

document.getElementById('cut').addEventListener('input', e => {
  const v = parseFloat(e.target.value);
  document.getElementById('cv').textContent = v.toFixed(2);
  applyCut(v);
});
document.getElementById('psz').addEventListener('input', e => { mat.size = parseFloat(e.target.value); });

addEventListener('resize', () => {
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

window.__viewerOK = true;
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
</script>
</body>
</html>
"""


def write_web_viewer(path: str, score: np.ndarray, uncertainty: np.ndarray, grid,
                     targets: List[Dict], max_points: int = 60000,
                     score_threshold: Optional[float] = None,
                     vexag: float = 3.0, aoi_name: str = "",
                     family: str = "") -> str:
    """有利度体 → 单文件三维查看器 HTML。返回写出路径。"""
    nz, ny, nx = score.shape
    finite = np.isfinite(score)
    if score_threshold is None:
        vals = score[finite]
        thr = float(np.percentile(vals, 92)) if vals.size else 0.0
    else:
        thr = float(score_threshold)
    thr = max(thr, 1e-6)

    mask = finite & (score >= thr)
    zi, yi, xi = np.where(mask)
    svals = score[zi, yi, xi]
    # 超上限→保留 score 最高的 max_points（确定性，保最强信号）
    if zi.size > max_points:
        keep = np.argsort(svals)[::-1][:max_points]
        zi, yi, xi, svals = zi[keep], yi[keep], xi[keep], svals[keep]

    width_m = nx * grid.res_m
    height_m = ny * grid.res_m
    depths = grid.depths()                      # (nz,) 负米
    # 局部中心化坐标：X=东(中心化), Z=北(中心化), Y=深度*vexag(向下为负)
    east = (xi + 0.5) * grid.res_m - 0.5 * width_m
    north = (ny - (yi + 0.5)) * grid.res_m - 0.5 * height_m
    yvert = depths[zi] * vexag                  # 负值=地表下
    X = np.round(east).astype(int)
    Z = np.round(-north).astype(int)            # 北朝 -Z
    Y = np.round(yvert).astype(int)
    S = np.clip(np.round(svals * 255), 0, 255).astype(int)

    # 靶点→同一局部坐标
    tgt_payload = []
    for t in (targets or []):
        rc = grid.lonlat_to_rowcol(t["lon"], t["lat"])
        if rc is None:
            continue
        r, c = rc
        ex = (c + 0.5) * grid.res_m - 0.5 * width_m
        no = (ny - (r + 0.5)) * grid.res_m - 0.5 * height_m
        yv = -abs(float(t.get("depth_m", 0))) * vexag
        tgt_payload.append({"x": round(ex, 1), "y": round(yv, 1), "z": round(-no, 1),
                            "rank": t.get("rank"), "depth_m": t.get("depth_m"),
                            "score": t.get("score"), "unc": t.get("uncertainty"),
                            "lon": t.get("lon"), "lat": t.get("lat")})

    # 点大小（世界单位=米，sizeAttenuation=true）需随网格尺度缩放：
    # 体元水平间距 = res_m，故默认点径取 ~1.5×res_m 以填满体元（否则点远小于体元，
    # 在千米级场景里渲染成亚像素的模糊雾团）。滑杆范围按 res_m 自适应。
    psize = max(1.0, round(1.5 * float(grid.res_m), 1))
    psmin = max(1.0, round(0.3 * float(grid.res_m), 1))
    psmax = round(8.0 * float(grid.res_m), 1)
    pstep = round((psmax - psmin) / 100.0, 2) or 0.5

    depth_span_m = float(abs(depths[-1]) * vexag) if nz else 0.0
    payload = {
        "meta": {
            "aoi": aoi_name, "family": family,
            "width_m": round(float(width_m), 1), "height_m": round(float(height_m), 1),
            "depth_span_m": round(depth_span_m, 1),
            "depth_min_y": round(float(depths[-1] * vexag) if nz else 0.0, 1),
            "res_m": round(float(grid.res_m), 2), "dz_m": float(grid.dz_m),
            "vexag": float(vexag), "thr": round(thr, 4), "n_points": int(X.size),
        },
        "x": X.tolist(), "y": Y.tolist(), "z": Z.tolist(), "s": S.tolist(),
        "targets": tgt_payload,
    }
    data_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    html = (_HTML_TEMPLATE
            .replace("__DATA__", data_json)
            .replace("__TITLE__", _esc(aoi_name or "三维成矿有利度"))
            .replace("__FAMILY__", _esc(family or "-"))
            .replace("__THR__", f"{thr:.2f}")
            .replace("__NPTS__", str(int(X.size)))
            .replace("__NTGT__", str(len(tgt_payload)))
            .replace("__VEXAG__", f"{vexag:g}")
            .replace("__PSIZE__", f"{psize:g}")
            .replace("__PSMIN__", f"{psmin:g}")
            .replace("__PSMAX__", f"{psmax:g}")
            .replace("__PSTEP__", f"{pstep:g}"))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
