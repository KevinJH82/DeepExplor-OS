"""
geo-preprocess — 遥感数据预处理子系统 (Flask)

从 geo-analyser 拆出,与 geo-downloader / geo-analyser / geo-stru 平级。
路由: /  /api/browse  /api/scan  /api/preview  /api/process
"""

import os
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
from flask import Flask, render_template, request, jsonify, Response, make_response

from config.config import HOST, PORT, DEBUG, MAX_CONTENT_LENGTH
from utils.pipeline import (
    scan_directory, read_image, generate_preview, process_single_file,
)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


@app.route("/")
def index():
    """预处理前端首页"""
    resp = make_response(render_template("preprocessing.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route('/api/browse', methods=['POST'])
def api_browse():
    """浏览目录内容（用于目录选择对话框）"""
    data = request.json
    current = data.get('path', '')

    # 默认从用户主目录开始
    if not current:
        current = str(Path.home())

    current = os.path.expanduser(current)
    current = os.path.abspath(current)

    if not os.path.isdir(current):
        current = str(Path.home())

    show_files = data.get('show_files', False)
    FILE_EXTS = {".tif", ".tiff", ".npy", ".jpg", ".jpeg", ".png"}

    try:
        entries = []
        for entry in sorted(Path(current).iterdir()):
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "type": "dir"
                })
            elif show_files and entry.is_file() and entry.suffix.lower() in FILE_EXTS:
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "type": "file"
                })

        # 计算父目录
        parent = str(Path(current).parent)
        if parent == current:
            parent = None  # 根目录

        return jsonify({
            "current": current,
            "parent": parent,
            "entries": entries
        })
    except PermissionError:
        return jsonify({"error": "没有权限访问该目录"}), 403


@app.route('/api/scan', methods=['POST'])
def api_scan():
    """扫描目录"""
    data = request.json
    directory = data.get('directory', '')

    if not directory:
        return jsonify({"error": "目录为空"}), 400

    # 展开 ~ 符号
    directory = os.path.expanduser(directory)

    if not os.path.isdir(directory):
        return jsonify({"error": f"目录不存在: {directory}"}), 400

    results = scan_directory(directory)
    return jsonify(results)


@app.route('/api/preview', methods=['POST'])
def api_preview():
    """生成预览图"""
    data = request.json
    file_path = data.get('file_path', '')

    if not file_path or (not os.path.isfile(file_path) and not os.path.isdir(file_path)):
        return jsonify({"error": "文件不存在"}), 400

    # 同时读一遍计算数值范围统计，便于前端判断"全零/常数"等退化情况
    stats = None
    try:
        arr, _ = read_image(file_path)
        if arr is not None and arr.size > 0:
            finite = arr[np.isfinite(arr)]
            positive_ratio = float((finite > 0).sum()) / float(finite.size) if finite.size else 0.0
            stats = {
                "shape": list(arr.shape),
                "min":   float(np.nanmin(arr)) if finite.size else 0.0,
                "max":   float(np.nanmax(arr)) if finite.size else 0.0,
                "mean":  float(np.nanmean(arr)) if finite.size else 0.0,
                "positive_ratio": positive_ratio,
            }
    except Exception:
        stats = None

    img_base64 = generate_preview(file_path)
    if img_base64:
        return jsonify({"preview": f"data:image/png;base64,{img_base64}", "stats": stats})
    else:
        return jsonify({"error": "预览生成失败"}), 500


@app.route('/api/process', methods=['POST'])
def api_process():
    """处理影像（流式返回进度）"""
    data = request.json
    input_dir = os.path.expanduser(data.get('input_dir', ''))
    output_dir = os.path.expanduser(data.get('output_dir', ''))
    file_list = data.get('files', [])
    params = data.get('params', {})

    if not input_dir or not output_dir:
        return jsonify({"error": "输入/输出目录为空"}), 400

    if not file_list:
        return jsonify({"error": "未选择文件"}), 400

    # 确保输出目录存在
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    def generate():
        """生成器：逐个处理文件并返回进度"""
        results = []
        total = len(file_list)

        for idx, file_info in enumerate(file_list, 1):
            file_path = file_info.get('path')
            rel_path = file_info.get('rel_path')

            # 进度回调
            def progress_callback(msg: str):
                yield json.dumps({
                    "type": "progress",
                    "current": idx,
                    "total": total,
                    "message": msg,
                    "percent": int((idx - 1 + 0.5) / total * 100)
                }) + "\n"

            # 处理文件
            try:
                result = process_single_file(
                    file_path, output_dir, rel_path, params, file_info, progress_callback
                )
                results.append(result)

                # 文件完成消息
                yield json.dumps({
                    "type": "file_complete",
                    "file": rel_path,
                    "status": result["status"],
                    "error": result.get("error"),
                    "current": idx,
                    "total": total,
                    "percent": int(idx / total * 100)
                }) + "\n"
            except Exception as e:
                yield json.dumps({
                    "type": "file_error",
                    "file": rel_path,
                    "error": str(e),
                    "current": idx,
                    "total": total
                }) + "\n"

        # 最终总结
        successful = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "error")

        yield json.dumps({
            "type": "complete",
            "summary": {
                "total": total,
                "successful": successful,
                "failed": failed
            },
            "results": results
        }) + "\n"

    return Response(generate(), mimetype='application/json')


if __name__ == "__main__":
    print(f"  geo-preprocess 数据预处理服务  →  http://127.0.0.1:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG)
