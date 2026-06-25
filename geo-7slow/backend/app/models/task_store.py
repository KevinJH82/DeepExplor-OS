"""内存任务状态管理"""
import asyncio
from typing import Optional


class TaskStore:
    def __init__(self):
        self._tasks: dict = {}
        self._ws_connections: dict[str, list] = {}

    def create(self, task_id: str, upload_id: str):
        self._tasks[task_id] = {
            "task_id": task_id,
            "upload_id": upload_id,
            "status": "queued",
            "progress": 0.0,
            "current_step": "初始化",
            "error": None,
            "results": None,
        }

    def get(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def update(self, task_id: str, status: str = None, progress: float = None,
               current_step: str = None, error: str = None, results: dict = None):
        task = self._tasks.get(task_id)
        if not task:
            return
        if status is not None:
            task["status"] = status
        if progress is not None:
            task["progress"] = progress
        if current_step is not None:
            task["current_step"] = current_step
        if error is not None:
            task["error"] = error
        if results is not None:
            task["results"] = results
        # 通知WebSocket客户端
        asyncio.create_task(self._notify_ws(task_id, task))

    async def add_ws(self, task_id: str, ws):
        if task_id not in self._ws_connections:
            self._ws_connections[task_id] = []
        self._ws_connections[task_id].append(ws)

    async def remove_ws(self, task_id: str, ws):
        if task_id in self._ws_connections:
            try:
                self._ws_connections[task_id].remove(ws)
            except ValueError:
                pass

    async def _notify_ws(self, task_id: str, data: dict):
        if task_id not in self._ws_connections:
            return
        import json
        msg = json.dumps(data, ensure_ascii=False)
        dead = []
        for ws in self._ws_connections[task_id]:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self._ws_connections[task_id].remove(ws)
            except ValueError:
                pass


task_store = TaskStore()
