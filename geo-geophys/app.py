#!/usr/bin/env python3
"""geo-geophys 物探处理系统 - Flask 应用。

独立服务：上传研究区 KML/KMZ + 选矿种(+IGRF日期/欧拉SI, 可选 ANT 速度体) →
经 broker 取 prospector 磁/重网格 → 位场处理 + 磁源深度 + 速度体接入 → 区域物探解释产品。
"""

import os
import re
import io
import time
import json
import zipfile
import threading
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file

from config.config import Config
from core.geophys_engine import GeophysEngine
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
MINERALS = ["铁", "铜", "钼", "铜钼", "金", "铅锌", "镍", "铬", "钛", "稀土",
            "金刚石", "铀", "锰", "锂", "钨", "锡"]

_REGISTRY = os.path.join(Config.RESULTS_FOLDER, '_tasks.json')


def _registry_load():
    try:
        with open(_REGISTRY, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _registry_save(task_id, result_dir, aoi_name):
    reg = _registry_load()
    reg[task_id] = {'result_dir': result_dir, 'aoi_name': aoi_name}
    os.makedirs(os.path.dirname(_REGISTRY), exist_ok=True)
    tmp = _REGISTRY + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _REGISTRY)


def _resolve_task(task_id):
    if task_id in analysis_tasks:
        return analysis_tasks[task_id]
    reg = _registry_load().get(task_id)
    if not reg or not os.path.isdir(reg.get('result_dir', '')):
        return None
    return {'id': task_id, 'aoi_name': reg.get('aoi_name', task_id),
            'status': 'completed', 'results': {'result_dir': reg['result_dir']}}


@app.route('/')
def index():
    return render_template('index.html', minerals=MINERALS, igrf_date=Config.IGRF_DATE)


@app.route('/api/start', methods=['POST'])
def start_analysis():
    global task_counter
    try:
        file = request.files.get('file')
        mineral = (request.form.get('mineral') or '').strip()
        if not file or not file.filename:
            return jsonify({'success': False, 'message': '请上传研究区文件 (.kml/.kmz)'})
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in REGION_EXTENSIONS:
            return jsonify({'success': False, 'message': '区域文件需为 .kml/.kmz'})

        os.makedirs(Config.TEMP_FOLDER, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]+', '_', file.filename)
        up_path = os.path.join(Config.TEMP_FOLDER, f"{int(time.time())}_{safe_name}")
        file.save(up_path)
        coords = parse_polygon(up_path)
        if not coords:
            return jsonify({'success': False, 'message': '区域文件解析失败'})
        bbox = bbox_of(coords)

        # 可选：ANT/地震速度体上传
        vel_path = None
        vfile = request.files.get('velocity')
        if vfile and vfile.filename:
            vext = vfile.filename.rsplit('.', 1)[-1].lower()
            if vext in ('nc', 'csv'):
                vel_path = os.path.join(Config.TEMP_FOLDER, f"vel_{int(time.time())}_{re.sub(r'[^A-Za-z0-9._-]+','_',vfile.filename)}")
                vfile.save(vel_path)

        aoi_name = (request.form.get('aoi_name') or
                    os.path.splitext(os.path.basename(file.filename))[0]).strip()
        safe_aoi = re.sub(r'[\\/:*?"<>|]+', '_', aoi_name).strip() or f"aoi_{int(time.time())}"
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        task_id = f"geophys_{ts}_{task_counter:03d}"
        task_counter += 1
        run_id = ts + '_' + f"{task_counter:03d}"
        output_dir = os.path.join(Config.RESULTS_FOLDER, safe_aoi, 'geophys', run_id)

        params = {'velocity_path': vel_path}
        v = request.form.get('igrf_date')
        if v:
            params['igrf_date'] = v
        # 欧拉 SI：'auto'/空 → 按矿种自动定（非专家友好）
        si_form = (request.form.get('euler_si') or 'auto').strip()
        params['euler_si'] = (Config.auto_euler_si(mineral) if si_form in ('', 'auto')
                              else float(si_form))
        for k in ('euler_window', 'depth_min_m', 'depth_max_m'):
            v = request.form.get(k)
            if v:
                try:
                    params[k] = float(v)
                except ValueError:
                    pass

        analysis_tasks[task_id] = {'id': task_id, 'aoi_name': aoi_name, 'mineral': mineral,
                                   'bbox': bbox, 'region_file': up_path,
                                   'status': 'running', 'progress': 0,
                                   'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                   'logs': [], 'results': None}

        def run_task():
            def on_log(msg, level='INFO'):
                t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                analysis_tasks[task_id]['logs'].append(f"[{t}] [{level}] {msg}")
                analysis_tasks[task_id]['logs'] = analysis_tasks[task_id]['logs'][-100:]
            try:
                analysis_tasks[task_id]['progress'] = 10
                res = GeophysEngine.run(aoi_name, mineral, bbox, output_dir,
                                        params=params, log_callback=on_log,
                                        roots=Config.upstream_roots())
                analysis_tasks[task_id].update(status='completed', progress=100, results=res,
                                               end_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                try:
                    _registry_save(task_id, output_dir, aoi_name)
                except Exception as e:
                    logger.error(f"注册表写入失败: {e}")
            except Exception as e:
                import traceback
                logger.error(f"物探处理失败: {traceback.format_exc()}")
                analysis_tasks[task_id].update(status='failed', error=str(e), progress=0)
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
    if task is None or task['status'] != 'completed' or not task.get('results'):
        return jsonify({'success': False, 'message': '任务不存在或未完成'}), 404
    result_dir = task['results'].get('result_dir')
    fpath = os.path.normpath(os.path.join(result_dir, filename))
    if not fpath.startswith(os.path.normpath(result_dir)) or not os.path.exists(fpath):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    as_attach = request.args.get('download') in ('1', 'true', 'yes')
    return send_file(fpath, as_attachment=as_attach,
                     download_name=os.path.basename(fpath) if as_attach else None)


@app.route('/api/download/<task_id>')
def download_results(task_id):
    task = _resolve_task(task_id)
    if task is None or task['status'] != 'completed' or not task.get('results'):
        return jsonify({'success': False, 'message': '任务不存在或未完成'}), 404
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
                zf.write(fp, arcname=os.path.join(top, os.path.relpath(fp, result_dir)))
    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True, download_name=f"{top}.zip")


@app.route('/api/chain/<task_id>', methods=['POST'])
def chain_model3d(task_id):
    """一键串联：把本任务的区域+矿种提交给 geo-model3d(8086) 生成三维靶点。"""
    task = _resolve_task(task_id)
    if task is None or task.get('status') != 'completed':
        return jsonify({'success': False, 'message': '任务未完成'}), 400
    region = task.get('region_file')
    mineral = task.get('mineral')
    if not region or not os.path.exists(region):
        return jsonify({'success': False,
                        'message': f'原始区域文件已不可用，请到 geo-model3d({Config.MODEL3D_URL}) 手动上传'}), 400
    try:
        import requests
        with open(region, 'rb') as f:
            r = requests.post(f"{Config.MODEL3D_URL}/api/start",
                              files={'file': (os.path.basename(region), f)},
                              data={'mineral': mineral, 'aoi_name': task.get('aoi_name', '')},
                              timeout=30)
        j = r.json()
        if not j.get('success'):
            return jsonify({'success': False, 'message': 'geo-model3d：' + j.get('message', '失败')}), 502
        return jsonify({'success': True, 'model3d_url': Config.MODEL3D_URL,
                        'model3d_task_id': j['task_id']})
    except Exception as e:
        return jsonify({'success': False,
                        'message': f'无法连接 geo-model3d（{Config.MODEL3D_URL}），请确认它在运行：{e}'}), 502


@app.route('/api/chain_status/<m3d_task_id>')
def chain_status(m3d_task_id):
    """代理 geo-model3d 任务状态（服务端转发，避免浏览器跨域）。"""
    try:
        import requests
        r = requests.get(f"{Config.MODEL3D_URL}/api/status/{m3d_task_id}", timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 502


if __name__ == '__main__':
    Config.create_directories()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG, threaded=True)
