"""
P0: Web API tests for task management
run/stop/status/tasks/delete/restart, concurrency limit, conflict detection, persistence.
"""
import json
import pytest
from pathlib import Path


@pytest.fixture
def web_app(monkeypatch, tmp_path):
    """Create Flask test client with isolated state."""
    import web.app as app_mod

    # Isolate paths
    monkeypatch.setattr(app_mod, "MAIN_PY", tmp_path / "main.py")
    (tmp_path / "main.py").write_text("# mock main.py")
    monkeypatch.setattr(app_mod, "UPLOAD_DIR", tmp_path / "uploads" / "kml")
    app_mod.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_mod, "_TASKS_PERSIST_FILE", tmp_path / ".geo_tasks_persist.json")

    # Reset module-level state
    import web.app
    web.app._tasks.clear()

    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as client:
        yield client

    # Cleanup
    web.app._tasks.clear()


@pytest.mark.p0
class TestStatusEndpoint:
    """GET /api/status."""

    def test_status_returns_json(self, web_app):
        resp = web_app.get("/api/status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "running" in data
        assert "tasks" in data

    def test_status_no_running_tasks_initially(self, web_app):
        resp = web_app.get("/api/status")
        data = json.loads(resp.data)
        assert data["running"] is False


@pytest.mark.p0
class TestTasksEndpoint:
    """GET /api/tasks."""

    def test_tasks_returns_valid_response(self, web_app):
        resp = web_app.get("/api/tasks")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        # Accept both JSON array and dict with tasks key
        if isinstance(data, dict):
            assert "tasks" in data
        else:
            assert isinstance(data, list)

    def test_tasks_empty_initially(self, web_app):
        resp = web_app.get("/api/tasks")
        data = json.loads(resp.data)
        if isinstance(data, dict):
            assert len(data["tasks"]) == 0
        else:
            assert len(data) == 0


@pytest.mark.p0
class TestConfigEndpoint:
    """GET/POST /api/config."""

    def test_get_config(self, web_app):
        resp = web_app.get("/api/config")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "copernicus" in data
        assert "task" in data

    def test_post_config_invalid_json(self, web_app):
        resp = web_app.post("/api/config", data="not json", content_type="application/json")
        assert resp.status_code == 400


@pytest.mark.p0
class TestUploadEndpoint:
    """POST /api/upload-kml."""

    def test_upload_no_file(self, web_app):
        resp = web_app.post("/api/upload-kml")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "未收到文件" in data["error"]

    def test_upload_empty_filename(self, web_app):
        data = {"file": (b"", "")}
        resp = web_app.post("/api/upload-kml", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_upload_invalid_extension(self, web_app):
        from io import BytesIO
        data = {"file": (BytesIO(b"test content"), "test.pdf")}
        resp = web_app.post("/api/upload-kml", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        err = json.loads(resp.data)
        assert "error" in err


@pytest.mark.p0
class TestDeleteTask:
    """DELETE /api/tasks/<task_id>."""

    def test_delete_nonexistent(self, web_app):
        resp = web_app.delete("/api/tasks/nonexistent-id")
        assert resp.status_code == 404


@pytest.mark.p0
class TestRunTaskWithoutSubprocess:
    """Run task validation (without actually spawning main.py)."""

    def test_run_empty_request_no_concurrency(self, web_app, monkeypatch):
        """Empty POST to /api/run should create task (kml existence checked later)."""
        import subprocess
        import threading
        from unittest.mock import MagicMock

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.stdout = open("/dev/null", "rb")
                self.pid = 12345
            def poll(self):
                return None

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        # Mock Thread to avoid background stdout reading
        mock_thread = MagicMock()
        monkeypatch.setattr(threading, "Thread", lambda *a, **kw: mock_thread)

        resp = web_app.post("/api/run", json={"task": {
            "kml": "/nonexistent/test.kml",
            "output": "./downloads",
        }})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "task_id" in data

    def test_run_preserves_zero_cloud_in_task_metadata(self, web_app, monkeypatch):
        import subprocess
        import threading
        from unittest.mock import MagicMock

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.stdout = open("/dev/null", "rb")
                self.pid = 12345
            def poll(self):
                return None

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        monkeypatch.setattr(threading, "Thread", lambda *a, **kw: MagicMock())

        resp = web_app.post("/api/run", json={"task": {
            "kml": "/tmp/test.kml",
            "sensor": ["dem"],
            "start": "2024-01-01",
            "end": "2024-12-31",
            "cloud": 0,
            "output": "./downloads",
        }})

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["argv"][data["argv"].index("--cloud") + 1] == "0"

        tasks = json.loads(web_app.get("/api/tasks").data)["tasks"]
        assert tasks[0]["cloud"] == 0

    def test_run_partial_task_uses_saved_cloud(self, web_app, monkeypatch):
        import subprocess
        import threading
        from unittest.mock import MagicMock
        import web.app as app_mod

        saved_config = {
            "task": {
                "kml": "/tmp/saved.kml",
                "sensor": ["dem"],
                "start": "2024-01-01",
                "end": "2024-12-31",
                "cloud": 3,
                "max_items": 5,
                "output": "./downloads",
            }
        }

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.stdout = open("/dev/null", "rb")
                self.pid = 12345
            def poll(self):
                return None

        monkeypatch.setattr(app_mod, "_load_yaml", lambda: saved_config.copy())
        monkeypatch.setattr(app_mod, "_save_yaml", lambda data: None)
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        monkeypatch.setattr(threading, "Thread", lambda *a, **kw: MagicMock())

        resp = web_app.post("/api/run", json={"task": {
            "kml": "/tmp/request.kml",
            "output": "./downloads",
        }})

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["argv"][data["argv"].index("--cloud") + 1] == "3"

        tasks = json.loads(web_app.get("/api/tasks").data)["tasks"]
        assert tasks[0]["cloud"] == 3
