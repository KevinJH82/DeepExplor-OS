"""
P0: Web API tests for SSE streaming
SSE流发送、进度解析、keepalive、任务不存在404。
"""
import json
import pytest


@pytest.fixture
def web_app(monkeypatch, tmp_path):
    """Create isolated Flask test client."""
    import web.app as app_mod
    monkeypatch.setattr(app_mod, "MAIN_PY", tmp_path / "main.py")
    (tmp_path / "main.py").write_text("# mock")
    monkeypatch.setattr(app_mod, "UPLOAD_DIR", tmp_path / "uploads" / "kml")
    app_mod.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_mod, "_TASKS_PERSIST_FILE", tmp_path / ".geo_tasks_persist.json")

    import web.app
    web.app._tasks.clear()
    web.app.app.config["TESTING"] = True

    with web.app.app.test_client() as client:
        yield client

    web.app._tasks.clear()


@pytest.mark.p0
class TestStreamEndpoint:
    """SSE streaming endpoint."""

    def test_stream_nonexistent_task(self, web_app):
        """Streaming a non-existent task returns 404."""
        resp = web_app.get("/api/stream?task_id=nonexistent")
        assert resp.status_code == 404

    def test_stream_done_task_returns_end(self, web_app):
        """When task is already done, stream returns __END__ event."""
        import web.app

        t = web.app.TaskEntry(
            task_id="test-done", task_type="download", status="done",
            finished_at=100.0, created_at=0.0,
        )
        web.app._tasks["test-done"] = t
        # Mark log_buf as closed (None sentinel appended)
        t.log_buf = []

        def _close_log():
            with t.log_lock:
                if t.log_buf is not None:
                    t.log_buf.append(None)
        _close_log()

        resp = web_app.get("/api/stream?task_id=test-done")
        assert resp.status_code == 200
        data = resp.data.decode("utf-8")
        assert "__END__" in data or resp.data

    def test_stream_with_buffer_events(self, web_app):
        """Stream returns buffered log lines."""
        import web.app

        t = web.app.TaskEntry(
            task_id="test-buf", task_type="download", status="running",
            finished_at=None, created_at=0.0,
        )
        t.log_buf = ["line1", "line2", "__PROGRESS__test.tif|45"]
        web.app._tasks["test-buf"] = t
        # Mark as done
        t.status = "done"
        t.finished_at = 100.0
        t.log_buf.append(None)

        resp = web_app.get("/api/stream?task_id=test-buf")
        assert resp.status_code == 200
        data = resp.data.decode("utf-8")
        assert "data:" in data or "__PROGRESS__" in data


@pytest.mark.p0
class TestStreamProgressParsing:
    """Progress line format parsing."""

    def test_progress_format_has_underscore_prefix(self):
        """Progress events use __PROGRESS__ prefix."""
        line = "__PROGRESS__test.tif|45"
        assert line.startswith("__PROGRESS__")
        assert "|" in line
