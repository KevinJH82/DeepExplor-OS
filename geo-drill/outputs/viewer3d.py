"""三维布孔交互查看器 —— 单文件自包含 HTML（three.js 经 CDN）。

有利度体下采样为点云（按 score 着色，作语境），**计划孔作彩色钻杆**从地表向地下延伸
（按预测得分着色）+ 孔口球 + 见矿(绿)/无矿(红)标记。拖拽旋转/滚轮缩放。
零 pip 依赖（纯 json+numpy+pyproj）；渲染需联网取 three.js CDN（无网时本服务另出静态 3D PNG）。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

import numpy as np
from pyproj import Transformer


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 三维布孔</title>
<style>
 html,body{margin:0;height:100%;background:#0b1020;color:#dfe7f5;font-family:system-ui,"PingFang SC",sans-serif;overflow:hidden}
 #c{position:fixed;inset:0;display:block}
 #hud{position:fixed;left:12px;top:12px;font-size:13px;line-height:1.6;background:rgba(12,18,38,.72);
      border:1px solid #28324f;border-radius:8px;padding:10px 14px;max-width:280px}
 #hud b{color:#ffd35c}
 #lg{position:fixed;right:12px;top:12px;background:rgba(12,18,38,.72);border:1px solid #28324f;
     border-radius:8px;padding:10px 12px;font-size:12px}
 #lg .bar{height:10px;width:150px;border-radius:5px;margin:6px 0;
     background:linear-gradient(90deg,#0d0887,#7e03a8,#cc4778,#f89540,#f0f921)}
 #err{position:fixed;inset:0;display:none;align-items:center;justify-content:center;
      background:rgba(8,12,26,.95);text-align:center;padding:24px;font-size:15px;line-height:1.8}
 .row{display:flex;justify-content:space-between;gap:14px}
 label{font-size:12px;color:#9fb0d0}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud"><div><b>__TITLE__</b></div>
 <div class="row"><span>计划孔</span><span>__NHOLE__</span></div>
 <div class="row"><span>有利度体元</span><span id="nshow">__NPTS__</span></div>
 <div class="row"><span>见矿/无矿</span><span>__NFB__</span></div>
 <div style="margin-top:8px"><label>有利度下限 <span id="cv">__THR__</span></label><br>
   <input id="cut" type="range" min="0" max="1" step="0.01" value="__THR__" style="width:150px"></div>
 <div style="margin-top:6px"><label><input type="checkbox" id="lbl" checked> 显示孔标注（编号/深度/见矿）</label></div>
 <div style="margin-top:6px;color:#7e8db0;font-size:11px">拖拽旋转 · 滚轮缩放 · 右键平移 · 垂向夸大 ×__VEXAG__</div></div>
<div id="lg">计划孔得分<div class="bar"></div><div class="row"><span>低</span><span>高</span></div>
 <div style="margin-top:6px">▮ 钻杆=计划孔 · ●白=孔口</div>
 <div style="color:#2ecc71">★ 见矿</div><div style="color:#e74c3c">✕ 无矿</div>
 <div style="margin-top:4px;color:#7e8db0">点云=三维有利度(语境)</div></div>
<div id="err">⚠ 三维查看器需联网加载 three.js (CDN)。无网时请看静态 3D 图 siting_3d.png。</div>
<script type="application/json" id="d">__DATA__</script>
<script>window.__ok=false;setTimeout(function(){if(!window.__ok)document.getElementById('err').style.display='flex';},4000);</script>
<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D=JSON.parse(document.getElementById('d').textContent),M=D.meta;
const canvas=document.getElementById('c');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setSize(innerWidth,innerHeight);
const scene=new THREE.Scene();scene.background=new THREE.Color(0x0b1020);
const camera=new THREE.PerspectiveCamera(55,innerWidth/innerHeight,0.1,1e8);
const W=M.width_m,H=M.height_m,Dz=M.depth_span_m||1,diag=Math.sqrt(W*W+H*H+Dz*Dz);
camera.position.set(W*0.85,Dz*1.2+H*0.4,H*1.05);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
controls.target.set(0,M.depth_min_y*0.5,0);
scene.add(new THREE.AmbientLight(0xffffff,0.95));
const dl=new THREE.DirectionalLight(0xffffff,0.5);dl.position.set(1,1,1);scene.add(dl);
function turbo(t){t=Math.max(0,Math.min(1,t));
 const r=Math.max(0,Math.min(1,34.61+t*(1172.33+t*(-10793.56+t*(33300.12+t*(-38394.49+t*14825.05))))))/255;
 const g=Math.max(0,Math.min(1,23.31+t*(557.33+t*(1225.33+t*(-3574.96+t*(3520.0+t*-1234.65))))))/255;
 const b=Math.max(0,Math.min(1,27.2+t*(3211.1+t*(-15327.97+t*(27814.0+t*(-22569.18+t*6838.66))))))/255;
 return [r,g,b];}
function plasma(t){t=Math.max(0,Math.min(1,t));  // 计划孔配色(与点云区分)
 const stops=[[13,8,135],[126,3,168],[204,71,120],[248,149,64],[240,249,33]];
 const f=t*4,i=Math.min(3,Math.floor(f)),k=f-i,a=stops[i],b=stops[i+1];
 return [(a[0]+(b[0]-a[0])*k)/255,(a[1]+(b[1]-a[1])*k)/255,(a[2]+(b[2]-a[2])*k)/255];}
// 点云(语境，半透明)
const n=D.x.length,sArr=D.s,geo=new THREE.BufferGeometry();
const mat=new THREE.PointsMaterial({size:M.res_m*0.9,vertexColors:true,transparent:true,opacity:0.45,sizeAttenuation:true});
scene.add(new THREE.Points(geo,mat));
function applyCut(cut){let m=0;for(let i=0;i<n;i++)if(sArr[i]/255>=cut)m++;
 const p=new Float32Array(m*3),c=new Float32Array(m*3);let j=0;
 for(let i=0;i<n;i++){if(sArr[i]/255<cut)continue;p[j*3]=D.x[i];p[j*3+1]=D.y[i];p[j*3+2]=D.z[i];
  const cc=turbo(sArr[i]/255);c[j*3]=cc[0];c[j*3+1]=cc[1];c[j*3+2]=cc[2];j++;}
 geo.setAttribute('position',new THREE.BufferAttribute(p,3));
 geo.setAttribute('color',new THREE.BufferAttribute(c,3));
 document.getElementById('nshow').textContent=m;}
applyCut(parseFloat(M.thr));
// 文本标注：面向相机的 sprite（编号 · 深度 · 见矿）
const labelGroup=new THREE.Group();scene.add(labelGroup);
function makeLabel(text,hex){
 const cv=document.createElement('canvas'),ctx=cv.getContext('2d');
 ctx.font='26px sans-serif';const w=Math.ceil(ctx.measureText(text).width)+18;cv.width=w;cv.height=36;
 ctx.font='26px sans-serif';ctx.fillStyle='rgba(8,12,26,0.85)';ctx.fillRect(0,0,w,36);
 ctx.strokeStyle=hex;ctx.lineWidth=2;ctx.strokeRect(1,1,w-2,34);
 ctx.fillStyle=hex;ctx.textBaseline='middle';ctx.fillText(text,9,19);
 const tex=new THREE.CanvasTexture(cv);tex.minFilter=THREE.LinearFilter;
 const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:tex,depthTest:false,transparent:true}));
 const sc=diag*0.0011;sp.scale.set(w*sc,36*sc,1);return sp;}
const ocColor={'见矿':'#2ecc71','无矿':'#e74c3c','未钻':'#cfe0ff'};
// 计划孔=钻杆(粗线+圆柱)+孔口球+标注
const collarGeo=new THREE.SphereGeometry(diag*0.008,12,12);
for(const h of D.holes){
 const col=plasma(h.t);const cc=new THREE.Color(col[0],col[1],col[2]);
 const a=new THREE.Vector3(h.x,0,h.z);
 const b=new THREE.Vector3(h.bx!=null?h.bx:h.x,h.yb,h.bz!=null?h.bz:h.z);  // 斜孔孔底
 scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([a,b]),new THREE.LineBasicMaterial({color:cc})));
 const dir=new THREE.Vector3().subVectors(b,a),len=dir.length();
 const cyl=new THREE.Mesh(new THREE.CylinderGeometry(diag*0.0028,diag*0.0028,len,8),new THREE.MeshBasicMaterial({color:cc}));
 cyl.position.copy(a).add(b).multiplyScalar(0.5);
 cyl.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),dir.clone().normalize());scene.add(cyl);
 const s=new THREE.Mesh(collarGeo,new THREE.MeshBasicMaterial({color:0xffffff}));s.position.copy(a);scene.add(s);
 const dipTag=(Math.abs(h.dip+90)>1)?(' '+h.dip+'°'):'';
 const lab=makeLabel(h.id+' · '+h.depth+'m · '+h.oc+dipTag, ocColor[h.oc]||'#cfe0ff');
 lab.position.set(h.x,diag*0.04,h.z);labelGroup.add(lab);}
document.getElementById('lbl').addEventListener('change',e=>{labelGroup.visible=e.target.checked;});
// 见矿/无矿
for(const f of D.feedback){
 const c=f.o==='ore'?0x2ecc71:0xe74c3c;
 const s=new THREE.Mesh(new THREE.SphereGeometry(diag*0.011,12,12),new THREE.MeshBasicMaterial({color:c}));
 s.position.set(f.x,diag*0.01,f.z);scene.add(s);}
// AOI 盒
scene.add(new THREE.Box3Helper(new THREE.Box3(new THREE.Vector3(-W/2,M.depth_min_y,-H/2),
 new THREE.Vector3(W/2,0,H/2)),0x3a4d7a));
document.getElementById('cut').addEventListener('input',e=>{const v=parseFloat(e.target.value);
 document.getElementById('cv').textContent=v.toFixed(2);applyCut(v);});
addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();
 renderer.setSize(innerWidth,innerHeight);});
window.__ok=true;(function loop(){requestAnimationFrame(loop);controls.update();renderer.render(scene,camera);})();
</script></body></html>"""


def write_drill_viewer(path: str, fav: Dict, holes: List[Dict], judged: List[Dict] = None,
                       max_points: int = 45000, vexag: float = 3.0, aoi_name: str = "") -> str:
    """计划孔 + 有利度体 → 单文件三维交互查看器 HTML。"""
    prosp = fav["prospectivity"]
    x = np.asarray(fav["x"], float); y = np.asarray(fav["y"], float)
    depth_m = np.asarray(fav["depth_m"], float)
    res_m = float(fav.get("res_m", 30.0)); epsg = int(fav["epsg"])
    nz, ny, nx = prosp.shape
    width_m = nx * res_m; height_m = ny * res_m
    cx, cy = 0.5 * (x[0] + x[-1]), 0.5 * (y[0] + y[-1])
    xmin_edge = x[0] - 0.5 * res_m; ymax_edge = y[0] + 0.5 * res_m

    finite = np.isfinite(prosp)
    vals = prosp[finite]
    thr = float(np.percentile(vals, 90)) if vals.size else 0.0
    thr = max(thr, 1e-6)
    mask = finite & (prosp >= thr)
    zi, yi, xi = np.where(mask)
    sv = prosp[zi, yi, xi]
    if zi.size > max_points:
        keep = np.argsort(sv)[::-1][:max_points]
        zi, yi, xi, sv = zi[keep], yi[keep], xi[keep], sv[keep]
    east = (xi + 0.5) * res_m - 0.5 * width_m
    north = (ny - (yi + 0.5)) * res_m - 0.5 * height_m
    X = np.round(east).astype(int)
    Z = np.round(-north).astype(int)
    Y = np.round(depth_m[zi] * vexag).astype(int)
    S = np.clip(np.round(sv * 255), 0, 255).astype(int)

    tr = Transformer.from_crs(4326, epsg, always_xy=True)

    def _en(lon, lat):
        ux, uy = tr.transform(lon, lat)
        col = (ux - xmin_edge) / res_m
        row = (ymax_edge - uy) / res_m
        e = col * res_m - 0.5 * width_m
        no = (ny - row) * res_m - 0.5 * height_m
        return round(float(e), 1), round(float(-no), 1)

    svals = [h.get("score", 0) for h in holes] or [0]
    lo, hi = min(svals), max(svals)
    oc_map = {r.get("hole_id"): r.get("outcome") for r in (judged or [])}
    _oc_cn = {"ore": "见矿", "barren": "无矿"}
    holes_payload = []
    for h in holes:
        ex, zz = _en(h["lon"], h["lat"])
        traj = h.get("trajectory") or []
        if len(traj) >= 2:                        # 斜孔：孔底取轨迹终点的局部坐标
            bx, bz = _en(traj[-1]["lon"], traj[-1]["lat"])
        else:
            bx, bz = ex, zz
        t = (h.get("score", 0) - lo) / (hi - lo + 1e-9)
        holes_payload.append({"x": ex, "z": zz, "bx": bx, "bz": bz,
                              "yb": round(-abs(h.get("target_depth_m", 0)) * vexag, 1),
                              "t": round(float(t), 3), "rank": h.get("rank"),
                              "hole_id": h.get("hole_id"), "priority": h.get("priority"),
                              "id": h.get("hole_id"), "depth": int(abs(h.get("target_depth_m", 0))),
                              "dip": int(h.get("dip_deg", -90)),
                              "oc": _oc_cn.get(oc_map.get(h.get("hole_id")), "未钻")})
    fb_payload = []
    for r in (judged or []):
        if r.get("outcome") in ("ore", "barren") and r.get("lon") is not None:
            ex, zz = _en(r["lon"], r["lat"])
            fb_payload.append({"x": ex, "z": zz, "o": r["outcome"]})

    depth_span_m = float(abs(depth_m[-1]) * vexag) if nz else 0.0
    payload = {
        "meta": {"aoi": aoi_name, "width_m": round(width_m, 1), "height_m": round(height_m, 1),
                 "depth_span_m": round(depth_span_m, 1),
                 "depth_min_y": round(float(depth_m[-1] * vexag) if nz else 0.0, 1),
                 "res_m": round(res_m, 2), "vexag": float(vexag),
                 "thr": round(thr, 4), "n_points": int(X.size)},
        "x": X.tolist(), "y": Y.tolist(), "z": Z.tolist(), "s": S.tolist(),
        "holes": holes_payload, "feedback": fb_payload,
    }
    html = (_HTML.replace("__DATA__", json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
            .replace("__TITLE__", _esc(aoi_name or "三维布孔"))
            .replace("__NHOLE__", str(len(holes_payload)))
            .replace("__NPTS__", str(int(X.size)))
            .replace("__NFB__", str(len(fb_payload)))
            .replace("__THR__", f"{thr:.2f}")
            .replace("__VEXAG__", f"{vexag:g}"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
