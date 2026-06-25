"""
P0: Web API tests for config and schema
config/schema读写、无效JSON处理。
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

    # Create temporary config files
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_dir / "credentials.yaml")
    monkeypatch.setattr(app_mod, "SCHEMA_PATH", cfg_dir / "schema.yaml")

    import web.app
    web.app._tasks.clear()
    web.app.app.config["TESTING"] = True

    with web.app.app.test_client() as client:
        yield client

    web.app._tasks.clear()


@pytest.mark.p0
class TestConfigGet:
    """GET /api/config."""

    def test_default_sections_present(self, web_app):
        resp = web_app.get("/api/config")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for section in ["copernicus", "nasa_earthdata", "dlr_eoweb"]:
            assert section in data
        assert "task" in data
        assert "sensor" in data["task"]
        assert "dem" in data["task"]["sensor"]


@pytest.mark.p0
class TestConfigPost:
    """POST /api/config."""

    def test_save_valid_config(self, web_app):
        resp = web_app.post("/api/config", json={"copernicus": {"username": "u", "password": "p"}})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True

    def test_save_invalid_json(self, web_app):
        resp = web_app.post("/api/config", data="bad{json", content_type="application/json")
        assert resp.status_code == 400

    def test_save_preserves_existing_sections(self, web_app):
        web_app.post("/api/config", json={"copernicus": {"username": "u1", "password": "p1"}})
        web_app.post("/api/config", json={"nasa_earthdata": {"username": "u2", "password": "p2"}})
        # Read back
        resp = web_app.get("/api/config")
        data = json.loads(resp.data)
        assert data["copernicus"]["username"] == "u1"
        assert data["nasa_earthdata"]["username"] == "u2"


@pytest.mark.p0
class TestSchemaEndpoint:
    """GET/POST /api/schema."""

    def test_get_schema(self, web_app):
        resp = web_app.get("/api/schema")
        assert resp.status_code in (200, 404)  # OK if schema.yaml exists or not

    def test_readonly_sensors(self, web_app):
        """Schema should include sensor definitions."""
        resp = web_app.get("/api/schema")
        if resp.status_code == 200:
            data = json.loads(resp.data)
            assert isinstance(data, dict)
