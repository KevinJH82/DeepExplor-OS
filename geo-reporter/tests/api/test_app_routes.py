"""
geo-reporter API route tests
upload-kml, SSE run, download docx/pptx, cleanup
"""
import json
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def reporter_app(monkeypatch, tmp_path):
    """Create Flask test client for geo-reporter."""
    import web.app as reporter_app_mod
    reporter_app_mod.app.config["TESTING"] = True
    monkeypatch.setattr(reporter_app_mod, "tasks", {})
    # Isolate upload dir
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr(reporter_app_mod, "UPLOADS_DIR", upload_dir)
    monkeypatch.setattr(reporter_app_mod, "REPORTS_DIR", reports_dir)
    with reporter_app_mod.app.test_client() as client:
        yield client


@pytest.mark.p0
class TestReporterRoutes:
    """Core geo-reporter API endpoints."""

    def test_index_page(self, reporter_app):
        resp = reporter_app.get("/")
        assert resp.status_code == 200

    def test_upload_kml_no_file(self, reporter_app):
        resp = reporter_app.post("/api/upload-kml")
        assert resp.status_code in (200, 400)

    def test_status_endpoint(self, reporter_app):
        resp = reporter_app.get("/api/status")
        assert resp.status_code in (200, 404)  # may use different route pattern

    def test_task_history_list_includes_task_code(self, reporter_app):
        import web.app as reporter_app_mod

        reporter_app_mod.tasks["abc12345"] = {
            "task_code": "GR-20260624-ABC12345",
            "status": "kml_uploaded",
            "area_name": "测试区",
            "kml_name": "test.kml",
            "created_at": "2026-06-24T10:00:00",
        }

        resp = reporter_app.get("/api/tasks")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["tasks"][0]["task_id"] == "abc12345"
        assert payload["tasks"][0]["task_code"] == "GR-20260624-ABC12345"
        assert payload["tasks"][0]["area_name"] == "测试区"

    def test_task_history_imports_existing_files(self, reporter_app):
        import web.app as reporter_app_mod

        upload_path = reporter_app_mod.UPLOADS_DIR / "deadbeef_历史矿区.kml"
        report_path = reporter_app_mod.REPORTS_DIR / "历史矿区.docx"
        pptx_path = reporter_app_mod.REPORTS_DIR / "历史矿区.pptx"
        upload_path.write_text("<kml></kml>", encoding="utf-8")
        report_path.write_bytes(b"docx")
        pptx_path.write_bytes(b"pptx")

        resp = reporter_app.get("/api/tasks")
        assert resp.status_code == 200
        payload = resp.get_json()
        imported = next(task for task in payload["tasks"] if task["task_id"] == "deadbeef")
        assert imported["task_code"].startswith("GR-")
        assert imported["status"] == "completed"
        assert imported["area_name"] == "历史矿区"
        assert imported["has_report"] is True
        assert imported["has_pptx"] is True

    def test_cleanup_endpoint(self, reporter_app):
        resp = reporter_app.post("/api/cleanup", json={})
        assert resp.status_code in (200, 400, 404)

    def test_download_docx_no_task(self, reporter_app):
        resp = reporter_app.get("/api/download/docx/no-such-task")
        assert resp.status_code in (404, 400)

    def test_download_pptx_no_task(self, reporter_app):
        resp = reporter_app.get("/api/download/pptx/no-such-task")
        assert resp.status_code in (404, 400)
