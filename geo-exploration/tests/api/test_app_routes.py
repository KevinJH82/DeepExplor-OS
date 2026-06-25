"""
geo-exploration API route tests
upload, start_analysis, task_status, download, clear_uploads
"""
import io
import json
import os
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "Python_Project" / "web_app"))


@pytest.fixture
def exploration_app(monkeypatch, tmp_path):
    """Create Flask test client for geo-exploration."""
    import app as exploration_app_mod
    exploration_app_mod.app.config["TESTING"] = True
    with exploration_app_mod.app.test_client() as client:
        yield client


@pytest.mark.p0
class TestExplorationRoutes:
    """Core geo-exploration API endpoints."""

    def test_index_page(self, exploration_app):
        resp = exploration_app.get("/")
        assert resp.status_code in (200, 302)

    def test_upload_no_file(self, exploration_app):
        resp = exploration_app.post("/upload")
        assert resp.status_code in (200, 302, 400, 404)

    def test_task_status_no_task(self, exploration_app):
        resp = exploration_app.get("/task_status/no-such-task")
        assert resp.status_code in (200, 404)

    def test_download_no_task(self, exploration_app):
        resp = exploration_app.get("/download/no-such-task")
        assert resp.status_code in (404, 200)

    def test_clear_uploads(self, exploration_app):
        resp = exploration_app.post("/clear_uploads")
        assert resp.status_code in (200, 302, 404)


@pytest.mark.p0
class TestUploadRegression:
    """上传相关回归用例 —— 覆盖 2026-05-14 修复的两个 bug：
    1) 中文文件名被 secure_filename 砍成只剩扩展名（如 经纬度坐标.csv -> csv）；
    2) save_uploaded_file 用相对路径 'uploads/...'，依赖运行时 CWD，
       换属主 / 换启动方式后写入失败或写错位置。
    """

    def test_make_safe_filename_preserves_chinese(self):
        from utils.file_utils import make_safe_filename
        # 回归点：中文名必须完整保留，不能被砍成只剩扩展名
        assert make_safe_filename("经纬度坐标.csv") == "经纬度坐标.csv"
        assert make_safe_filename("测试数据.zip") == "测试数据.zip"

    def test_make_safe_filename_strips_path_traversal(self):
        from utils.file_utils import make_safe_filename
        # 路径穿越 / 分隔符必须被剥掉
        assert make_safe_filename("../../etc/passwd") == "passwd"
        assert "/" not in make_safe_filename("a/b/c.csv")
        assert "\\" not in make_safe_filename("a\\b\\c.csv")

    def test_make_safe_filename_empty_fallback(self):
        from utils.file_utils import make_safe_filename
        # 退化输入（全是点 / 空）走时间戳兜底，不能返回空串
        assert make_safe_filename("...").startswith("upload_")
        assert make_safe_filename("").startswith("upload_")

    def test_save_uploaded_file_absolute_and_preserves_name(self, tmp_path, monkeypatch):
        from werkzeug.datastructures import FileStorage
        from config.config import Config
        from utils import file_utils

        monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        monkeypatch.setattr(Config, "TEMP_FOLDER", str(tmp_path / "uploads" / "temp"))

        fs = FileStorage(stream=io.BytesIO(b"lon,lat\n116.3,39.9\n"),
                         filename="经纬度坐标.csv")
        path = file_utils.save_uploaded_file(fs, fs.filename, "roi_file")

        # 回归点1：返回绝对路径，落在 Config.UPLOAD_FOLDER 下，不依赖 CWD
        assert os.path.isabs(path)
        assert path.startswith(str(tmp_path))
        # 回归点2：中文名完整保留并真实落盘
        assert os.path.basename(path) == "经纬度坐标.csv"
        assert os.path.exists(path)

    def test_upload_api_preserves_chinese_filename(self, exploration_app, tmp_path, monkeypatch):
        from config.config import Config

        monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        monkeypatch.setattr(Config, "TEMP_FOLDER", str(tmp_path / "uploads" / "temp"))

        data = {
            "type": "roi_file",
            "file": (io.BytesIO(b"lon,lat\n116.3,39.9\n"), "经纬度坐标.csv"),
        }
        resp = exploration_app.post("/api/upload", data=data,
                                    content_type="multipart/form-data")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        # 回归点：app.py 不能再在调用 save_uploaded_file 之前 secure_filename 砍一刀
        assert body["file_info"]["name"] == "经纬度坐标.csv"
        assert os.path.isabs(body["file_info"]["path"])


# 一个最小的 OGC KML 多边形（带命名空间），坐标为 经度,纬度,高程
OVKML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
 <Document><Placemark><Polygon><outerBoundaryIs><LinearRing>
  <coordinates>116.30,39.90,0 116.40,39.90,0 116.40,40.00,0 116.30,40.00,0</coordinates>
 </LinearRing></outerBoundaryIs></Polygon></Placemark></Document>
</kml>"""


@pytest.mark.p0
class TestOvkmlRoiUpload:
    """ROI 坐标上传支持 .ovkml（= OGC KML/XML）的回归用例。

    要点：.ovkml 不能只过白名单 —— ROI 解析器原本只读表格，
    必须真的从 <coordinates> 标签解析出经纬度，且不能误入表格列识别逻辑。
    """

    def test_ovkml_extension_allowed(self):
        import app as exploration_app_mod
        # 白名单必须接纳 .ovkml（大小写无关）
        assert exploration_app_mod.allowed_file("roi.ovkml") is True
        assert exploration_app_mod.allowed_file("ROI.OVKML") is True

    def test_parse_kml_coordinates_namespaced(self, tmp_path):
        from utils.geo_utils import _parse_kml_coordinates
        f = tmp_path / "roi.ovkml"
        f.write_text(OVKML_SAMPLE, encoding="utf-8")

        lon, lat = _parse_kml_coordinates(str(f))
        # 命名空间无关地取出全部 4 个点，高程被丢弃
        assert list(lon) == [116.30, 116.40, 116.40, 116.30]
        assert list(lat) == [39.90, 39.90, 40.00, 40.00]

    def test_parse_kml_coordinates_no_namespace(self, tmp_path):
        from utils.geo_utils import _parse_kml_coordinates
        # 无 xmlns、且元组只有 经度,纬度（无高程）也要能解析
        kml = ('<kml><Placemark><LineString>'
               '<coordinates>100,30 101,30 101,31</coordinates>'
               '</LineString></Placemark></kml>')
        f = tmp_path / "roi.kml"
        f.write_text(kml, encoding="utf-8")

        lon, lat = _parse_kml_coordinates(str(f))
        assert list(lon) == [100.0, 101.0, 101.0]
        assert list(lat) == [30.0, 30.0, 31.0]

    def test_parse_kml_coordinates_missing_raises(self, tmp_path):
        from utils.geo_utils import _parse_kml_coordinates
        # 没有 <coordinates> / 点数不足 -> 明确报错，不能默默返回乱码
        f = tmp_path / "empty.ovkml"
        f.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>',
                     encoding="utf-8")
        with pytest.raises(ValueError):
            _parse_kml_coordinates(str(f))

    def test_parse_kml_coordinates_bad_xml_raises(self, tmp_path):
        from utils.geo_utils import _parse_kml_coordinates
        # 非法 XML -> ValueError（而不是裸 ParseError 冒泡）
        f = tmp_path / "bad.ovkml"
        f.write_text("<kml><not-closed>", encoding="utf-8")
        with pytest.raises(ValueError):
            _parse_kml_coordinates(str(f))

    def test_read_roi_robust_ovkml_closes_polygon(self, tmp_path):
        from utils.geo_utils import read_roi_robust
        f = tmp_path / "roi.ovkml"
        f.write_text(OVKML_SAMPLE, encoding="utf-8")

        roi = read_roi_robust(str(f))
        # 走 KML 分支：经纬度数组正确
        assert list(roi["lon_roi"]) == [116.30, 116.40, 116.40, 116.30]
        assert list(roi["lat_roi"]) == [39.90, 39.90, 40.00, 40.00]
        # 多边形自动闭合：首尾点相同，且比原始点多一个
        poly = roi["roi_poly"]
        assert poly.shape == (5, 2)
        assert (poly[0] == poly[-1]).all()

    def test_upload_api_accepts_ovkml(self, exploration_app, tmp_path, monkeypatch):
        from config.config import Config

        monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        monkeypatch.setattr(Config, "TEMP_FOLDER", str(tmp_path / "uploads" / "temp"))

        data = {
            "type": "roi_file",
            "file": (io.BytesIO(OVKML_SAMPLE.encode("utf-8")), "区域边界.ovkml"),
        }
        resp = exploration_app.post("/api/upload", data=data,
                                    content_type="multipart/form-data")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        # .ovkml 被接受并完整保留中文名落盘
        assert body["file_info"]["name"] == "区域边界.ovkml"
        assert os.path.isabs(body["file_info"]["path"])
