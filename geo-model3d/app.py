#!/usr/bin/env python3
"""geo-model3d 三维地质建模与立体成矿预测系统 - Flask 应用。

独立服务：上传 KML/KMZ + 选矿种 → 经 broker 自动拉齐上游产物 → 出三维有利度体/靶点。
"""

import os
import re
import io
import time
import zipfile
import threading
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file

from config.config import Config
from core.model3d_engine import Model3DEngine
from utils.geom import parse_polygon, bbox_of
from utils.logger import get_logger

app = Flask(__name__)
# ── 内部鉴权:拒绝绕过 BFF 的直连(PORTAL_INTERNAL_KEY 配置后生效) ──
try:
    import sys as _ia_sys
    if '/opt/deepexplor-services' not in _ia_sys.path:
        _ia_sys.path.insert(0, '/opt/deepexplor-services')
    from commons.internal_auth import init_internal_auth as _init_internal_auth
    _init_internal_auth(app)
except Exception as _ia_e:
    print(f'[internal_auth] 跳过接入: {_ia_e}')
app.secret_key = Config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH

logger = get_logger(__name__, Config.LOG_FILE)

task_counter = 0
analysis_tasks = {}

REGION_EXTENSIONS = {'kml', 'kmz', 'ovkml'}

MINERALS = ["铜", "钼", "铜钼", "金", "银", "铅锌", "铁", "钨", "锡", "锂",
            "镍", "钴", "稀土", "铀", "锑", "汞", "萤石", "锰", "铝土", "金刚石",
            "石油", "天然气"]

# ── 任务注册表持久化（让结果在服务重启/调试重载后仍可下载）──
_REGISTRY = os.path.join(Config.RESULTS_FOLDER, '_tasks.json')


def _registry_load() -> dict:
    try:
        import json as _json
        with open(_REGISTRY, 'r', encoding='utf-8') as f:
            return _json.load(f)
    except Exception:
        return {}


def _registry_save(task_id: str, result_dir: str, aoi_name: str):
    import json as _json
    reg = _registry_load()
    reg[task_id] = {'result_dir': result_dir, 'aoi_name': aoi_name}
    os.makedirs(os.path.dirname(_REGISTRY), exist_ok=True)
    tmp = _REGISTRY + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        _json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _REGISTRY)


def _resolve_task(task_id: str):
    """取任务：优先内存；内存没有(重启后)则从磁盘注册表重建一个最小完成态任务。"""
    if task_id in analysis_tasks:
        return analysis_tasks[task_id]
    reg = _registry_load().get(task_id)
    if reg:
        result_dir = reg.get('result_dir')
        if not result_dir or not os.path.isdir(result_dir):
            return None
        return {'id': task_id, 'aoi_name': reg.get('aoi_name', task_id),
                'status': 'completed', 'results': {'result_dir': result_dir}}
    # 注册表无此 id → 兜底:把 id 当作 run_id(产物目录名)直接定位
    # (前端从"已有产物"加载时用 run_id 当 taskId,需能据此取 viewer/产物)
    import glob as _glob
    for d in _glob.glob(os.path.join(Config.RESULTS_FOLDER, '*', 'model3d', task_id)):
        if os.path.isdir(d):
            return {'id': task_id, 'aoi_name': os.path.basename(os.path.dirname(os.path.dirname(d))),
                    'status': 'completed', 'results': {'result_dir': d}}
    return None


@app.route('/')
def index():
    return render_template('index.html', minerals=MINERALS)


@app.route('/api/start', methods=['POST'])
def start_analysis():
    """提交建模任务：表单含区域文件(file) + 矿种(mineral) [+ 可选网格参数]。"""
    global task_counter
    try:
        file = request.files.get('file')
        mineral = (request.form.get('mineral') or '').strip()
        if not file or not file.filename:
            return jsonify({'success': False, 'message': '请上传区域文件 (.kml/.kmz)'})
        if not mineral:
            return jsonify({'success': False, 'message': '请选择矿种'})
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in REGION_EXTENSIONS:
            return jsonify({'success': False, 'message': '区域文件需为 .kml/.kmz'})

        # 保存上传文件
        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]+', '_', file.filename)
        up_path = os.path.join(Config.TEMP_FOLDER, f"{int(time.time())}_{safe_name}")
        file.save(up_path)

        # 解析 bbox
        coords = parse_polygon(up_path)
        if not coords:
            return jsonify({'success': False, 'message': '区域文件解析失败：未找到多边形'})
        bbox = bbox_of(coords)

        aoi_name = (request.form.get('aoi_name') or
                    os.path.splitext(os.path.basename(file.filename))[0]).strip()
        safe_aoi = re.sub(r'[\\/:*?"<>|]+', '_', aoi_name).strip() or f"aoi_{int(time.time())}"

        # task_id 含时间戳 → 跨重启唯一，避免注册表碰撞
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        task_id = f"model3d_{ts}_{task_counter:03d}"
        task_counter += 1
        run_id = ts + '_' + f"{task_counter:03d}"
        output_dir = os.path.join(Config.RESULTS_FOLDER, safe_aoi, 'model3d', run_id)

        params = {}
        for k in ('res_m', 'z_max_m', 'dz_m', 'top_n'):
            v = request.form.get(k)
            if v:
                try:
                    params[k] = float(v) if k != 'top_n' else int(float(v))
                except ValueError:
                    pass
        # 2D 证据融合方法（P2 特性B）：knowledge|fuzzy|bayesian
        fm = (request.form.get('fusion_method') or '').strip().lower()
        if fm in ('knowledge', 'fuzzy', 'bayesian'):
            params['fusion_method'] = fm
        # 可选上传：方向四已知矿点标签 / 方向五钻孔回灌反馈（引擎已支持对应 *_path 参数）
        for field, pkey in (('known_deposits', 'known_deposits_path'), ('drill_feedback', 'drill_feedback_path')):
            uf = request.files.get(field)
            if uf and uf.filename:
                sn = re.sub(r'[^A-Za-z0-9._\-]+', '_', uf.filename)
                up = os.path.join(Config.TEMP_FOLDER, f"{field}_{int(time.time())}_{sn}")
                uf.save(up)
                params[pkey] = up

        analysis_tasks[task_id] = {
            'id': task_id, 'aoi_name': aoi_name, 'mineral': mineral, 'bbox': bbox,
            'status': 'running', 'progress': 0,
            'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'logs': [], 'results': None,
        }

        def run_task():
            def on_log(msg, level='INFO'):
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                analysis_tasks[task_id]['logs'].append(f"[{ts}] [{level}] {msg}")
                analysis_tasks[task_id]['logs'] = analysis_tasks[task_id]['logs'][-100:]
            try:
                analysis_tasks[task_id]['progress'] = 10
                res = Model3DEngine.run(aoi_name, mineral, bbox, output_dir,
                                        params=params, log_callback=on_log,
                                        roots=Config.upstream_roots())
                analysis_tasks[task_id]['status'] = 'completed'
                analysis_tasks[task_id]['progress'] = 100
                analysis_tasks[task_id]['results'] = res
                analysis_tasks[task_id]['end_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                try:
                    _registry_save(task_id, output_dir, aoi_name)
                except Exception as e:
                    logger.error(f"注册表写入失败: {e}")
            except Exception as e:
                import traceback
                logger.error(f"建模失败: {traceback.format_exc()}")
                analysis_tasks[task_id]['status'] = 'failed'
                analysis_tasks[task_id]['error'] = str(e)
                analysis_tasks[task_id]['progress'] = 0
                on_log(str(e), 'ERROR')

        params['tenant_id'] = request.headers.get('X-Tenant-Id')
        threading.Thread(target=run_task, daemon=True).start()
        return jsonify({'success': True, 'task_id': task_id, 'bbox': bbox})

    except Exception as e:
        logger.error(f"启动失败: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/status/<task_id>')
def task_status(task_id):
    task = _resolve_task(task_id)
    if task is None:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    if task.get('status') == 'running' and task.get('progress', 0) < 90:
        task['progress'] = min(task.get('progress', 0) + 8, 90)
    return jsonify({'success': True, 'task': task})


@app.route('/api/result/<task_id>/<path:filename>')
def result_file(task_id, filename):
    task = _resolve_task(task_id)
    if task is None:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    if task['status'] != 'completed' or not task.get('results'):
        return jsonify({'success': False, 'message': '任务未完成'}), 400
    result_dir = task['results'].get('result_dir')
    fpath = os.path.normpath(os.path.join(result_dir, filename))
    if not fpath.startswith(os.path.normpath(result_dir)) or not os.path.exists(fpath):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    as_attach = request.args.get('download') in ('1', 'true', 'yes')
    return send_file(fpath, as_attachment=as_attach,
                     download_name=os.path.basename(fpath) if as_attach else None)


@app.route('/api/slices/<task_id>')
def list_slices(task_id):
    """列出某任务的深度切片 GeoTIFF（按深度升序），供前端生成逐层下载链接。"""
    task = _resolve_task(task_id)
    if task is None:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    if task['status'] != 'completed' or not task.get('results'):
        return jsonify({'success': False, 'message': '任务未完成'}), 400
    result_dir = task['results'].get('result_dir')
    slice_dir = os.path.join(result_dir, 'depth_slices')
    slices = []
    if os.path.isdir(slice_dir):
        for fn in os.listdir(slice_dir):
            if not fn.endswith('.tif'):
                continue
            m = re.search(r'-(\d+)m', fn)
            depth = int(m.group(1)) if m else 0
            slices.append({'depth_m': depth, 'filename': fn,
                           'rel': f'depth_slices/{fn}'})
    slices.sort(key=lambda s: s['depth_m'])
    return jsonify({'success': True, 'slices': slices})


@app.route('/api/download/<task_id>')
def download_results(task_id):
    """把整个结果目录打包成 ZIP 下载（含 NetCDF 体/深度切片/靶点/图件/metadata）。"""
    task = _resolve_task(task_id)
    if task is None:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    if task['status'] != 'completed' or not task.get('results'):
        return jsonify({'success': False, 'message': '任务未完成'}), 400
    result_dir = task['results'].get('result_dir')
    if not result_dir or not os.path.isdir(result_dir):
        return jsonify({'success': False, 'message': '结果目录不存在'}), 404

    safe_aoi = re.sub(r'[\\/:*?"<>|]+', '_', task.get('aoi_name') or task_id).strip() or task_id
    top = f"{safe_aoi}_{task_id}"
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(result_dir):
            for fn in files:
                fp = os.path.join(root, fn)
                arc = os.path.join(top, os.path.relpath(fp, result_dir))
                zf.write(fp, arcname=arc)
    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True,
                     download_name=f"{top}.zip")


if __name__ == '__main__':
    Config.create_directories()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG, threaded=True)
