"""
Flask Web Backend for Geo-Reporter
处理 KML 上传、并行搜索、报告生成和下载。
"""

import os
import sys
import json
import uuid
import threading
import traceback
import hashlib
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from flask import Flask, request, jsonify, send_file, Response

from reporter.kml_parser import parse_kml, KMLParseError
from reporter.tabular_parser import parse_tabular, TabularParseError
from reporter.geocoder import create_location_context, GeocoderError
from reporter.search_engine import SearchEngine, SearchEngineError
from reporter.report_builder import ReportBuilder


# 配置
BASE_DIR = Path(__file__).parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
REPORTS_DIR = BASE_DIR / "reports"
TEMPLATES_DIR = BASE_DIR / "templates"

for _d in (UPLOADS_DIR, REPORTS_DIR, BASE_DIR / "cache"):
    _d.mkdir(parents=True, exist_ok=True)

# 加载 config.yaml
_cfg_path = BASE_DIR / "config.yaml"
with open(_cfg_path, "r", encoding="utf-8") as _f:
    APP_CONFIG = yaml.safe_load(_f)

_ext_cfg = APP_CONFIG.get("extraction", {})
TAVILY_API_KEY = APP_CONFIG.get("tavily_api_key", "") or os.environ.get("TAVILY_API_KEY", "")
ANTHROPIC_API_KEY = APP_CONFIG.get("anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")

ALLOWED_EXTENSIONS = {".kml", ".kmz", ".ovkml", ".ovkmz", ".csv", ".xlsx", ".xls"}
REPORT_EXTENSIONS = {".docx", ".pptx"}




def _parse_uploaded_file(file_path: str):
    """根据文件后缀选择解析器，统一返回 (geometry, bbox, name, area_name, description)"""
    suffix = Path(file_path).suffix.lower()
    if suffix in (".kml", ".kmz", ".ovkml", ".ovkmz"):
        return parse_kml(file_path)
    elif suffix in (".csv", ".xlsx", ".xls"):
        return parse_tabular(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}")


# Flask 应用
app = Flask(__name__, template_folder=str(BASE_DIR / "web" / "templates"))
# ── 内部鉴权:拒绝绕过 BFF 的直连(PORTAL_INTERNAL_KEY 配置后生效) ──
try:
    import sys as _ia_sys
    if '/opt/deepexplor-services' not in _ia_sys.path:
        _ia_sys.path.insert(0, '/opt/deepexplor-services')
    from commons.internal_auth import init_internal_auth as _init_internal_auth
    _init_internal_auth(app)
except Exception as _ia_e:
    print(f'[internal_auth] 跳过接入: {_ia_e}')
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB 限制

# 任务状态跟踪
tasks = {}


def _new_task_code(task_id: str) -> str:
    """Generate a human-readable unique task code for debugging."""
    return f"GR-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{task_id.upper()}"


def _task_code_from_mtime(task_id: str, path: Path) -> str:
    try:
        created = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        created = datetime.now()
    return f"GR-{created.strftime('%Y%m%d-%H%M%S')}-{task_id.upper()}"


def _iso_from_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return datetime.now().isoformat()


def _stable_history_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def _split_upload_filename(path: Path) -> tuple[str, str]:
    stem = path.stem
    prefix, sep, rest = stem.partition("_")
    if sep and len(prefix) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in prefix):
        return prefix.lower(), rest or stem
    return _stable_history_id(str(path)), stem


def _task_summary(task_id: str, task: dict) -> dict:
    report_path = task.get("report_path")
    pptx_path = task.get("pptx_path")
    return {
        "task_id": task_id,
        "task_code": task.get("task_code") or task_id,
        "status": task.get("status", "unknown"),
        "area_name": task.get("area_name") or task.get("name") or "未命名任务",
        "kml_name": task.get("kml_name"),
        "mineral_type": task.get("mineral_type"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "error": task.get("error"),
        "has_report": bool(report_path and Path(report_path).exists()),
        "has_pptx": bool(pptx_path and Path(pptx_path).exists()),
    }


def _merge_imported_task(task_id: str, imported: dict) -> None:
    existing = tasks.get(task_id)
    if not existing:
        tasks[task_id] = imported
        return

    for key, value in imported.items():
        if value is not None and key not in existing:
            existing[key] = value

    if existing.get("status") in (None, "unknown", "kml_uploaded") and imported.get("status") == "completed":
        existing["status"] = "completed"


def import_history_from_disk() -> int:
    """Import task history inferred from upload/report files on disk."""
    report_pairs = {}
    for report_path in REPORTS_DIR.glob("*"):
        if not report_path.is_file() or report_path.suffix.lower() not in REPORT_EXTENSIONS:
            continue
        pair = report_pairs.setdefault(report_path.stem, {})
        if report_path.suffix.lower() == ".docx":
            pair["report_path"] = str(report_path)
        elif report_path.suffix.lower() == ".pptx":
            pair["pptx_path"] = str(report_path)

    imported_count = 0
    assigned_report_stems = set()

    for upload_path in UPLOADS_DIR.glob("*"):
        if not upload_path.is_file() or upload_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue

        task_id, area_name = _split_upload_filename(upload_path)
        report_pair = report_pairs.get(area_name) or report_pairs.get(f"{task_id}_{area_name}") or {}
        if report_pair:
            assigned_report_stems.add(Path(report_pair.get("report_path") or report_pair.get("pptx_path")).stem)

        imported = {
            "task_code": _task_code_from_mtime(task_id, upload_path),
            "status": "completed" if report_pair else "kml_uploaded",
            "kml_path": str(upload_path),
            "kml_name": upload_path.name,
            "name": area_name,
            "area_name": area_name,
            "description": "Imported from existing upload/report files.",
            "created_at": _iso_from_mtime(upload_path),
            "completed_at": _iso_from_mtime(Path(report_pair.get("report_path") or report_pair.get("pptx_path"))) if report_pair else None,
            "report_path": report_pair.get("report_path"),
            "pptx_path": report_pair.get("pptx_path"),
            "imported": True,
        }
        before = task_id in tasks
        _merge_imported_task(task_id, imported)
        if not before:
            imported_count += 1

    for stem, report_pair in report_pairs.items():
        if stem in assigned_report_stems:
            continue

        primary = Path(report_pair.get("report_path") or report_pair.get("pptx_path"))
        task_id = f"r{_stable_history_id(stem)[:7]}"
        imported = {
            "task_code": _task_code_from_mtime(task_id, primary),
            "status": "completed",
            "area_name": stem,
            "name": stem,
            "created_at": _iso_from_mtime(primary),
            "completed_at": _iso_from_mtime(primary),
            "report_path": report_pair.get("report_path"),
            "pptx_path": report_pair.get("pptx_path"),
            "imported": True,
        }
        before = task_id in tasks
        _merge_imported_task(task_id, imported)
        if not before:
            imported_count += 1

    return imported_count


@app.route("/", methods=["GET"])
def index():
    """主页"""
    return open(BASE_DIR / "web" / "templates" / "index.html").read(), 200, {
        "Content-Type": "text/html; charset=utf-8",
        # 禁止浏览器缓存前端，避免改版后仍显示旧页面（如旧的 8 类进度）
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


@app.route("/api/upload-kml", methods=["POST"])
def upload_kml():
    """
    上传地理文件（KML/KMZ/ovKML/CSV/Excel）并验证。

    Returns
    -------
    {
        "task_id": "...",
        "status": "kml_uploaded",
        "kml_name": "...",
        "area_name": "..."
    }
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"不支持的文件格式 {suffix}，仅支持 .kml / .ovkml / .kmz / .csv / .xlsx / .xls"}), 400

    # 生成任务 ID
    task_id = str(uuid.uuid4())[:8]
    task_code = _new_task_code(task_id)

    # 保存并解析文件
    upload_path = UPLOADS_DIR / f"{task_id}_{file.filename}"
    try:
        file.save(str(upload_path))
        geometry, bbox, name, area_name, description = _parse_uploaded_file(str(upload_path))
        # 可选:随 KML 一并上传经济参数(JSON 字符串),供新版报告价值评估章(Phase C)
        econ_params = None
        try:
            _ep = request.form.get("econ_params")
            if _ep:
                import json as _json
                econ_params = _json.loads(_ep)
        except Exception:
            econ_params = None
        tasks[task_id] = {
            "task_code": task_code,
            "status": "kml_uploaded",
            "kml_path": str(upload_path),
            "kml_name": file.filename,
            "geometry": geometry,
            "bbox": bbox,
            "name": name,
            "area_name": area_name,
            "description": description,
            "econ_params": econ_params,
            "created_at": datetime.now().isoformat()
        }
        return jsonify({
            "task_id": task_id,
            "task_code": task_code,
            "status": "kml_uploaded",
            "kml_name": file.filename,
            "area_name": area_name
        }), 200

    except (KMLParseError, TabularParseError) as e:
        return jsonify({"error": f"文件解析错误: {str(e)}"}), 400

    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@app.route("/api/run/<task_id>", methods=["GET"])
def run_report_generation(task_id: str):
    """
    开始生成报告，流式推送进度。

    Returns
    -------
    Server-Sent Events stream
    """
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404

    mineral_type = request.args.get("mineral", "").strip()
    tenant_id = request.headers.get("X-Tenant-Id")   # P2 隔离:BFF 经反代/适配器注入

    def generate_events():
        def ev(payload):
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        def keepalive():
            return ": keepalive\n\n"

        def pump_keepalive(fn, box, interval=8):
            """在后台线程运行 fn()，期间每 interval 秒 yield 一次 SSE 心跳，避免长任务静默导致连接超时。
            结果写入 box['result']，异常写入 box['error']。"""
            done = threading.Event()

            def _worker():
                try:
                    box["result"] = fn()
                except Exception as exc:  # noqa: BLE001
                    box["error"] = exc
                finally:
                    done.set()

            threading.Thread(target=_worker, daemon=True).start()
            while not done.wait(timeout=interval):
                yield ": keepalive\n\n"

        try:
            task = tasks[task_id]
            task["status"] = "running"
            task["started_at"] = datetime.now().isoformat()
            task["mineral_type"] = mineral_type or None
            task.pop("error", None)

            # 步骤 1：地理定位
            yield ev({'step': 1, 'message': '正在确定地理位置...'})
            try:
                location = create_location_context(
                    task["bbox"],
                    task["area_name"],
                    task["description"]
                )
            except GeocoderError as e:
                task["status"] = "failed"
                task["error"] = str(e)
                yield ev({'error': f'Geocoding failed: {str(e)}'})
                return

            # 步骤 2-9：串行搜索 8 类数据
            yield ev({'step': 2, 'message': '正在搜索数据...（Tavily 并发搜索 + Claude API 提取，预计 30-60 秒）'})
            try:
                search_engine = SearchEngine(
                    templates_dir=str(TEMPLATES_DIR),
                    tavily_api_key=TAVILY_API_KEY,
                    tavily_max_results=_ext_cfg.get("tavily_max_results", 5),
                    tavily_search_depth=_ext_cfg.get("tavily_search_depth", "advanced"),
                    cache_db=str(BASE_DIR / "cache" / "geo_cache.db")
                )
                search_results = {}

                for item in search_engine.search_all_categories_stream(location, mineral_type=mineral_type):
                    if item[0] == "keepalive":
                        yield ": keepalive\n\n"
                        continue
                    idx, total, cat_id, result = item
                    search_results[cat_id] = result
                    yield ev({'step': 'search_progress', 'idx': idx, 'total': total,
                               'cat_id': cat_id, 'cat_name': result.category_name,
                               'success': not bool(result.error), 'error_msg': result.error or ''})

                total = len(search_results)
                success = sum(1 for r in search_results.values() if not r.error)
                yield ev({'step': 3, 'message': f'数据搜索完成：{success}/{total} 个类别成功'})

                if success == 0:
                    yield ev({'warning': '所有搜索都失败了。这可能是由于 API 限流。请稍后重试。'})

            except SearchEngineError as e:
                task["status"] = "failed"
                task["error"] = str(e)
                yield ev({'error': f'搜索失败：{str(e)}。请稍后重试。'})
                return

            # 步骤 3.5：靶区推荐图 + 综合置信评价（后台线程 + 心跳，避免长任务静默断连）
            yield ev({'step': 'synthesis', 'message': '正在统一研判靶区与综合置信...（同一次 Claude 研判，二者自洽，预计 30-90 秒）'})
            from reporter.synthesis import evaluate_synthesis

            def _do_synthesis():
                try:
                    # 统一研判：靶区评级与综合置信共用同一证据上下文、单次产出，保证逻辑闭合
                    return evaluate_synthesis(location, mineral_type, search_results, str(TEMPLATES_DIR))
                except Exception as exc:
                    print(f"[Synthesis] 统一研判失败：{exc}")
                    return None, None

            syn_box = {}
            yield from pump_keepalive(_do_synthesis, syn_box)
            target_figure, confidence = syn_box.get("result", (None, None))
            if confidence:
                yield ev({'step': 'confidence', 'message': f"综合置信评价：{confidence.get('grade','?')} 级（{confidence.get('grade_label','')}）"})

            # 步骤 10：生成报告（后台线程 + 心跳，期间会联网拼接底图，耗时较长）
            yield ev({'step': 4, 'message': '正在生成报告...'})
            build_box = {}

            def _do_build():
                report_builder = ReportBuilder(str(REPORTS_DIR))
                return report_builder.build_report(
                    location, search_results, output_name=task["area_name"],
                    mineral_type=mineral_type, target_figure=target_figure, confidence=confidence,
                    tenant_id=tenant_id, econ_params=task.get("econ_params"))

            yield from pump_keepalive(_do_build, build_box)
            if "error" in build_box:
                task["status"] = "failed"
                task["error"] = str(build_box["error"])
                yield ev({'error': f'Report generation failed: {build_box["error"]}'})
                return
            _res = build_box["result"]
            # 兼容旧 2 元组与新 4 元组(旧版docx, 旧版pptx, 新版docx, 新版pptx)
            report_path, pptx_path = _res[0], _res[1]
            report_path_v2 = _res[2] if len(_res) > 2 else None
            pptx_path_v2 = _res[3] if len(_res) > 3 else None
            task["report_path"] = report_path
            task["pptx_path"] = pptx_path
            task["report_path_v2"] = report_path_v2
            task["pptx_path_v2"] = pptx_path_v2
            task["status"] = "completed"
            task["completed_at"] = datetime.now().isoformat()
            yield ev({'step': 5, 'message': '报告生成完成！', 'report_path': report_path, 'pptx_path': pptx_path,
                      'report_path_v2': report_path_v2, 'pptx_path_v2': pptx_path_v2})

        except Exception as e:
            traceback.print_exc()
            if task_id in tasks:
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = str(e)
            yield ev({'error': f'Unexpected error: {str(e)}'})

    resp = Response(generate_events(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/download/<task_id>", methods=["GET"])
def download_report(task_id: str):
    """
    下载生成的报告文件。
    支持 ?format=pptx 下载 PPT 格式。

    Returns
    -------
    Word (.docx) 或 PowerPoint (.pptx) 文件
    """
    if task_id not in tasks:
        import_history_from_disk()

    if task_id not in tasks:
        return jsonify({"error": "Report not found or not yet generated"}), 404

    fmt = request.args.get("format", "docx")          # docx | pptx
    version = request.args.get("version", "legacy")   # legacy | v2
    # 4 文件并存:按 (version, format) 取对应路径
    key = {
        ("legacy", "docx"): "report_path", ("legacy", "pptx"): "pptx_path",
        ("v2", "docx"): "report_path_v2", ("v2", "pptx"): "pptx_path_v2",
    }.get((version, fmt))
    if not key:
        return jsonify({"error": "Unknown version/format"}), 400
    path = tasks[task_id].get(key)
    if not path or not Path(path).exists():
        return jsonify({"error": f"{version} {fmt} file not found"}), 404
    if fmt == "pptx":
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return send_file(path, mimetype=media, as_attachment=True, download_name=Path(path).name)


@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    """获取历史任务列表"""
    import_history_from_disk()
    task_items = sorted(
        (_task_summary(task_id, task) for task_id, task in tasks.items()),
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )
    return jsonify({"tasks": task_items}), 200


@app.route("/api/tasks/import", methods=["POST"])
def import_tasks():
    """从磁盘导入历史任务"""
    imported_count = import_history_from_disk()
    return jsonify({
        "message": "History imported",
        "imported_count": imported_count,
        "total": len(tasks),
    }), 200


@app.route("/api/status/<task_id>", methods=["GET"])
def get_task_status(task_id: str):
    """获取任务状态"""
    if task_id not in tasks:
        import_history_from_disk()

    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404

    task = tasks[task_id]
    return jsonify(_task_summary(task_id, task)), 200


@app.route("/api/cleanup/<task_id>", methods=["DELETE"])
def cleanup_task(task_id: str):
    """清理任务（删除上传和生成的文件）"""
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404

    task = tasks[task_id]

    # 删除上传文件
    if "kml_path" in task and Path(task["kml_path"]).exists():
        try:
            Path(task["kml_path"]).unlink()
        except Exception as e:
            app.logger.warning(f"Failed to delete KML file: {e}")

    # 删除报告文件
    if "report_path" in task and Path(task["report_path"]).exists():
        try:
            Path(task["report_path"]).unlink()
        except Exception as e:
            app.logger.warning(f"Failed to delete report file: {e}")

    # 从任务列表中删除
    del tasks[task_id]

    return jsonify({"message": "Task cleaned up"}), 200


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8081, threaded=True)
