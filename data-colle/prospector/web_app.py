#!/usr/bin/env python3
"""Prospector Web 版 — Flask 应用"""

import os
import re
import sys
import uuid
import json
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, jsonify, send_file,
)

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.roi_parser import parse_roi, expand_bbox
from src.mineral_kb import get_mineral_info, list_all_minerals
from src.tectonic_units import analyze_roi_location, TECTONIC_UNITS, PETROLEUM_BASINS
from src.geo_fetcher import fetch_all_geological
from src.geophy_fetcher import fetch_all_geophysical
from src.geochem_fetcher import fetch_all_geochemical
from src.live_fetcher import fetch_all_live_data
from src.report_generator import generate_report, save_json_summary
from src.exceptions import NetworkError, ROIError, FetchError
from src.logger import get_logger, setup_logging

from config import (
    HOST, PORT, DEBUG, MAX_UPLOAD_SIZE,
    UPLOAD_DIR, OUTPUT_DIR, LOG_DIR,
    TASK_MAX_AGE_DAYS,
)

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
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB，支持全球数据文件上传

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# 初始化日志
setup_logging(LOG_DIR)
logger = get_logger("web")

# 任务状态存储（内存 + 磁盘持久化）
tasks: dict = {}

STEP_PROGRESS = {
    '等待中': 0,
    '解析 ROI': 8,
    '定位构造单元': 18,
    '查询矿种知识库': 28,
    '收集地质资料': 40,
    '收集地球物理资料': 58,
    '收集地球化学资料': 72,
    '实时查询学术论文': 84,
    '生成报告': 94,
    '完成': 100,
    '失败': 100,
}


# 后台线程池（限制最大并发数）
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")


def _task_meta_path(output_dir: Path) -> Path:
    return Path(output_dir) / "task_meta.json"


def _save_task_meta(task: dict) -> None:
    """将任务元数据持久化到 JSON 文件"""
    try:
        meta = {k: v for k, v in task.items()}
        if isinstance(meta.get('output_dir'), Path):
            meta['output_dir'] = str(meta['output_dir'])
        # 决策轨迹血缘三键（容错，不影响产物）：task 自带 trace_id 优先 → 自生成
        try:
            import sys as _sys
            if "/opt/deepexplor-services" not in _sys.path:
                _sys.path.insert(0, "/opt/deepexplor-services")
            from commons.trace import stamp_metadata
            stamp_metadata(meta, explicit_trace_id=task.get('trace_id'), tenant_id=task.get('tenant_id'))
        except Exception:
            pass
        with open(_task_meta_path(task['output_dir']), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("保存 task_meta 失败: %s", e)


def _recover_tasks() -> None:
    """从 OUTPUT_DIR 恢复历史任务"""
    if not OUTPUT_DIR.exists():
        return
    recovered = 0
    for d in OUTPUT_DIR.iterdir():
        if not d.is_dir():
            continue
        meta_path = _task_meta_path(d)
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            meta['output_dir'] = Path(meta['output_dir'])
            tasks[meta['id']] = meta
            recovered += 1
        except Exception as e:
            logger.warning("恢复任务 %s 失败: %s", d.name, e)
    if recovered:
        logger.info("恢复了 %d 个历史任务", recovered)


def _cleanup_upload(upload_path: str) -> None:
    try:
        os.remove(upload_path)
    except Exception:
        pass


def _save_viz_data(output_dir: Path, roi, geochemical, live_data) -> None:
    """保存可视化所需的结构化数据"""
    output_dir = Path(output_dir)
    geochem = geochemical or {}
    bgs = geochem.get('backgrounds', {})
    ld = live_data or {}

    viz = {
        'bbox': roi.get('bbox'),
        'thresholds': bgs.get('anomaly_thresholds'),
        'national_ref': bgs.get('national_reference'),
        'source_unit': bgs.get('source_unit'),
        'papers': ld.get('papers', []),
    }
    try:
        with open(output_dir / 'viz_data.json', 'w', encoding='utf-8') as f:
            json.dump(viz, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("保存 viz_data 失败: %s", e)


def run_pipeline_thread(task_id: str, roi_path: str, mineral: str,
                        buffer_km: float, auto_download: bool):
    """在后台线程中运行完整流水线"""
    task = tasks[task_id]
    try:
        task['status'] = 'running'
        task['step'] = '解析 ROI'
        _save_task_meta(task)

        roi = parse_roi(roi_path)
        roi = expand_bbox(roi, buffer_km)

        task['step'] = '定位构造单元'
        _save_task_meta(task)
        location = analyze_roi_location(roi)

        task['step'] = '查询矿种知识库'
        _save_task_meta(task)
        mineral_info = get_mineral_info(mineral)

        task['step'] = '收集地质资料'
        _save_task_meta(task)
        geological = fetch_all_geological(roi, task['output_dir'], mineral, mineral_info, location)

        task['step'] = '收集地球物理资料'
        _save_task_meta(task)
        geophysical = fetch_all_geophysical(roi, task['output_dir'], mineral_info, auto_download)

        task['step'] = '收集地球化学资料'
        _save_task_meta(task)
        geochemical = fetch_all_geochemical(roi, task['output_dir'], mineral, mineral_info, location)

        task['step'] = '实时查询学术论文'
        _save_task_meta(task)
        mag_file = geophysical.get('magnetic', {}).get('file') if geophysical.get('magnetic') else None
        grav_file = geophysical.get('gravity', {}).get('file') if geophysical.get('gravity') else None
        live_data = fetch_all_live_data(
            roi, mineral, location, mineral_info,
            magnetic_file=mag_file,
            gravity_file=grav_file,
            output_dir=task['output_dir'],
        )

        task['step'] = '生成报告'
        _save_task_meta(task)
        report_path = generate_report(
            roi, mineral, mineral_info, location,
            geological, geophysical, geochemical, live_data,
            task['output_dir'],
        )
        json_path = save_json_summary(
            roi, mineral, mineral_info,
            geological, geophysical, geochemical,
            task['output_dir'],
        )

        task['result'] = {
            'area_km2': roi['area_km2'],
            'center_lon': roi['center']['lon'],
            'center_lat': roi['center']['lat'],
            'bbox': roi['bbox'],
            'map_sheet': geological.get('map_sheet', ''),
            'metallogenic_types': [
                mt['name'] for mt in mineral_info.get('metallogenic_types', [])
            ],
            'key_elements': mineral_info.get('all_key_elements', []),
            'n_geological_links': (
                len(geological.get('ngac_geology', [])) +
                len(geological.get('ngac_mineral', []))
            ),
            'n_geochem_links': len(geochemical.get('ngac_links', [])),
            'n_cnki_links': len(geological.get('cnki', [])),
            'magnetic_downloaded': geophysical.get('magnetic') is not None,
            'gravity_downloaded': geophysical.get('gravity') is not None,
        }

        task['status'] = 'completed'
        task['step'] = '完成'
        _save_task_meta(task)

        # 保存可视化数据供 task-detail API 使用
        _save_viz_data(task['output_dir'], roi, geochemical, live_data)

        _cleanup_upload(roi_path)
        logger.info("任务 %s 完成", task_id)

    except Exception as e:
        task['status'] = 'failed'
        task['error'] = str(e)
        task['step'] = '失败'
        _save_task_meta(task)
        logger.error("任务 %s 失败: %s", task_id, e, exc_info=True)


@app.route('/')
def index():
    """主页"""
    minerals = list_all_minerals()
    return render_template('index.html', minerals=minerals)


@app.route('/api/upload', methods=['POST'])
def api_upload():
    """上传 ROI 并启动任务"""
    if 'file' not in request.files:
        return jsonify({'error': '未上传文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '文件名为空'}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ('.kml', '.ovkml', '.xlsx', '.xls'):
        return jsonify({'error': f'不支持的文件格式: {ext}，请上传 .kml / .ovkml / .xlsx'}), 400

    mineral = request.form.get('mineral', '铜')
    buffer_km = float(request.form.get('buffer', 20))
    auto_download = request.form.get('auto_download', 'false') == 'true'
    trace_id = request.form.get('trace_id') or request.headers.get('X-Trace-Id') or ''

    task_id = uuid.uuid4().hex[:12]
    project_name = f"{Path(file.filename).stem}_{mineral}_{datetime.now().strftime('%Y%m%d_%H%M')}"
    task_output_dir = OUTPUT_DIR / project_name
    task_output_dir.mkdir(parents=True, exist_ok=True)

    upload_path = UPLOAD_DIR / f"{task_id}_{file.filename}"
    file.save(upload_path)

    tasks[task_id] = {
        'id': task_id,
        'tenant_id': request.headers.get('X-Tenant-Id'),   # P2 隔离:BFF 注入
        'status': 'pending',
        'step': '等待中',
        'output_dir': task_output_dir,
        'output_name': project_name,
        'mineral': mineral,
        'trace_id': trace_id or None,
        'created_at': datetime.now().isoformat(),
    }
    _save_task_meta(tasks[task_id])

    _executor.submit(
        run_pipeline_thread,
        task_id, str(upload_path), mineral, buffer_km, auto_download,
    )

    logger.info("新任务 %s: mineral=%s, buffer=%.1fkm", task_id, mineral, buffer_km)
    return jsonify({'task_id': task_id, 'status': 'started'})


@app.route('/api/status/<task_id>')
def api_status(task_id):
    """查询任务状态"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    step = task.get('step', '')
    resp = {
        'task_id': task_id,
        'status': task['status'],
        'step': step,
        'progress': int(task.get('progress') or STEP_PROGRESS.get(step, 0)),
        'output_name': task.get('output_name', ''),
        'trace_id': task.get('trace_id'),
    }

    if task['status'] == 'completed':
        resp['result'] = task.get('result', {})
    elif task['status'] == 'failed':
        resp['error'] = task.get('error', '未知错误')

    return jsonify(resp)


@app.route('/api/tasks')
def api_tasks():
    """列出所有任务（含历史）"""
    result = []
    for tid, task in tasks.items():
        result.append({
            'id': tid,
            'mineral': task.get('mineral', ''),
            'status': task.get('status', ''),
            'step': task.get('step', ''),
            'progress': int(task.get('progress') or STEP_PROGRESS.get(task.get('step', ''), 0)),
            'output_name': task.get('output_name', ''),
            'trace_id': task.get('trace_id'),
            'created_at': task.get('created_at', ''),
        })
    result.sort(key=lambda t: t.get('created_at', ''), reverse=True)
    return jsonify({'tasks': result})


@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def api_delete_task(task_id):
    """删除任务及其输出目录"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    output_dir = task.get('output_dir')
    if isinstance(output_dir, Path) and output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)

    zip_path = OUTPUT_DIR / f"{task.get('output_name', '')}.zip"
    if zip_path.exists():
        zip_path.unlink()

    del tasks[task_id]
    logger.info("删除任务 %s", task_id)
    return jsonify({'status': 'deleted'})


@app.route('/api/report/<task_id>')
def api_report(task_id):
    """获取报告内容"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    report_path = task['output_dir'] / '00_项目摘要.md'
    if not report_path.exists():
        return jsonify({'error': '报告尚未生成'}), 404

    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return jsonify({'content': content})


@app.route('/api/file/<task_id>/<path:filepath>')
def api_file(task_id, filepath):
    """服务任务输出目录中的文件（图片、GeoTIFF 等）"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    full_path = (task['output_dir'] / filepath).resolve()
    task_dir = task['output_dir'].resolve()
    if not str(full_path).startswith(str(task_dir)):
        return jsonify({'error': '非法路径'}), 403
    if not full_path.exists():
        return jsonify({'error': '文件不存在'}), 404

    from flask import send_from_directory
    return send_from_directory(str(full_path.parent), full_path.name)


@app.route('/api/cache-files', methods=['GET'])
def api_list_cache_files():
    """查看缓存目录中已有的大文件"""
    from config import CACHE_DIR
    cache_dir = Path(CACHE_DIR)
    files = []
    for f in cache_dir.iterdir():
        if f.is_file() and f.stat().st_size > 1_000_000:
            files.append({
                'name': f.name,
                'size_mb': round(f.stat().st_size / 1024 / 1024, 1),
            })
    return jsonify({'files': files})


@app.route('/api/cache-upload', methods=['POST'])
def api_cache_upload():
    """上传全球数据文件到缓存目录（EMAG2 磁异常等）"""
    from config import CACHE_DIR
    if 'file' not in request.files:
        return jsonify({'error': '未选择文件'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': '未选择文件'}), 400

    cache_dir = Path(CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)

    filename = f.filename.lower()

    # 判断文件类型并确定目标路径
    if 'emag2' in filename and filename.endswith('.tif'):
        if 'sealevel' in filename:
            target = cache_dir / 'emag2_sealevel_global.tif'
        else:
            target = cache_dir / 'emag2_upcont_global.tif'
        label = 'EMAG2 磁异常'
    elif 'emag2' in filename and filename.endswith('.zip'):
        # 解压 zip 中的 .tif
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / 'upload.zip'
            f.save(zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                for entry in zf.namelist():
                    if entry.lower().endswith('.tif'):
                        zf.extract(entry, tmp)
                        tif = Path(tmp) / entry
                        if 'sealevel' in entry.lower():
                            target = cache_dir / 'emag2_sealevel_global.tif'
                        else:
                            target = cache_dir / 'emag2_upcont_global.tif'
                        shutil.move(str(tif), str(target))
                        logger.info("解压并保存: %s → %s", entry, target)
                        return jsonify({'message': f'已解压保存 {entry}', 'target': target.name})
        return jsonify({'error': 'ZIP 中未找到 .tif 文件'}), 400
    elif filename.endswith('.tif') or filename.endswith('.nc'):
        target = cache_dir / f.filename
        label = f.filename
    else:
        return jsonify({'error': '不支持的文件格式，请上传 .tif / .nc / .zip'}), 400

    f.save(target)
    size_mb = round(target.stat().st_size / 1024 / 1024, 1)
    logger.info("%s 缓存文件已上传: %s (%.1f MB)", label, target.name, size_mb)
    return jsonify({'message': f'{label} 已保存 ({size_mb} MB)', 'target': target.name})


@app.route('/api/download/<task_id>')
def api_download(task_id):
    """下载完整成果包 (zip)"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    output_dir = task['output_dir']
    if not output_dir.exists():
        return jsonify({'error': '成果包不存在'}), 404

    zip_path = output_dir.parent / f"{task['output_name']}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(output_dir)
                zf.write(file_path, arcname)

    return send_file(
        zip_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"{task['output_name']}.zip",
    )


@app.route('/api/download/<task_id>/<path:filepath>')
def api_download_file(task_id, filepath):
    """下载单个文件"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    full_path = task['output_dir'] / filepath
    if not full_path.exists():
        return jsonify({'error': '文件不存在'}), 404

    return send_file(full_path, as_attachment=True)


@app.route('/api/minerals')
def api_minerals():
    """列出支持的矿种"""
    return jsonify({'minerals': list_all_minerals()})


@app.route('/api/parse-roi', methods=['POST'])
def api_parse_roi():
    """仅解析 ROI 文件，返回 GeoJSON（不启动流水线）"""
    if 'file' not in request.files:
        return jsonify({'error': '未上传文件'}), 400

    file = request.files['file']
    ext = Path(file.filename).suffix.lower()
    if ext not in ('.kml', '.ovkml', '.xlsx', '.xls'):
        return jsonify({'error': f'不支持的文件格式: {ext}'}), 400

    buffer_km = float(request.form.get('buffer', 20))

    tmp_path = UPLOAD_DIR / f"preview_{uuid.uuid4().hex[:8]}_{file.filename}"
    file.save(tmp_path)

    try:
        roi = parse_roi(str(tmp_path))
        roi = expand_bbox(roi, buffer_km)

        # 序列化 GeoJSON 坐标
        from shapely.geometry import mapping, shape
        geom = shape(roi['geometry'])
        geojson = mapping(geom)

        return jsonify({
            'geometry': geojson,
            'bbox': roi['bbox'],
            'expanded_bbox': roi.get('expanded_bbox'),
            'center': roi['center'],
            'area_km2': roi['area_km2'],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@app.route('/api/tectonic-overlay')
def api_tectonic_overlay():
    """返回给定 bbox 范围内的构造单元 + 含油气盆地 GeoJSON"""
    from shapely.geometry import box as shapely_box, mapping

    try:
        west = float(request.args.get('west', 70))
        south = float(request.args.get('south', 15))
        east = float(request.args.get('east', 140))
        north = float(request.args.get('north', 55))
    except (ValueError, TypeError):
        return jsonify({'error': '无效的 bbox 参数'}), 400

    query_box = shapely_box(west, south, east, north)
    features = []

    for unit in TECTONIC_UNITS:
        try:
            if query_box.intersects(unit["polygon"]):
                features.append({
                    "type": "Feature",
                    "properties": {
                        "name": unit["name"],
                        "name_en": unit["name_en"],
                        "level": unit["level"],
                        "type": "tectonic",
                        "major_minerals": unit["major_minerals"],
                    },
                    "geometry": mapping(unit["polygon"]),
                })
        except Exception:
            pass

    for basin in PETROLEUM_BASINS:
        try:
            if query_box.intersects(basin["polygon"]):
                features.append({
                    "type": "Feature",
                    "properties": {
                        "name": basin["name"],
                        "type": "basin",
                        "area_km2": basin["area_km2"],
                    },
                    "geometry": mapping(basin["polygon"]),
                })
        except Exception:
            pass

    return jsonify({
        "type": "FeatureCollection",
        "features": features,
    })


@app.route('/api/task-detail/<task_id>')
def api_task_detail(task_id):
    """返回任务的详细数据，用于前端可视化（图表、地图、论文卡片等）"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    output_dir = task['output_dir']
    if isinstance(output_dir, str):
        output_dir = Path(output_dir)

    # 优先读取 viz_data.json（流水线完成后由 _save_viz_data 写入）
    viz_path = output_dir / 'viz_data.json'
    if viz_path.exists():
        try:
            with open(viz_path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except Exception:
            pass

    # 兜底：从 task result 中拼凑基础信息
    result = {
        'bbox': task.get('result', {}).get('bbox'),
        'thresholds': None,
        'national_ref': None,
        'source_unit': None,
        'papers': [],
    }
    return jsonify(result)


# 启动时恢复历史任务
_recover_tasks()

# 检查过期目录
if OUTPUT_DIR.exists():
    cutoff = datetime.now() - timedelta(days=TASK_MAX_AGE_DAYS)
    old_dirs = []
    for d in OUTPUT_DIR.iterdir():
        if d.is_dir():
            ctime = datetime.fromtimestamp(d.stat().st_ctime)
            if ctime < cutoff:
                old_dirs.append((d.name, ctime))
    if old_dirs:
        logger.warning("发现 %d 个超过 %d 天的输出目录: %s",
                       len(old_dirs), TASK_MAX_AGE_DAYS,
                       ", ".join(name for name, _ in old_dirs[:5]))


if __name__ == '__main__':
    print("\n🏔️  Prospector Web 版")
    print("─" * 40)
    print(f"  地址: http://127.0.0.1:{PORT}")
    print(f"  上传目录: {UPLOAD_DIR}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  日志目录: {LOG_DIR}")
    print("─" * 40)
    app.run(debug=DEBUG, host=HOST, port=PORT)
