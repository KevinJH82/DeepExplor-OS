"""Web API 端点测试 — 覆盖所有端点"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
os.environ["FLASK_ENV"] = "test"

from web_app import app


TEST_DIR = Path(__file__).parent


def test_index_page():
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Prospector" in resp.data


def test_api_minerals():
    client = app.test_client()
    resp = client.get("/api/minerals")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "minerals" in data
    assert len(data["minerals"]) >= 8
    assert "铜" in data["minerals"]


def test_api_tasks():
    client = app.test_client()
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tasks" in data
    assert isinstance(data["tasks"], list)


def test_api_status_not_found():
    client = app.test_client()
    resp = client.get("/api/status/nonexistent_task_id")
    assert resp.status_code == 404


def test_api_upload_no_file():
    client = app.test_client()
    resp = client.post("/api/upload")
    assert resp.status_code == 400


def test_api_upload_bad_format():
    client = app.test_client()
    data = {"file": (__file__, "test.txt")}
    resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_api_upload_kml_starts_task():
    client = app.test_client()
    kml_path = TEST_DIR / "test_roi.kml"
    with open(kml_path, "rb") as f:
        data = {"file": (f, "test_roi.kml"), "mineral": "铜"}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    result = resp.get_json()
    assert "task_id" in result
    assert result["status"] == "started"

    resp2 = client.get(f"/api/status/{result['task_id']}")
    assert resp2.status_code == 200
    status = resp2.get_json()
    assert status["task_id"] == result["task_id"]
    assert status["status"] in ("pending", "running")


def test_api_delete_task():
    client = app.test_client()
    kml_path = TEST_DIR / "test_roi.kml"
    with open(kml_path, "rb") as f:
        data = {"file": (f, "test_roi.kml"), "mineral": "铜"}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    task_id = resp.get_json()["task_id"]

    resp2 = client.delete(f"/api/tasks/{task_id}")
    assert resp2.status_code == 200

    resp3 = client.get(f"/api/status/{task_id}")
    assert resp3.status_code == 404


# ===== 第三阶段新增端点 =====

def test_api_parse_roi():
    client = app.test_client()
    kml_path = TEST_DIR / "test_roi.kml"
    with open(kml_path, "rb") as f:
        data = {"file": (f, "test_roi.kml"), "buffer": "20"}
        resp = client.post("/api/parse-roi", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    result = resp.get_json()
    assert "geometry" in result
    assert "bbox" in result
    assert "center" in result
    assert "area_km2" in result
    assert result["center"]["lon"] > 117
    assert result["area_km2"] > 0


def test_api_parse_roi_bad_file():
    client = app.test_client()
    data = {"file": (__file__, "test.txt")}
    resp = client.post("/api/parse-roi", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_api_tectonic_overlay():
    client = app.test_client()
    resp = client.get("/api/tectonic-overlay?west=115&south=28&east=122&north=33")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) > 0
    names = [f["properties"]["name"] for f in data["features"]]
    assert any("扬子" in n for n in names)


def test_api_tectonic_overlay_default():
    client = app.test_client()
    resp = client.get("/api/tectonic-overlay")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["features"]) > 0


def test_api_task_detail_not_found():
    client = app.test_client()
    resp = client.get("/api/task-detail/nonexistent")
    assert resp.status_code == 404


def test_api_projects_crud():
    client = app.test_client()

    # 创建
    resp = client.post("/api/projects",
                       data=json.dumps({"name": "测试项目", "description": "单元测试"}),
                       content_type="application/json")
    assert resp.status_code == 201
    project = resp.get_json()
    pid = project["id"]
    assert project["name"] == "测试项目"

    # 列表
    resp2 = client.get("/api/projects")
    assert resp2.status_code == 200
    projects = resp2.get_json()["projects"]
    assert any(p["id"] == pid for p in projects)

    # 删除
    resp3 = client.delete(f"/api/projects/{pid}")
    assert resp3.status_code == 200

    # 删除后确认
    resp4 = client.get("/api/projects")
    projects_after = resp4.get_json()["projects"]
    assert not any(p["id"] == pid for p in projects_after)


def test_api_projects_empty_name():
    client = app.test_client()
    resp = client.post("/api/projects",
                       data=json.dumps({"name": "  "}),
                       content_type="application/json")
    assert resp.status_code == 400
