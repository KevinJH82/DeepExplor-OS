#!/usr/bin/env python3
"""geo-orchestrator 智能编排引擎 - Flask 应用。

用户上传 KML/KMZ + 选矿种 → ROI 分析 + 矿种匹配 → 输出任务编排单（JSON）。
端口 8090。
"""

import os
import re
import json
import time
import threading
from datetime import datetime

from flask import (Flask, render_template, request, jsonify, send_from_directory,
                   Response, stream_with_context)

from config.config import Config
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
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64MB
logger = get_logger(__name__, Config.LOG_FILE)

REGION_EXTENSIONS = {'kml', 'kmz', 'ovkml'}

task_counter = 0
plan_tasks = {}
exec_tasks = {}   # plan_id -> {'tracker': ProgressTracker, 'executor': Executor, 'thread': Thread}

# ── 工具函数 ──────────────────────────────────────────────────


def _safe_aoi(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', '_', name).strip() or 'unnamed'


def _import_commons():
    import sys
    for _repo in (Config.REPO_DIR, "/opt/deepexplor-services"):
        if _repo not in sys.path:
            sys.path.insert(0, _repo)


# ── 首页 ──────────────────────────────────────────────────────


@app.route('/')
def index():
    return render_template('index.html')


# ── API：生成编排单 ───────────────────────────────────────────


@app.route('/api/plan', methods=['POST'])
def api_plan():
    """接收 KML + 矿种，生成编排单。"""
    global task_counter

    # 解析输入
    mineral = request.form.get('mineral', '').strip()
    if not mineral:
        return jsonify({'success': False, 'message': '请选择目标矿种'})

    kml_file = request.files.get('file')
    if not kml_file:
        return jsonify({'success': False, 'message': '请上传研究区文件 (.kml/.kmz)'})

    ext = kml_file.filename.rsplit('.', 1)[-1].lower() if '.' in kml_file.filename else ''
    if ext not in REGION_EXTENSIONS:
        return jsonify({'success': False, 'message': f'不支持的文件格式 .{ext}，请上传 .kml/.kmz'})

    # 保存上传文件
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = _safe_aoi(kml_file.filename.rsplit('.', 1)[0])
    upload_name = f"{ts}_{safe_name}.{ext}"
    upload_path = os.path.join(Config.UPLOAD_FOLDER, upload_name)
    kml_file.save(upload_path)

    # 创建任务
    task_id = f"plan_{task_counter:04d}"
    task_counter += 1

    # 生成全局 trace_id —— 一次端到端 ROI 请求的唯一标识，贯穿 11 服务（见架构蓝图 §1）
    _import_commons()
    try:
        from commons.trace import new_trace_id
        trace_id = new_trace_id()
    except Exception:
        trace_id = None

    plan_tasks[task_id] = {
        'id': task_id,
        'aoi_name': safe_name,
        'mineral': mineral,
        'trace_id': trace_id,
        'status': 'running',
        'progress': 0,
        'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'logs': [],
        'plan': None,
    }

    def run_plan():
        try:
            _import_commons()

            from core.roi_analyzer import ROIAnalyzer
            from core.mineral_engine import MineralEngine
            from core.planner import Planner

            task = plan_tasks[task_id]

            def on_log(msg, level='INFO'):
                ts_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                task['logs'].append(f"[{ts_str}] [{level}] {msg}")
                if len(task['logs']) > 200:
                    task['logs'] = task['logs'][-200:]
                logger.info(f"[{task_id}] {msg}")

            # 步骤 1: ROI 分析
            task['progress'] = 10
            on_log(f"开始分析 ROI：{safe_name}")
            roi_ctx = ROIAnalyzer.analyze(upload_path, Config.upstream_roots())
            on_log(f"ROI 分析完成：面积 {roi_ctx.area_km2:.1f} km²，"
                   f"高程 {roi_ctx.elevation_range[0]:.0f}–{roi_ctx.elevation_range[1]:.0f} m，"
                   f"植被覆盖 {roi_ctx.vegetation_cover}")

            # 步骤 2: 矿种推荐
            task['progress'] = 30
            on_log(f"矿种匹配：{mineral}")
            recommendation = MineralEngine.recommend(mineral, roi_ctx)
            on_log(f"推荐传感器：{', '.join(r.sensor for r in recommendation.sensors)}")

            # 步骤 3: 生成编排单
            task['progress'] = 50
            on_log("生成编排单...")
            planner = Planner()
            plan = planner.plan(upload_path, mineral, roi_ctx, recommendation, on_log)

            # 注入 trace_id 到编排单顶层 —— 下游服务据此沿血缘传播（P1）/ orchestrator 注入（P2）
            if trace_id:
                plan['trace_id'] = trace_id

            # 注入执行所需信息：KML 绝对路径 + bbox + aoi_name（P2 执行器逐服务上传要用）
            plan.setdefault('roi', {})
            plan['roi']['kml_path'] = upload_path
            plan['roi'].setdefault('bbox', list(roi_ctx.bbox))
            plan['roi'].setdefault('aoi_name', roi_ctx.aoi_name)

            # P4：跨 ROI 知识迁移（经验提示）+ 学习闭环（策略偏置）+ 注册编排单
            try:
                from core.registry import PlanRegistry
                roi_dict = ROIAnalyzer.to_dict(roi_ctx)
                exp = PlanRegistry.find_similar(roi_dict, mineral, plan.get('family', ''))
                bias = PlanRegistry.strategy_bias(plan.get('family', ''))
                if exp:
                    plan['experience'] = exp
                    on_log(f"召回 {len(exp)} 个历史相似 ROI 经验")
                if bias:
                    plan['strategy_bias'] = bias
                    on_log(f"应用 {len(bias)} 条历史策略偏置")
                PlanRegistry.register_plan(task_id, plan, source='auto')
            except Exception as _e:
                on_log(f"知识迁移/注册跳过：{_e}", "WARNING")

            plan_tasks[task_id]['chat'] = []
            plan_tasks[task_id]['upload_path'] = upload_path

            # 记录编排阶段决策轨迹（D1/D2/D3）—— 容错，不影响主流程
            if trace_id:
                try:
                    from core.trace_hooks import record_planning_trace
                    planner_mode = (plan.get('meta') or {}).get('planner_mode', 'deterministic')
                    record_planning_trace(trace_id, mineral, roi_ctx, recommendation,
                                          plan, planner_mode)
                    on_log(f"决策轨迹已记录 (trace_id={trace_id})")
                except Exception as _e:
                    on_log(f"决策轨迹记录跳过：{_e}", "WARNING")

            task['progress'] = 100
            task['status'] = 'completed'
            task['plan'] = plan
            on_log("编排单生成完成")

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            logger.error(f"编排单生成失败: {err}")
            plan_tasks[task_id]['status'] = 'failed'
            plan_tasks[task_id]['logs'].append(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] {str(e)}")

    thread = threading.Thread(target=run_plan, daemon=True)
    thread.start()

    return jsonify({'success': True, 'task_id': task_id, 'trace_id': trace_id})


# ── API：轮询状态 ─────────────────────────────────────────────


@app.route('/api/status/<task_id>')
def api_status(task_id):
    task = plan_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'message': '任务不存在'})
    return jsonify({
        'success': True,
        'task': {
            'id': task['id'],
            'status': task['status'],
            'progress': task['progress'],
            'trace_id': task.get('trace_id'),
            'logs': task['logs'][-50:],
            'plan': task.get('plan'),
        }
    })


# ── API：执行编排单（P2）──────────────────────────────────────


@app.route('/api/execute/<plan_id>', methods=['POST'])
def api_execute(plan_id):
    """按已生成的编排单驱动全管线执行。"""
    task = plan_tasks.get(plan_id)
    if not task or not task.get('plan'):
        return jsonify({'success': False, 'message': '编排单不存在或尚未生成'})

    existing = exec_tasks.get(plan_id)
    if existing and existing['thread'].is_alive():
        return jsonify({'success': False, 'message': '该编排单正在执行中'})

    _import_commons()
    from core.progress import ProgressTracker
    from core.executor import Executor

    plan = task['plan']
    # 断点续跑：若有持久化状态则恢复，否则新建
    tracker = ProgressTracker.load(plan_id, (plan.get('roi') or {}).get('aoi_name', 'unnamed'))
    if tracker is None:
        tracker = ProgressTracker(plan_id, plan)
    executor = Executor()

    def run_exec():
        try:
            executor.execute(plan, tracker)
        except Exception as e:
            logger.error(f"[{plan_id}] 执行线程异常：{e}")
        finally:
            # P4 学习闭环：执行结束落执行结果到注册表
            try:
                from core.registry import PlanRegistry
                PlanRegistry.record_outcome(plan_id, tracker.snapshot())
            except Exception as _e:
                logger.warning(f"[{plan_id}] 执行结果记录跳过：{_e}")

    thread = threading.Thread(target=run_exec, daemon=True)
    exec_tasks[plan_id] = {'tracker': tracker, 'executor': executor, 'thread': thread}
    thread.start()
    return jsonify({'success': True, 'plan_id': plan_id})


@app.route('/api/execute/<plan_id>/status')
def api_execute_status(plan_id):
    et = exec_tasks.get(plan_id)
    if not et:
        return jsonify({'success': False, 'message': '执行任务不存在'})
    return jsonify({'success': True, 'execution': et['tracker'].snapshot()})


@app.route('/api/execute/<plan_id>/stream')
def api_execute_stream(plan_id):
    et = exec_tasks.get(plan_id)
    if not et:
        return jsonify({'success': False, 'message': '执行任务不存在'}), 404
    tracker = et['tracker']

    @stream_with_context
    def gen():
        yield from tracker.sse_events()

    resp = Response(gen(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/api/execute/<plan_id>/skip/<service>', methods=['POST'])
def api_execute_skip(plan_id, service):
    et = exec_tasks.get(plan_id)
    if not et:
        return jsonify({'success': False, 'message': '执行任务不存在'})
    et['executor'].skip_service(service)
    return jsonify({'success': True, 'skipped': service})


@app.route('/api/execute/<plan_id>/pause', methods=['POST'])
def api_execute_pause(plan_id):
    et = exec_tasks.get(plan_id)
    if not et:
        return jsonify({'success': False, 'message': '执行任务不存在'})
    et['executor'].pause()
    return jsonify({'success': True})


@app.route('/api/execute/<plan_id>/resume', methods=['POST'])
def api_execute_resume(plan_id):
    et = exec_tasks.get(plan_id)
    if not et:
        return jsonify({'success': False, 'message': '执行任务不存在'})
    et['executor'].resume()
    return jsonify({'success': True})


# ── API：多轮对话 Agent（P4 · 4.1）────────────────────────────


@app.route('/api/chat/<plan_id>', methods=['POST'])
def api_chat(plan_id):
    task = plan_tasks.get(plan_id)
    if not task or not task.get('plan'):
        return jsonify({'success': False, 'message': '编排单不存在'})
    message = (request.json or {}).get('message', '').strip()
    if not message:
        return jsonify({'success': False, 'message': '消息为空'})

    _import_commons()
    from core.agent import OrchestratorAgent

    history = task.setdefault('chat', [])
    agent = OrchestratorAgent()
    try:
        result = agent.chat(task['plan'], history, message)
    except Exception as e:
        return jsonify({'success': False, 'message': f'对话失败：{e}'})

    reply = result.get('reply', '')
    proposal = result.get('proposal')
    history.append({'role': 'user', 'content': message})
    history.append({'role': 'assistant', 'content': reply})

    proposal_id = None
    if proposal:
        proposals = task.setdefault('proposals', {})
        proposal_id = f"prop_{len(proposals)}"
        proposals[proposal_id] = proposal

    return jsonify({'success': True, 'reply': reply,
                    'has_proposal': bool(proposal), 'proposal_id': proposal_id,
                    'proposal': proposal})


@app.route('/api/chat/<plan_id>/confirm/<proposal_id>', methods=['POST'])
def api_chat_confirm(plan_id, proposal_id):
    """确认对话提案 → 落为新版本编排单（parent 关联原单）。"""
    global task_counter
    task = plan_tasks.get(plan_id)
    if not task:
        return jsonify({'success': False, 'message': '编排单不存在'})
    proposal = (task.get('proposals') or {}).get(proposal_id)
    if not proposal:
        return jsonify({'success': False, 'message': '提案不存在'})

    new_id = f"plan_{task_counter:04d}"
    task_counter += 1
    # 继承 KML 路径，生成新 trace_id
    proposal.setdefault('roi', {})
    proposal['roi'].setdefault('kml_path', (task['plan'].get('roi') or {}).get('kml_path'))
    _import_commons()
    try:
        from commons.trace import new_trace_id
        proposal['trace_id'] = new_trace_id()
    except Exception:
        proposal['trace_id'] = None

    plan_tasks[new_id] = {
        'id': new_id, 'aoi_name': task.get('aoi_name'), 'mineral': proposal.get('mineral'),
        'trace_id': proposal.get('trace_id'), 'status': 'completed', 'progress': 100,
        'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'logs': [f"由对话提案 {proposal_id}（来自 {plan_id}）确认生成"],
        'plan': proposal, 'chat': [], 'parent_plan_id': plan_id,
        'upload_path': (task['plan'].get('roi') or {}).get('kml_path'),
    }
    try:
        from core.registry import PlanRegistry
        PlanRegistry.register_plan(new_id, proposal, source='chat', parent_plan_id=plan_id)
    except Exception as e:
        logger.warning(f"新版本注册跳过：{e}")

    return jsonify({'success': True, 'plan_id': new_id, 'plan': proposal})


# ── API：编排单版本管理 + 对比（P4 · 4.2）─────────────────────


@app.route('/api/plans')
def api_plans():
    _import_commons()
    from core.registry import PlanRegistry
    aoi = request.args.get('aoi')
    return jsonify({'success': True, 'plans': PlanRegistry.list_plans(aoi)})


@app.route('/api/plans/compare')
def api_plans_compare():
    _import_commons()
    from core.registry import PlanRegistry
    a, b = request.args.get('a'), request.args.get('b')
    if not a or not b:
        return jsonify({'success': False, 'message': '需提供 a 与 b 两个 plan_id'})
    return jsonify({'success': True, 'diff': PlanRegistry.compare(a, b)})


# ── main ──────────────────────────────────────────────────────

if __name__ == '__main__':
    pass  # 由 run.py 启动
