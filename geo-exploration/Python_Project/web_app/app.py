#!/usr/bin/env python3
"""
舒曼波共振遥感矿产预测系统 - Web 界面
基于 Flask + Bootstrap 的专业 Web 应用
"""

import os
import sys
import re
import shutil
from datetime import datetime
import json
import threading
import time
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
from werkzeug.utils import secure_filename
import pandas as pd
import numpy as np
from config.config import Config
from core.mineral_engine import MineralEngine
from utils.file_utils import save_uploaded_file, get_file_size
from utils.logger import get_logger

# 初始化 Flask 应用
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

# 初始化日志
logger = get_logger(__name__, Config.LOG_FILE)

# 全局变量
mineral_engine = None
analysis_tasks = {}
task_counter = 0
uploaded_files = {}  # 跟踪上传的文件

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv', 'kml', 'ovkml', 'kmz', 'tif', 'tiff', 'zip'}

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# 自动归档:分析完成后把交付产物复制到项目内 results/ 固定目录
RESULTS_ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
ARCHIVE_EXCLUDE_EXT = ('.npy', '.mat')  # 多 GB 中间数组不归档


def auto_archive_results(result_dir, roi_name, mineral_type, task_name=''):
    """把 result_dir 里的交付产物(图/KMZ/metadata,排除 .npy/.mat)复制到
    results/<ROI名或任务名>_<矿种>_<run时间戳>/。返回归档目录路径或 None。"""
    try:
        if not result_dir or not os.path.isdir(result_dir):
            return None
        # 子目录命名:优先任务名,否则 ROI 文件名(去扩展名 + 去尾部 _<10位上传时间戳>)
        stem = (task_name or roi_name or 'roi')
        stem = os.path.splitext(stem)[0]
        stem = re.sub(r'_\d{10}$', '', stem)                 # 去掉上传时追加的时间戳后缀
        stem = re.sub(r'[\\/:*?"<>|]+', '_', stem).strip() or 'roi'
        run_ts = os.path.basename(result_dir).replace('mineral_analysis_', '')
        dest = os.path.join(RESULTS_ARCHIVE_DIR, f"{stem}_{mineral_type}_{run_ts}")
        os.makedirs(dest, exist_ok=True)
        copied = []
        for fname in os.listdir(result_dir):
            if fname.lower().endswith(ARCHIVE_EXCLUDE_EXT):
                continue
            src = os.path.join(result_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest, fname))
                copied.append(fname)
        logger.info(f"分析结果已自动归档到: {dest} (共 {len(copied)} 个文件)")
        return dest
    except Exception as e:
        logger.error(f"自动归档结果失败: {e}")
        return None

@app.route('/')
def index():
    """主页"""
    mineral_types = Config.MINERAL_TYPES
    detectors = Config.DETECTORS
    return render_template('index.html', minerals=mineral_types, detectors=detectors)

@app.route('/api/mineral_types')
def get_mineral_types():
    """获取矿物类型列表"""
    mineral_types = Config.MINERAL_TYPES
    return jsonify(mineral_types)

@app.route('/api/debug_session')
def debug_session():
    """调试session内容"""
    return jsonify({
        'session': dict(session),
        'uploads': session.get('uploads', {})
    })

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """文件上传处理"""
    try:
        upload_type = request.form.get('type')
        file = request.files.get('file')

        if not file or not allowed_file(file.filename):
            return jsonify({'success': False, 'message': '文件类型不支持'})

        # 保存文件（保留原始文件名，安全化由 save_uploaded_file 内部处理）
        original_name = file.filename
        file_path = save_uploaded_file(file, original_name, upload_type)

        # 获取文件信息
        file_info = {
            'name': original_name,
            'path': file_path,
            'size': get_file_size(file_path),
            'upload_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        # 保存到会话
        if 'uploads' not in session:
            session['uploads'] = {}
        session['uploads'][upload_type] = file_info
        # 确保session被保存
        session.permanent = True
        session.modified = True

        # 同时保存到全局字典
        uploaded_files[upload_type] = file_info

        # 调试信息
        logger.info(f"Session after upload: {dict(session)}")
        logger.info(f"Session items: {list(session.items())}")
        logger.info(f"Global uploaded_files: {uploaded_files}")

        return jsonify({
            'success': True,
            'message': f'{upload_type} 文件上传成功',
            'file_info': file_info
        })

    except Exception as e:
        logger.error(f"文件上传失败: {str(e)}")
        return jsonify({'success': False, 'message': f'上传失败: {str(e)}'})

@app.route('/api/start_analysis', methods=['POST'])
def start_analysis():
    """开始分析任务"""
    global task_counter, mineral_engine

    try:
        # 获取参数
        data = request.json
        mineral_type = data.get('mineral_type')
        detectors = data.get('detectors', [])
        fusion_mode = data.get('fusion_mode', True)
        kmz_threshold = data.get('kmz_threshold', 0.6)
        task_name = data.get('task_name', '')

        # 外部系统接入配置:默认取 Config,请求可逐键覆盖(默认全关 → 零行为变更)
        alteration_cfg = {**Config.ALTERATION, **(data.get('alteration') or {})}
        structural_cfg = {**Config.STRUCTURAL, **(data.get('structural') or {})}
        insar_fusion_cfg = {**Config.INSAR_FUSION, **(data.get('insar_fusion') or {})}
        # 交付库自动取数配置(请求可覆盖 season 等)
        delivery_cfg = {**Config.DELIVERY, **(data.get('delivery') or {})}

        # 确保session是永久的
        session.permanent = True

        # 验证必要参数
        if not mineral_type:
            return jsonify({'success': False, 'message': '请选择目标矿种'})

        # ROI 坐标文件必填(它既是分析区域,也是交付库自动取数的匹配键)
        if not uploaded_files.get('roi_file'):
            return jsonify({'success': False, 'message': '请上传坐标文件'})

        # 数据目录可选:未上传则按 ROI 从交付库自动拉取(zip 上传作为后备/显式)
        if not uploaded_files.get('data_dir'):
            logger.info("未上传数据目录,将按 ROI 从交付库自动匹配取数")

        # 调试信息
        logger.info(f"Session content in start_analysis: {dict(session)}")
        logger.info(f"Session uploads: {session.get('uploads', {})}")
        logger.info(f"Global uploaded_files: {uploaded_files}")

        # 创建任务ID
        task_id = f"task_{task_counter:04d}"
        task_counter += 1

        # 创建任务记录
        analysis_tasks[task_id] = {
            'id': task_id,
            'status': 'running',
            'progress': 0,
            'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'mineral_type': mineral_type,
            'detectors': detectors,
            'fusion_mode': fusion_mode,
            'kmz_threshold': kmz_threshold,
            'task_name': task_name,
            'logs': [],
            'results': None
        }

        # 启动分析线程
        def run_analysis(session_data, task_config):
            try:
                # 初始化引擎
                local_mineral_engine = MineralEngine()

                # 重写 log 方法，将日志实时添加到任务记录
                original_log = local_mineral_engine.log
                def realtime_log(message: str, level: str = 'INFO'):
                    # 调用原始 log 方法（打印到控制台）
                    original_log(message, level)
                    # 同时添加到任务记录（实时）
                    if task_id in analysis_tasks:
                        if 'logs' not in analysis_tasks[task_id]:
                            analysis_tasks[task_id]['logs'] = []
                        analysis_tasks[task_id]['logs'].append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}")

                local_mineral_engine.log = realtime_log

                # 准备配置
                config = {
                    'mineral_type': task_config['mineral_type'],
                    'detectors': task_config['detectors'],
                    'fusion_mode': task_config['fusion_mode'],
                    'kmz_threshold': task_config['kmz_threshold'],
                    'data_dir': (uploaded_files.get('data_dir') or {}).get('path', ''),
                    'roi_file': uploaded_files['roi_file']['path'],
                    'kmz_path': uploaded_files.get('kml_file', {}).get('path', ''),
                    'out_dir': os.path.join(Config.UPLOAD_FOLDER, f"results_{task_id}"),
                    'alteration': task_config.get('alteration', {}),
                    'structural': task_config.get('structural', {}),
                    'insar_fusion': task_config.get('insar_fusion', {}),
                    'delivery': task_config.get('delivery', {}),
                    'tenant_id': task_config.get('tenant_id'),
                }

                # 确保输出目录存在
                os.makedirs(config['out_dir'], exist_ok=True)

                # 执行分析
                logs, results = local_mineral_engine.run_analysis(config)

                # 更新任务状态
                analysis_tasks[task_id]['status'] = 'completed'
                analysis_tasks[task_id]['progress'] = 100
                analysis_tasks[task_id]['logs'] = logs
                analysis_tasks[task_id]['results'] = results
                analysis_tasks[task_id]['end_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                # 完成后自动把交付产物归档到项目内 results/ 固定目录
                archived = auto_archive_results(
                    results.get('result_dir'),
                    (uploaded_files.get('roi_file') or {}).get('name', ''),
                    analysis_tasks[task_id].get('mineral_type', ''),
                    analysis_tasks[task_id].get('task_name', ''),
                )
                if archived:
                    analysis_tasks[task_id]['archived_dir'] = archived
                    if 'logs' in analysis_tasks[task_id]:
                        analysis_tasks[task_id]['logs'].append(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] 结果已自动保存到本地: {archived}")

            except Exception as e:
                logger.error(f"分析任务失败: {str(e)}")
                import traceback
                error_detail = traceback.format_exc()
                logger.error(error_detail)
                analysis_tasks[task_id]['status'] = 'failed'
                analysis_tasks[task_id]['error'] = str(e)
                analysis_tasks[task_id]['error_detail'] = error_detail
                analysis_tasks[task_id]['end_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                # 添加错误日志
                if 'logs' not in analysis_tasks[task_id]:
                    analysis_tasks[task_id]['logs'] = []
                analysis_tasks[task_id]['logs'].append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] {str(e)}")

        # 准备要传递给线程的数据
        thread_session_data = {
            'uploads': session.get('uploads', {}),
            'session_id': session.sid if hasattr(session, 'sid') else None
        }
        thread_task_config = {
            'mineral_type': mineral_type,
            'detectors': detectors,
            'fusion_mode': fusion_mode,
            'kmz_threshold': kmz_threshold,
            'alteration': alteration_cfg,
            'structural': structural_cfg,
            'insar_fusion': insar_fusion_cfg,
            'delivery': delivery_cfg,
            'tenant_id': request.headers.get('X-Tenant-Id')
        }

        # 启动后台线程，传递session数据和任务配置
        analysis_thread = threading.Thread(
            target=run_analysis,
            args=(thread_session_data, thread_task_config)
        )
        analysis_thread.daemon = True
        analysis_thread.start()

        return jsonify({
            'success': True,
            'message': '分析任务已启动',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"启动分析失败: {str(e)}")
        return jsonify({'success': False, 'message': f'启动失败: {str(e)}'})

@app.route('/api/task_status/<task_id>')
def get_task_status(task_id):
    """获取任务状态"""
    if task_id not in analysis_tasks:
        return jsonify({'success': False, 'message': '任务不存在'})

    task = analysis_tasks[task_id]

    # 模拟进度更新（实际应用中应该从分析引擎获取真实进度）
    if task['status'] == 'running':
        task['progress'] = min(task['progress'] + 5, 95)

    # 构建可序列化的任务副本 (排除不可 JSON 序列化的字段)
    task_copy = {}
    for k, v in task.items():
        if k == 'results' and isinstance(v, dict):
            results_copy = {}
            for rk, rv in v.items():
                if rk == 'post_data':
                    continue  # numpy 数组不可 JSON 序列化，跳过
                try:
                    import json
                    json.dumps(rv)
                    results_copy[rk] = rv
                except (TypeError, ValueError):
                    results_copy[rk] = str(rv)
            task_copy[k] = results_copy
        else:
            task_copy[k] = v

    return jsonify({
        'success': True,
        'task': task_copy
    })

@app.route('/api/download/<task_id>')
def download_results(task_id):
    """下载分析结果"""
    if task_id not in analysis_tasks:
        return jsonify({'success': False, 'message': '任务不存在'})

    task = analysis_tasks[task_id]
    if task['status'] != 'completed':
        return jsonify({'success': False, 'message': '任务未完成'})

    result_path = task['results'].get('result_dir')
    if not result_path or not os.path.exists(result_path):
        return jsonify({'success': False, 'message': '结果文件不存在'})

    # 打包结果为 zip 文件
    # 默认排除多 GB 的全分辨率中间数组(.npy/.mat),只打包交付产物(图/KMZ/metadata),
    # 否则整目录可达 10+GB,HTTP 下载会超时。需要原始数组可加 ?full=1。
    import zipfile
    full = request.args.get('full') in ('1', 'true', 'yes')
    EXCLUDE_EXT = () if full else ('.npy', '.mat')
    zip_path = os.path.join(Config.UPLOAD_FOLDER, f"{task_id}_results.zip")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(result_path):
            for file in files:
                if file.lower().endswith(EXCLUDE_EXT):
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, result_path)
                zipf.write(file_path, arcname)

    return send_file(zip_path, as_attachment=True,
                     download_name=f"mineral_analysis_{task_id}.zip")

@app.route('/api/logs/<task_id>')
def get_logs(task_id):
    """获取任务日志"""
    if task_id not in analysis_tasks:
        return jsonify({'success': False, 'message': '任务不存在'})

    task = analysis_tasks[task_id]
    logs = task.get('logs', [])

    return jsonify({
        'success': True,
        'logs': logs
    })

@app.route('/api/clear_uploads')
def clear_uploads():
    """清除上传的文件"""
    session.pop('uploads', None)
    session.modified = True
    uploaded_files.clear()
    return jsonify({'success': True, 'message': '已清除所有上传文件'})


@app.route('/api/debug_intrinsic/<task_id>')
def debug_intrinsic(task_id):
    """调试本征吸收掩码"""
    if task_id not in analysis_tasks:
        return jsonify({'success': False, 'message': '任务不存在'})

    task = analysis_tasks[task_id]
    if task['status'] != 'completed':
        return jsonify({'success': False, 'message': '任务未完成'})

    from core.detectors.intrinsic_detector import IntrinsicDetector
    import numpy as np

    # 获取探测器结果
    engine = task.get('_engine')
    if engine and 'intrinsic' in engine.results:
        res = engine.results['intrinsic']
        mask = res.mask
        debug = res.debug_data
        roi = task['results'].get('post_data', {}).get('inROI')

        info = {
            'mask_dtype': str(mask.dtype),
            'mask_shape': list(mask.shape),
            'mask_min': float(np.nanmin(mask)),
            'mask_max': float(np.nanmax(mask)),
            'mask_mean': float(np.nanmean(mask)),
            'mask_nonzero': int(np.count_nonzero(mask)),
            'mask_total': int(mask.size),
            'F_abs_dtype': str(debug.get('F_abs', np.array([])).dtype),
        }
        if roi is not None:
            mask_roi = mask[roi.astype(bool)]
            info['mask_roi_mean'] = float(np.nanmean(mask_roi))
            info['mask_roi_max'] = float(np.nanmax(mask_roi))
            info['mask_roi_gt001'] = int(np.sum(mask_roi > 0.01))
            info['mask_roi_gt01'] = int(np.sum(mask_roi > 0.1))
            info['F_abs_roi_mean'] = float(np.nanmean(debug['F_abs'][roi.astype(bool)]))
            info['F_abs_roi_max'] = float(np.nanmax(debug['F_abs'][roi.astype(bool)]))
            info['F_abs_roi_min'] = float(np.nanmin(debug['F_abs'][roi.astype(bool)]))

        return jsonify({'success': True, 'debug': info})

    # 从 mat 文件读取
    from scipy.io import loadmat
    import glob
    mat_files = glob.glob(os.path.join(Config.UPLOAD_FOLDER, f"results_{task_id}", '**', '*_Result.mat'), recursive=True)
    if not mat_files:
        return jsonify({'success': False, 'message': '无 mat 文件'})

    mat = loadmat(mat_files[0])
    fab = mat.get('anomaly_mask_fabs', np.array([]))
    roi = mat.get('inROI', np.array([]))

    info = {
        'source': 'mat_file',
        'mask_dtype': str(fab.dtype),
        'mask_shape': list(fab.shape),
        'mask_min': float(np.nanmin(fab)),
        'mask_max': float(np.nanmax(fab)),
        'mask_mean': float(np.nanmean(fab)),
        'mask_nonzero': int(np.count_nonzero(fab)),
    }
    if roi.size > 0:
        fab_roi = fab[roi.astype(bool)]
        info['mask_roi_mean'] = float(np.nanmean(fab_roi))
        info['mask_roi_max'] = float(np.nanmax(fab_roi))
        info['mask_roi_gt001'] = int(np.sum(fab_roi > 0.01))
        info['mask_roi_gt01'] = int(np.sum(fab_roi > 0.1))

    return jsonify({'success': True, 'debug': info})


@app.route('/api/results/<task_id>/<filename>')
def get_result_file(task_id, filename):
    """获取结果文件（图片等）"""
    if task_id not in analysis_tasks:
        return jsonify({'success': False, 'message': '任务不存在'}), 404

    task = analysis_tasks[task_id]
    if task['status'] != 'completed':
        return jsonify({'success': False, 'message': '任务未完成'}), 400

    result_dir = task['results'].get('result_dir')
    if not result_dir or not os.path.exists(result_dir):
        return jsonify({'success': False, 'message': '结果目录不存在'}), 404

    file_path = os.path.join(result_dir, filename)
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': '文件不存在'}), 404

    return send_file(file_path)
if __name__ == '__main__':
    # 创建必要的目录
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(Config.UPLOAD_FOLDER, 'temp'), exist_ok=True)

    # 启动 Flask 应用
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        threaded=True
    )